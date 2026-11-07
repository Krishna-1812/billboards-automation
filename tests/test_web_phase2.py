"""Phase 2 pages: the inbox, a proposal, the Outbox, the accuracy test, costs and settings."""

import io

from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops import intake, settings
from billboard_ops.models import Booking, BookingDocument, EvalCase, Message, Proposal

from conftest import ai_answer, ai_line, future


def form(**kw):
    return {"csrf_token": "tok", **kw}


def proposal_form(p, screen, client_id, **kw):
    ln = p.draft["lines"][0] if p.draft and p.draft["lines"] else {}
    data = form(client_id=str(client_id), title="Spring", notes="", line_id="", screen_id=str(screen.id),
                start_date=ln.get("start_date", future(10).isoformat()), end_date=ln.get("end_date", future(16).isoformat()),
                start_hour="", end_hour="", ad_seconds="10", freq_mode="slots", freq_value="2")
    data.update(kw)
    return data


def read(s, body="Two slots on SZR-01 please", **kw):
    p, _, _ = intake.receive(s, channel="email", sender="ads@acme.example", sender_name="Sara", subject="Booking",
                             body=body, **kw)
    intake.process(s, p)
    s.commit()
    return p


def test_pages_render(app, s, people, inventory, fake_ai, login):
    p = read(s)
    fake_ai.default = ai_answer(lines=[ai_line(codes=(), hint="opposite the mall", unit="unknown", value=None,
                                               conf="low")], missing=["screens", "frequency"],
                                questions=["Which screen?"])
    p2 = read(s, body="something by the mall", external_id="<x2>")
    s.add(EvalCase(name="c1", text="t", expected={"client": "Acme Foods", "lines": [
        {"screen": "SZR-01", "start": "2026-12-01", "end": "2026-12-02"}]}))
    s.commit()
    c = login(people["owner"])
    draft = s.scalars(select(Message).where(Message.status == "draft")).first()
    for url in ["/inbox", "/inbox?tab=all", "/inbox?tab=held", "/inbox/new", f"/inbox/{p.ref}", f"/inbox/{p2.ref}",
                "/outbox", f"/outbox/{draft.id}", "/inbox/accuracy", "/inbox/accuracy/new",
                f"/inbox/accuracy/{s.scalars(select(EvalCase)).first().id}", "/settings/costs", "/settings",
                "/settings/connections", "/"]:
        r = c.get(url)
        assert r.status_code == 200, url
    page = c.get(f"/inbox/{p2.ref}").get_data(as_text=True)
    assert "opposite the mall" in page and "Which screen?" in page
    assert "Proposals" in c.get("/").get_data(as_text=True)


def test_paste_a_request(app, s, people, inventory, login):
    c = login(people["sales"])
    r = c.post("/inbox/new", data={**form(body="Need DT screens next week", sender_name="Walk-in",
                                          sender="walkin@example.com"),
                                    "files": (io.BytesIO(b"hello"), "notes.txt")},
               content_type="multipart/form-data")
    p = s.scalars(select(Proposal)).one()
    assert r.headers["Location"].endswith(f"/inbox/{p.ref}") and p.channel == "manual"
    assert p.messages[0].attachments[0].filename == "notes.txt"
    assert c.post("/inbox/new", data=form()).status_code == 400
    assert login(people["ops"]).post("/inbox/new", data=form(body="x")).status_code == 403


def test_hold_from_the_proposal_page_then_approve_the_confirmation(app, s, people, inventory, fake_ai, login):
    p = read(s)
    c = login(people["sales"])
    r = c.post(f"/inbox/{p.ref}/hold", data=proposal_form(p, inventory["a"], inventory["client"].id))
    assert r.status_code == 302
    s.expire_all()
    p = s.get(Proposal, p.id)
    b = s.get(Booking, p.booking_id)
    assert p.status == "held" and b.status == "hold" and b.lines[0].plays_per_hour == 60
    conf = s.scalars(select(Message).where(Message.kind == "hold_confirmation")).one()
    page = c.get(f"/outbox/{conf.id}").get_data(as_text=True)
    assert "Approve and send" in page and "SMTP" in page                    # warns that email isn't set up
    c.post(f"/outbox/{conf.id}", data=form(action="approve", channel="manual", recipient="", subject=conf.subject,
                                           body=conf.body + "\nThanks!"))
    s.expire_all()
    conf = s.get(Message, conf.id)
    assert conf.status == "approved" and conf.body.endswith("Thanks!") and conf.approved_by == "sales@example.com"
    c.post(f"/outbox/{conf.id}", data=form(action="sent"))
    s.expire_all()
    assert s.get(Message, conf.id).status == "sent"
    # Confirm with the signed PO attached.
    mgr = login(people["manager"])
    mgr.post(f"/bookings/{b.code}/confirm", data={**form(), "document": (io.BytesIO(b"%PDF signed"), "po.pdf")},
             content_type="multipart/form-data")
    s.expire_all()
    doc = s.scalars(select(BookingDocument)).one()
    assert s.get(Booking, b.id).status == "confirmed" and doc.filename == "po.pdf"
    page = mgr.get(f"/bookings/{b.code}").get_data(as_text=True)
    assert "po.pdf" in page and p.ref in page
    assert mgr.get(f"/bookings/{b.code}/documents/{doc.id}").data == b"%PDF signed"


def test_a_line_without_a_screen_is_never_dropped_silently(app, s, people, inventory, fake_ai, login):
    fake_ai.default = ai_answer(lines=[ai_line(), ai_line(codes=(), hint="near the airport", unit="plays_per_hour",
                                                          value=40, conf="low")], missing=["screens"])
    p = read(s)
    c = login(people["sales"])
    page = c.get(f"/inbox/{p.ref}").get_data(as_text=True)
    assert "near the airport" in page and 'name="freq_value" value="40"' in page
    data = proposal_form(p, inventory["a"], inventory["client"].id)
    data = list(data.items()) + [("line_id", ""), ("screen_id", ""), ("start_date", future(10).isoformat()),
                                 ("end_date", future(16).isoformat()), ("start_hour", ""), ("end_hour", ""),
                                 ("ad_seconds", ""), ("freq_mode", "pph"), ("freq_value", "40")]
    from werkzeug.datastructures import MultiDict
    r = c.post(f"/inbox/{p.ref}/hold", data=MultiDict(data))
    assert r.status_code == 400 and "Choose a screen for every line" in r.get_data(as_text=True)
    assert s.scalars(select(Booking)).first() is None


def test_edits_are_saved_and_rechecked(app, s, people, inventory, fake_ai, login):
    p = read(s)
    c = login(people["sales"])
    c.post(f"/inbox/{p.ref}/save", data=proposal_form(p, inventory["b"], inventory["client"].id, freq_value="3"))
    s.expire_all()
    p = s.get(Proposal, p.id)
    assert p.draft["lines"][0]["screen_code"] == "SZR-02" and p.draft["lines"][0]["confidence"]["screen"] == "checked"
    assert p.checks[0]["screen_code"] == "SZR-02" and p.status == "needs_review"


def test_overbooking_from_a_proposal(app, s, people, inventory, fake_ai, login):
    svc.create(s, people["manager"], inventory["client"],
               [svc.LineIn(inventory["a"].id, future(10), future(16), plays_per_hour=360)], status="confirmed")
    s.commit()
    p = read(s)
    r = login(people["sales"]).post(f"/inbox/{p.ref}/hold", data=proposal_form(p, inventory["a"], inventory["client"].id))
    assert r.status_code == 400 and "Not enough room" in r.get_data(as_text=True)
    r = login(people["sales"]).post(f"/inbox/{p.ref}/decline", data=form())
    s.expire_all()
    m = s.scalars(select(Message).where(Message.kind == "decline")).one()
    assert r.headers["Location"].endswith(f"/outbox/{m.id}") and s.get(Proposal, p.id).status == "declined"


def test_ask_dismiss_reopen_and_read_again(app, s, people, inventory, fake_ai, login):
    p = read(s)
    c = login(people["sales"])
    r = c.post(f"/inbox/{p.ref}/ask", data=form())
    m = s.scalars(select(Message).where(Message.kind == "clarify")).one()
    assert r.headers["Location"].endswith(f"/outbox/{m.id}")
    c.post(f"/inbox/{p.ref}/dismiss", data=form())
    s.expire_all()
    assert s.get(Proposal, p.id).status == "not_proposal"
    c.post(f"/inbox/{p.ref}/reopen", data=form())
    c.post(f"/inbox/{p.ref}/read", data=form())
    s.expire_all()
    assert s.get(Proposal, p.id).status == "new"


def test_roles_on_phase2_pages(app, s, people, inventory, fake_ai, login):
    p = read(s)
    ops, sales = login(people["ops"]), login(people["sales"])
    assert ops.get(f"/inbox/{p.ref}").status_code == 200
    assert ops.post(f"/inbox/{p.ref}/hold", data=form()).status_code == 403
    assert sales.get("/inbox/accuracy").status_code == 403
    assert sales.get("/settings/costs").status_code == 403
    m = s.scalars(select(Message)).first()
    draft = s.scalars(select(Message).where(Message.direction == "out")).first() or m
    assert ops.post(f"/outbox/{draft.id}", data=form(action="discard")).status_code in (403, 404)


def test_save_a_case_and_run_the_test(app, s, people, inventory, fake_ai, login):
    p = read(s)
    intake.place_hold(s, people["sales"], p, client=inventory["client"], lines=intake.lines_from_draft(p.draft["lines"]))
    s.commit()
    mgr = login(people["manager"])
    mgr.post(f"/inbox/{p.ref}/case", data=form())
    assert s.scalars(select(EvalCase)).one().source_proposal_id == p.id
    r = mgr.post("/inbox/accuracy/run", data=form())
    assert "/inbox/accuracy/runs/" in r.headers["Location"]
    assert "Running" in mgr.get(r.headers["Location"]).get_data(as_text=True)
    r = mgr.post("/inbox/accuracy/new", data=form(name="by hand", text="SZR-02 1-3 Dec", e_client="Acme Foods",
                                                  e_screen="szr-02", e_start="2026-12-01", e_end="2026-12-03",
                                                  e_start_hour="", e_end_hour="", e_pph="", e_whole="0"))
    assert r.status_code == 302
    assert s.scalars(select(EvalCase).where(EvalCase.name == "by hand")).one().expected["lines"][0]["screen"] == "SZR-02"
    assert mgr.post("/inbox/accuracy/new", data=form(name="bad", e_screen="X", e_start="soon", e_end="",
                                                     e_start_hour="", e_end_hour="", e_pph="",
                                                     e_whole="0")).status_code == 400


def test_auto_hold_setting(app, s, people, login):
    c = login(people["owner"])
    c.post("/settings", data=form(hold_days="3", hold_reminder_hours="24", auto_hold="1", remind_clients="1"))
    s.expire_all()
    assert settings.get(s, "auto_hold") is True
    page = c.get("/settings").get_data(as_text=True)
    assert "hasn&#39;t been run yet" in page and "a person places them" in page
