"""The guided import from the client's Google Sheets (or an exported .xlsx / .csv).

1. Read: the first non-empty row is the header.
2. Match columns: guessed from the header names, then corrected by hand once.
3. Trial run: every row is read and checked, nothing is saved. Rows that can't be read are listed with the reason;
   bookings that together go past a screen's capacity are listed as clashes.
4. Import: only once the trial run is clean (or the team chooses to skip bad rows, or to import clashes flagged
   for review). Re-running an import never duplicates: screens and specs are matched by code and name, bookings
   by where they came from.
5. Calendar: existing Google Calendar events are matched to the imported bookings, so the mirror updates them
   instead of making duplicates. Events with no matching booking are listed for review.
"""

import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from . import settings, sync, vacancy
from .activity import log, notify
from .models import (OCCUPYING, Booking, BookingLine, Client, ImportRun, Screen, SheetWatch, Spec, utcnow)
from .parsing import (blank, norm, parse_bool, parse_date, parse_hour, parse_hours_range, parse_number,
                      parse_status)

# (field, label, required, header words it is guessed from)
FIELDS = {
    "specs": [
        ("name", "Spec name", True, ["spec", "specname", "name", "format"]),
        ("width_px", "Width (px)", True, ["width", "widthpx", "w"]),
        ("height_px", "Height (px)", True, ["height", "heightpx", "h"]),
        ("resolution", "Resolution (e.g. 1920x1080)", False, ["resolution", "size", "pixels", "dimensions"]),
        ("file_types", "File types", False, ["filetypes", "formats", "filetype", "fileformat"]),
        ("video_codecs", "Video codecs", False, ["codec", "codecs", "videocodec"]),
        ("durations_s", "Ad lengths (seconds)", False, ["duration", "durations", "length", "adlength", "seconds"]),
        ("fps_min", "Lowest frame rate", False, ["fpsmin", "minfps"]),
        ("fps_max", "Highest frame rate", False, ["fpsmax", "maxfps", "fps", "framerate"]),
        ("max_file_mb", "Largest file (MB)", False, ["maxsize", "maxfilesize", "maxmb", "filesize"]),
        ("audio_allowed", "Sound allowed", False, ["audio", "sound", "audioallowed"]),
        ("safe_margin_pct", "Safe margin (%)", False, ["safearea", "safemargin", "margin"]),
        ("notes", "Notes", False, ["notes", "comments"]),
    ],
    "screens": [
        ("code", "Screen code", True, ["code", "screencode", "id", "screenid", "ref", "siteid", "sitecode"]),
        ("name", "Screen name", True, ["name", "screen", "screenname", "site", "sitename", "location"]),
        ("area", "Area", False, ["area", "district", "city", "zone", "region"]),
        ("address", "Address", False, ["address", "street"]),
        ("lat", "Latitude", False, ["lat", "latitude"]),
        ("lng", "Longitude", False, ["lng", "lon", "long", "longitude"]),
        ("physical_size", "Physical size", False, ["physicalsize", "sizem", "dimensionsm", "facesize"]),
        ("spec", "Spec name", False, ["spec", "specname", "format"]),
        ("resolution", "Resolution (e.g. 1920x1080)", False, ["resolution", "pixels", "pixelsize"]),
        ("loop_seconds", "Loop length (seconds)", True, ["loop", "looplength", "loopseconds", "looptime"]),
        ("slot_seconds", "Slot length (seconds)", True, ["slot", "slotlength", "slotseconds", "spotlength"]),
        ("sellable_pct", "Sellable share (%)", False, ["sellable", "sellablepct"]),
        ("hours", "Operating hours (e.g. 06:00-24:00)", False, ["hours", "operatinghours", "timings", "ontime"]),
        ("open_hour", "Opens at", False, ["open", "opens", "openhour", "start"]),
        ("close_hour", "Closes at", False, ["close", "closes", "closehour", "end"]),
        ("timezone", "Time zone", False, ["timezone", "tz"]),
        ("calendar_id", "Google Calendar ID", False, ["calendar", "calendarid"]),
        ("notes", "Notes", False, ["notes", "comments"]),
    ],
    "bookings": [
        ("booking_ref", "Booking reference", False, ["bookingref", "ref", "bookingid", "booking", "po", "order",
                                                     "orderid", "reference", "campaignid"]),
        ("client", "Client", True, ["client", "advertiser", "customer", "brand", "company", "clientname"]),
        ("screen", "Screen (code or name)", True, ["screen", "screencode", "site", "location", "billboard",
                                                   "board", "screenname"]),
        ("start_date", "Start date", True, ["start", "startdate", "from", "fromdate", "begin"]),
        ("end_date", "End date", True, ["end", "enddate", "to", "todate", "until", "finish"]),
        ("title", "Campaign", False, ["campaign", "title", "campaignname", "description"]),
        ("status", "Status", False, ["status", "state", "stage"]),
        ("ad_seconds", "Ad length (seconds)", False, ["adlength", "duration", "seconds", "spotlength", "length"]),
        ("plays_per_hour", "Plays per hour", False, ["playsperhour", "frequency", "spotsperhour", "pph"]),
        ("slots", "Slots in the loop", False, ["slots", "slotsinloop", "share", "spots"]),
        ("hours", "Hours (e.g. 18:00-02:00)", False, ["hours", "daypart", "timing", "timings", "time"]),
        ("start_hour", "From hour", False, ["fromhour", "starthour", "starttime"]),
        ("end_hour", "To hour", False, ["tohour", "endhour", "endtime"]),
        ("exclusive", "Whole screen", False, ["exclusive", "wholescreen", "fullscreen", "sov100"]),
        ("contact_email", "Client email", False, ["email", "clientemail", "contactemail"]),
        ("notes", "Notes", False, ["notes", "comments", "remarks"]),
    ],
}


def field_list(kind):
    return FIELDS[kind]


# ── Reading ─────────────────────────────────────────────────────────────────────

def _cell(v):
    if isinstance(v, datetime):
        return v.date().isoformat() if (v.hour, v.minute, v.second) == (0, 0, 0) else v.isoformat(" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return "" if v is None else v


def split_header(values):
    values = [[_cell(c) for c in row] for row in values]
    while values and all(blank(c) for c in values[0]):
        values.pop(0)
    if not values:
        return [], []
    header = [str(h).strip() for h in values[0]]
    rows = [r for r in values[1:] if any(not blank(c) for c in r)]
    return header, rows


def read_file(filename, data, sheet_name=""):
    """(header, rows) from an uploaded .xlsx or .csv."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.worksheets[0]
        return split_header([list(r) for r in ws.iter_rows(values_only=True)])
    if name.endswith((".csv", ".txt")):
        text = data.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return split_header(list(csv.reader(io.StringIO(text), dialect)))
    raise ValueError("Upload an .xlsx or .csv file.")


def guess_mapping(kind, header):
    """{field: column index} from the header's words. Each column is used once, best matches first."""
    normed = [norm(h) for h in header]
    mapping, used = {}, set()
    for exact in (True, False):
        for field, _label, _req, words in FIELDS[kind]:
            if field in mapping:
                continue
            for i, h in enumerate(normed):
                if i in used or not h:
                    continue
                hit = h in words or h == norm(field) if exact else any(w in h for w in words if len(w) > 2)
                if hit:
                    mapping[field] = i
                    used.add(i)
                    break
    return mapping


def _get(row, mapping, field):
    i = mapping.get(field)
    if i is None or i == "" or int(i) >= len(row):
        return None
    v = row[int(i)]
    return None if blank(v) else v


def _resolution(v):
    if blank(v):
        return None, None
    s = str(v).lower().replace("×", "x").replace("*", "x").replace(" ", "")
    if "x" in s:
        a, b = s.split("x", 1)
        w, h = parse_number(a), parse_number(b)
        if w and h:
            return int(w), int(h)
    return None, None


# ── Trial run ───────────────────────────────────────────────────────────────────

def preview(session, run, today=None):
    """Read and check every row without saving anything. The result is stored on the run."""
    today = today or date.today()
    fn = {"specs": _preview_specs, "screens": _preview_screens, "bookings": _preview_bookings}[run.kind]
    out = fn(session, run, today)
    out["counts"] = _count(out["rows"])
    run.preview = out
    run.status = "previewed"
    return out


def _count(rows):
    c = defaultdict(int)
    for r in rows:
        c[r["state"]] += 1
    c["total"] = len(rows)
    return dict(c)


def _row(n, state="ok", errors=None, warnings=None, show=None, **extra):
    return {"n": n, "state": state, "errors": errors or [], "warnings": warnings or [], "show": show or {}, **extra}


def _missing(run, row, kind):
    return [label for f, label, req, _ in FIELDS[kind] if req and f not in ("width_px", "height_px")
            and _get(row, run.mapping, f) is None]


def _spec_values(run, row):
    m = run.mapping
    vals, errors = {}, []
    w, h = parse_number(_get(row, m, "width_px")), parse_number(_get(row, m, "height_px"))
    if not (w and h):
        w, h = _resolution(_get(row, m, "resolution"))
    if not (w and h):
        errors.append("Width and height (or a resolution such as 1920x1080) are needed.")
    vals.update(width_px=int(w or 0), height_px=int(h or 0))
    for f in ("file_types", "video_codecs", "durations_s", "notes"):
        v = _get(row, m, f)
        if v is not None:
            vals[f] = str(v).replace(";", ",").replace("/", ",").replace(" ", "").lower() if f != "notes" else str(v)
    for f in ("fps_min", "fps_max", "max_file_mb", "safe_margin_pct"):
        v = _get(row, m, f)
        if v is not None:
            n = parse_number(v)
            if n is None:
                errors.append(f"Can't read '{v}' as a number.")
            else:
                vals[f] = n
    if _get(row, m, "audio_allowed") is not None:
        vals["audio_allowed"] = parse_bool(_get(row, m, "audio_allowed"))
    return vals, errors


def _preview_specs(session, run, today):
    rows, seen = [], set()
    existing = {norm(s.name): s for s in session.scalars(select(Spec))}
    for n, row in enumerate(run.rows, start=2):
        name = _get(row, run.mapping, "name")
        errors = [] if name else ["Spec name is missing."]
        vals, errs = _spec_values(run, row)
        errors += errs
        key = norm(name)
        if key in seen:
            errors.append(f"'{name}' appears twice in the sheet.")
        seen.add(key)
        action = "update" if key in existing else "new"
        rows.append(_row(n, "error" if errors else "ok", errors, show={"name": name, **vals}, action=action))
    return {"rows": rows}


def _screen_values(session, run, row, specs):
    m = run.mapping
    errors, warnings, vals = [], [], {}
    for f in ("code", "name", "area", "address", "physical_size", "timezone", "calendar_id", "notes"):
        v = _get(row, m, f)
        if v is not None:
            vals[f] = str(v).strip()
    for f in ("lat", "lng", "sellable_pct"):
        v = _get(row, m, f)
        if v is not None:
            n = parse_number(v)
            if n is None:
                errors.append(f"Can't read '{v}' as a number.")
            else:
                vals[f] = n
    for f in ("loop_seconds", "slot_seconds"):
        n = parse_number(_get(row, m, f))
        if not n or n <= 0:
            errors.append(f"{dict((x[0], x[1]) for x in FIELDS['screens'])[f]} is missing or not a number.")
        else:
            vals[f] = int(n)
    if vals.get("loop_seconds") and vals.get("slot_seconds") and vals["loop_seconds"] % vals["slot_seconds"]:
        warnings.append(f"The loop ({vals['loop_seconds']}s) isn't a whole number of {vals['slot_seconds']}s slots.")
    try:
        o, c = parse_hours_range(_get(row, m, "hours"))
    except ValueError as e:
        errors.append(str(e).capitalize() + ".")
        o = c = None
    if _get(row, m, "open_hour") is not None or _get(row, m, "close_hour") is not None:
        o, c = parse_hour(_get(row, m, "open_hour")), parse_hour(_get(row, m, "close_hour"))
        if o is None or c is None:
            errors.append("Can't read the opening hours.")
    if o is None and c is None and not errors:
        if _get(row, m, "hours") is None:
            warnings.append("No operating hours given; using 06:00–24:00.")
            o, c = 6, 24
        else:
            o, c = 0, 24
    vals["open_hour"], vals["close_hour"] = o, c
    spec_name = _get(row, m, "spec")
    w, h = _resolution(_get(row, m, "resolution"))
    spec = None
    if spec_name is not None:
        spec = specs.get(norm(spec_name))
        if spec is None:
            errors.append(f"No spec called '{spec_name}'. Import the specs first.")
    elif w and h:
        spec = next((s for s in specs.values() if (s.width_px, s.height_px) == (w, h)), None)
        vals["_new_spec"] = None if spec else f"{w}x{h}"
        if spec is None:
            warnings.append(f"A new spec '{w}x{h}' will be made; fill in its file rules after the import.")
    else:
        warnings.append("No spec or resolution given.")
    vals["_spec"] = spec.name if spec else vals.get("_new_spec")
    return vals, errors, warnings


def _preview_screens(session, run, today):
    specs = {norm(s.name): s for s in session.scalars(select(Spec))}
    existing = {s.code.lower() for s in session.scalars(select(Screen))}
    rows, seen = [], set()
    for n, row in enumerate(run.rows, start=2):
        errors = [f"{label} is missing." for label in _missing(run, row, "screens")]
        vals, errs, warns = _screen_values(session, run, row, specs)
        errors += [e for e in errs if e not in errors]
        code = (vals.get("code") or "").lower()
        if code and code in seen:
            errors.append(f"Screen code '{vals['code']}' appears twice in the sheet.")
        seen.add(code)
        show = {k: v for k, v in vals.items() if not k.startswith("_") or k == "_spec"}
        rows.append(_row(n, "error" if errors else "ok", errors, warns, show,
                         action="update" if code in existing else "new"))
    return {"rows": rows}


def _screen_lookup(session):
    by = {}
    for s in session.scalars(select(Screen)):
        by[norm(s.code)] = s
        by.setdefault(norm(s.name), s)
    return by


def _booking_values(run, row, screens, date_order):
    m = run.mapping
    errors, warnings = [], []
    v = {"client": str(_get(row, m, "client") or "").strip(),
         "ref": str(_get(row, m, "booking_ref") or "").strip(),
         "title": str(_get(row, m, "title") or "").strip(),
         "notes": str(_get(row, m, "notes") or "").strip(),
         "contact_email": str(_get(row, m, "contact_email") or "").strip()}
    raw_screen = _get(row, m, "screen")
    screen = screens.get(norm(raw_screen)) if raw_screen is not None else None
    if raw_screen is not None and screen is None:
        errors.append(f"No screen with code or name '{raw_screen}'. Import the screens first.")
    v["screen"] = screen
    for f, label in (("start_date", "start date"), ("end_date", "end date")):
        raw = _get(row, m, f)
        d = parse_date(raw, date_order)
        if raw is not None and d is None:
            errors.append(f"Can't read the {label} '{raw}'.")
        v[f] = d
    if v["start_date"] and v["end_date"] and v["end_date"] < v["start_date"]:
        errors.append("The end date is before the start date.")
    status, known = parse_status(_get(row, m, "status"))
    if _get(row, m, "status") is not None and not known:
        warnings.append(f"Status '{_get(row, m, 'status')}' not recognised; imported as a hold.")
    v["status"] = status
    try:
        sh, eh = parse_hours_range(_get(row, m, "hours"))
    except ValueError as e:
        errors.append(str(e).capitalize() + ".")
        sh = eh = None
    if _get(row, m, "start_hour") is not None or _get(row, m, "end_hour") is not None:
        sh, eh = parse_hour(_get(row, m, "start_hour")), parse_hour(_get(row, m, "end_hour"))
        if sh is None or eh is None:
            errors.append("Can't read the from and to hours.")
    v["start_hour"], v["end_hour"] = sh, eh
    v["exclusive"] = parse_bool(_get(row, m, "exclusive"))
    ad = parse_number(_get(row, m, "ad_seconds"))
    if ad is None and screen is not None:
        ad = screen.slot_seconds
        if not v["exclusive"]:
            warnings.append(f"No ad length; using the screen's slot, {ad}s.")
    v["ad_seconds"] = int(ad or 10)
    pph = parse_number(_get(row, m, "plays_per_hour"))
    slots = parse_number(_get(row, m, "slots"))
    if pph is None and screen is not None:
        pph = (slots or 1) * screen.plays_per_hour_per_slot
        if slots is None and not v["exclusive"]:
            warnings.append(f"No frequency; using one slot in the loop, {pph:g} plays an hour.")
    v["plays_per_hour"] = float(pph or 0)
    return v, errors, warnings


def _source_prefix(run):
    return f"{run.spreadsheet_id or run.source_label}:{run.tab}" if run.tab else (run.spreadsheet_id or run.source_label)


def _preview_bookings(session, run, today):
    date_order = settings.get(session, "date_order")
    screens = _screen_lookup(session)
    clients = {norm(c.name): c for c in session.scalars(select(Client))}
    prefix = _source_prefix(run)
    done_refs = {r for (r,) in session.execute(select(Booking.source_ref).where(Booking.source_ref.like(prefix + ":%")))}
    rows, parsed = [], []
    for n, row in enumerate(run.rows, start=2):
        errors = [f"{label} is missing." for label in _missing(run, row, "bookings")]
        v, errs, warns = _booking_values(run, row, screens, date_order)
        errors += errs
        if v["status"] in ("hold", "confirmed") and v["end_date"] and v["end_date"] < today:
            if v["status"] == "confirmed":
                v["status"] = "completed"
            else:
                v["status"] = "expired"
                warns.append("A hold in the past; imported as expired.")
        ref = f"{prefix}:{v['ref'] or 'row ' + str(n)}"
        state = "error" if errors else ("skip" if ref in done_refs else "ok")
        if state == "skip":
            warns.append("Already imported; left as it is.")
        show = {"client": v["client"], "screen": v["screen"].code if v["screen"] else _get(row, run.mapping, "screen"),
                "start": v["start_date"].isoformat() if v["start_date"] else "",
                "end": v["end_date"].isoformat() if v["end_date"] else "",
                "status": v["status"],
                "hours": sync.hours_text(v["start_hour"], v["end_hour"]),
                "frequency": "whole screen" if v["exclusive"] else f"{v['ad_seconds']}s × {v['plays_per_hour']:g}/h",
                "ref": v["ref"]}
        r = _row(n, state, errors, warns, show, source_ref=ref,
                 new_client=bool(v["client"]) and norm(v["client"]) not in clients)
        rows.append(r)
        parsed.append((r, v))
    # Rows sharing a booking reference become one booking: they must agree on the client.
    groups = defaultdict(list)
    for r, v in parsed:
        if v["ref"]:
            groups[v["ref"]].append((r, v))
    for ref, members in groups.items():
        if len({norm(v["client"]) for _, v in members}) > 1:
            for r, _ in members:
                r["errors"].append(f"Rows with reference '{ref}' name different clients.")
                r["state"] = "error"
    _find_clashes(session, parsed)
    return {"rows": rows, "new_clients": sorted({v["client"] for r, v in parsed if r["new_client"] and v["client"]})}


def _find_clashes(session, parsed):
    """Place the imported bookings one by one on top of what's already booked; any that don't fit clash."""
    by_screen = defaultdict(list)
    for r, v in parsed:
        if r["state"] == "ok" and v["status"] in OCCUPYING and v["screen"] is not None:
            by_screen[v["screen"].id].append((r, v))
    for sid, items in by_screen.items():
        screen = items[0][1]["screen"]
        rules = vacancy.load_rules(session, screen)
        lo = min(v["start_date"] for _, v in items)
        hi = max(v["end_date"] for _, v in items)
        uses = vacancy.load_uses(session, sid, lo, hi)
        for r, v in sorted(items, key=lambda x: (x[1]["start_date"], x[0]["n"])):
            req = vacancy.Request(v["start_date"], v["end_date"], v["start_hour"], v["end_hour"],
                                  v["ad_seconds"], v["plays_per_hour"], v["exclusive"])
            res = vacancy.check(rules, uses, req, sid)
            if res.status != "fits":
                r["state"] = "clash"
                r["clash_with"] = res.blocking
                r["clash_reason"] = res.reason
            uses.append(vacancy.Use(0, f"row {r['n']}", v["status"], v["start_date"], v["end_date"],
                                    v["start_hour"], v["end_hour"], v["ad_seconds"], v["plays_per_hour"],
                                    v["exclusive"], v["client"]))


# ── Import ──────────────────────────────────────────────────────────────────────

class ImportBlocked(Exception):
    pass


def commit(session, run, user, skip_errors=False, import_clashes=False, now=None):
    from .bookings import actor_of
    if run.status == "committed":
        raise ImportBlocked("This import has already been done.")
    out = preview(session, run)
    counts = out["counts"]
    if counts.get("error") and not skip_errors:
        raise ImportBlocked(f"{counts['error']} row(s) can't be read. Fix them in the sheet and read it again, "
                            "or choose to skip them.")
    if counts.get("clash") and not import_clashes:
        raise ImportBlocked(f"{counts['clash']} booking(s) clash. Fix them in the sheet, or choose to import "
                            "them flagged for review.")
    actor = actor_of(user)
    fn = {"specs": _commit_specs, "screens": _commit_screens, "bookings": _commit_bookings}[run.kind]
    result = fn(session, run, actor, now or utcnow())
    run.status = "committed"
    run.committed_at = now or utcnow()
    run.preview = {**out, "result": result}
    if run.source_kind == "sheet" and run.spreadsheet_id:
        session.add(SheetWatch(import_run_id=run.id, spreadsheet_id=run.spreadsheet_id, tab=run.tab,
                               key_column=run.mapping.get("booking_ref") if run.kind == "bookings" else
                               run.mapping.get("code" if run.kind == "screens" else "name"),
                               row_hashes=sync.baseline([run.header] + run.rows,
                                                        run.mapping.get("booking_ref") if run.kind == "bookings"
                                                        else run.mapping.get("code" if run.kind == "screens"
                                                                             else "name"))))
    log(session, actor, "imported", "import", run.id,
        f"Imported {run.kind} from {run.source_label}: " + ", ".join(f"{v} {k}" for k, v in result.items()))
    return result


def _commit_specs(session, run, actor, now):
    existing = {norm(s.name): s for s in session.scalars(select(Spec))}
    made = updated = 0
    for r, row in zip(run.preview["rows"], run.rows):
        if r["state"] != "ok":
            continue
        name = str(_get(row, run.mapping, "name")).strip()
        vals, _ = _spec_values(run, row)
        spec = existing.get(norm(name))
        if spec is None:
            spec = Spec(name=name, width_px=vals["width_px"], height_px=vals["height_px"])
            session.add(spec)
            existing[norm(name)] = spec
            made += 1
        else:
            updated += 1
        for k, v in vals.items():
            setattr(spec, k, v)
    return {"new": made, "updated": updated}


def _commit_screens(session, run, actor, now):
    specs = {norm(s.name): s for s in session.scalars(select(Spec))}
    existing = {s.code.lower(): s for s in session.scalars(select(Screen))}
    tz = settings.get(session, "default_timezone")
    made = updated = 0
    for r, row in zip(run.preview["rows"], run.rows):
        if r["state"] != "ok":
            continue
        vals, _, _ = _screen_values(session, run, row, specs)
        new_spec = vals.pop("_new_spec", None)
        spec_name = vals.pop("_spec", None)
        if new_spec:
            w, h = (int(x) for x in new_spec.split("x"))
            spec = specs.get(norm(new_spec)) or Spec(name=new_spec, width_px=w, height_px=h)
            if norm(new_spec) not in specs:
                session.add(spec)
                specs[norm(new_spec)] = spec
        else:
            spec = specs.get(norm(spec_name)) if spec_name else None
        screen = existing.get(vals["code"].lower())
        if screen is None:
            screen = Screen(code=vals["code"], name=vals["name"], timezone=tz)
            session.add(screen)
            existing[vals["code"].lower()] = screen
            made += 1
        else:
            updated += 1
        for k, v in vals.items():
            setattr(screen, k, v)
        if spec is not None:
            screen.spec = spec
    return {"new": made, "updated": updated}


def _commit_bookings(session, run, actor, now):
    from .bookings import next_code
    date_order = settings.get(session, "date_order")
    screens = _screen_lookup(session)
    clients = {norm(c.name): c for c in session.scalars(select(Client))}
    hold_days = int(settings.get(session, "hold_days"))
    bookings, made_clients, clashes = {}, 0, 0
    for r, row in zip(run.preview["rows"], run.rows):
        if r["state"] not in ("ok", "clash"):
            continue
        v, _, _ = _booking_values(run, row, screens, date_order)
        v["status"] = r["show"]["status"]
        key = norm(v["client"])
        client = clients.get(key)
        if client is None:
            client = Client(name=v["client"], email=v["contact_email"])
            session.add(client)
            clients[key] = client
            made_clients += 1
        group = r["source_ref"] if not v["ref"] else f"{_source_prefix(run)}:{v['ref']}"
        b = bookings.get(group)
        if b is None:
            b = Booking(code=next_code(session, now.year), client=client, title=v["title"] or v["ref"],
                        status=v["status"], source="import", source_ref=group, notes=v["notes"],
                        created_by=actor)
            if b.status == "hold":
                b.hold_expires_at = now + timedelta(days=hold_days)
            if b.status in ("confirmed", "completed"):
                b.confirmed_at, b.confirmed_by = now, actor
            session.add(b)
            bookings[group] = b
        if r["state"] == "clash":
            clashes += 1
            b.override_reason = "Imported with a clash: " + ", ".join(r.get("clash_with") or []) + ". Review it."
        b.lines.append(BookingLine(screen=v["screen"], start_date=v["start_date"], end_date=v["end_date"],
                                   start_hour=v["start_hour"], end_hour=v["end_hour"], ad_seconds=v["ad_seconds"],
                                   plays_per_hour=v["plays_per_hour"], exclusive=v["exclusive"]))
    session.flush()
    if clashes:
        notify(session, f"{clashes} imported booking line(s) clash and are flagged for review.", kind="warning",
               link="/bookings?flag=override")
    sync.queue_sheet_mirror(session)
    return {"bookings": len(bookings), "new clients": made_clients, "clashes flagged": clashes,
            "_booking_ids": [b.id for b in bookings.values()]}


# ── Matching the existing Google Calendar ───────────────────────────────────────

def _event_dates(ev):
    s, e = ev.get("start", {}), ev.get("end", {})
    if "date" in s:
        start = date.fromisoformat(s["date"])
        end = date.fromisoformat(e.get("date", s["date"])) - timedelta(days=1)
    else:
        start = date.fromisoformat(s.get("dateTime", "")[:10])
        end = date.fromisoformat(e.get("dateTime", s.get("dateTime", ""))[:10])
    return start, max(start, end)


def match_calendar(session, run, google, shared_calendar_id=""):
    """Link existing calendar events to the imported booking lines. Each screen's own calendar is read; with a
    shared calendar, an event is tied to a screen by its code or name in the title."""
    ids = (run.preview.get("result") or {}).get("_booking_ids") or []
    lines = session.scalars(select(BookingLine).where(BookingLine.booking_id.in_(ids))).all() if ids else []
    if not lines:
        return {"matched": [], "unmatched": [], "without_event": []}
    lo = min(l.start_date for l in lines) - timedelta(days=1)
    hi = max(l.end_date for l in lines) + timedelta(days=2)
    tmin, tmax = f"{lo.isoformat()}T00:00:00Z", f"{hi.isoformat()}T00:00:00Z"
    by_cal = defaultdict(list)
    for l in lines:
        by_cal[shared_calendar_id or l.screen.calendar_id].append(l)
    matched, unmatched, taken = [], [], set()
    for cal, cal_lines in by_cal.items():
        if not cal:
            continue
        # A calendar holding several screens' events: an event must name its screen to be matched.
        mixed = bool(shared_calendar_id) or len({l.screen_id for l in cal_lines}) > 1
        for ev in google.list_events(cal, tmin, tmax):
            try:
                es, ee = _event_dates(ev)
            except ValueError:
                continue
            text = norm(ev.get("summary", "") + " " + ev.get("description", ""))
            best, score = None, 0
            for l in cal_lines:
                if l.id in taken or l.end_date < es or l.start_date > ee:
                    continue
                if mixed and norm(l.screen.code) not in text and norm(l.screen.name) not in text:
                    continue
                sc = 1 + (3 if (l.start_date, l.end_date) == (es, ee) else 0)
                sc += 2 if norm(l.booking.client.name) and norm(l.booking.client.name) in text else 0
                ref = l.booking.source_ref.rsplit(":", 1)[-1]
                sc += 3 if ref and not ref.startswith("row ") and norm(ref) in text else 0
                if sc > score:
                    best, score = l, sc
            if best is not None and score >= 3:
                taken.add(best.id)
                if not shared_calendar_id:
                    best.calendar_event_id = ev["id"]
                matched.append({"event": ev.get("summary", ""), "dates": f"{es} – {ee}", "booking": best.booking.code,
                                "screen": best.screen.code})
            else:
                unmatched.append({"event": ev.get("summary", ""), "dates": f"{es} – {ee}", "calendar": cal})
    without = [{"booking": l.booking.code, "screen": l.screen.code} for l in lines if l.id not in taken]
    return {"matched": matched, "unmatched": unmatched, "without_event": without}


def start_calendar_mirror(session, run):
    """After matching, mirror every imported line: matched events are updated, the rest are created."""
    ids = (run.preview.get("result") or {}).get("_booking_ids") or []
    for b in session.scalars(select(Booking).where(Booking.id.in_(ids))).all() if ids else []:
        sync.queue_booking(session, b)
    return len(ids)


def import_counts(session):
    return dict(session.execute(select(ImportRun.kind, func.count()).where(ImportRun.status == "committed")
                                .group_by(ImportRun.kind)).all())
