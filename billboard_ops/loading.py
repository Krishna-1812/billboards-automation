"""Ready to load on the screens (Phase 4): playlists, and the approved files named the way the screens expect.

    playlist()       for each screen, day and hour: which approved file plays, and how many times.
    export_*()       the same as CSV, Excel or PDF, for whoever loads the screen software.
    zip_for()        a booking's approved files in a ZIP, one folder per screen, renamed for loading.
    ready_to_load    (a job) copies approved files into a "Ready to load" Drive folder, one sub-folder per screen.

Until the screen software can take playlists through an API, Ops loads the files and ticks "Loaded" in the app.
"""

import csv
import io
import os
import re
import tempfile
import zipfile
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from . import creative_store, settings, vacancy
from .jobs import handler
from .lifecycle import approved_files
from .models import BOOKED, Booking, BookingLine, Creative

PLAYING = BOOKED


def _slug(v):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(v or "")).strip("-") or "x"


def load_name(session, line, creative):
    pattern = settings.get(session, "load_filename") or "{screen}_{booking}_{start}_{end}_v{version}{ext}"
    ext = os.path.splitext(creative.filename)[1].lower()
    try:
        name = pattern.format(screen=_slug(line.screen.code), booking=_slug(line.booking.code),
                              client=_slug(line.booking.client.name), start=f"{line.start_date:%Y%m%d}",
                              end=f"{line.end_date:%Y%m%d}", version=creative.version, ext=ext,
                              file=_slug(os.path.splitext(creative.filename)[0]))
    except (KeyError, IndexError, ValueError):
        name = f"{_slug(line.screen.code)}_{_slug(line.booking.code)}_v{creative.version}{ext}"
    if ext and not name.lower().endswith(ext):
        name += ext
    return name


def _lines(session, screen_ids, start, end, booking_id=None):
    q = (select(BookingLine).join(Booking)
         .options(joinedload(BookingLine.booking).joinedload(Booking.client), joinedload(BookingLine.screen))
         .where(Booking.status.in_(PLAYING), BookingLine.start_date <= end + timedelta(days=1),
                BookingLine.end_date >= start - timedelta(days=1)))
    if screen_ids:
        q = q.where(BookingLine.screen_id.in_(screen_ids))
    if booking_id:
        q = q.where(Booking.id == booking_id)
    return session.scalars(q.order_by(BookingLine.screen_id, Booking.code)).unique().all()


def playlist(session, screens, start, end, booking_id=None):
    """Rows {date, hour, screen, booking, client, file, plays, ready, plan_confirmed}, by screen, day and hour.
    Several approved files for one screen share its plays in turn."""
    rows = []
    lines = _lines(session, [s.id for s in screens], start, end, booking_id)
    files = defaultdict(list)
    ids = {l.booking_id for l in lines}
    if ids:
        for c in session.scalars(select(Creative).where(Creative.booking_id.in_(ids), Creative.status == "approved")
                                 .order_by(Creative.version)):
            files[c.booking_id].append(c)
    rules = {}
    for l in lines:
        r = rules.get(l.screen_id) or rules.setdefault(l.screen_id, vacancy.load_rules(session, l.screen))
        use = vacancy.use_from_line(l)
        approved = approved_files(session, l, files[l.booking_id])
        names = [(c, load_name(session, l, c)) for c in approved]
        for day in vacancy.days_between(max(start, l.start_date), min(end, l.end_date)):
            for slot in sorted(vacancy.playing_slots(r, use, day)):
                need = vacancy.slot_demand(use, r, day, slot)
                if need <= 0:
                    continue
                plays = need / max(1, l.ad_seconds) if not l.exclusive else r.capacity / max(1, l.ad_seconds)
                base = {"day": day, "date": slot[0], "hour": slot[1], "screen": l.screen.code,
                        "screen_name": l.screen.name, "booking": l.booking.code, "client": l.booking.client.name,
                        "ad_seconds": l.ad_seconds, "whole_screen": l.exclusive,
                        "plan_confirmed": l.confirmed_plan is not None, "loaded": l.loaded_at is not None}
                if names:
                    for c, name in names:
                        rows.append(dict(base, file=name, plays=plays / len(names), ready=True))
                else:
                    rows.append(dict(base, file="", plays=plays, ready=False))
    rows.sort(key=lambda x: (x["screen"], x["date"], x["hour"], x["booking"]))
    return rows


COLUMNS = [("date", "Date"), ("hour", "Hour"), ("screen", "Screen"), ("booking", "Booking"), ("client", "Client"),
           ("file", "File"), ("plays", "Plays"), ("ad_seconds", "Seconds")]


def _cell(row, key):
    v = row[key]
    if key == "date":
        return v.isoformat()
    if key == "hour":
        return f"{v:02d}:00"
    if key == "plays":
        return round(v, 1)
    if key == "file" and not v:
        return "(no approved file yet)"
    return v


def export_csv(rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([label for _, label in COLUMNS])
    for r in rows:
        w.writerow([_cell(r, k) for k, _ in COLUMNS])
    return out.getvalue().encode("utf-8-sig")


def export_xlsx(rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Playlist"
    ws.append([label for _, label in COLUMNS])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([_cell(r, k) for k, _ in COLUMNS])
    for col, width in zip("ABCDEFGH", (12, 7, 10, 15, 24, 48, 8, 8)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_pdf(rows, title):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=28, rightMargin=28, topMargin=28,
                            bottomMargin=28, title=title)
    styles = getSampleStyleSheet()
    data = [[label for _, label in COLUMNS]] + [[str(_cell(r, k)) for k, _ in COLUMNS] for r in rows]
    if len(data) == 1:
        data.append(["Nothing plays in this period."] + [""] * (len(COLUMNS) - 1))
    table = Table(data, repeatRows=1, colWidths=[62, 40, 56, 80, 120, 290, 40, 44])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2EFE8")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DEDACD")),
        ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    doc.build([Paragraph(title, styles["Heading2"]), Spacer(1, 6), table])
    return buf.getvalue()


def zip_for(session, booking):
    """A temporary ZIP of the booking's approved files, a folder per screen. The caller deletes it."""
    fd, path = tempfile.mkstemp(suffix=".zip", prefix=f"{booking.code}-")
    os.close(fd)
    work = tempfile.mkdtemp(prefix="load-")
    count = 0
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
            fetched = {}
            for l in booking.lines:
                for c in approved_files(session, l):
                    if c.id not in fetched:
                        sub = os.path.join(work, str(c.id))
                        os.makedirs(sub, exist_ok=True)
                        fetched[c.id] = creative_store.materialise(session, c, sub)
                    zf.write(fetched[c.id], f"{_slug(l.screen.code)}/{load_name(session, l, c)}")
                    count += 1
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    return path, count


@handler("ready_to_load")
def ready_to_load(session, payload):
    """Copy approved files into Ready to load / <screen code> in the Shared Drive."""
    b = session.scalars(select(Booking).options(selectinload(Booking.lines))
                        .where(Booking.id == payload["booking_id"])).first()
    g = creative_store.drive(session)
    if b is None or g is None:
        return 0
    parent = settings.get(session, "drive_parent_folder_id")
    root = b.load_folder_id or g.drive_folder("Ready to load", parent)
    b.load_folder_id = root
    copied = 0
    for l in b.lines:
        have = list(l.load_files or [])
        done = {x.get("creative_id") for x in have}
        todo = [c for c in approved_files(session, l) if c.id not in done]
        if not todo:
            continue
        folder = g.drive_folder(l.screen.code, root)
        for c in todo:
            name = load_name(session, l, c)
            if c.drive_file_id:
                fid = g.drive_copy(c.drive_file_id, folder, name)
            else:
                work = tempfile.mkdtemp(prefix="load-")
                try:
                    fid = g.drive_upload(folder, name, creative_store.materialise(session, c, work), c.content_type)
                finally:
                    import shutil
                    shutil.rmtree(work, ignore_errors=True)
            have.append({"creative_id": c.id, "drive_file_id": fid, "name": name})
            copied += 1
        l.load_files = have
    return copied
