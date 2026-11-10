"""The public pages: the front page about the company, becoming a client, and the way in."""

from sqlalchemy import select

from billboard_ops import settings
from billboard_ops.models import Message, Proposal, Screen, User


def _tok(c, path="/welcome"):
    c.get(path)
    with c.session_transaction() as sess:
        return sess["csrf"]


def test_a_visitor_sees_the_front_page_not_a_sign_in_form(app):
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/welcome")
    page = c.get("/welcome").get_data(as_text=True)
    assert "Seen by Dubai" in page and "seconds</em> at a time" in page
    assert "Plan a campaign" in page and "Become a client" in page and "/signup" in page and "/login" in page
    # Other team pages still go to the sign-in page, and remember where the visitor was going.
    r = c.get("/bookings")
    assert r.headers["Location"].endswith("/login")
    assert 'id="t-team"' in c.get("/login").get_data(as_text=True)


def test_the_front_page_lists_screens_but_never_prices(app, s, inventory):
    s.get(Screen, inventory["a"].id).rate_week = 12345
    s.get(Screen, inventory["a"].id).physical_size = "12m x 4m"
    s.commit()
    page = app.test_client().get("/welcome").get_data(as_text=True)
    assert "SZR-01" in page and "Sheikh Zayed Rd North" in page and "12m x 4m" in page
    assert "12345" not in page and "12,345" not in page
    assert 'data-count="3"' in page                              # three screens
    settings.put(s, "public_show_screens", False)
    s.commit()
    page = app.test_client().get("/welcome").get_data(as_text=True)
    assert "SZR-01" not in page and 'id="network"' not in page


def test_the_front_page_reads_the_company_settings(app, s):
    settings.put(s, "company_name", "Skyline Media")
    settings.put(s, "public_about", "We light up Al Wasl.")
    settings.put(s, "public_email", "hello@skyline.example")
    s.commit()
    page = app.test_client().get("/welcome").get_data(as_text=True)
    assert "Skyline Media" in page and "We light up Al Wasl." in page and "mailto:hello@skyline.example" in page


def test_the_front_page_in_arabic(app):
    c = app.test_client()
    page = c.get("/welcome?lang=ar").get_data(as_text=True)
    assert 'dir="rtl"' in page and "كن عميلاً" in page
    assert 'dir="rtl"' in c.get("/signup").get_data(as_text=True)          # the choice is remembered


def test_becoming_a_client_lands_in_requests(app, s, people):
    c = app.test_client()
    tok = _tok(c, "/signup")
    r = c.post("/signup", data={"csrf_token": tok, "name": "Layla Haddad", "company": "Oasis Juice",
                                "email": "Layla@Oasis.example", "phone": "+971 50 000 0000", "kind": "agency",
                                "start": "2027-01-10", "end": "2027-01-31", "where": "Downtown",
                                "budget": "25,000 – 100,000", "notes": "Launch of a new flavour"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/signup/thanks")
    p = s.scalars(select(Proposal)).one()
    assert p.channel == "web" and p.sender == "layla@oasis.example" and p.sender_name == "Layla Haddad"
    body = s.scalars(select(Message).where(Message.proposal_id == p.id)).one().body
    assert "Oasis Juice" in body and "an agency" in body and "2027-01-10 to 2027-01-31" in body and "Downtown" in body
    page = c.get("/signup/thanks").get_data(as_text=True)
    assert p.ref in page and "Layla Haddad" in page
    from billboard_ops import intake
    assert intake._recipient(s, p) == ("email", "layla@oasis.example")     # the reply goes back by email
    # The team sees where it came from.
    team = app.test_client()
    with team.session_transaction() as sess:
        sess["uid"] = people["owner"].id
    assert "Website sign-up" in team.get("/inbox").get_data(as_text=True)


def test_becoming_a_client_checks_the_form(app, s):
    c = app.test_client()
    tok = _tok(c, "/signup")
    r = c.post("/signup", data={"csrf_token": tok, "name": "Layla", "company": "", "email": "layla@oasis.example"})
    assert r.status_code == 400 and "Add your name, company" in r.get_data(as_text=True)
    r = c.post("/signup", data={"csrf_token": tok, "name": "Layla", "company": "Oasis", "email": "not-an-email"})
    assert r.status_code == 400
    r = c.post("/signup", data={"csrf_token": tok, "name": "Layla", "company": "Oasis", "email": "l@o.example",
                                "start": "2027-02-10", "end": "2027-02-01"})
    assert r.status_code == 400 and "before the start" in r.get_data(as_text=True)
    assert 'value="Oasis"' in r.get_data(as_text=True)                       # what they typed is kept
    assert s.scalars(select(Proposal)).first() is None


def test_bots_are_thanked_and_forgotten(app, s):
    c = app.test_client()
    tok = _tok(c, "/signup")
    r = c.post("/signup", data={"csrf_token": tok, "name": "Bot", "company": "Spam", "email": "bot@spam.example",
                                "website": "http://spam.example"})
    assert r.status_code == 302
    assert s.scalars(select(Proposal)).first() is None


def test_sign_ups_are_slowed_down(app, s):
    c = app.test_client()
    tok = _tok(c, "/signup")
    codes = [c.post("/signup", data={"csrf_token": tok, "name": "A", "company": f"C{i}", "email": f"a{i}@b.example"})
             .status_code for i in range(7)]
    assert codes[:6] == [302] * 6 and codes[6] == 429


def test_the_sign_in_page_has_both_doors(app, s):
    c = app.test_client()
    page = c.get("/login").get_data(as_text=True)
    assert 'action="/portal/login"' in page and "/auth/dev" in page
    assert 'id="t-client" aria-controls="d-client" aria-selected="true"' in page
    page = c.get("/login?door=team").get_data(as_text=True)
    assert 'id="t-team" aria-controls="d-team" aria-selected="true"' in page
    # A client asks for a link from here, exactly as from the portal's own page.
    s.add(User(email="sara@acme.example", role="client", client_id=None, active=True))
    s.commit()
    r = c.post("/portal/login", data={"csrf_token": _tok(c, "/login"), "email": "nobody@acme.example"})
    assert r.status_code == 200


def test_signed_in_people_are_offered_their_way_back(app, s, people):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = people["sales"].id
    page = c.get("/welcome").get_data(as_text=True)
    assert "Open the app" in page and "Become a client" not in page
    assert c.get("/login").status_code == 302


def test_signing_out_goes_to_the_front_page(app, s, people):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = people["owner"].id
        sess["csrf"] = "tok"
    r = c.post("/logout", data={"csrf_token": "tok"})
    assert r.headers["Location"].endswith("/welcome")


# ── A sign-up fills in its own draft when Claude can't read it ──────────────────

def _sign_up(app, **over):
    c = app.test_client()
    data = {"csrf_token": _tok(c, "/signup"), "name": "Layla Haddad", "company": "Desert Air",
            "email": "layla@desertair.example", "phone": "+971 50 123 4567", "kind": "brand",
            "start": "2030-12-01", "end": "2030-12-14", "where": "Sheikh Zayed Rd, both directions",
            "budget": "25,000 – 100,000", "notes": "Summer fares. 2 plays in every loop."}
    data.update(over)
    assert c.post("/signup", data=data).status_code == 302


def _read_it(s):
    from billboard_ops import intake
    p = s.scalars(select(Proposal).order_by(Proposal.id.desc())).first()
    intake.process(s, p)
    s.commit()
    return p


def test_without_claude_a_sign_up_fills_in_the_draft_from_its_form(app, s, people, inventory):
    inventory["a"].area = inventory["b"].area = "Sheikh Zayed Rd"
    s.commit()
    _sign_up(app)
    p = _read_it(s)
    d = p.draft
    assert p.status == "needs_review" and p.error == "" and p.read_count == 0
    assert d["client_id"] is None and d["client_name"] == "Desert Air"
    assert d["contact"] == {"name": "Layla Haddad", "email": "layla@desertair.example", "phone": "+971 50 123 4567"}
    assert [ln["screen_code"] for ln in d["lines"]] == ["SZR-01", "SZR-02"]
    assert all(ln["start_date"] == "2030-12-01" and ln["end_date"] == "2030-12-14" for ln in d["lines"])
    assert all(ln["confidence"]["screen"] == "medium" and ln["confidence"]["dates"] == "high" for ln in d["lines"])
    assert d["budget"] == {"amount": 100000, "currency": "AED"}
    assert "2 plays in every loop" in d["notes"] and "25,000 – 100,000" in d["notes"]
    assert d["missing"] == ["frequency"] and not d["ready"]
    assert d["flags"][0].startswith("Filled in from the website sign-up form")
    assert [c["status"] for c in p.checks] == ["fits", "fits"]            # vacancy is checked as usual
    team = app.test_client()
    with team.session_transaction() as sess:
        sess["uid"] = people["sales"].id
    page = team.get(f"/inbox/{p.ref}").get_data(as_text=True)
    assert "Filled in from the website sign-up form" in page and 'value="Desert Air"' in page
    assert 'value="2030-12-01"' in page


def test_a_sign_up_naming_a_screen_or_nowhere_in_particular(app, s, people, inventory):
    _sign_up(app, where="SZR-02 please", email="a@one.example")
    d = _read_it(s).draft
    assert [ln["screen_code"] for ln in d["lines"]] == ["SZR-02"] and d["lines"][0]["confidence"]["screen"] == "high"
    _sign_up(app, where="near the Marina", email="b@two.example", start="2030-12-03", end="2030-12-09")
    d = _read_it(s).draft
    assert [ln["screen_code"] for ln in d["lines"]] == ["MAR-01"]
    _sign_up(app, where="SZR and the Marina", email="d@four.example", start="2030-12-03", end="2030-12-09")
    assert [ln["screen_code"] for ln in _read_it(s).draft["lines"]] == ["MAR-01", "SZR-01", "SZR-02"]
    _sign_up(app, where="Any", email="c@three.example", start="2030-12-05", end="2030-12-12", budget="Over 250,000")
    d = _read_it(s).draft
    assert d["lines"] == [] and d["unmatched"][0]["start_date"] == "2030-12-05"
    assert "screens" in d["missing"] and d["budget"]["amount"] is None


def test_a_returning_client_is_recognised_by_their_email(app, s, people, inventory):
    _sign_up(app, company="ACME foods ltd", email="ads@acme.example", where="SZR")    # the name doesn't match
    d = _read_it(s).draft
    assert d["client_id"] == inventory["client"].id and d["client_name"] == "Acme Foods"


def test_with_claude_the_agent_reads_it_and_the_form_is_kept(app, s, people, inventory, fake_ai):
    _sign_up(app)
    p = _read_it(s)
    assert p.read_count == 1 and "Filled in from" not in " ".join(p.draft["flags"])
    assert p.reading["signup"]["company"] == "Desert Air"
