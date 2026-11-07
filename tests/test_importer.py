"""The guided import, end to end: specs → screens → bookings, trial runs, clashes, calendar matching and the
watch on the old sheet."""

import io
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from billboard_ops import importer, sync
from billboard_ops.jobs import run_due
from billboard_ops.models import Booking, Client, ImportRun, Screen, SheetChange, SheetWatch, Spec

from conftest import future

SPECS = [["Spec", "Resolution", "Formats", "Durations", "Max size MB", "Sound"],
         ["Landscape FHD", "1920x1080", "MP4, JPG", "10;15", "50", "no"],
         ["Portrait", "1080 x 1920", "mp4", "10", "", "no"]]

SCREENS = [["Site ID", "Site name", "District", "Spec", "Loop (s)", "Slot length", "Hours", "Lat", "Lng"],
           ["SZR-01", "Sheikh Zayed Rd North", "SZR", "Landscape FHD", 120, 10, "06:00-24:00", 25.21, 55.27],
           ["SZR-02", "Sheikh Zayed Rd South", "SZR", "Landscape FHD", 120, 10, "6am - midnight", 25.19, 55.26],
           ["MAR-01", "Marina Walk", "Marina", "Portrait", 60, 10, "6pm to 2am", "", ""]]


def run_for(s, kind, values, people, source="sheet", sid="SHEET1", tab="Tab"):
    header, rows = importer.split_header(values)
    run = ImportRun(kind=kind, source_kind=source, source_label="test", spreadsheet_id=sid if source == "sheet" else "",
                    tab=tab if source == "sheet" else "", header=header, rows=rows, created_by="owner@example.com")
    run.mapping = importer.guess_mapping(kind, header)
    s.add(run)
    s.flush()
    return run


def load_inventory(s, people):
    for kind, values in (("specs", SPECS), ("screens", SCREENS)):
        run = run_for(s, kind, values, people, sid=f"S-{kind}")
        importer.commit(s, run, people["owner"])
    s.commit()


def d(n):
    return future(n).strftime("%d/%m/%Y")


def test_column_guessing():
    m = importer.guess_mapping("screens", SCREENS[0])
    assert m == {"code": 0, "name": 1, "area": 2, "spec": 3, "loop_seconds": 4, "slot_seconds": 5, "hours": 6,
                 "lat": 7, "lng": 8}
    m = importer.guess_mapping("bookings", ["Ref", "Advertiser", "Board", "From", "To", "Status", "Spots per hour"])
    assert m["booking_ref"] == 0 and m["client"] == 1 and m["screen"] == 2 and m["start_date"] == 3
    assert m["end_date"] == 4 and m["status"] == 5 and m["plays_per_hour"] == 6


def test_specs_and_screens_import(s, people):
    load_inventory(s, people)
    land = s.scalars(select(Spec).where(Spec.name == "Landscape FHD")).one()
    assert (land.width_px, land.height_px, land.file_types, land.durations_s, land.max_file_mb) == \
        (1920, 1080, "mp4,jpg", "10,15", 50)
    mar = s.scalars(select(Screen).where(Screen.code == "MAR-01")).one()
    assert (mar.open_hour, mar.close_hour, mar.loop_seconds, mar.spec.name, mar.lat) == (18, 2, 60, "Portrait", None)
    szr2 = s.scalars(select(Screen).where(Screen.code == "SZR-02")).one()
    assert (szr2.open_hour, szr2.close_hour) == (6, 24)
    # Importing again updates, never duplicates.
    run = run_for(s, "screens", SCREENS, people, sid="again")
    assert importer.commit(s, run, people["owner"]) == {"new": 0, "updated": 3}
    assert len(s.scalars(select(Screen)).all()) == 3


def test_screen_rows_with_problems(s, people):
    load_inventory(s, people)
    values = [SCREENS[0], ["X-1", "Somewhere", "", "No such spec", 120, 10, "", "", ""],
              ["X-2", "Elsewhere", "", "", "", 10, "evenings", "", ""]]
    run = run_for(s, "screens", values, people)
    out = importer.preview(s, run)
    errs = {r["n"]: r["errors"] for r in out["rows"]}
    assert "No spec called 'No such spec'" in errs[2][0]
    assert any("Loop length" in e for e in errs[3]) and any("can't read hours" in e.lower() for e in errs[3])
    with pytest.raises(importer.ImportBlocked):
        importer.commit(s, run, people["owner"])


def bookings_sheet():
    return [["Ref", "Client", "Screen", "Start", "End", "Status", "Ad length", "Slots", "Hours"],
            ["PO-1", "Acme Foods", "SZR-01", d(10), d(16), "Confirmed", 10, 6, ""],
            ["PO-1", "Acme Foods", "SZR-02", d(10), d(16), "Confirmed", 10, 6, ""],          # same booking
            ["PO-2", "Beta Motors", "Sheikh Zayed Rd North", d(12), d(14), "Tentative", 10, 6, ""],
            ["PO-3", "Gamma Bank", "SZR-01", d(13), d(13), "Booked", 10, 1, ""],              # clash: 6+6+1 > 12
            ["PO-4", "Delta", "NOPE", "soon", d(20), "booked", 10, 1, ""],                    # unreadable
            ["PO-5", "Old Client", "MAR-01", (date.today() - timedelta(days=40)).isoformat(),
             (date.today() - timedelta(days=30)).isoformat(), "Confirmed", 10, 1, "6pm-2am"],
            ["PO-6", "Acme Foods", "SZR-02", d(30), d(31), "pencil", "", "", ""]]


def test_bookings_trial_run(s, people):
    load_inventory(s, people)
    run = run_for(s, "bookings", bookings_sheet(), people)
    out = importer.preview(s, run)
    by = {r["n"]: r for r in out["rows"]}
    assert out["counts"] == {"ok": 5, "clash": 1, "error": 1, "total": 7}
    assert by[5]["state"] == "clash" and by[5]["clash_with"] == ["row 2", "row 4"]
    assert any("No screen with code or name 'NOPE'" in e for e in by[6]["errors"])
    assert any("Can't read the start date 'soon'" in e for e in by[6]["errors"])
    assert by[7]["show"]["status"] == "completed"           # a confirmed booking in the past
    assert by[8]["show"]["status"] == "hold" and by[8]["show"]["frequency"] == "10s × 30/h"
    assert any("one slot in the loop" in w for w in by[8]["warnings"])
    assert set(out["new_clients"]) == {"Acme Foods", "Beta Motors", "Gamma Bank", "Delta", "Old Client"}


def test_bookings_import_blocks_until_problems_are_dealt_with(s, people):
    load_inventory(s, people)
    run = run_for(s, "bookings", bookings_sheet(), people)
    with pytest.raises(importer.ImportBlocked, match="can't be read"):
        importer.commit(s, run, people["owner"])
    with pytest.raises(importer.ImportBlocked, match="clash"):
        importer.commit(s, run, people["owner"], skip_errors=True)
    result = importer.commit(s, run, people["owner"], skip_errors=True, import_clashes=True)
    s.commit()
    assert result["bookings"] == 5 and result["new clients"] == 4 and result["clashes flagged"] == 1
    po1 = s.scalars(select(Booking).where(Booking.source_ref == "SHEET1:Tab:PO-1")).one()
    assert po1.status == "confirmed" and sorted(l.screen.code for l in po1.lines) == ["SZR-01", "SZR-02"]
    gamma = s.scalars(select(Booking).join(Client).where(Client.name == "Gamma Bank")).one()
    assert gamma.override_reason.startswith("Imported with a clash")
    assert s.scalars(select(Booking).where(Booking.source_ref.like("%PO-6"))).one().hold_expires_at is not None
    # The sheet is now watched, from this baseline.
    w = s.scalars(select(SheetWatch)).all()[-1]
    assert w.spreadsheet_id == "SHEET1" and w.key_column == 0 and "PO-1" in w.row_hashes


def test_reimporting_the_same_sheet_skips_what_is_already_in(s, people):
    load_inventory(s, people)
    first = run_for(s, "bookings", bookings_sheet(), people)
    importer.commit(s, first, people["owner"], skip_errors=True, import_clashes=True)
    s.commit()
    again = run_for(s, "bookings", bookings_sheet(), people)
    out = importer.preview(s, again)
    assert out["counts"].get("skip") == 6 and out["counts"].get("error") == 1


def test_rows_with_one_reference_must_share_a_client(s, people):
    load_inventory(s, people)
    values = [bookings_sheet()[0], ["PO-9", "A", "SZR-01", d(5), d(6), "", 10, 1, ""],
              ["PO-9", "B", "SZR-02", d(5), d(6), "", 10, 1, ""]]
    out = importer.preview(s, run_for(s, "bookings", values, people))
    assert all(r["state"] == "error" for r in out["rows"])


def test_xlsx_upload_with_real_dates(s, people):
    from openpyxl import Workbook
    load_inventory(s, people)
    wb = Workbook()
    ws = wb.active
    ws.append([])                                             # a blank first row is skipped
    ws.append(["Client", "Screen", "Start", "End"])
    ws.append(["Acme Foods", "SZR-01", datetime.combine(future(3), datetime.min.time()),
               datetime.combine(future(4), datetime.min.time())])
    buf = io.BytesIO()
    wb.save(buf)
    header, rows = importer.read_file("bookings.xlsx", buf.getvalue())
    assert header == ["Client", "Screen", "Start", "End"] and rows[0][2] == future(3).isoformat()
    run = run_for(s, "bookings", [header] + rows, people, source="file")
    out = importer.preview(s, run)
    assert out["counts"]["ok"] == 1 and out["rows"][0]["show"]["status"] == "confirmed"


def test_csv_with_semicolons():
    header, rows = importer.read_file("x.csv", "Client;Screen\r\nAcme;SZR-01\r\n".encode("utf-8-sig"))
    assert header == ["Client", "Screen"] and rows == [["Acme", "SZR-01"]]
    with pytest.raises(ValueError):
        importer.read_file("x.pdf", b"")


def test_calendar_matching_links_existing_events(s, people, google):
    load_inventory(s, people)
    for code, cal in (("SZR-01", "cal-1"), ("SZR-02", "cal-2")):
        s.scalars(select(Screen).where(Screen.code == code)).one().calendar_id = cal
    run = run_for(s, "bookings", bookings_sheet()[:4], people)
    importer.commit(s, run, people["owner"])
    s.commit()
    start, end = future(10), future(16)
    google.events["cal-1"] = {
        "old1": {"summary": "Acme Foods PO-1", "start": {"date": start.isoformat()},
                 "end": {"date": (end + timedelta(days=1)).isoformat()}},
        "old2": {"summary": "Someone not in the sheet", "start": {"date": future(40).isoformat()},
                 "end": {"date": future(41).isoformat()}},
    }
    res = importer.match_calendar(s, run, google)
    assert [m["event"] for m in res["matched"]] == ["Acme Foods PO-1"]
    assert [u["event"] for u in res["unmatched"]] == ["Someone not in the sheet"]
    assert {(w["screen"]) for w in res["without_event"]} == {"SZR-02", "SZR-01"}   # PO-1 on SZR-02, PO-2 on SZR-01
    s.commit()
    importer.start_calendar_mirror(s, run)
    s.commit()
    run_due()
    # The matched event was updated in place, not duplicated; the rest were created.
    assert "old1" in google.events["cal-1"] and google.events["cal-1"]["old1"]["summary"].startswith("[CONFIRMED]")
    assert len(google.events["cal-2"]) == 1


def test_sheet_watch_flags_direct_edits_once(s, people, google):
    load_inventory(s, people)
    values = bookings_sheet()[:4]
    run = run_for(s, "bookings", values, people)
    importer.commit(s, run, people["owner"])
    s.commit()
    watch = s.scalars(select(SheetWatch).where(SheetWatch.spreadsheet_id == "SHEET1")).one()
    edited = [list(r) for r in values]
    edited[3][4] = d(20)                                       # PO-2's end date changed in the sheet
    edited.append(["PO-7", "New Co", "SZR-02", d(50), d(51), "", 10, 1, ""])
    google.sheets[("SHEET1", "Tab")] = [edited[0], edited[1], edited[3], edited[4]]   # one PO-1 row deleted
    for _ in range(2):                                         # a second check doesn't repeat the flags
        sync._sheet_watch_check(s, {"watch_id": watch.id})
        s.commit()
    changes = {(c.row_key, c.change) for c in s.scalars(select(SheetChange))}
    assert changes == {("PO-2", "changed"), ("PO-7", "added"), ("PO-1 (row 3)", "removed")}
    ch = s.scalars(select(SheetChange).where(SheetChange.row_key == "PO-2")).one()
    sync.accept_change(s, ch, "manager@example.com")
    s.commit()
    assert ch.resolved_at is not None
    assert sync.compare(watch, google.read_values("SHEET1", "Tab"))[0][0] != "PO-2"
