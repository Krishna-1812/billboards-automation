"""Placing, changing, confirming and cancelling bookings.

Every change goes through here, so every change is checked against the vacancy engine, logged, and mirrored to
Google. An overbooking is refused unless a Manager (or the owner) overrides it and says why.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select

from . import settings, sync, vacancy
from .activity import diff, log, notify
from .mail import queue as queue_email
from .models import Booking, BookingCounter, BookingLine, Screen, User, utcnow
from .permissions import require

LINE_FIELDS = ("screen_id", "start_date", "end_date", "start_hour", "end_hour", "ad_seconds", "plays_per_hour",
               "exclusive")


@dataclass
class LineIn:
    screen_id: int
    start_date: date
    end_date: date
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    ad_seconds: int = 10
    plays_per_hour: float = 30
    exclusive: bool = False
    id: Optional[int] = None          # an existing line being edited

    def request(self):
        return vacancy.Request(self.start_date, self.end_date, self.start_hour, self.end_hour,
                               self.ad_seconds, self.plays_per_hour, self.exclusive)


class BookingError(Exception):
    pass


class Overbooked(BookingError):
    def __init__(self, results):
        self.results = results     # [(LineIn, Screen, vacancy.Result)]
        bad = [f"{s.code}: {r.status} ({r.reason or 'full'})" for _, s, r in results if r.status != "fits"]
        super().__init__("Not enough room: " + "; ".join(bad))


def actor_of(user):
    return user.email if user is not None else "system"


def next_code(session, year):
    q = select(BookingCounter).where(BookingCounter.year == year)
    from . import db
    if db.is_postgres():
        q = q.with_for_update()
    row = session.scalars(q).first()
    if row is None:
        row = BookingCounter(year=year, last=0)
        session.add(row)
        session.flush()
    row.last += 1
    return f"BK-{year}-{row.last:04d}"


def validate_line(screen, li):
    errors = []
    if screen is None:
        return ["Unknown screen."]
    if not screen.active:
        errors.append(f"{screen.code} is not active.")
    if li.end_date < li.start_date:
        errors.append("The end date is before the start date.")
    if (li.start_hour is None) != (li.end_hour is None):
        errors.append("Give both a start and an end hour, or neither for all operating hours.")
    for h in (li.start_hour, li.end_hour):
        if h is not None and not 0 <= h <= 24:
            errors.append("Hours must be between 0 and 24.")
            break
    if not li.exclusive:
        if not li.ad_seconds or li.ad_seconds <= 0:
            errors.append("The ad length must be more than 0 seconds.")
        if not li.plays_per_hour or li.plays_per_hour <= 0:
            errors.append("Plays per hour must be more than 0.")
    return errors


def check_lines(session, lines, exclude_booking_id=None, today=None, alternatives=False):
    out = []
    for li in lines:
        screen = session.get(Screen, li.screen_id)
        res = vacancy.check_screen(session, screen, li.request(), exclude_booking_id, today, alternatives)
        out.append((li, screen, res))
    return out


def _checked(session, user, lines, override_reason, exclude_booking_id=None):
    """Validate and check every line; refuse an overbooking unless it is a permitted override."""
    if not lines:
        raise BookingError("A booking needs at least one screen.")
    errors = []
    for li in lines:
        errors += validate_line(session.get(Screen, li.screen_id), li)
    if errors:
        raise BookingError(" ".join(dict.fromkeys(errors)))
    _lock_screens(session, {li.screen_id for li in lines})
    results = check_lines(session, lines, exclude_booking_id)
    if any(r.status != "fits" for _, _, r in results):
        if not override_reason.strip():
            raise Overbooked(results)
        require(user, "override")
    return results


def _lock_screens(session, screen_ids):
    """On Postgres, two people booking the same screen at the same moment take turns: each checks vacancy and
    saves inside its own transaction while holding the screen's row, so both can't take the last free slot."""
    from . import db
    if db.is_postgres() and screen_ids:
        session.execute(select(Screen.id).where(Screen.id.in_(sorted(screen_ids))).order_by(Screen.id)
                        .with_for_update())


def _apply_lines(booking, lines):
    """Bring booking.lines in line with `lines`. Returns the calendar events of lines taken out."""
    keep = {li.id for li in lines if li.id}
    removed = []
    for old in list(booking.lines):
        if old.id not in keep:
            removed.append((old.screen.calendar_id if old.screen else "", old.calendar_event_id))
            booking.lines.remove(old)
    by_id = {l.id: l for l in booking.lines}
    for li in lines:
        target = by_id.get(li.id) if li.id else None
        if target is None:
            target = BookingLine()
            booking.lines.append(target)
        elif target.screen_id != li.screen_id and target.calendar_event_id:
            # Moved to another screen: its event leaves the old screen's calendar; a new one is made.
            removed.append((target.screen.calendar_id if target.screen else "", target.calendar_event_id))
            target.calendar_event_id = ""
        for f in LINE_FIELDS:
            setattr(target, f, getattr(li, f))
    return removed


def _line_summary(booking):
    return [{f: (getattr(l, f).isoformat() if isinstance(getattr(l, f), date) else getattr(l, f))
             for f in LINE_FIELDS} for l in booking.lines]


def create(session, user, client, lines, title="", status="hold", override_reason="", notes="",
           source="manual", source_ref="", now=None):
    require(user, "confirm" if status != "hold" else "hold")
    now = now or utcnow()
    _checked(session, user, lines, override_reason)
    b = Booking(code=next_code(session, now.year), client=client, title=title, status=status, notes=notes,
                override_reason=override_reason.strip(), source=source, source_ref=source_ref,
                created_by=actor_of(user))
    if status == "hold":
        b.hold_expires_at = now + timedelta(days=int(settings.get(session, "hold_days")))
    elif status == "confirmed":
        b.confirmed_at, b.confirmed_by = now, actor_of(user)
    session.add(b)
    _apply_lines(b, lines)
    session.flush()
    what = "hold" if status == "hold" else "booking"
    log(session, actor_of(user), f"created_{status}", "booking", b.code,
        f"New {what} for {client.name}: {len(lines)} screen(s)" +
        (f". Over capacity, overridden: {override_reason.strip()}" if override_reason.strip() else ""),
        {"lines": _line_summary(b)})
    if override_reason.strip():
        notify(session, f"{b.code} was booked past a screen's capacity by {actor_of(user)}: "
                        f"{override_reason.strip()}", kind="warning", link=f"/bookings/{b.code}")
    sync.queue_booking(session, b)
    return b


def update(session, user, booking, lines, title=None, notes=None, override_reason=""):
    require(user, "hold" if booking.status == "hold" else "edit_confirmed")
    if booking.status in ("cancelled", "expired", "completed"):
        raise BookingError(f"A {booking.status} booking can't be changed.")
    before = {"title": booking.title, "notes": booking.notes, "lines": _line_summary(booking)}
    _checked(session, user, lines, override_reason, exclude_booking_id=booking.id)
    removed = _apply_lines(booking, lines)
    if title is not None:
        booking.title = title
    if notes is not None:
        booking.notes = notes
    if override_reason.strip():
        booking.override_reason = override_reason.strip()
        notify(session, f"{booking.code} was changed past a screen's capacity by {actor_of(user)}: "
                        f"{override_reason.strip()}", kind="warning", link=f"/bookings/{booking.code}")
    session.flush()
    after = {"title": booking.title, "notes": booking.notes, "lines": _line_summary(booking)}
    log(session, actor_of(user), "updated", "booking", booking.code, "Booking changed", diff(before, after))
    sync.queue_booking(session, booking, removed)
    return booking


def confirm(session, user, booking, now=None, document=None):
    """Confirm a hold. `document` (filename, content_type, bytes) is the signed proposal or purchase order."""
    require(user, "confirm")
    if booking.status != "hold":
        raise BookingError(f"Only a hold can be confirmed; this booking is {booking.status}.")
    now = now or utcnow()
    if document and document[2]:
        from .models import BookingDocument
        session.add(BookingDocument(booking_id=booking.id, filename=document[0][:300], content_type=document[1][:120],
                                    data=document[2], uploaded_by=actor_of(user)))
    booking.status = "confirmed"
    booking.confirmed_at, booking.confirmed_by = now, actor_of(user)
    booking.hold_expires_at = None
    log(session, actor_of(user), "confirmed", "booking", booking.code, f"Confirmed for {booking.client.name}")
    sync.queue_booking(session, booking)
    return booking


def cancel(session, user, booking, reason):
    require(user, "cancel_hold" if booking.status == "hold" else "cancel_confirmed")
    if booking.status in ("cancelled", "expired", "completed"):
        raise BookingError(f"This booking is already {booking.status}.")
    if not (reason or "").strip():
        raise BookingError("Say why the booking is cancelled.")
    old = booking.status
    booking.status = "cancelled"
    booking.cancel_reason = reason.strip()
    booking.hold_expires_at = None
    log(session, actor_of(user), "cancelled", "booking", booking.code, f"Cancelled ({old}): {reason.strip()}")
    sync.queue_booking(session, booking)
    return booking


def extend_hold(session, user, booking, days, now=None):
    require(user, "hold")
    if booking.status != "hold":
        raise BookingError("Only a hold can be extended.")
    now = now or utcnow()
    start = max(booking.hold_expires_at or now, now)
    booking.hold_expires_at = start + timedelta(days=int(days))
    booking.hold_reminded_at = None
    log(session, actor_of(user), "extended_hold", "booking", booking.code,
        f"Hold extended by {int(days)} day(s), to {booking.hold_expires_at:%d %b %Y %H:%M} UTC")
    sync.queue_booking(session, booking)
    return booking


# ── Run by the worker every minute ──────────────────────────────────────────────

def _team_emails(session, roles=("owner", "manager")):
    return [u.email for u in session.scalars(select(User).where(User.active.is_(True), User.role.in_(roles)))]


def expire_holds(session, now=None):
    now = now or utcnow()
    due = session.scalars(select(Booking).where(Booking.status == "hold", Booking.hold_expires_at.is_not(None),
                                                Booking.hold_expires_at <= now)).all()
    for b in due:
        b.status = "expired"
        log(session, "system", "expired", "booking", b.code, "Hold expired without being confirmed")
        notify(session, f"Hold {b.code} for {b.client.name} expired; its screen time is free again.",
               kind="info", link=f"/bookings/{b.code}")
        sync.queue_booking(session, b)
    return due


def remind_holds(session, now=None):
    now = now or utcnow()
    hours = int(settings.get(session, "hold_reminder_hours"))
    due = session.scalars(select(Booking).where(Booking.status == "hold", Booking.hold_reminded_at.is_(None),
                                                Booking.hold_expires_at.is_not(None),
                                                Booking.hold_expires_at <= now + timedelta(hours=hours),
                                                Booking.hold_expires_at > now)).all()
    for b in due:
        b.hold_reminded_at = now
        left = b.hold_expires_at - now
        text = (f"Hold {b.code} for {b.client.name} expires in {int(left.total_seconds() // 3600)} hours "
                f"({b.hold_expires_at:%d %b %H:%M} UTC). Confirm it, extend it, or let it go.")
        notify(session, text, kind="reminder", link=f"/bookings/{b.code}")
        to = sorted(set(_team_emails(session) + ([b.created_by] if "@" in (b.created_by or "") else [])))
        queue_email(session, to, f"Hold {b.code} expires soon", text)
        from .intake import hold_reminder_drafts
        if hold_reminder_drafts(session, b) is not None:
            notify(session, f"A reminder to {b.client.name} about hold {b.code} is waiting for approval in the "
                            "Outbox.", kind="reminder", link="/outbox")
    return due


def complete_finished(session, today):
    """Confirmed bookings whose last day has passed are marked completed."""
    done = []
    for b in session.scalars(select(Booking).where(Booking.status.in_(("confirmed", "scheduled", "live")))).all():
        if b.end_date and b.end_date < today:
            b.status = "completed"
            log(session, "system", "completed", "booking", b.code, "Campaign ended")
            sync.queue_booking(session, b)
            done.append(b)
    return done
