"""From approved files to the end of the campaign (Phase 4).

A confirmed booking is ready to play when three things are done for every screen: its file is approved, its play
plan is confirmed, and Ops has loaded it onto the screen. The status follows:

    creative stages → Creative approved → Play plan set → Scheduled (loaded) → Live (start date) → Completed

    advance()    moves a booking along as those three things get done, and notices when a screen needs loading
                 again (a newer file was approved, or the plan changed, after it was loaded).
    tick()       once a minute: the day-before readiness alert, going live on the start date, the renewal offer
                 before the end, and the wrap-up after it.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import client_messages, creatives, settings, sync
from .activity import log
from .bookings import actor_of
from .jobs import enqueue
from .models import BOOKED, CREATIVE_STAGES, Booking, BookingLine, Proof, utcnow
from .notices import alert

READY_FLOW = ("creative_approved", "plan_set", "scheduled")


class LoadError(Exception):
    pass


def local_now(session):
    tz = settings.get(session, "default_timezone") or "Asia/Dubai"
    try:
        return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 - an unknown zone name falls back to UTC
        return datetime.utcnow()


def today(session):
    return local_now(session).date()


# ── Where a booking stands ──────────────────────────────────────────────────────

def approved_files(session, line, files=None):
    """The approved files that play on this screen (the client's own approval too, when it's asked for)."""
    from .models import Creative
    if files is None:
        files = session.scalars(select(Creative).where(Creative.booking_id == line.booking_id,
                                                       Creative.status == "approved")
                                .order_by(Creative.version)).all()
    need_client = settings.get(session, "client_approves_final")
    return [c for c in files if c.status == "approved" and line.id in (c.line_ids or [])
            and (not need_client or c.client_approved_at is not None)]


def line_signature(session, line, files=None):
    """What was loaded: which files, under which plan. When it changes, the screen needs loading again."""
    from .plans import signature
    ids = ",".join(str(c.id) for c in approved_files(session, line, files))
    return f"{ids}|{signature(line.confirmed_plan)}"


def readiness(session, booking):
    cov = creatives.coverage(session, booking) if booking.lines else []
    files_ok = bool(cov) and all(x["state"] == "approved" for x in cov)
    plan_ok = booking.plan_status == "confirmed"
    loaded_ok = bool(booking.lines) and all(l.loaded_at for l in booking.lines)
    missing = []
    if not files_ok:
        waiting = [x["screen"].code for x in cov if x["state"] != "approved"]
        missing.append("approved files" + (f" ({', '.join(waiting)})" if waiting else ""))
    if not plan_ok:
        missing.append({"none": "a play plan", "draft": "the play plan confirmed",
                        "waiting_client": "the client's approval of the play plan"}.get(booking.plan_status,
                                                                                       "a play plan"))
    if not loaded_ok:
        todo = [l.screen.code for l in booking.lines if not l.loaded_at]
        missing.append("loading onto the screens" + (f" ({', '.join(todo)})" if todo else ""))
    return {"files": files_ok, "plan": plan_ok, "loaded": loaded_ok, "missing": missing, "coverage": cov,
            "ready": files_ok and plan_ok and loaded_ok}


def _check_reload(session, booking, actor):
    for l in booking.lines:
        if l.loaded_at and l.loaded_signature != line_signature(session, l):
            l.loaded_at, l.loaded_by = None, ""
            log(session, actor, "needs_reload", "booking", booking.code,
                f"{l.screen.code} needs loading again: its file or play plan changed after it was loaded")
            alert(session, "live", f"{booking.code}: {l.screen.code} needs loading again. Its file or play plan "
                                   "changed after it was loaded.", link=f"/bookings/{booking.code}", kind="warning")


def advance(session, booking, actor="agent", on=None):
    """Move the booking to the stage its files, plan and loading have reached."""
    if booking.status not in CREATIVE_STAGES + READY_FLOW + ("live",):
        return None
    session.flush()
    _check_reload(session, booking, actor)
    r = readiness(session, booking)
    if r["files"]:
        _queue_ready_folder(session, booking)
    if booking.status == "live":
        return r
    before = booking.status
    if not r["files"]:
        new = "awaiting_creative" if before in READY_FLOW else before
    elif r["plan"] and r["loaded"]:
        new = "scheduled"
    elif r["plan"]:
        new = "plan_set"
    else:
        new = "creative_approved"
    if new != before:
        booking.status = new
        log(session, actor, "stage", "booking", booking.code, f"Now {new.replace('_', ' ')}")
        sync.queue_booking(session, booking)
    day = on or today(session)
    if booking.status == "scheduled" and booking.start_date and booking.start_date <= day <= booking.end_date:
        go_live(session, booking, actor)
    return r


def _queue_ready_folder(session, booking):
    """Copy newly approved files into the Ready to load folder (only when there's something new to copy)."""
    from . import creative_store
    if creative_store.drive(session) is None:
        return
    todo = False
    for l in booking.lines:
        done = {x.get("creative_id") for x in (l.load_files or [])}
        if any(c.id not in done for c in approved_files(session, l)):
            todo = True
    if todo:
        session.flush()
        enqueue(session, "ready_to_load", {"booking_id": booking.id}, dedupe_key=f"ready_to_load:{booking.id}")


# ── Loading ─────────────────────────────────────────────────────────────────────

def mark_loaded(session, user, booking, line, loaded=True):
    from .permissions import require
    require(user, "load")
    if line.booking_id != booking.id:
        raise LoadError("That screen isn't part of this booking.")
    if loaded:
        if not approved_files(session, line):
            raise LoadError(f"{line.screen.code} has no approved file to load yet.")
        if booking.plan_status != "confirmed":
            raise LoadError("Confirm the play plan before loading, so the screen gets the right frequency.")
        line.loaded_at, line.loaded_by = utcnow(), actor_of(user)
        line.loaded_signature = line_signature(session, line)
        log(session, actor_of(user), "loaded", "booking", booking.code, f"{line.screen.code} loaded on the screen")
    else:
        line.loaded_at, line.loaded_by, line.loaded_signature = None, "", ""
        log(session, actor_of(user), "unloaded", "booking", booking.code, f"{line.screen.code} marked not loaded")
    return advance(session, booking, actor=actor_of(user))


# ── Going live and ending ───────────────────────────────────────────────────────

def go_live(session, booking, actor="agent"):
    booking.status = "live"
    booking.live_at = utcnow()
    log(session, actor, "live", "booking", booking.code, "Live: the campaign started")
    client_messages.live_notice(session, booking)
    alert(session, "live", f"{booking.code} for {booking.client.name} is live.", link=f"/bookings/{booking.code}")
    sync.queue_booking(session, booking)


def tick(session, on=None):
    """The day-before readiness alert, going live, renewal offers and wrap-ups."""
    day = on or today(session)
    # Only bookings around their start need looking at here: every other change (a file approved, a plan
    # confirmed, a screen loaded) moves its booking on at the moment it happens.
    near = select(BookingLine.booking_id).where(BookingLine.start_date <= day + timedelta(days=1),
                                                BookingLine.end_date >= day)
    q = (select(Booking).options(selectinload(Booking.lines).selectinload(BookingLine.screen))
         .where(Booking.status.in_(BOOKED), Booking.status != "live", Booking.id.in_(near)))
    for b in session.scalars(q).all():
        if not b.lines:
            continue
        if b.status != "live":
            advance(session, b, on=day)
        if b.status in ("live", "completed"):
            continue
        to_start = (b.start_date - day).days
        if to_start <= 1 and b.end_date >= day and b.ready_alerted_on != day:
            r = readiness(session, b)
            if r["ready"]:
                continue
            b.ready_alerted_on = day
            when = "tomorrow" if to_start == 1 else ("today" if to_start == 0 else f"on {b.start_date:%d %b}")
            text = (f"Not ready: {b.code} for {b.client.name} starts {when} and still needs "
                    + "; ".join(r["missing"]) + ".")
            alert(session, "readiness", text, link=f"/bookings/{b.code}", kind="error",
                  subject=f"Not ready: {b.code} starts {when}")
            log(session, "agent", "not_ready", "booking", b.code, text[:500])
    renew_days = int(settings.get(session, "renewal_days") or 0)
    for b in session.scalars(select(Booking).where(Booking.status == "live")).all():
        if (renew_days and b.renewal_prompted_at is None and b.end_date
                and 0 <= (b.end_date - day).days <= renew_days):
            b.renewal_prompted_at = utcnow()
            client_messages.renewal(session, b)
            alert(session, "renewals", f"{b.code} for {b.client.name} ends on {b.end_date:%d %b}. A renewal offer to "
                                       "the client is in the Outbox; follow it up.", link=f"/bookings/{b.code}",
                  subject=f"Renewal: {b.client.name}'s {b.code} ends {b.end_date:%d %b}")
            log(session, "agent", "renewal_offer", "booking", b.code, "Renewal offer drafted for the client")
    for b in session.scalars(select(Booking).where(Booking.status == "completed", Booking.live_at.is_not(None),
                                                   Booking.wrapped_up_at.is_(None))).all():
        if b.end_date and (day - b.end_date).days <= 7:
            has_proof = session.scalar(select(Proof.id).where(Proof.booking_id == b.id, Proof.shared.is_(True))
                                       .limit(1)) is not None
            b.wrapped_up_at = utcnow()
            client_messages.wrap_up(session, b, has_proof)
            alert(session, "live", f"{b.code} for {b.client.name} has ended. The wrap-up for the client is ready"
                                   + ("" if has_proof else "; no proof of play was added") + ".",
                  link=f"/bookings/{b.code}")
            log(session, "agent", "wrapped_up", "booking", b.code, "Campaign ended; wrap-up drafted")
