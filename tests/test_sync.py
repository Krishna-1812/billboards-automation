"""Mirroring to Google Calendar and Sheets, and the job queue's retries."""

from datetime import timedelta

from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops import jobs, settings, sync
from billboard_ops.jobs import enqueue, handler, run_due
from billboard_ops.models import Job, Notification, utcnow

from conftest import future


def hold(s, people, inv, screen):
    li = svc.LineIn(screen_id=screen.id, start_date=future(5), end_date=future(9), ad_seconds=10, plays_per_hour=30)
    b = svc.create(s, people["sales"], inv["client"], [li], title="Spring")
    s.commit()
    return b


def test_a_hold_appears_on_the_screens_calendar_and_follows_its_status(s, people, inventory, google):
    b = hold(s, people, inventory, inventory["a"])
    run_due()
    s.expire_all()
    ev_id = b.lines[0].calendar_event_id
    ev = google.events["cal-szr01"][ev_id]
    assert ev["summary"] == f"[HOLD] Acme Foods · Spring ({b.code})"
    assert ev["colorId"] == "5" and ev["start"] == {"date": future(5).isoformat()}
    assert ev["end"] == {"date": future(10).isoformat()}                      # all-day end is exclusive
    assert "https://ops.example.com/bookings/" + b.code in ev["description"]
    svc.confirm(s, people["manager"], b)
    s.commit()
    run_due()
    assert google.events["cal-szr01"][ev_id]["colorId"] == "10"                # same event, now green
    svc.cancel(s, people["manager"], b, "budget cut")
    s.commit()
    run_due()
    s.expire_all()
    assert ev_id not in google.events["cal-szr01"] and b.lines[0].calendar_event_id == ""


def test_a_screen_without_a_calendar_is_skipped_not_failed(s, people, inventory, google):
    hold(s, people, inventory, inventory["c"])
    run_due()
    j = s.scalars(select(Job).where(Job.kind == "calendar_line")).one()
    assert j.status == "skipped" and "MAR-01 has no Google Calendar" in j.last_error


def test_without_google_jobs_are_skipped_with_the_reason(s, people, inventory):
    hold(s, people, inventory, inventory["a"])
    run_due()
    j = s.scalars(select(Job).where(Job.kind == "calendar_line")).one()
    assert j.status == "skipped" and "GOOGLE_SERVICE_ACCOUNT_JSON" in j.last_error


def test_mirror_tab(s, people, inventory, google):
    b = hold(s, people, inventory, inventory["a"])
    run_due()
    assert not any(c[0] == "write" for c in google.calls)                     # no mirror sheet set yet
    settings.put(s, "mirror_spreadsheet_id", "MIRROR")
    sync.queue_sheet_mirror(s)
    s.commit()
    jobs_due_now(s)
    run_due()
    rows = google.sheets[("MIRROR", "Bookings (from app)")]
    assert rows[0] == sync.MIRROR_HEADER
    assert rows[1][:5] == [b.code, "hold", "Acme Foods", "Spring", "SZR-01"] and rows[1][8] == "all operating hours"


def jobs_due_now(s):
    for j in s.scalars(select(Job).where(Job.status == "queued")):
        j.run_at = utcnow() - timedelta(seconds=1)
    s.commit()


def test_failing_jobs_back_off_then_tell_the_team(s, app):
    calls = []

    @handler("test_boom")
    def _boom(session, payload):
        calls.append(1)
        raise RuntimeError("Google said 500")

    enqueue(s, "test_boom", {})
    s.commit()
    for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
        jobs_due_now(s)
        run_due()
        s.expire_all()
        j = s.scalars(select(Job).where(Job.kind == "test_boom")).one()
        if attempt < jobs.MAX_ATTEMPTS:
            assert j.status == "queued" and j.run_at > utcnow() + timedelta(seconds=50)
    assert j.status == "failed" and len(calls) == jobs.MAX_ATTEMPTS
    note = s.scalars(select(Notification).where(Notification.kind == "error")).one()
    assert "test_boom" in note.text and "Google said 500" in note.text


def test_dedupe_keeps_one_waiting_job(s, app):
    a = enqueue(s, "sheet_mirror", {"n": 1}, dedupe_key="sheet_mirror")
    s.flush()
    b = enqueue(s, "sheet_mirror", {"n": 2}, dedupe_key="sheet_mirror")
    assert a is b and b.payload == {"n": 2}
