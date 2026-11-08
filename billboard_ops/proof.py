"""Proof that a campaign played (Phase 4): photos and videos of the screen, and play reports from the screen
software, counted against the play plan. Shared proof shows in the client's portal and the wrap-up.
"""

import csv
import io
import os
from collections import defaultdict
from datetime import date

from . import creative_store, settings, vacancy
from .activity import log
from .bookings import actor_of
from .models import Proof
from .parsing import parse_date, parse_number
from .permissions import require

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
REPORT_EXT = {".csv", ".xlsx", ".xls"}


class ProofError(Exception):
    pass


def kind_of(filename, content_type=""):
    ext = os.path.splitext(filename or "")[1].lower()
    ct = (content_type or "").lower()
    if ext in REPORT_EXT or "spreadsheet" in ct or ct in ("text/csv",):
        return "report"
    if ct.startswith("video/") or ext in VIDEO_EXT:
        return "video"
    if ct.startswith("image/") or ext in IMAGE_EXT:
        return "photo"
    return ""


def add(session, user, booking, *, filename, content_type="", data=b"", line_id=None, taken_on=None, note="",
        shared=True):
    require(user, "load")
    kind = kind_of(filename, content_type)
    if kind == "":
        raise ProofError("Add a photo, a video, or a play report (CSV or Excel).")
    if line_id and line_id not in {l.id for l in booking.lines}:
        raise ProofError("That screen isn't part of this booking.")
    p = Proof(booking_id=booking.id, line_id=line_id or None, kind=kind, filename=(filename or "file")[:300],
              content_type=(content_type or "")[:120], taken_on=taken_on, note=(note or "")[:2000], shared=shared,
              uploaded_by=actor_of(user))
    if kind == "report":
        p.summary = count_report(session, booking, filename, data)
    session.add(p)
    try:
        creative_store.save(session, booking, p, data=data)
    except creative_store.StoreError as e:
        session.expunge(p)
        raise ProofError(str(e)) from e
    session.flush()
    what = {"photo": "Photo", "video": "Video", "report": "Play report"}[kind]
    extra = ""
    if kind == "report":
        s = p.summary or {}
        extra = f": {s.get('played', 0):,.0f} plays counted against {s.get('planned', 0):,.0f} planned"
    log(session, actor_of(user), "added_proof", "booking", booking.code, f"{what} of play added ({p.filename}){extra}")
    return p


# ── Play reports ────────────────────────────────────────────────────────────────

HEADS = {
    "screen": ("screen", "player", "display", "device", "site", "location", "panel", "frame"),
    "date": ("date", "day", "played at", "play time", "start", "time", "timestamp"),
    "plays": ("plays", "play count", "count", "spots", "loops", "impressions", "qty", "quantity", "times"),
    "file": ("file", "media", "content", "creative", "asset", "ad name", "advert", "campaign", "name"),
}


def _rows(filename, data):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in (".xlsx", ".xls"):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001
            raise ProofError(f"The spreadsheet couldn't be read: {e}") from e
        return [[("" if c is None else c) for c in r] for r in wb.active.iter_rows(values_only=True)]
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


def _find_columns(header):
    names = [str(h or "").strip().lower() for h in header]
    found = {}
    for key, words in HEADS.items():
        for w in words:
            for i, n in enumerate(names):
                if i not in found.values() and (n == w or w in n):
                    found.setdefault(key, i)
            if key in found:
                break
    return found


def _when(v, order):
    return parse_date(v if isinstance(v, date) else str(v or "").strip(), order)


def planned_on(session, line, day, rules=None):
    rules = rules or vacancy.load_rules(session, line.screen)
    use = vacancy.use_from_line(line)
    total = 0.0
    for slot in vacancy.playing_slots(rules, use, day):
        total += vacancy.slot_demand(use, rules, day, slot) / max(1, line.ad_seconds)
    return total


def count_report(session, booking, filename, data):
    """Plays per screen and day for this booking, from a play report, against what the plan planned."""
    rows = _rows(filename, data)
    if len(rows) < 2:
        raise ProofError("The report is empty.")
    cols = _find_columns(rows[0])
    if "screen" not in cols or "date" not in cols:
        raise ProofError("The report needs a column for the screen (or player) and one for the date.")
    order = settings.get(session, "date_order") or "dmy"
    stems = {booking.code.lower()}
    from sqlalchemy import select
    from .models import Creative
    for (fn,) in session.execute(select(Creative.filename).where(Creative.booking_id == booking.id)):
        stems.add(os.path.splitext(fn)[0].lower())
    by_line = defaultdict(lambda: defaultdict(float))
    matched = skipped = 0
    for r in rows[1:]:
        if not any(str(x).strip() for x in r):
            continue
        get = lambda k: r[cols[k]] if k in cols and cols[k] < len(r) else ""  # noqa: E731
        screen = str(get("screen")).strip().lower()
        day = _when(get("date"), order)
        if not screen or day is None:
            skipped += 1
            continue
        if "file" in cols:
            f = str(get("file")).lower()
            if not any(s and s in f for s in stems):
                continue
        n = parse_number(get("plays")) if "plays" in cols else 1
        n = float(n) if n is not None else 1.0
        for l in booking.lines:
            code, name = l.screen.code.lower(), l.screen.name.lower()
            if (screen == code or code in screen or screen == name) and l.start_date <= day <= l.end_date:
                by_line[l.id][day] += n
                matched += 1
                break
    out = {"lines": {}, "played": 0.0, "planned": 0.0, "rows": len(rows) - 1, "matched": matched,
           "skipped": skipped, "by_file": "file" in cols}
    for l in booking.lines:
        days = by_line.get(l.id, {})
        rules = vacancy.load_rules(session, l.screen)
        planned = sum(planned_on(session, l, d, rules) for d in days)
        played = sum(days.values())
        out["lines"][str(l.id)] = {"screen": l.screen.code, "played": played, "planned": planned,
                                   "days": {d.isoformat(): n for d, n in sorted(days.items())}}
        out["played"] += played
        out["planned"] += planned
    if not out["by_file"]:
        out["warning"] = ("The report has no file or campaign column, so every play on these screens and dates was "
                          "counted, including other advertisers'.")
    return out
