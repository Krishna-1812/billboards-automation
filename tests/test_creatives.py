"""Phase 3 end to end: asking for files, receiving them every way, checking, feedback, versions, approval,
reminders, the check test set, and the pages."""

import io
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops import creative_checks as cc
from billboard_ops import creative_eval, creatives, intake, settings
from billboard_ops.jobs import run_due
from billboard_ops.models import (AiCall, Booking, Creative, CreativeCase, CreativeRun, Job, Message, Notification,
                                  Proposal, utcnow)

from conftest import future
from media import H, W, media, read  # noqa: F401
from test_creative_checks import answer as content_answer


@pytest.fixture
def small(s, inventory):
    sp = inventory["spec"]
    sp.width_px, sp.height_px, sp.durations_s, sp.file_types = W, H, "10", "mp4,jpg,png"
    sp.fps_min = sp.fps_max = None
    s.commit()
    return inventory


@pytest.fixture
def ai_reads_ads(fake_ai):
    def respond(kw):
        if kw["output_config"]["format"]["schema"] is cc.CONTENT_SCHEMA:
            return content_answer()
        from conftest import ai_answer
        return ai_answer()
    fake_ai.default = respond
    return fake_ai


def jobs_now(s):
    for j in s.scalars(select(Job).where(Job.status == "queued")):
        j.run_at = utcnow() - timedelta(seconds=1)
    s.commit()


def confirmed(s, people, inv, start=None):
    start = start or future(20)
    b = svc.create(s, people["sales"], inv["client"],
                   [svc.LineIn(inv["a"].id, start, start + timedelta(days=13), ad_seconds=10, plays_per_hour=30),
                    svc.LineIn(inv["b"].id, start, start + timedelta(days=13), ad_seconds=10, plays_per_hour=30)],
                   title="Launch")
    svc.confirm(s, people["manager"], b, po_number="PO-1")
    s.commit()
    jobs_now(s)
    run_due()
    s.expire_all()
    return s.get(Booking, b.id)


def test_working_days():
    fri = date(2026, 11, 6)       # with Saturday and Sunday off
    assert creatives.working_days_before(date(2026, 11, 13), 5, {5, 6}) == fri
    assert creatives.working_days_before(date(2026, 11, 13), 5, {4, 5}) == date(2026, 11, 8)   # Fri–Sat off


def test_confirming_asks_for_the_files(s, people, small):
    b = confirmed(s, people, small)
    assert b.status == "awaiting_creative" and len(b.upload_token) > 20
    assert b.creative_deadline == creatives.working_days_before(b.start_date, 5, {5, 6})
    m = s.scalars(select(Message).where(Message.kind == "creative_request")).one()
    assert m.status == "draft" and m.recipient == "ads@acme.example" and m.channel == "email"
    assert "SZR-01, SZR-02: 640x360 pixels (landscape), 10 seconds exactly, MP4/JPG/PNG, no sound" in m.body
    assert f"https://ops.example.com/upload/{b.upload_token}" in m.body and "Arabic, or in Arabic and English" in m.body


def test_the_file_request_gives_the_frame_rate_range_the_spec_accepts(s, people, small):
    small["spec"].fps_min, small["spec"].fps_max = 25, 30
    s.commit()
    confirmed(s, people, small)
    m = s.scalars(select(Message).where(Message.kind == "creative_request")).one()
    assert "25–30 frames per second" in m.body


def test_with_drive_the_folder_is_made_and_shared(s, people, small, google):
    settings.put(s, "drive_parent_folder_id", "PARENT")
    s.commit()
    b = confirmed(s, people, small)
    assert b.drive_folder_id and ("share", b.drive_folder_id, "ads@acme.example", "writer") in google.calls
    m = s.scalars(select(Message).where(Message.kind == "creative_request")).one()
    assert f"drive.google.com/drive/folders/{b.drive_folder_id}" in m.body


def test_the_clients_upload_link(app, s, people, small, media):
    b = confirmed(s, people, small)
    c = app.test_client()
    page = c.get(f"/upload/{b.upload_token}").get_data(as_text=True)
    assert "640x360" in page and b.code in page
    assert c.get("/upload/not-a-real-token-at-all-xx").status_code == 404
    c.get(f"/upload/{b.upload_token}")
    with c.session_transaction() as sess:
        tok = sess["csrf"]
    r = c.post(f"/upload/{b.upload_token}", data={"csrf_token": tok,
                                                   "files": [(io.BytesIO(read(media["good.mp4"])), "ad.mp4"),
                                                             (io.BytesIO(b"x"), "notes.txt")]},
               content_type="multipart/form-data")
    text = r.get_data(as_text=True)
    assert "We have your files" in text and "notes.txt" in text
    s.expire_all()
    cr = s.scalars(select(Creative)).one()
    assert (cr.version, cr.source, cr.filename, s.get(Booking, b.id).status) == (1, "upload", "ad.mp4", "checking")
    small["client"].language = "ar"
    s.commit()
    assert "ملفات الإعلان" in c.get(f"/upload/{b.upload_token}").get_data(as_text=True)


def test_a_failing_file_then_a_fixed_version(s, people, small, media, ai_reads_ads):
    b = confirmed(s, people, small)
    v1 = creatives.add_file(s, b, filename="ad.mp4", content_type="video/mp4", data=read(media["long.mp4"]))
    creatives.check(s, v1)
    s.commit()
    assert v1.status == "failed" and b.status == "changes_requested"
    assert sorted(v1.line_ids) == sorted(l.id for l in b.lines)
    assert "fails 1 of" in v1.team_summary and "ad length: 15.0s, needs 10s" in v1.team_summary
    fb = s.scalars(select(Message).where(Message.kind == "creative_feedback")).one()
    assert fb.status == "draft" and "The video is 15.0s long. This screen plays 10s ads" in fb.body
    call = s.scalars(select(AiCall).where(AiCall.creative_id == v1.id)).one()
    assert call.purpose == "check_creative" and call.booking_id == b.id and call.client_id == b.client_id
    img = [blk for blk in ai_reads_ads.calls[-1]["messages"][0]["content"] if blk["type"] == "image"]
    assert len(img) == len(v1.frames) >= 5
    v2 = creatives.add_file(s, b, filename="ad-v2.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, v2)
    s.commit()
    assert v2.version == 2 and v2.status == "passed" and v1.status == "superseded"
    assert fb.status == "discarded"                       # v1's unsent feedback is out of date
    assert v2.results["fixed"] == {"previous": 1, "had": 1, "fixed": ["duration"], "still": [], "new": []}
    with pytest.raises(Exception):
        creatives.approve(s, people["sales"], v2)            # Sales can't approve files
    creatives.approve(s, people["ops"], v2)
    assert v2.status == "approved" and b.status == "creative_approved"


def test_approving_a_failed_file_needs_a_manager_and_a_reason(s, people, small, media, ai_reads_ads):
    b = confirmed(s, people, small)
    v = creatives.add_file(s, b, filename="ad.mp4", data=read(media["sound.mp4"]))
    creatives.check(s, v)
    from billboard_ops.permissions import Forbidden
    with pytest.raises(Forbidden):
        creatives.approve(s, people["ops"], v, "fine")
    with pytest.raises(creatives.CreativeError):
        creatives.approve(s, people["manager"], v, "")
    creatives.approve(s, people["manager"], v, "The screen's speakers are off anyway")
    assert v.status == "approved" and v.decision_note.startswith("The screen")


def test_reject_sends_the_note(s, people, small, media, ai_reads_ads):
    b = confirmed(s, people, small)
    v = creatives.add_file(s, b, filename="ad.png", content_type="image/png", data=read(media["good.png"]))
    creatives.check(s, v)
    assert v.status == "passed"
    m = creatives.reject(s, people["ops"], v, "The logo is too small on this screen.")
    assert v.status == "rejected" and b.status == "changes_requested" and "The logo is too small" in m.body


def test_without_claude_content_needs_a_person(s, people, small, media):
    b = confirmed(s, people, small)
    v = creatives.add_file(s, b, filename="ad.mp4", data=read(media["good.mp4"]))
    creatives.check(s, v)
    assert v.status == "warnings"
    lang = next(x for x in v.results["content"] if x["key"] == "language")
    assert lang["status"] == "warn" and "Check by eye" in lang["detail"]


def test_measured_faults_can_go_out_automatically(s, people, small, media, ai_reads_ads):
    settings.put(s, "auto_send_technical", True)
    b = confirmed(s, people, small)
    v = creatives.add_file(s, b, filename="ad.mp4", data=read(media["letterbox.mp4"]))
    creatives.check(s, v)
    fb = s.scalars(select(Message).where(Message.kind == "creative_feedback")).one()
    assert fb.status == "approved" and fb.approved_by == "agent" and "black bars" in fb.body


def test_files_sent_by_email_reply_join_the_booking(s, people, small, media, ai_reads_ads):
    b = confirmed(s, people, small)
    req = s.scalars(select(Message).where(Message.kind == "creative_request")).one()
    req.status, req.external_id = "sent", "<files-request@ops>"
    s.commit()
    p, m, new = intake.receive(s, channel="email", sender="ads@acme.example", subject="Re: Files needed",
                               body="Here you go", attachments=[{"filename": "ad.mp4", "content_type": "video/mp4",
                                                                 "data": read(media["good.mp4"])}],
                               external_id="<reply@acme>", reply_ids=["<files-request@ops>"])
    assert p is None and not new and m.booking_id == b.id and m.kind == "files"
    assert s.scalars(select(Creative)).one().source == "email"
    assert s.scalars(select(Proposal)).first() is None


def test_files_on_whatsapp_from_a_known_number(s, people, small, media, ai_reads_ads):
    from billboard_ops.channels import whatsapp
    small["client"].whatsapp = "+971 50 000 0001"
    b = confirmed(s, people, small)
    from billboard_ops import runtime
    runtime.config.WHATSAPP_TOKEN, runtime.config.WHATSAPP_PHONE_NUMBER_ID = "t", "1"

    class HTTP:
        def get(self, url, headers=None, timeout=None):
            if url.endswith("/vid-1"):
                return SimpleNamespace(raise_for_status=lambda: None,
                                       json=lambda: {"url": "https://cdn/v", "mime_type": "video/mp4"})
            return SimpleNamespace(raise_for_status=lambda: None, content=read(media["good.mp4"]))
    whatsapp.set_http(HTTP())
    try:
        p, m, _ = intake.receive(s, channel="whatsapp", sender="+971500000001", body="our ad",
                                 attachments=[{"filename": "video.mp4", "content_type": "video/mp4",
                                               "external_ref": "vid-1", "data": None}], external_id="wamid.v")
    finally:
        whatsapp.set_http(None)
        runtime.config.WHATSAPP_TOKEN = runtime.config.WHATSAPP_PHONE_NUMBER_ID = ""
    c = s.scalars(select(Creative)).one()
    assert p is None and c.booking_id == b.id and c.source == "whatsapp" and c.size > 1000


def test_new_files_in_the_drive_folder(s, people, small, media, google, ai_reads_ads):
    settings.put(s, "drive_parent_folder_id", "PARENT")
    s.commit()
    b = confirmed(s, people, small)
    google.files = {"d1": {"folder": b.drive_folder_id, "name": "final.mp4", "mimeType": "video/mp4",
                           "data": read(media["good.mp4"])},
                    "d2": {"folder": b.drive_folder_id, "name": "brief.docx", "mimeType": "application/msword",
                           "data": b"x"}}
    assert creatives.poll_drive(s) == 1
    assert creatives.poll_drive(s) == 0                   # seen already
    c = s.scalars(select(Creative)).one()
    assert c.drive_file_id == "d1" and c.data is None
    creatives.check(s, c)                                 # downloaded from Drive to check
    assert c.status == "passed"


def test_uploads_go_to_drive_when_it_is_set_up(s, people, small, media, google):
    settings.put(s, "drive_parent_folder_id", "PARENT")
    s.commit()
    b = confirmed(s, people, small)
    c = creatives.add_file(s, b, filename="ad.png", content_type="image/png", data=read(media["good.png"]))
    assert c.drive_file_id and c.data is None and google.files[c.drive_file_id]["folder"] == b.drive_folder_id


def test_reminders_and_start_date_risk(s, people, small):
    b = confirmed(s, people, small, start=future(12))
    today = b.creative_deadline - timedelta(days=1)
    creatives.tick(s, today)
    assert s.scalars(select(Message).where(Message.kind == "creative_reminder")).one().status == "draft"
    risk_day = creatives.working_days_before(b.start_date, 5, {5, 6})
    creatives.tick(s, risk_day)
    creatives.tick(s, risk_day)                           # once a day
    alerts = s.scalars(select(Notification).where(Notification.kind == "error")).all()
    assert len(alerts) == 1 and "At risk" in alerts[0].text
    assert len(s.scalars(select(Message).where(Message.kind == "creative_reminder")).all()) == 1
    emails = [j for j in s.scalars(select(Job).where(Job.kind == "send_email")) if "At risk" in j.payload["subject"]]
    assert {"owner@example.com", "manager@example.com", "ops@example.com"} <= set(emails[0].payload["to"])


def test_the_check_test_set(s, people, small, media):
    sp = small["spec"]
    s.add_all([CreativeCase(name="too long", filename="a.mp4", data=read(media["long.mp4"]), spec_id=sp.id,
                            ad_seconds=10, expected={"duration": "fail", "resolution": "pass"}),
               CreativeCase(name="good", filename="b.mp4", data=read(media["good.mp4"]), spec_id=sp.id,
                            ad_seconds=10, expected={"duration": "pass", "sound": "pass", "borders": "pass"}),
               CreativeCase(name="sound", filename="c.mp4", data=read(media["sound.mp4"]), spec_id=sp.id,
                            ad_seconds=10, expected={"sound": "fail"})])
    run = CreativeRun(started_by="manager@example.com")
    s.add(run)
    s.commit()
    creative_eval._run_job(s, {"run_id": run.id})
    assert run.status == "done" and run.summary["measured"] == 100.0 and run.summary["missed"] == 0
    assert run.summary["passed"] is True and run.summary["cases"] == 3
    assert creative_eval.summarise([{"rows": [{"key": "duration", "want": "fail", "got": "pass",
                                               "result": "missed"}]}])["passed"] is False


def test_pages(app, s, people, small, media, ai_reads_ads, login):
    b = confirmed(s, people, small)
    v = creatives.add_file(s, b, filename="ad.mp4", content_type="video/mp4", data=read(media["long.mp4"]))
    creatives.check(s, v)
    s.add(CreativeCase(name="x", filename="a.mp4", data=b"x", spec_id=small["spec"].id, expected={"sound": "pass"}))
    s.add(CreativeRun(status="done", summary={"cases": 1, "measured": 100.0, "content": None, "missed": 0,
                                              "false_alarms": 0, "language_missed": 0, "passed": True}))
    s.commit()
    c = login(people["owner"])
    for url in ["/creatives", f"/creatives/{v.id}", f"/creatives/frames/{v.frames[0].id}", f"/bookings/{b.code}",
                "/creatives/test-set", "/creatives/test-set/new", "/creatives/test-set/runs/1", "/settings"]:
        assert c.get(url).status_code == 200, url
    page = c.get(f"/creatives/{v.id}").get_data(as_text=True)
    assert "Ad length" in page and "15.0s" in page and "Reject and tell the client" in page
    assert c.get(f"/creatives/{v.id}/file").data[4:8] == b"ftyp"
    r = c.post(f"/creatives/{v.id}/approve", data={"csrf_token": "tok", "note": ""}, follow_redirects=True)
    assert "say why" in r.get_data(as_text=True)
    booking_page = c.get(f"/bookings/{b.code}").get_data(as_text=True)
    assert "Client's upload link" in booking_page and "v1 ad.mp4" in booking_page
    assert login(people["sales"]).post(f"/creatives/{v.id}/recheck", data={"csrf_token": "tok"},
                                       follow_redirects=True).status_code == 200
    s.expire_all()
    assert s.get(Creative, v.id).status == "failed"        # Sales can't re-check (no change)


def test_team_upload_and_settings(app, s, people, small, media, login):
    b = confirmed(s, people, small)
    c = login(people["ops"])
    c.post(f"/bookings/{b.code}/files/upload", data={"csrf_token": "tok",
                                                       "files": (io.BytesIO(read(media["good.png"])), "a.png")},
           content_type="multipart/form-data")
    assert s.scalars(select(Creative)).one().source == "team"
    o = login(people["owner"])
    o.post("/settings", data={"csrf_token": "tok", "hold_days": "3", "hold_reminder_hours": "24",
                              "creative_lead_days": "7", "weekend_days": "4,5", "black_max_s": "abc",
                              "freeze_max_s": "4", "content_rules": "No alcohol\nNo gambling",
                              "drive_parent_folder_id": "https://drive.google.com/drive/folders/XYZ?usp=sharing"})
    s.expire_all()
    assert settings.get(s, "creative_lead_days") == 7 and settings.get(s, "weekend_days") == "4,5"
    assert settings.get(s, "black_max_s") == 0.5 and settings.get(s, "drive_parent_folder_id") == "XYZ"
    o.post("/settings", data={"csrf_token": "tok", "hold_days": "3", "hold_reminder_hours": "24"})
    s.expire_all()
    assert settings.get(s, "weekend_days") == "4,5"        # a form without the section leaves it alone


def test_preview_cache_stays_under_its_limit(tmp_path, monkeypatch):
    from billboard_ops import creative_store
    monkeypatch.setattr(creative_store, "CACHE", str(tmp_path))
    import os
    import time
    for i in range(5):
        p = tmp_path / f"{i}.mp4"
        p.write_bytes(b"x" * 1000)
        os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
    creative_store._prune(limit=2500)
    assert sorted(f.name for f in tmp_path.iterdir()) == ["3.mp4", "4.mp4"]
