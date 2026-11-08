"""The pages: sign-in, roles, CSRF, and every page rendering with real data."""

import io
from datetime import timedelta
from unittest import mock

from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops.models import Booking, Client, ImportRun, Screen, User

from conftest import future

CSRF = {"csrf_token": "tok"}


def form(**kw):
    return {**CSRF, **kw}


def booking_form(screen, start, end, client_id, **kw):
    data = form(client_id=str(client_id), title="Launch", line_id="", screen_id=str(screen.id),
                start_date=start.isoformat(), end_date=end.isoformat(), start_hour="", end_hour="",
                ad_seconds="", freq_mode="slots", freq_value="1")
    data.update(kw)
    return data


# ── Sign-in ─────────────────────────────────────────────────────────────────────

def test_pages_need_sign_in(app):
    c = app.test_client()
    r = c.get("/bookings")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


def test_dev_sign_in_lets_in_known_people_and_bootstraps_owners(app, s, people):
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as sess:
        tok = sess["csrf"]
    r = c.post("/auth/dev", data={"csrf_token": tok, "email": "stranger@example.com"})
    assert r.headers["Location"].endswith("/login")
    r = c.post("/auth/dev", data={"csrf_token": tok, "email": "Owner@Example.com"})
    assert r.status_code == 302 and c.get("/").status_code == 200
    owner = s.scalars(select(User).where(User.email == "owner@example.com")).one()
    assert owner.role == "owner" and owner.last_login_at is not None


def test_google_sign_in_checks_the_token(app, s, people):
    app.config["APP"].GOOGLE_CLIENT_ID = "cid"
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as sess:
        tok = sess["csrf"]
    with mock.patch("billboard_ops.views.auth._verify_google", side_effect=ValueError("bad sig")):
        r = c.post("/auth/google", json={"credential": "x"}, headers={"X-CSRF-Token": tok})
        assert r.status_code == 401
    with mock.patch("billboard_ops.views.auth._verify_google",
                    return_value={"email": "sales@example.com", "email_verified": True, "name": "Sam"}):
        r = c.post("/auth/google", json={"credential": "x"}, headers={"X-CSRF-Token": tok})
        assert r.get_json()["ok"] is True
    c = app.test_client()            # signing in starts a new session (and token); start over signed out
    c.get("/login")
    with c.session_transaction() as sess:
        tok = sess["csrf"]
    with mock.patch("billboard_ops.views.auth._verify_google",
                    return_value={"email": "sales@example.com", "email_verified": False}):
        r = c.post("/auth/google", json={"credential": "x"}, headers={"X-CSRF-Token": tok})
        assert r.status_code == 403


def test_dev_sign_in_is_off_when_google_is_set_up(app):
    from billboard_ops.config import Config
    import os
    os.environ["DEV_LOGIN"] = "1"
    os.environ["GOOGLE_CLIENT_ID"] = "cid"
    try:
        assert Config().DEV_LOGIN is False
    finally:
        os.environ.pop("DEV_LOGIN")
        os.environ.pop("GOOGLE_CLIENT_ID")


def test_with_sign_in_off_everyone_is_the_owner(app, s):
    app.config["APP"].LOGIN_REQUIRED = False
    c = app.test_client()
    r = c.get("/bookings")
    assert r.status_code == 200 and b"Sign-in is switched off" in r.data and b"Sign out" not in r.data
    assert c.get("/login").status_code == 302
    owner = s.scalars(select(User).where(User.role == "owner")).one()
    assert c.get("/health").get_json()["sign_in"] == "off"
    c.get("/screens")
    assert s.scalars(select(User).where(User.role == "owner")).one().id == owner.id   # made once, then reused


def test_sign_in_switch(app):
    from billboard_ops.config import Config
    import os
    saved = {k: os.environ.pop(k, None) for k in ("LOGIN_REQUIRED", "GOOGLE_CLIENT_ID")}
    try:
        assert Config().LOGIN_REQUIRED is False                 # off for now
        os.environ["GOOGLE_CLIENT_ID"] = "cid"
        assert Config().LOGIN_REQUIRED is True                  # comes back once Google sign-in is set up
        os.environ["LOGIN_REQUIRED"] = "0"
        assert Config().LOGIN_REQUIRED is False                 # unless told to stay off
        os.environ.pop("GOOGLE_CLIENT_ID")
        os.environ["LOGIN_REQUIRED"] = "1"
        assert Config().LOGIN_REQUIRED is True
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_removed_people_are_signed_out(app, s, people, login):
    c = login(people["sales"])
    people["sales"].active = False
    s.commit()
    assert c.get("/").status_code == 302


def test_clients_see_the_portal_notice(app, s, inventory, login):
    u = User(email="buyer@acme.example", role="client", client_id=inventory["client"].id)
    s.add(u)
    s.commit()
    r = login(u).get("/bookings")
    assert r.headers["Location"].endswith("/portal")


def test_posts_need_the_csrf_token(app, people, login):
    c = login(people["owner"])
    assert c.post("/settings", data={"hold_days": "5"}).status_code == 400


# ── Roles ───────────────────────────────────────────────────────────────────────

def test_roles_are_enforced_on_pages(app, people, inventory, login):
    sales, ops, manager = login(people["sales"]), login(people["ops"]), login(people["manager"])
    assert sales.get("/settings").status_code == 403
    assert sales.get("/imports").status_code == 403
    assert manager.get("/imports").status_code == 200
    assert ops.get("/bookings/new").status_code == 403
    assert sales.get(f"/screens/{inventory['a'].id}/edit").status_code == 403
    assert ops.get(f"/screens/{inventory['a'].id}/edit").status_code == 200
    assert sales.post(f"/specs/{inventory['spec'].id}", data=form(name="x")).status_code == 403


# ── Every page renders ──────────────────────────────────────────────────────────

def test_every_page_renders_with_data(app, s, people, inventory, login, google):
    b = svc.create(s, people["manager"], inventory["client"],
                   [svc.LineIn(inventory["a"].id, future(1), future(5), 18, 22, 10, 30),
                    svc.LineIn(inventory["c"].id, future(1), future(5))], status="confirmed")
    svc.create(s, people["sales"], inventory["client"], [svc.LineIn(inventory["b"].id, future(0), future(2))])
    s.commit()
    c = login(people["owner"])
    a = inventory["a"]
    pages = ["/", "/calendar", "/calendar?view=week", "/calendar?view=quarter&area=SZR",
             f"/calendar/{a.id}/{future(2).isoformat()}", "/availability", "/bookings", "/bookings?status=open",
             "/bookings?q=acme", f"/bookings/{b.code}", f"/bookings/{b.code}/edit", "/bookings/new", "/clients",
             f"/clients/{inventory['client'].id}", "/clients/new", "/screens", "/screens?view=map",
             f"/screens/{a.id}", "/screens/new", "/specs", f"/specs/{inventory['spec'].id}", "/specs/new",
             "/closures", "/imports", "/settings", "/settings/people", "/settings/connections",
             "/settings/sheet-changes", "/activity", "/notices", "/health"]
    for p in pages:
        r = c.get(p)
        assert r.status_code == 200, p
    page = c.get("/").get_data(as_text=True)
    assert "Acme Foods" in page and b.code in page


def test_arabic_flips_the_layout(app, people, login):
    c = login(people["sales"])
    c.post("/language", data=form(next="/"))
    page = c.get("/").get_data(as_text=True)
    assert 'dir="rtl"' in page and "نظرة عامة" in page


def test_health_reports_the_worker(app, s):
    r = app.test_client().get("/health")
    assert r.status_code == 200 and r.get_json()["database"] == "ok"
    assert r.get_json()["worker_seconds_since_heartbeat"] is None


# ── Doing things through the pages ──────────────────────────────────────────────

def test_check_vacancy_then_place_a_hold(app, s, people, inventory, login):
    c = login(people["sales"])
    a = inventory["a"]
    r = c.post("/availability", data=form(screen_id=str(a.id), start_date=future(3).isoformat(),
                                          end_date=future(4).isoformat(), freq_mode="slots", freq_value="2"))
    assert "Fits" in r.get_data(as_text=True)
    r = c.post("/bookings/new", data=booking_form(a, future(3), future(4), inventory["client"].id, freq_value="2"))
    assert r.status_code == 302
    b = s.scalars(select(Booking)).one()
    assert b.status == "hold" and b.lines[0].plays_per_hour == 60 and b.title == "Launch"


def test_new_client_from_the_booking_form(app, s, people, inventory, login):
    c = login(people["sales"])
    c.post("/bookings/new", data=booking_form(inventory["a"], future(3), future(4), "new", new_client="Zeta Air"))
    assert s.scalars(select(Client).where(Client.name == "Zeta Air")).one()


def test_overbooking_through_the_form(app, s, people, inventory, login):
    a = inventory["a"]
    full = svc.LineIn(a.id, future(3), future(4), plays_per_hour=360)
    svc.create(s, people["manager"], inventory["client"], [full], status="confirmed")
    s.commit()
    r = login(people["sales"]).post("/bookings/new", data=booking_form(a, future(3), future(4), inventory["client"].id))
    text = r.get_data(as_text=True)
    assert r.status_code == 400 and "Not enough room" in text and "ask a Manager" in text
    assert 'name="override_reason"' not in text
    mgr = login(people["manager"])
    r = mgr.post("/bookings/new", data=booking_form(a, future(3), future(4), inventory["client"].id))
    assert 'name="override_reason"' in r.get_data(as_text=True)
    r = mgr.post("/bookings/new", data=booking_form(a, future(3), future(4), inventory["client"].id,
                                                   override_reason="Owner said yes"))
    assert r.status_code == 302 and len(s.scalars(select(Booking)).all()) == 2
    # The availability page now offers other dates and the twin screen.
    r = mgr.get(f"/availability?screen_id={a.id}&start_date={future(3).isoformat()}&end_date={future(4).isoformat()}"
                "&freq_mode=slots&freq_value=1")
    text = r.get_data(as_text=True)
    assert "Doesn&#39;t fit" in text or "Doesn't fit" in text
    assert "Nearest dates that fit" in text and "SZR-02" in text


def test_confirm_extend_and_cancel_buttons(app, s, people, inventory, login):
    b = svc.create(s, people["sales"], inventory["client"], [svc.LineIn(inventory["a"].id, future(3), future(4))])
    s.commit()
    sales, mgr = login(people["sales"]), login(people["manager"])
    sales.post(f"/bookings/{b.code}/confirm", data=form())
    s.refresh(b)
    assert b.status == "hold"
    old = b.hold_expires_at
    sales.post(f"/bookings/{b.code}/extend", data=form(days="2"))
    s.refresh(b)
    assert b.hold_expires_at - old == timedelta(days=2)
    mgr.post(f"/bookings/{b.code}/confirm", data=form())
    s.refresh(b)
    assert b.status == "hold"          # no purchase order yet
    mgr.post(f"/bookings/{b.code}/confirm", data=form(no_po_reason="Known client, PO follows"))
    s.refresh(b)
    assert b.status == "confirmed"
    mgr.post(f"/bookings/{b.code}/cancel", data=form(reason="Changed plans"))
    s.refresh(b)
    assert b.status == "cancelled" and b.cancel_reason == "Changed plans"


def test_edit_a_booking(app, s, people, inventory, login):
    b = svc.create(s, people["sales"], inventory["client"], [svc.LineIn(inventory["a"].id, future(3), future(4))])
    s.commit()
    lid = b.lines[0].id
    data = booking_form(inventory["a"], future(3), future(8), inventory["client"].id, line_id=str(lid),
                        freq_mode="pph", freq_value="45", title="Longer")
    r = login(people["sales"]).post(f"/bookings/{b.code}/edit", data=data)
    assert r.status_code == 302
    s.refresh(b)
    assert b.title == "Longer" and b.lines[0].end_date == future(8) and b.lines[0].plays_per_hour == 45


def test_screen_form_validates_and_keeps_what_was_typed(app, s, people, inventory, login):
    c = login(people["ops"])
    r = c.post("/screens/new", data=form(code="SZR-01", name="Dup", loop_seconds="120", slot_seconds="10",
                                         open_hour="6", close_hour="24", sellable_pct="100", timezone="Mars/Base",
                                         active="1"))
    text = r.get_data(as_text=True)
    assert r.status_code == 400 and "already used" in text and "isn&#39;t a time zone" in text and 'value="Dup"' in text
    r = c.post("/screens/new", data=form(code="DXB-9", name="Airport Rd", loop_seconds="60", slot_seconds="15",
                                         open_hour="0", close_hour="24", sellable_pct="90", timezone="Asia/Dubai",
                                         active="1", spec_id=str(inventory["spec"].id)))
    assert r.status_code == 302
    sc = s.scalars(select(Screen).where(Screen.code == "DXB-9")).one()
    assert sc.slots_per_loop == 4 and sc.spec_id == inventory["spec"].id


def test_blackout_through_the_page_warns_about_bookings(app, s, people, inventory, login):
    svc.create(s, people["sales"], inventory["client"], [svc.LineIn(inventory["a"].id, future(3), future(4))])
    s.commit()
    c = login(people["ops"])
    r = c.post("/closures/add", data=form(kind="blackout", screen_id=str(inventory["a"].id),
                                          start_date=future(4).isoformat(), reason="Maintenance"),
               follow_redirects=True)
    assert "may lose airtime" in r.get_data(as_text=True)


def test_import_through_the_pages(app, s, people, inventory, login):
    c = login(people["manager"])
    csv = ("Client,Screen,Start,End,Status\n"
           f"Acme Foods,SZR-02,{future(20).isoformat()},{future(22).isoformat()},confirmed\n"
           f"Acme Foods,NOPE,{future(20).isoformat()},{future(22).isoformat()},confirmed\n")
    r = c.post("/imports/new", data={**form(kind="bookings"), "file": (io.BytesIO(csv.encode()), "old.csv")},
               content_type="multipart/form-data")
    run = s.scalars(select(ImportRun)).one()
    assert r.headers["Location"].endswith(f"/imports/{run.id}/map")
    page = c.get(f"/imports/{run.id}/map").get_data(as_text=True)
    assert "Start date" in page
    c.post(f"/imports/{run.id}/map", data=form(map_client="0", map_screen="1", map_start_date="2", map_end_date="3",
                                               map_status="4"))
    page = c.get(f"/imports/{run.id}").get_data(as_text=True)
    assert "No screen with code or name" in page
    r = c.post(f"/imports/{run.id}/commit", data=form(), follow_redirects=True)
    assert "can&#39;t be read" in r.get_data(as_text=True)
    r = c.post(f"/imports/{run.id}/commit", data=form(skip_errors="1"))
    assert r.headers["Location"].endswith(f"/imports/{run.id}/calendar")
    assert len(s.scalars(select(Booking)).all()) == 1


def test_people_page(app, s, people, login):
    c = login(people["owner"])
    c.post("/settings/people", data=form(email="New@Example.com", name="New", role="ops"))
    u = s.scalars(select(User).where(User.email == "new@example.com")).one()
    assert u.role == "ops"
    r = c.post("/settings/people", data=form(user_id=str(people["owner"].id), role="sales", active="1"),
               follow_redirects=True)
    assert "can&#39;t remove your own owner access" in r.get_data(as_text=True)


def test_settings_page(app, s, people, login):
    from billboard_ops import settings as st
    c = login(people["owner"])
    c.post("/settings", data=form(hold_days="5", hold_reminder_hours="12", date_order="mdy",
                                  mirror_spreadsheet_id="https://docs.google.com/spreadsheets/d/ABC123/edit#gid=0",
                                  mirror_tab="Mirror"))
    s.expire_all()
    assert st.get(s, "hold_days") == 5 and st.get(s, "date_order") == "mdy"
    assert st.get(s, "mirror_spreadsheet_id") == "ABC123"
