"""Proposals, negotiation and the purchase order: the rate card, options drafted from a request, sending versions,
the client's answer in the portal, the hold made on acceptance, and confirming only with a PO."""

from datetime import date

import pytest
from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops import pricing, quotes, settings
from billboard_ops.models import (Activity, Attachment, Booking, Client, Message, Notification, Proposal, Quote,
                                  QuoteVersion, Screen, User)

from conftest import future


def form(**kw):
    return {"csrf_token": "tok", **kw}


@pytest.fixture
def priced(s, inventory):
    """The test screens on the rate card: 10,000 a slot a week (the Marina screen has no rate)."""
    inventory["a"].rate_week = 10000
    inventory["b"].rate_week = 8000
    s.commit()
    return inventory


def request_for(s, inv, lines, budget=None):
    p = Proposal(ref="PR-00001", channel="email", sender="ads@acme.example", sender_name="Sara",
                 subject="Ramadan campaign", client=inv["client"], status="needs_review",
                 draft={"campaign": "Ramadan", "lines": lines,
                        "budget": {"amount": budget, "currency": "AED" if budget else ""}})
    s.add(p)
    s.commit()
    return p


def line(screen, start, end, **kw):
    return {"screen_id": screen.id, "start_date": start.isoformat(), "end_date": end.isoformat(), **kw}


# ── The rate card ───────────────────────────────────────────────────────────────

def test_the_rate_card_formula(priced):
    a = priced["a"]                       # 120 s loop of 10 s slots: one slot plays 30 times an hour, 06–24
    d = date(2026, 12, 1)
    one_week = lambda **kw: pricing.list_price(a, d, d.replace(day=7), **kw)  # noqa: E731
    assert one_week(ad_seconds=10, plays_per_hour=30) == 10000                    # one slot, one week
    assert one_week(ad_seconds=10, plays_per_hour=60) == 20000                    # two slots
    assert one_week(ad_seconds=15, plays_per_hour=30) == 15000                    # a longer ad takes more airtime
    assert pricing.list_price(a, d, d.replace(day=14), ad_seconds=10, plays_per_hour=30) == 20000   # two weeks
    assert one_week(start_hour=18, end_hour=24, ad_seconds=10, plays_per_hour=30) == round(10000 * 6 / 18)
    assert one_week(exclusive=True) == 120000                                     # the whole screen: 12 slots
    assert pricing.list_price(priced["c"], d, d, ad_seconds=10, plays_per_hour=60) is None   # no rate yet
    assert pricing.plays(a, d, d.replace(day=7), plays_per_hour=30) == 30 * 18 * 7


# ── Drafting from a request ─────────────────────────────────────────────────────

def test_a_proposal_drafted_from_a_request(s, people, priced):
    a, b = priced["a"], priced["b"]
    # SZR-01 is taken in those days, so option B offers the free screen next to it.
    svc.create(s, people["manager"], priced["client"], [svc.LineIn(a.id, future(10), future(16), exclusive=True)],
               status="confirmed")
    p = request_for(s, priced, [line(a, future(10), future(16), ad_seconds=10, plays_per_hour=30)], budget=6000)
    q = quotes.draft_from_request(s, people["sales"], p)
    s.commit()
    assert q.client == priced["client"] and q.request == p and q.title == "Ramadan" and q.budget == 6000
    assert [o.name.split(":")[0] for o in q.options] == ["Option A", "Option B", "Option C"]
    assert q.options[1].lines[0].screen_id == b.id                                  # free screen nearby
    a_net = quotes.totals(q.options[0])["net"]
    assert a_net == 10000
    c = q.options[2]                                                                # within the budget
    assert quotes.totals(c)["net"] <= 6000 and c.lines[0].plays_per_hour < 30
    assert q.status == "draft" and q.version == 0 and q.ref.startswith("QT-")


def test_totals_discount_agreed_price_and_bonus(s, people, priced):
    q = quotes.create(s, people["sales"], priced["client"], "Launch")
    o = quotes.add_option(s, q, "A", [
        {"screen_id": priced["a"].id, "start_date": date(2026, 12, 1), "end_date": date(2026, 12, 7),
         "ad_seconds": 10, "plays_per_hour": 30},
        {"screen_id": priced["b"].id, "start_date": date(2026, 12, 1), "end_date": date(2026, 12, 7),
         "ad_seconds": 10, "plays_per_hour": 30, "bonus": True}])
    s.flush()
    s.refresh(o)
    t = quotes.totals(o)
    assert (t["gross"], t["discount"], t["net"], t["bonus_value"]) == (10000, 0, 10000, 8000)
    o.discount_pct = 15
    assert quotes.totals(o)["net"] == 8500 and quotes.totals(o)["discount"] == 1500
    o.agreed_total = 9000                                                           # a negotiated figure wins
    t = quotes.totals(o)
    assert t["net"] == 9000 and t["discount"] == 1000 and round(t["discount_pct"]) == 10
    o.lines[0].price = 12000                                                        # an edited line price
    o.agreed_total, o.discount_pct = None, 0
    assert quotes.totals(o)["net"] == 12000


# ── Sending and negotiating ─────────────────────────────────────────────────────

def _sent_quote(s, people, priced, discount=0):
    p = request_for(s, priced, [line(priced["a"], future(10), future(16), ad_seconds=10, plays_per_hour=30)])
    q = quotes.draft_from_request(s, people["sales"], p)
    q.options[0].discount_pct = discount
    quotes.send(s, people["manager"], q)
    s.commit()
    return q


def test_sending_makes_a_version_a_pdf_and_a_draft(s, people, priced):
    q = _sent_quote(s, people, priced)
    assert q.status == "sent" and q.version == 1 and q.request.status == "proposal_sent"
    v = s.scalars(select(QuoteVersion)).one()
    assert v.snapshot["options"][0]["totals"]["net"] == 10000
    m = s.scalars(select(Message).where(Message.kind == "proposal")).one()
    assert m.status == "draft" and m.recipient == "ads@acme.example" and m.proposal_id == q.request_id
    assert "Option A" in m.body and "AED 10,000" in m.body and "/portal/quotes/" + q.ref in m.body
    att = s.scalars(select(Attachment).where(Attachment.message_id == m.id)).one()
    assert att.filename == f"{q.ref}-v1.pdf" and att.data.startswith(b"%PDF")


def test_a_screen_with_no_rate_needs_a_price_before_sending(s, people, priced):
    p = request_for(s, priced, [line(priced["c"], future(10), future(12), ad_seconds=10, plays_per_hour=60)])
    q = quotes.draft_from_request(s, people["sales"], p)
    with pytest.raises(quotes.QuoteError, match="set a price for MAR-01"):
        quotes.send(s, people["sales"], q)
    q.options[0].lines[0].price = 5000
    quotes.send(s, people["sales"], q)
    assert q.version == 1


def test_a_big_discount_needs_a_manager(s, people, priced):
    settings.put(s, "max_discount_pct", 10)
    p = request_for(s, priced, [line(priced["a"], future(10), future(16), ad_seconds=10, plays_per_hour=30)])
    q = quotes.draft_from_request(s, people["sales"], p)
    q.options[0].discount_pct = 20
    with pytest.raises(quotes.QuoteError, match="Above 10% a Manager sends it"):
        quotes.send(s, people["sales"], q)
    quotes.send(s, people["manager"], q)
    assert q.status == "sent"


def test_negotiation_keeps_every_version(s, people, priced):
    q = _sent_quote(s, people, priced)
    quotes.ask_changes(s, "ads@acme.example", q, "Can you do 10% off?")
    assert q.status == "changes_requested" and q.client_note == "Can you do 10% off?"
    assert s.scalars(select(Notification).where(Notification.link == f"/quotes/{q.ref}")).first() is not None
    q.options[0].discount_pct = 10
    quotes.send(s, people["sales"], q, note="10% off as asked")
    s.commit()
    assert q.version == 2 and q.status == "sent"
    versions = s.scalars(select(QuoteVersion).order_by(QuoteVersion.version)).all()
    assert [v.snapshot["options"][0]["totals"]["net"] for v in versions] == [10000, 9000]
    assert versions[1].note == "10% off as asked"
    second = s.scalars(select(Message).where(Message.kind == "proposal").order_by(Message.id.desc())).first()
    assert second.subject.startswith("Our revised proposal")


def test_proposals_past_their_date_expire(s, people, priced):
    q = _sent_quote(s, people, priced)
    assert quotes.expire(s, today=q.valid_until) == 0
    assert quotes.expire(s, today=date.fromordinal(q.valid_until.toordinal() + 1)) == 1
    assert q.status == "expired"


# ── Accepting: the hold, with the agreed price ──────────────────────────────────

def test_accepting_places_the_hold_with_the_agreed_price(s, people, priced):
    q = _sent_quote(s, people, priced, discount=10)
    other = quotes.draft_from_request(s, people["sales"], q.request)               # a second, still open
    b = quotes.accept(s, people["sales"], q, q.options[0])
    s.commit()
    assert b.status == "hold" and b.agreed_amount == 9000 and b.currency == "AED" and b.source == "proposal"
    assert q.status == "accepted" and q.booking_id == b.id and q.request.booking_id == b.id
    assert q.request.status == "held" and other.status == "withdrawn"
    assert [l.screen_id for l in b.lines] == [priced["a"].id]


def test_accepting_updates_the_requests_hold(s, people, priced):
    q = _sent_quote(s, people, priced)
    held = svc.create(s, people["sales"], priced["client"], [svc.LineIn(priced["b"].id, future(10), future(12))])
    q.request.booking_id = held.id
    s.commit()
    b = quotes.accept(s, people["sales"], q, q.options[0])
    assert b.id == held.id and [l.screen_id for l in b.lines] == [priced["a"].id]


# ── The purchase order ──────────────────────────────────────────────────────────

def test_confirming_needs_the_po(s, people, priced):
    b = svc.create(s, people["sales"], priced["client"], [svc.LineIn(priced["a"].id, future(3), future(4))])
    b.agreed_amount, b.currency = 9000, "AED"
    with pytest.raises(svc.BookingError, match="A purchase order is needed"):
        svc.confirm(s, people["manager"], b)
    svc.record_po(s, "sales@example.com", b, "PO-77", 8500)
    assert svc.po_mismatch(b) == "The PO says AED 8,500 but the agreed price is AED 9,000."
    svc.confirm(s, people["manager"], b)
    assert b.status == "confirmed" and b.po_number == "PO-77"


def test_a_manager_can_confirm_without_a_po_with_a_reason(s, people, priced):
    b = svc.create(s, people["sales"], priced["client"], [svc.LineIn(priced["a"].id, future(3), future(4))])
    svc.confirm(s, people["manager"], b, no_po_reason="Long-standing client; PO next week")
    assert b.status == "confirmed" and b.no_po_reason.startswith("Long-standing")
    assert "without a PO" in s.scalars(select(Activity).where(Activity.action == "confirmed")).one().summary
    assert s.scalars(select(Notification).where(Notification.kind == "warning")).first() is not None


def test_the_po_rule_can_be_switched_off(s, people, priced):
    settings.put(s, "po_required", False)
    b = svc.create(s, people["sales"], priced["client"], [svc.LineIn(priced["a"].id, future(3), future(4))])
    svc.confirm(s, people["manager"], b)
    assert b.status == "confirmed"


def test_the_team_adds_the_po_on_the_booking_page(app, s, people, priced, login):
    b = svc.create(s, people["sales"], priced["client"], [svc.LineIn(priced["a"].id, future(3), future(4))])
    s.commit()
    page = login(people["manager"]).get(f"/bookings/{b.code}").get_data(as_text=True)
    assert "Get the client's purchase order" in page and "PO received" in page
    import io
    sales = login(people["sales"])
    sales.post(f"/bookings/{b.code}/po", data=form(po_number="PO-9", po_amount="1,000",
                                                     document=(io.BytesIO(b"%PDF-1.4"), "po.pdf")),
               content_type="multipart/form-data")
    s.expire_all()
    b = s.get(Booking, b.id)
    assert b.po_number == "PO-9" and b.po_amount == 1000 and b.status == "hold"     # Sales can't confirm
    login(people["manager"]).post(f"/bookings/{b.code}/confirm", data=form())
    s.expire_all()
    assert s.get(Booking, b.id).status == "confirmed"


def test_saving_straight_as_confirmed_follows_the_po_rule(app, s, people, priced, login):
    mgr = login(people["manager"])
    data = form(client_id=str(priced["client"].id), title="Direct", screen_id=str(priced["a"].id), line_id="",
                start_date=future(5).isoformat(), end_date=future(6).isoformat(), start_hour="", end_hour="",
                ad_seconds="10", freq_mode="slots", freq_value="1", status="confirmed")
    r = mgr.post("/bookings/new", data=data)
    assert r.status_code == 400 and s.scalars(select(Booking)).first() is None
    mgr.post("/bookings/new", data={**data, "po_number": "PO-5"})
    s.expire_all()
    b = s.scalars(select(Booking)).one()
    assert b.status == "confirmed" and b.po_number == "PO-5"


# ── The team's pages ────────────────────────────────────────────────────────────

def test_the_team_drafts_edits_and_sends_from_the_pages(app, s, people, priced, login):
    p = request_for(s, priced, [line(priced["a"], future(10), future(16), ad_seconds=10, plays_per_hour=30)])
    sales = login(people["sales"])
    r = sales.post(f"/inbox/{p.ref}/quote", data=form(
        client_id=str(priced["client"].id), title="Ramadan", screen_id=str(priced["a"].id), line_id="",
        start_date=future(10).isoformat(), end_date=future(16).isoformat(), start_hour="", end_hour="",
        ad_seconds="10", freq_mode="slots", freq_value="1"))
    q = s.scalars(select(Quote)).one()
    assert r.headers["Location"].endswith(f"/quotes/{q.ref}")
    page = sales.get(f"/quotes/{q.ref}").get_data(as_text=True)
    assert "Option A: as asked" in page and "AED 10,000" in page and "Fits" in page
    o = q.options[0]
    sales.post(f"/quotes/{q.ref}/options/{o.id}", data=form(
        name="Option A", discount_pct="5", agreed_total="", value_adds="Extra week on SZR-02",
        screen_id=str(priced["a"].id), start_date=future(10).isoformat(), end_date=future(16).isoformat(),
        start_hour="", end_hour="", ad_seconds="10", freq_mode="slots", freq_value="2", price="", bonus="0"))
    s.expire_all()
    o = s.get(type(o), o.id)
    assert o.discount_pct == 5 and o.lines[0].plays_per_hour == 60 and quotes.totals(o)["net"] == 19000
    r = sales.post(f"/quotes/{q.ref}/send", data=form())
    assert "/outbox/" in r.headers["Location"]
    assert sales.get("/quotes").status_code == 200
    assert b"%PDF" in sales.get(f"/quotes/{q.ref}/pdf?v=1").data[:5]


# ── The client's side, in the portal ────────────────────────────────────────────

def _client_login(app, s, client):
    u = User(email="sara@acme.example", name="Sara", role="client", client_id=client.id)
    s.add(u)
    s.commit()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = u.id
        sess["csrf"] = "tok"
    return c


def test_the_client_accepts_an_option_and_sends_the_po(app, s, people, priced):
    q = _sent_quote(s, people, priced)
    c = _client_login(app, s, priced["client"])
    home = c.get("/portal").get_data(as_text=True)
    assert q.ref in home and "Waiting for your answer" in home
    page = c.get(f"/portal/quotes/{q.ref}").get_data(as_text=True)
    assert "Accept this option" in page and "AED 10,000" in page
    assert c.get(f"/portal/quotes/{q.ref}/pdf").data.startswith(b"%PDF")
    r = c.post(f"/portal/quotes/{q.ref}/accept", data=form(option_id=str(q.options[0].id)))
    s.expire_all()
    q = s.get(Quote, q.id)
    b = q.booking
    assert q.status == "accepted" and b.status == "hold" and b.client_accepted_at is not None
    assert r.headers["Location"].endswith(f"/portal/bookings/{b.code}#po")
    assert "Agreed price: AED 10,000" in c.get(f"/portal/bookings/{b.code}").get_data(as_text=True)


def test_the_client_asks_for_changes_or_declines(app, s, people, priced):
    q = _sent_quote(s, people, priced)
    c = _client_login(app, s, priced["client"])
    c.post(f"/portal/quotes/{q.ref}/changes", data=form(text="Can we add the Marina screen?"))
    s.expire_all()
    q = s.get(Quote, q.id)
    assert q.status == "changes_requested" and "Marina" in q.client_note
    c.post(f"/portal/quotes/{q.ref}/decline", data=form(text="Budget moved"))
    s.expire_all()
    assert s.get(Quote, q.id).status == "declined"


def test_an_option_changed_since_sending_cant_be_accepted(app, s, people, priced):
    q = _sent_quote(s, people, priced)
    q.options[0].discount_pct = 50                                                  # edited, not sent yet
    s.commit()
    c = _client_login(app, s, priced["client"])
    page = c.get(f"/portal/quotes/{q.ref}").get_data(as_text=True)
    assert "AED 10,000" in page                                                     # they see what was sent
    c.post(f"/portal/quotes/{q.ref}/accept", data=form(option_id=str(q.options[0].id)))
    s.expire_all()
    assert s.get(Quote, q.id).status == "sent"


def test_clients_only_see_their_own_proposals(app, s, people, priced):
    q = _sent_quote(s, people, priced)
    globex = Client(name="Globex")
    s.add(globex)
    s.commit()
    draft = quotes.create(s, people["sales"], priced["client"], "Not sent")
    s.commit()
    c = _client_login(app, s, globex)
    assert c.get(f"/portal/quotes/{q.ref}").status_code == 404
    s.add(User(email="omar@acme.example", role="client", client_id=priced["client"].id))
    s.commit()
    omar = s.scalars(select(User).where(User.email == "omar@acme.example")).one()
    c2 = app.test_client()
    with c2.session_transaction() as sess:
        sess["uid"] = omar.id
        sess["csrf"] = "tok"
    assert c2.get(f"/portal/quotes/{draft.ref}").status_code == 404                 # never sent: not shown
    assert c2.get(f"/portal/quotes/{q.ref}").status_code == 200


def test_screen_rate_is_saved_from_the_form(app, s, people, inventory, login):
    a = inventory["a"]
    mgr = login(people["owner"])
    page = mgr.get(f"/screens/{a.id}/edit").get_data(as_text=True)
    assert "Price of one slot for one week (AED)" in page
    s.expire_all()
    assert s.get(Screen, a.id).rate_week is None

