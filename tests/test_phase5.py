"""Phase 5: the owner dashboard and its numbers, exports, the Monday summary, hardening (headers, rate limits, every
page behind sign-in), backups and restore, monitoring, data rules, deleting a client, and the launch checklist."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from billboard_ops import backup, bookings as svc, db, insights, launch, monitor, plans, retention, settings, summary
from billboard_ops.models import (Activity, Base, Booking, Client, Creative, CreativeFrame, Job, Message,
                                  Notification, Proposal, Screen, SheetWatch, User, utcnow)

from conftest import future
from media import media  # noqa: F401
from test_creatives import ai_reads_ads, confirmed, small  # noqa: F401


def form(**kw):
    return {"csrf_token": "tok", **kw}


def hold(s, people, inv, start, days=7, pph=30, screen="a", client=None):
    b = svc.create(s, people["sales"], client or inv["client"],
                   [svc.LineIn(inv[screen].id, start, start + timedelta(days=days - 1), ad_seconds=10,
                               plays_per_hour=pph)])
    s.commit()
    return b


# ── The numbers ─────────────────────────────────────────────────────────────────

def test_occupancy_is_loop_time_used(s, people, inventory):
    w0, w1 = insights.week_of(date.today())
    b = hold(s, people, inventory, w0, days=7, pph=36)                 # 360 s of 3600 = 10% of every hour
    svc.confirm(s, people["manager"], b, po_number="PO-1")
    s.commit()
    avg, per = insights.occupancy(s, [inventory["a"], inventory["b"]], w0, w1)
    assert per[inventory["a"].id] == pytest.approx(10.0) and per[inventory["b"].id] == 0 and avg == pytest.approx(5.0)
    top = insights.top_numbers(s, date.today())
    assert top["week"] == pytest.approx(10.0 / 3, abs=0.01)               # three screens, one 10% full


def test_pipeline_and_unreplied(s, people, inventory):
    today = date.today()
    p = Proposal(channel="email", sender="ads@acme.example", status="needs_review", ref="PR-1")
    s.add(p)
    b = hold(s, people, inventory, future(20))
    svc.confirm(s, people["manager"], b, po_number="PO-1")
    s.flush()
    m = Message(proposal_id=p.id, booking_id=b.id, direction="out", channel="email", kind="creative_request",
                status="sent", subject="Files needed", sent_at=utcnow() - timedelta(days=4))
    s.add(m)
    s.commit()
    m0, m1 = insights.month_of(today)
    pipe = insights.pipeline(s, m0, m1)
    assert pipe["requests"] == 1 and pipe["holds"] == 1 and pipe["confirmed"] == 1
    risks = insights.at_risk(s, today)
    assert [r["kind"] for r in risks] == ["no_reply"] and "No reply for 4 days" in risks[0]["text"]
    assert risks[0]["owner"] == "Sales"
    s.add(Message(proposal_id=p.id, direction="in", channel="email", body="Here you go", status="received"))
    s.commit()
    assert insights.at_risk(s, today) == []


def test_at_risk_lists_unready_starts_and_expiring_holds(s, people, small):
    b = confirmed(s, people, small, start=date.today() + timedelta(days=2))
    h = hold(s, people, small, future(30), screen="c")
    h.hold_expires_at = utcnow() + timedelta(hours=5)
    s.commit()
    risks = {r["code"]: r for r in insights.at_risk(s, date.today())}
    assert set(risks) == {b.code, h.code}
    assert risks[b.code]["text"].startswith("Starts in 2 days and still needs approved files")
    assert risks[b.code]["owner"] == "Acme Foods (files), chased by Sales"
    assert risks[h.code]["text"].startswith("Hold expires in 4 hours")


def test_the_agents_work_and_peoples_changes(s, people, inventory, fake_ai):
    from billboard_ops import intake, outbox
    b = hold(s, people, inventory, future(20))
    svc.confirm(s, people["manager"], b, po_number="PO-1")
    plans.draft(s, b)
    p = plans.build(plans.dayparts(s), {"Morning peak": 10, "Daytime": 30, "Evening peak": 60, "Night": 0})
    plans.save(s, people["ops"], b, {b.lines[0].id: p})
    plans.confirm(s, people["ops"], b)
    assert b.plan_edited
    m = Message(direction="out", channel="email", kind="clarify", status="draft", recipient="a@x.example",
                subject="Hi", body="Draft text")
    s.add(m)
    s.flush()
    outbox.approve(s, people["sales"], m, body="Draft text, kinder")
    assert m.edited
    pr = Proposal(channel="email", sender="ads@acme.example", ref="PR-9", status="needs_review", read_count=1,
                  client_id=inventory["client"].id,
                  draft={"client_id": inventory["client"].id, "lines": [
                      {"screen_id": inventory["a"].id, "start_date": future(40).isoformat(),
                       "end_date": future(46).isoformat()}]})
    s.add(pr)
    s.flush()
    intake.place_hold(s, people["sales"], pr, client=inventory["client"],
                      lines=[svc.LineIn(inventory["b"].id, future(40), future(46), ad_seconds=10, plays_per_hour=30)])
    assert pr.edited                                                    # another screen than the agent read
    s.commit()
    perf = insights.performance(s, date.today())
    assert perf["changed"]["Play plans the agent drafted"] == {"n": 1, "changed": 1, "pct": 100.0}
    assert perf["changed"]["Messages the agent drafted"]["changed"] == 1
    assert perf["changed"]["Proposals the agent read"]["changed"] == 1
    assert perf["agent_work"]["Play plans drafted"] == 1


# ── The pages and exports ───────────────────────────────────────────────────────

def test_dashboard_and_exports(app, s, people, small, login, google):
    small["a"].lat, small["a"].lng = 25.1, 55.2
    b = confirmed(s, people, small, start=date.today() + timedelta(days=1))
    c = login(people["owner"])
    page = c.get("/").get_data(as_text=True)
    for bit in ("Loop time sold this month", "This week", "Starting this week", "Open holds",
                "Files waiting for a decision", "At risk", "The next 12 weeks", "Pipeline", "Campaigns started",
                b.code, "leaflet@1.9.4", '"code": "SZR-01"', "Average share of loop time sold"):
        assert bit in page, bit
    for what in ("risk", "grid", "pipeline", "screens"):
        for fmt, ctype in (("csv", "text/csv"), ("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
            r = c.get(f"/?export={what}&format={fmt}")
            assert r.status_code == 200 and ctype in r.headers["Content-Type"], (what, fmt)
    assert b.code in c.get("/?export=risk&format=csv").get_data(as_text=True)
    assert c.get("/?export=nope&format=csv").status_code == 404
    r = c.post("/export/sheet", data=form(what="grid"), follow_redirects=True)
    assert "Couldn&#39;t write to Google Sheets" in r.get_data(as_text=True)
    settings.put(s, "mirror_spreadsheet_id", "SHEET")
    s.commit()
    c.post("/export/sheet", data=form(what="grid"))
    tab = [k for k in google.sheets if k[0] == "SHEET"][0]
    assert tab[1].startswith("Occupancy 12 weeks") and google.sheets[tab][0][:2] == ["Screen", "Name"]
    assert login(people["sales"]).post("/export/sheet", data=form(what="grid")).status_code == 403


def test_performance_client_screen_and_costs_pages(app, s, people, small, login):
    b = confirmed(s, people, small)
    c = login(people["owner"])
    assert "How often people changed the agent" in c.get("/performance").get_data(as_text=True)
    assert c.get("/performance?format=xlsx").status_code == 200
    page = c.get(f"/clients/{small['client'].id}").get_data(as_text=True)
    assert "At a glance" in page and "Plays booked" in page and "Delete all of Acme Foods" in page
    page = c.get(f"/screens/{small['a'].id}").get_data(as_text=True)
    assert "The next 12 weeks" in page and "1 campaign(s) in the last 12 months, mostly Acme Foods (1)" in page
    assert "By booking, this month" not in c.get("/settings/costs").get_data(as_text=True)    # no Claude calls yet
    assert login(people["sales"]).get("/performance").status_code == 403
    assert b


# ── The Monday summary ──────────────────────────────────────────────────────────

def test_the_monday_summary(app, s, people, small, login):
    confirmed(s, people, small, start=date.today() + timedelta(days=1))
    settings.put(s, "weekly_summary_to", "board@acme-owner.example")
    s.commit()
    subject, body = summary.compose(s, date.today())
    assert "% sold" in subject and "at risk" in subject
    assert "THE NUMBERS" in body and "AT RISK (1)" in body and "WAITING FOR A DECISION" in body
    assert "Messages to approve: 1" in body and "GAPS WORTH SELLING" in body
    sunday = datetime(2026, 11, 8, 6, 0)                    # 10:00 in Dubai, a Sunday
    monday_early = datetime(2026, 11, 9, 2, 0)              # 06:00 in Dubai
    monday = datetime(2026, 11, 9, 4, 0)                    # 08:00 in Dubai
    assert summary.maybe_send(s, sunday) is None and summary.maybe_send(s, monday_early) is None
    to = summary.maybe_send(s, monday)
    assert to == ["board@acme-owner.example", "owner@example.com"]
    assert summary.maybe_send(s, monday + timedelta(hours=2)) is None                       # once a week
    s.commit()
    mail = [j.payload for j in s.scalars(select(Job).where(Job.kind == "send_email"))]
    assert any(m["subject"].startswith("Billboard Ops weekly") for m in mail)
    c = login(people["owner"])
    assert "THE NUMBERS" in c.get("/summary").get_data(as_text=True)
    assert c.post("/summary", data=form()).status_code == 302


# ── Hardening ───────────────────────────────────────────────────────────────────

def test_security_headers(app):
    r = app.test_client().get("/portal/login")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert r.headers["Referrer-Policy"] == "same-origin" and "no-store" in r.headers["Cache-Control"]
    r = app.test_client().get("/health", base_url="https://ops.example.com")
    assert r.headers["Strict-Transport-Security"].startswith("max-age=")


def test_guessing_is_slowed_down(app, s):
    c = app.test_client()
    c.get("/portal/login")
    with c.session_transaction() as sess:
        tok = sess["csrf"]
    codes = [c.post("/portal/login", data={"csrf_token": tok, "email": f"x{i}@y.example"}).status_code
             for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    codes = [c.get(f"/upload/{'x' * 32}{i}").status_code for i in range(61)]
    assert set(codes[:60]) == {404} and codes[60] == 429


PUBLIC = {"static", "main.health", "main.health_full", "auth.login", "portal.login", "webhooks.whatsapp_verify",
          "public.welcome", "public.signup", "public.signup_done"}


def test_every_page_needs_sign_in(app, s, people, small):
    """Every page without a parameter, opened by someone not signed in, goes to a sign-in page (or answers
    nothing). Pages with parameters are covered by their own tests."""
    c = app.test_client()
    seen = 0
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments or rule.endpoint in PUBLIC:
            continue
        r = c.get(rule.rule)
        loc = r.headers.get("Location", "")
        assert r.status_code in (302, 401, 403, 404) and (r.status_code != 302 or loc.endswith(("/login", "/portal/login", "/welcome"))), \
            (rule.rule, r.status_code, loc)
        seen += 1
    assert seen > 30


def test_a_client_never_reaches_a_team_page(app, s, people, small):
    u = User(email="sara@acme.example", role="client", client_id=small["client"].id)
    s.add(u)
    s.commit()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = u.id
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments or rule.endpoint in PUBLIC:
            continue
        if rule.endpoint.startswith("portal."):
            continue
        r = c.get(rule.rule)
        assert r.status_code in (302, 403, 404) and (r.status_code != 302 or
                                                     r.headers["Location"].endswith("/portal")), rule.rule


# ── Backups ─────────────────────────────────────────────────────────────────────

def test_backup_and_restore_round_trip(app, s, people, small, tmp_path):
    b = confirmed(s, people, small)
    s.add(Creative(booking_id=b.id, version=1, filename="a.mp4", data=b"VIDEO", line_ids=[b.lines[0].id],
                   status="approved"))
    s.commit()
    data = backup.loads(backup.dumps(backup.export(s)))
    assert data["tables"]["creatives"]["rows"][0][data["tables"]["creatives"]["columns"].index("data")] is None
    with pytest.raises(backup.BackupError, match="already has screens"):
        backup.restore(s, data)
    with pytest.raises(backup.BackupError):
        backup.loads(b"not a backup")
    # Into a fresh, empty database.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    if db.is_postgres():
        pytest.skip("the empty second database is a SQLite file here; CI's Postgres run covers export")
    eng = create_engine(f"sqlite:///{tmp_path}/restored.db")
    Base.metadata.create_all(eng)
    with Session(eng) as other:
        counts = backup.restore(other, data)
        other.commit()
        assert counts["bookings"] == 1 and counts["users"] == len(people)
        rb = other.scalars(select(Booking)).one()
        assert rb.code == b.code and rb.start_date == b.start_date and rb.confirmed_at == b.confirmed_at
        assert other.scalars(select(Screen).order_by(Screen.code)).first().code == "MAR-01"
        assert other.scalars(select(Creative)).one().data is None


def test_nightly_backup_to_drive(app, s, people, small, google, login):
    with pytest.raises(Exception):
        backup.nightly(s)                                   # no Drive: skipped, and recorded
    assert settings.get(s, "last_backup")["ok"] is False
    settings.put(s, "drive_parent_folder_id", "PARENT")
    google.files = {f"old{i}": {"folder": google.drive_folder("Backups", "PARENT"),
                                "name": f"billboard-ops-2026-01-{i + 1:02d}.json.gz", "data": b""} for i in range(31)}
    backup.nightly(s)
    s.commit()
    last = settings.get(s, "last_backup")
    assert last["ok"] and last["rows"] > 0 and last["file"] == f"billboard-ops-{date.today():%Y-%m-%d}.json.gz"
    kept = [f for f in google.files.values() if f["name"].startswith("billboard-ops-")]
    assert len(kept) == backup.KEEP and ("delete_file", "old0") in google.calls
    local = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=1)
    assert backup.maybe_queue(s, local) is False
    assert backup.maybe_queue(s, local + timedelta(hours=2)) is True
    assert backup.maybe_queue(s, local + timedelta(hours=3)) is False                     # once a night
    s.commit()
    c = login(people["owner"])
    r = c.post("/settings/backup.json.gz", data=form())
    assert r.status_code == 200 and backup.loads(r.data)["app"] == "billboard-ops"
    page = c.get("/settings/connections").get_data(as_text=True)
    assert "Nightly backup to the Shared Drive" in page and "last one" in page


# ── Monitoring ──────────────────────────────────────────────────────────────────

def test_connections_are_watched(app, s, people):
    t0 = utcnow()
    monitor.record(s, "send_email", True)
    monitor.record(s, "poll_drive", False, "403 Forbidden: the folder isn't shared")
    assert {n: st for n, _, st, _ in monitor.status(s, t0)} == {"email_out": "ok", "drive": "failing"}
    assert monitor.check(s, t0 + timedelta(minutes=10)) == []
    assert monitor.check(s, t0 + timedelta(minutes=31)) == ["drive"]
    assert monitor.check(s, t0 + timedelta(minutes=40)) == []                              # not again for 6 hours
    s.commit()
    note = s.scalars(select(Notification).where(Notification.topic == "system")).one()
    assert "Google Drive has been failing for over 30 minutes" in note.text
    monitor.record(s, "poll_drive", True)
    assert dict((n, st) for n, _, st, _ in monitor.status(s))["drive"] == "ok"


def test_health_full(app, s):
    c = app.test_client()
    r = c.get("/health/full")
    assert r.status_code == 503 and r.get_json()["worker_ok"] is False
    settings.put(s, "worker_heartbeat", utcnow().isoformat())
    s.commit()
    r = c.get("/health/full")
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_jobs_report_their_connection(app, s):
    from billboard_ops import jobs
    from test_creatives import jobs_now
    jobs.enqueue(s, "sheet_mirror", {})
    s.commit()
    jobs_now(s)
    jobs.run_due()
    s.expire_all()
    assert "sheets" not in (settings.get(s, "connection_status") or {})      # not set up: skipped, not a failure


# ── Data rules and deleting a client ────────────────────────────────────────────

def test_old_files_and_messages_are_removed(s, people, small):
    b = confirmed(s, people, small)
    c = Creative(booking_id=b.id, version=1, filename="a.mp4", data=b"VIDEO", line_ids=[], status="approved")
    s.add(c)
    s.flush()
    s.add(CreativeFrame(creative_id=c.id, at_seconds=0, jpeg=b"J"))
    old_msg = Message(direction="in", channel="email", body="old words", status="received",
                      created_at=utcnow() - timedelta(days=800))
    s.add(old_msg)
    s.commit()
    first = retention.purge(s, date.today())
    assert first["files"] == 0 and first["messages"] == 1                # running campaign: files kept
    b.status = "completed"
    for l in b.lines:
        l.start_date, l.end_date = date.today() - timedelta(days=500), date.today() - timedelta(days=400)
    s.commit()
    counts = retention.purge(s, date.today())
    s.commit()
    s.expire_all()
    assert counts == {"files": 1, "stills": 1, "proof": 0, "messages": 0, "attachments": 0}
    assert s.get(Creative, c.id).data is None and s.get(Creative, c.id).status == "approved"
    assert s.get(Message, old_msg.id).body == ""


def test_deleting_a_client(app, s, people, small, login):
    from billboard_ops import proof
    mine = confirmed(s, people, small)
    other = Client(name="Globex")
    s.add(other)
    s.flush()
    keep = hold(s, people, small, future(60), screen="c", client=other)
    proof.add(s, people["ops"], mine, filename="p.jpg", content_type="image/jpeg", data=b"\xff\xd8x")
    s.add(User(email="sara@acme.example", role="client", client_id=small["client"].id))
    s.add(Proposal(channel="email", sender="ads@acme.example", client_id=small["client"].id, ref="PR-7",
                   booking_id=mine.id))
    s.commit()
    cid = small["client"].id
    c = login(people["manager"])
    assert c.post(f"/clients/{cid}/delete-all", data=form(confirm_name="Acme Foods")).status_code == 403
    c = login(people["owner"])
    r = c.post(f"/clients/{cid}/delete-all", data=form(confirm_name="acme"), follow_redirects=True)
    assert "Type the client" in r.get_data(as_text=True)
    r = c.post(f"/clients/{cid}/delete-all", data=form(confirm_name="Acme Foods"))
    assert r.status_code == 302
    s.expire_all()
    assert s.get(Client, cid) is None and s.scalars(select(Booking)).all() == [s.get(Booking, keep.id)]
    assert not s.scalars(select(User).where(User.role == "client")).all()
    assert s.scalar(select(func.count()).select_from(Proposal)) == 0
    assert not s.scalars(select(Activity).where(Activity.summary.contains("Acme"))).all()
    assert s.scalars(select(Activity).where(Activity.action == "deleted_client")).one()


# ── Launch ──────────────────────────────────────────────────────────────────────

def test_the_launch_checklist(app, s, people, small, login):
    cfg = app.config["APP"]
    groups = dict(launch.checks(s, cfg))
    service = {c["label"]: c["state"] for c in groups["The service"]}
    assert service["The app uses the Postgres database"] == ("ok" if db.is_postgres() else "todo")
    assert service["Sign-in with Google is on"] == "todo"
    launch.tick(s, people["owner"], "training_team")
    with pytest.raises(Exception):
        launch.tick(s, people["manager"], "training_team")
    from billboard_ops.models import ImportRun
    run = ImportRun(kind="bookings", source_kind="sheet")
    s.add(run)
    s.flush()
    s.add(SheetWatch(import_run_id=run.id, spreadsheet_id="OLD", tab="Bookings"))
    s.commit()
    c = login(people["owner"])
    page = c.get("/settings/launch").get_data(as_text=True)
    assert "must-haves left" in page and "Ticked by owner@example.com" in page
    c.post("/settings/launch", data=form(action="start_parallel"))
    assert "Day 0 of 14" in c.get("/settings/launch").get_data(as_text=True)
    r = c.post("/settings/launch", data=form(action="cutover", confirm="yes"), follow_redirects=True)
    assert "Type CUT OVER" in r.get_data(as_text=True)
    c.post("/settings/launch", data=form(action="cutover", confirm="CUT OVER"))
    s.expire_all()
    assert settings.get(s, "cutover_at") and not s.scalars(select(SheetWatch)).one().active


def test_guides(app, s, people, small, login):
    c = login(people["ops"])
    page = c.get("/help").get_data(as_text=True)
    assert "Operations" in page and "Clients: your portal" in page
    page = c.get("/help?role=ops&lang=ar").get_data(as_text=True)
    assert 'dir="rtl"' in page and "العمليات" in page and "The owner" not in page.split("guide")[-1]
    u = User(email="sara@acme.example", role="client", client_id=small["client"].id, language="ar")
    s.add(u)
    s.commit()
    cl = app.test_client()
    with cl.session_transaction() as sess:
        sess["uid"] = u.id
    assert "بوابتكم" in cl.get("/portal/help").get_data(as_text=True)



def test_the_overview_leaves_out_tasks_skipped_for_want_of_a_connection(app, s, people, login):
    s.add(Job(kind="calendar_line", status="skipped", last_error="Screen SZR-01 has no Google Calendar set."))
    s.commit()
    owner = login(people["owner"])
    assert "Background tasks" not in owner.get("/").get_data(as_text=True)
    assert "no Google Calendar set" in owner.get("/settings/connections").get_data(as_text=True)
    s.add(Job(kind="send_email", status="failed", attempts=5, last_error="SMTP said no"))
    s.commit()
    page = owner.get("/").get_data(as_text=True)
    assert "Background tasks" in page and "SMTP said no" in page
