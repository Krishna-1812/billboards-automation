"""Mirroring to Google, and watching the old sheet.

- Every booking line is an all-day event on its screen's Google Calendar, coloured by status. Cancelled and
  expired bookings have their events removed.
- The app rewrites one tab (Settings → General → mirror sheet) with every booking, so the team's sheet views
  keep working.
- Sheets we imported from are re-read every 10 minutes during the switch-over. A row someone edits there
  directly is flagged in Settings → Sheet changes, so the sheet and the app never quietly drift apart.
"""

import hashlib
import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from . import runtime, settings
from .activity import notify
from .google_client import NotConnected, get_client
from .jobs import Skip, enqueue, handler
from .models import OCCUPYING, Booking, BookingLine, SheetChange, SheetWatch, utcnow

# Google Calendar's event colours: 5 yellow, 10 green, 9 blue, 8 grey, 11 red.
STATUS_COLOUR = {"hold": "5", "confirmed": "10", "live": "9", "completed": "8"}
STATUS_LABEL = {"hold": "HOLD", "confirmed": "CONFIRMED", "live": "LIVE", "completed": "DONE"}


def _google():
    try:
        return get_client(runtime.config)
    except NotConnected as e:
        raise Skip(str(e)) from e


def _app_link(path):
    base = runtime.config.APP_URL if runtime.config else ""
    return f"{base}{path}" if base else path


def hours_text(start_hour, end_hour):
    if start_hour is None or end_hour is None:
        return "all operating hours"
    return f"{start_hour:02d}:00–{end_hour:02d}:00"


# ── Queueing ────────────────────────────────────────────────────────────────────

def queue_booking(session, booking, removed=()):
    """Queue the calendar and sheet updates for a booking that just changed.
    `removed` holds (calendar_id, event_id) of lines that were taken out of it."""
    session.flush()
    for line in booking.lines:
        enqueue(session, "calendar_line", {"line_id": line.id}, dedupe_key=f"cal:line:{line.id}")
    for cal_id, event_id in removed:
        if cal_id and event_id:
            enqueue(session, "calendar_delete", {"calendar_id": cal_id, "event_id": event_id})
    queue_sheet_mirror(session)


def queue_sheet_mirror(session):
    enqueue(session, "sheet_mirror", {}, dedupe_key="sheet_mirror", delay_seconds=3)


# ── Calendar ────────────────────────────────────────────────────────────────────

def event_body(line):
    b, s = line.booking, line.screen
    label = STATUS_LABEL.get(b.status, b.status.replace("_", " ").upper())
    title = f" · {b.title}" if b.title else ""
    desc = "\n".join([
        f"Booking: {b.code} ({b.status.replace('_', ' ')})",
        f"Client: {b.client.name}",
        f"Screen: {s.code} {s.name}",
        f"Dates: {line.start_date:%d %b %Y} to {line.end_date:%d %b %Y}",
        f"Hours: {hours_text(line.start_hour, line.end_hour)}",
        "Whole screen" if line.exclusive else f"Ad: {line.ad_seconds}s × {line.plays_per_hour:g} plays an hour",
        f"Hold expires: {b.hold_expires_at:%d %b %Y %H:%M} UTC" if b.status == "hold" and b.hold_expires_at else "",
        _app_link(f"/bookings/{b.code}"),
    ])
    return {
        "summary": f"[{label}] {b.client.name}{title} ({b.code})",
        "description": desc.replace("\n\n", "\n"),
        "start": {"date": line.start_date.isoformat()},
        "end": {"date": (line.end_date + timedelta(days=1)).isoformat()},
        "colorId": STATUS_COLOUR.get(b.status, "7"),
        "transparency": "transparent",
        "extendedProperties": {"private": {"billboard_ops_line": str(line.id), "booking": b.code}},
    }


@handler("calendar_line")
def _calendar_line(session, payload):
    line = session.get(BookingLine, payload["line_id"],
                       options=[joinedload(BookingLine.booking).joinedload(Booking.client),
                                joinedload(BookingLine.screen)])
    if line is None:
        return
    cal = line.screen.calendar_id
    if not cal:
        raise Skip(f"Screen {line.screen.code} has no Google Calendar set.")
    g = _google()
    if line.booking.status not in OCCUPYING:
        if line.calendar_event_id:
            g.delete_event(cal, line.calendar_event_id)
            line.calendar_event_id = ""
        return
    line.calendar_event_id = g.upsert_event(cal, line.calendar_event_id, event_body(line))


@handler("calendar_delete")
def _calendar_delete(session, payload):
    _google().delete_event(payload["calendar_id"], payload["event_id"])


# ── The mirror tab ──────────────────────────────────────────────────────────────

MIRROR_HEADER = ["Booking", "Status", "Client", "Campaign", "Screen code", "Screen", "Start", "End", "Hours",
                 "Ad seconds", "Plays per hour", "Whole screen", "Hold expires (UTC)", "Updated (UTC)"]


def mirror_rows(session):
    lines = session.scalars(select(BookingLine).join(Booking)
                            .options(joinedload(BookingLine.booking).joinedload(Booking.client),
                                     joinedload(BookingLine.screen))
                            .order_by(BookingLine.start_date.desc(), Booking.code)).unique().all()
    rows = [MIRROR_HEADER]
    for l in lines:
        b = l.booking
        rows.append([b.code, b.status, b.client.name, b.title, l.screen.code, l.screen.name,
                     l.start_date.isoformat(), l.end_date.isoformat(), hours_text(l.start_hour, l.end_hour),
                     l.ad_seconds, l.plays_per_hour, "yes" if l.exclusive else "",
                     b.hold_expires_at.strftime("%Y-%m-%d %H:%M") if b.status == "hold" and b.hold_expires_at else "",
                     b.updated_at.strftime("%Y-%m-%d %H:%M") if b.updated_at else ""])
    return rows


@handler("sheet_mirror")
def _sheet_mirror(session, payload):
    sheet = settings.get(session, "mirror_spreadsheet_id")
    if not sheet:
        raise Skip("No mirror sheet set (Settings → General).")
    _google().write_values(sheet, settings.get(session, "mirror_tab"), mirror_rows(session))


# ── Watching the old sheet ──────────────────────────────────────────────────────

def _clean(row):
    vals = [str(v).strip() for v in row]
    while vals and vals[-1] == "":
        vals.pop()
    return vals


def row_hash(row):
    return hashlib.sha1(json.dumps(_clean(row), ensure_ascii=False).encode()).hexdigest()[:16]


def keyed_rows(values, key_column):
    """{row key: row} for a sheet's data rows (the first row is the header). Rows are keyed by the booking
    reference column when there is one, else by their row number."""
    out = {}
    for i, row in enumerate(values[1:], start=2):
        row = _clean(row)
        if not row:
            continue
        key = ""
        if key_column is not None and key_column < len(row):
            key = row[key_column]
        key = key or f"row {i}"
        if key in out:
            key = f"{key} (row {i})"
        out[key] = row
    return out


def baseline(values, key_column):
    return {k: row_hash(r) for k, r in keyed_rows(values, key_column).items()}


def compare(watch, values):
    """[(row_key, change, before_hash, row)] for rows added, changed or removed since the baseline."""
    now = keyed_rows(values, watch.key_column)
    base = watch.row_hashes or {}
    out = []
    for k, row in now.items():
        if k not in base:
            out.append((k, "added", row))
        elif base[k] != row_hash(row):
            out.append((k, "changed", row))
    for k in base:
        if k not in now:
            out.append((k, "removed", None))
    return out


@handler("sheet_watch_check")
def _sheet_watch_check(session, payload):
    watch = session.get(SheetWatch, payload["watch_id"])
    if watch is None or not watch.active:
        return
    values = _google().read_values(watch.spreadsheet_id, watch.tab)
    watch.last_checked_at = utcnow()
    open_flags = {(c.row_key, c.change, json.dumps(c.after)) for c in session.scalars(
        select(SheetChange).where(SheetChange.watch_id == watch.id, SheetChange.resolved_at.is_(None)))}
    new = 0
    for key, change, row in compare(watch, values):
        if (key, change, json.dumps(row)) in open_flags:
            continue
        session.add(SheetChange(watch_id=watch.id, row_key=key, change=change, after=row))
        new += 1
    if new:
        notify(session, f"{new} row(s) were edited directly in the old sheet '{watch.tab}'. Review them so the "
                        "app and the sheet agree.", kind="warning", link="/settings/sheet-changes")


def accept_change(session, change, actor):
    """Mark a sheet edit reviewed: the sheet's current row becomes the new baseline."""
    watch = session.get(SheetWatch, change.watch_id)
    hashes = dict(watch.row_hashes or {})
    if change.change == "removed":
        hashes.pop(change.row_key, None)
    else:
        hashes[change.row_key] = row_hash(change.after or [])
    watch.row_hashes = hashes
    change.resolved_at = utcnow()
    change.resolved_by = actor
