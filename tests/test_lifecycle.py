"""Phase 4: from approved files to the end of the campaign. Readiness, loading, going live, the day-before alert,
renewals, the wrap-up, playlists and their exports, the ZIP and Drive copies, and proof of play."""

import io
import zipfile
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops import creatives, lifecycle, loading, plans, proof, settings
from billboard_ops.jobs import run_due
from billboard_ops.models import Activity, Booking, Job, Message, Notification, Proof

from media import media, read  # noqa: F401
from test_creatives import ai_reads_ads, confirmed, jobs_now, small  # noqa: F401


def approved(s, people, inv, media, start=None):
    b = confirmed(s, people, inv, start=start)
    c = creatives.add_file(s, b, filename="ad.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, c)
    creatives.approve(s, people["ops"], c)
    s.commit()
    return b, c


def ready(s, people, inv, media, start=None):
    b, c = approved(s, people, inv, media, start)
    plans.confirm(s, people["ops"], b)
    for l in b.lines:
        lifecycle.mark_loaded(s, people["ops"], b, l)
    s.commit()
    return b, c


def test_the_stages(s, people, small, media, ai_reads_ads):
    b, c = approved(s, people, small, media)
    assert b.status == "creative_approved" and b.plan_status == "draft"      # drafted when it was confirmed
    r = lifecycle.readiness(s, b)
    assert r["files"] and not r["plan"] and not r["loaded"]
    assert r["missing"] == ["the play plan confirmed", "loading onto the screens (SZR-01, SZR-02)"]
    with pytest.raises(lifecycle.LoadError, match="Confirm the play plan"):
        lifecycle.mark_loaded(s, people["ops"], b, b.lines[0])
    plans.confirm(s, people["ops"], b)
    assert b.status == "plan_set"
    with pytest.raises(Exception):
        lifecycle.mark_loaded(s, people["sales"], b, b.lines[0])            # Sales doesn't load screens
    lifecycle.mark_loaded(s, people["ops"], b, b.lines[0])
    assert b.status == "plan_set"
    lifecycle.mark_loaded(s, people["ops"], b, b.lines[1])
    assert b.status == "scheduled" and lifecycle.readiness(s, b)["ready"]
    lifecycle.mark_loaded(s, people["ops"], b, b.lines[1], loaded=False)
    assert b.status == "plan_set"


def test_a_newer_file_or_plan_needs_loading_again(s, people, small, media, ai_reads_ads):
    b, c = ready(s, people, small, media)
    assert b.status == "scheduled"
    v2 = creatives.add_file(s, b, filename="ad-v2.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, v2)
    assert b.status == "scheduled"                         # the approved file still covers it while v2 is checked
    creatives.approve(s, people["ops"], v2)
    s.commit()
    assert all(l.loaded_at is None for l in b.lines) and b.status == "plan_set"
    assert s.scalars(select(Notification).where(Notification.text.contains("needs loading again"))).first()
    for l in b.lines:
        lifecycle.mark_loaded(s, people["ops"], b, l)
    p = plans.build(plans.dayparts(s), {"Morning peak": 10, "Daytime": 10, "Evening peak": 30, "Night": 10})
    plans.save(s, people["ops"], b, {b.lines[0].id: p})
    plans.confirm(s, people["ops"], b)
    s.commit()
    assert b.lines[0].loaded_at is None and b.lines[1].loaded_at is not None     # only the changed screen


def test_going_live_renewal_and_wrap_up(s, people, small, media, ai_reads_ads):
    start = date.today() + timedelta(days=3)
    b, c = ready(s, people, small, media, start=start)
    lifecycle.tick(s, on=start - timedelta(days=1))
    assert b.status == "scheduled" and b.ready_alerted_on is None             # ready: no alert
    lifecycle.tick(s, on=start)
    s.commit()
    assert b.status == "live" and b.live_at is not None
    live = s.scalars(select(Message).where(Message.kind == "live_notice")).one()
    assert live.status == "draft" and "started today" in live.body and "/portal/bookings/" in live.body
    end = b.end_date
    lifecycle.tick(s, on=end - timedelta(days=8))
    assert b.renewal_prompted_at is None
    lifecycle.tick(s, on=end - timedelta(days=7))
    s.commit()
    renew = s.scalars(select(Message).where(Message.kind == "renewal")).one()
    assert "Would you like to extend it" in renew.body
    emails = [j.payload for j in s.scalars(select(Job).where(Job.kind == "send_email"))]
    assert any(e["to"] == ["sales@example.com"] and "Renewal" in e["subject"] for e in emails)
    lifecycle.tick(s, on=end - timedelta(days=6))
    assert len(s.scalars(select(Message).where(Message.kind == "renewal")).all()) == 1       # once
    proof.add(s, people["ops"], b, filename="night.jpg", content_type="image/jpeg", data=read(media["good.png"]))
    svc.complete_finished(s, end + timedelta(days=1))
    lifecycle.tick(s, on=end + timedelta(days=1))
    s.commit()
    assert b.status == "completed" and b.wrapped_up_at is not None
    wrap = s.scalars(select(Message).where(Message.kind == "wrap_up")).one()
    assert "Thank you for advertising with us" in wrap.body and "are in your portal" in wrap.body


def test_the_day_before_alert_catches_an_unready_booking(s, people, small, media, ai_reads_ads):
    start = date.today() + timedelta(days=1)
    b, c = approved(s, people, small, media, start=start)            # files approved; no plan, nothing loaded
    lifecycle.tick(s, on=date.today())
    s.commit()
    n = s.scalars(select(Notification).where(Notification.topic == "readiness")).one()
    assert n.kind == "error" and n.text.startswith(f"Not ready: {b.code}") and "starts tomorrow" in n.text
    assert "the play plan confirmed" in n.text and "loading onto the screens" in n.text
    mail = [j.payload for j in s.scalars(select(Job).where(Job.kind == "send_email"))
            if j.payload["subject"].startswith("Not ready")]
    assert sorted(mail[0]["to"]) == ["manager@example.com", "ops@example.com", "owner@example.com"]
    lifecycle.tick(s, on=date.today())
    assert len(s.scalars(select(Notification).where(Notification.topic == "readiness")).all()) == 1   # once a day
    lifecycle.tick(s, on=start)
    s.commit()
    assert b.status != "live"
    again = s.scalars(select(Notification).where(Notification.topic == "readiness")
                      .order_by(Notification.id.desc())).first()
    assert "starts today" in again.text


def test_someone_who_turned_an_alert_off_doesnt_get_it(s, people, small, media, ai_reads_ads):
    people["ops"].notify_prefs = {"readiness": {"app": False, "email": False}}
    people["sales"].notify_prefs = {"readiness": {"app": True, "email": True}}
    s.commit()
    b, c = approved(s, people, small, media, start=date.today() + timedelta(days=1))
    lifecycle.tick(s, on=date.today())
    s.commit()
    mail = [j.payload for j in s.scalars(select(Job).where(Job.kind == "send_email"))
            if j.payload["subject"].startswith("Not ready")][0]
    assert sorted(mail["to"]) == ["manager@example.com", "owner@example.com", "sales@example.com"]
    from billboard_ops.views.main import notices_for
    assert not any(n.topic == "readiness" for n in notices_for(s, people["ops"]))
    assert any(n.topic == "readiness" for n in notices_for(s, people["owner"]))


# ── Playlists and files to load ─────────────────────────────────────────────────

def test_playlist_and_exports(s, people, small, media, ai_reads_ads):
    b, c = ready(s, people, small, media)
    day = b.start_date
    rows = loading.playlist(s, [small["a"]], day, day)
    assert [r["hour"] for r in rows] == list(range(6, 24))
    assert rows[0]["file"] == f"SZR-01_{b.code}_{day:%Y%m%d}_{b.end_date:%Y%m%d}_v1.mp4" and rows[0]["plays"] == 30
    assert rows[0]["plan_confirmed"] and rows[0]["loaded"]
    data = loading.export_csv(rows).decode("utf-8-sig").splitlines()
    assert data[0] == "Date,Hour,Screen,Booking,Client,File,Plays,Seconds" and data[1].startswith(f"{day},06:00,SZR-01")
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(loading.export_xlsx(rows))).active
    assert ws["A1"].value == "Date" and ws.max_row == 19
    assert loading.export_pdf(rows, "Playlist").startswith(b"%PDF")
    settings.put(s, "load_filename", "{client}-{screen}-{file}{ext}")
    assert loading.playlist(s, [small["a"]], day, day)[0]["file"] == "Acme-Foods-SZR-01-ad.mp4"


def test_a_booking_without_an_approved_file_shows_in_the_playlist(s, people, small):
    b = confirmed(s, people, small)
    rows = loading.playlist(s, [small["a"]], b.start_date, b.start_date)
    assert rows and not rows[0]["ready"] and rows[0]["file"] == "" and not rows[0]["plan_confirmed"]


def test_the_zip_of_files_to_load(s, people, small, media, ai_reads_ads):
    b, c = ready(s, people, small, media)
    path, count = loading.zip_for(s, b)
    with zipfile.ZipFile(path) as zf:
        names = sorted(zf.namelist())
    assert count == 2 and names == [f"SZR-01/SZR-01_{b.code}_{b.start_date:%Y%m%d}_{b.end_date:%Y%m%d}_v1.mp4",
                                    f"SZR-02/SZR-02_{b.code}_{b.start_date:%Y%m%d}_{b.end_date:%Y%m%d}_v1.mp4"]


def test_approved_files_are_copied_to_the_ready_to_load_folder(s, people, small, media, google, ai_reads_ads):
    settings.put(s, "drive_parent_folder_id", "PARENT")
    s.commit()
    b, c = approved(s, people, small, media)
    jobs_now(s)
    run_due()
    s.expire_all()
    b = s.get(Booking, b.id)
    copies = [x for x in google.calls if x[0] == "copy"]
    assert len(copies) == 2 and {x[3] for x in copies} == {loading.load_name(s, l, c) for l in b.lines
                                                          for c in lifecycle.approved_files(s, l)}
    assert google.folders[("PARENT", "Ready to load")] == b.load_folder_id
    assert [x["name"] for x in b.lines[0].load_files][0].startswith("SZR-01_")
    lifecycle.advance(s, b)
    s.commit()
    assert not s.scalars(select(Job).where(Job.kind == "ready_to_load", Job.status == "queued")).first()


# ── Proof of play ───────────────────────────────────────────────────────────────

def test_photos_and_play_reports(s, people, small, media, ai_reads_ads):
    b, c = ready(s, people, small, media)
    with pytest.raises(proof.ProofError, match="photo, a video, or a play report"):
        proof.add(s, people["ops"], b, filename="notes.txt", data=b"hi")
    p = proof.add(s, people["ops"], b, filename="szr.jpg", content_type="image/jpeg", data=read(media["good.png"]),
                  line_id=b.lines[0].id, taken_on=b.start_date, note="Evening shot")
    assert p.kind == "photo" and p.data and p.shared
    d1, d2 = b.start_date, b.start_date + timedelta(days=1)
    report = ("Player;Played at;Media;Plays\n"
              f"SZR-01;{d1:%d/%m/%Y} 10:00;SZR-01_{b.code}_v1.mp4;300\n"
              f"SZR-01;{d1:%d/%m/%Y} 20:00;SZR-01_{b.code}_v1.mp4;240\n"
              f"SZR-02;{d2:%Y-%m-%d};SZR-02_{b.code}_v1.mp4;500\n"
              f"SZR-02;{d2:%Y-%m-%d};someone-else.mp4;999\n"
              "SZR-02;not a date;x;1\n")
    r = proof.add(s, people["ops"], b, filename="report.csv", content_type="text/csv", data=report.encode())
    sm = r.summary
    assert r.kind == "report" and sm["matched"] == 3 and sm["skipped"] == 1 and sm["played"] == 1040
    assert sm["planned"] == 1080 and sm["lines"][str(b.lines[0].id)]["days"] == {d1.isoformat(): 540}
    assert "warning" not in sm
    act = s.scalars(select(Activity).where(Activity.action == "added_proof").order_by(Activity.id.desc())).first()
    assert "1,040 plays counted against 1,080 planned" in act.summary
    with pytest.raises(proof.ProofError, match="column for the screen"):
        proof.add(s, people["ops"], b, filename="r.csv", data=b"a,b\n1,2\n")


# ── Pages ───────────────────────────────────────────────────────────────────────

def test_pages(app, s, people, small, media, ai_reads_ads, login):
    b, c = approved(s, people, small, media)
    c_ops = login(people["ops"])
    page = c_ops.get(f"/bookings/{b.code}").get_data(as_text=True)
    assert "Getting ready to play" in page and "Play plan (draft)" in page and "Proof of play" in page
    assert f"SZR-01_{b.code}_" in page
    plans.confirm(s, people["ops"], b)
    s.commit()
    r = c_ops.post(f"/bookings/{b.code}/lines/{b.lines[0].id}/loaded", data={"csrf_token": "tok", "loaded": "1"})
    assert r.status_code == 302
    s.expire_all()
    assert s.get(Booking, b.id).lines[0].loaded_at is not None
    r = c_ops.get(f"/playlists?booking={b.code}&from={b.start_date}&days=2")
    assert r.status_code == 200 and f"SZR-02_{b.code}" in r.get_data(as_text=True)
    for fmt, ctype in (("csv", "text/csv"), ("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
        r = c_ops.get(f"/playlists?booking={b.code}&from={b.start_date}&days=1&format={fmt}")
        assert r.status_code == 200 and ctype in r.headers["Content-Type"]
    r = c_ops.get(f"/bookings/{b.code}/load.zip")
    assert r.status_code == 200 and r.headers["Content-Type"] == "application/zip"
    r = c_ops.post(f"/bookings/{b.code}/proof", data={"csrf_token": "tok", "files": (io.BytesIO(read(media["good.png"])),
                                                                                       "shot.png"), "shared": "1"},
                   content_type="multipart/form-data")
    assert r.status_code == 302
    p = s.scalars(select(Proof)).one()
    assert c_ops.get(f"/proof/{p.id}").status_code == 200
    c_ops.post(f"/proof/{p.id}/share", data={"csrf_token": "tok"})
    s.expire_all()
    assert s.get(Proof, p.id).shared is False
    sales = login(people["sales"])
    assert sales.post(f"/proof/{p.id}/delete", data={"csrf_token": "tok"}).status_code == 403
