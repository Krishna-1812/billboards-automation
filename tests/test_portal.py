"""Phase 4: the client portal. One-time sign-in links, each client seeing only their own bookings and files,
requests, uploads, approvals, Arabic, and the team giving access."""

import io
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops import creatives, plans, proof, settings
from billboard_ops.models import (Activity, Booking, BookingDocument, Client, Creative, Job, LoginLink, Message,
                                  Proposal, User, utcnow)

from conftest import future
from media import media, read  # noqa: F401
from test_creatives import ai_reads_ads, confirmed, small  # noqa: F401


@pytest.fixture
def portal_people(s, small):
    other = Client(name="Globex", email="ads@globex.example")
    s.add(other)
    s.flush()
    sara = User(email="sara@acme.example", name="Sara", role="client", client_id=small["client"].id)
    omar = User(email="omar@globex.example", name="Omar", role="client", client_id=other.id, language="ar")
    s.add_all([sara, omar])
    s.commit()
    return {"sara": sara, "omar": omar, "acme": small["client"], "globex": other}


def as_client(app, user):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = user.id
        sess["csrf"] = "tok"
    return c


def form(**kw):
    return {"csrf_token": "tok", **kw}


def _link_from_mail(s):
    job = s.scalars(select(Job).where(Job.kind == "send_email").order_by(Job.id.desc())).first()
    return re.search(r"https://ops\.example\.com(/portal/link/\S+)", job.payload["body"]).group(1), job.payload


# ── Signing in ──────────────────────────────────────────────────────────────────

def test_sign_in_with_a_one_time_link(app, s, portal_people):
    c = app.test_client()
    assert c.get("/portal").headers["Location"].endswith("/portal/login")
    c.get("/portal/login")
    with c.session_transaction() as sess:
        tok = sess["csrf"]
    r = c.post("/portal/login", data={"csrf_token": tok, "email": "nobody@acme.example"})
    assert "If that address has access" in r.get_data(as_text=True)
    assert not s.scalars(select(Job).where(Job.kind == "send_email")).first()      # nothing sent, same answer
    r = c.post("/portal/login", data={"csrf_token": tok, "email": "Sara@Acme.example"})
    assert "If that address has access" in r.get_data(as_text=True)
    path, mail = _link_from_mail(s)
    assert mail["to"] == ["sara@acme.example"] and "It works once, for 30 minutes" in mail["body"]
    row = s.scalars(select(LoginLink)).one()
    assert row.token_hash != path.rsplit("/", 1)[1] and len(row.token_hash) == 64      # only the hash is kept
    page = c.get(path)
    assert page.status_code == 200 and "Signing in as sara@acme.example" in page.get_data(as_text=True)
    s.expire_all()
    assert s.scalars(select(LoginLink)).one().used_at is None                          # opening it uses nothing
    r = c.post(path, data={"csrf_token": tok})
    assert r.headers["Location"].endswith("/portal")
    assert "Acme Foods" in c.get("/portal").get_data(as_text=True)
    assert c.get(path).status_code == 410                                               # once only
    # A client is never let into the team's pages.
    assert c.get("/bookings").headers["Location"].endswith("/portal")
    assert c.get("/settings").headers["Location"].endswith("/portal")


def test_expired_links_and_too_many_asks(app, s, portal_people):
    from billboard_ops.views import portal
    with app.test_request_context():
        url = portal.make_link(s, portal_people["sara"], minutes=30, created_by="owner@example.com")
    s.scalars(select(LoginLink)).one().expires_at = utcnow() - timedelta(minutes=1)
    s.commit()
    c = app.test_client()
    assert c.get(url.replace("https://ops.example.com", "")).status_code == 410
    c.get("/portal/login")
    with c.session_transaction() as sess:
        tok = sess["csrf"]
    for _ in range(5):
        assert c.post("/portal/login", data={"csrf_token": tok, "email": "sara@acme.example"}).status_code == 200
    assert c.post("/portal/login", data={"csrf_token": tok, "email": "sara@acme.example"}).status_code == 429
    portal_people["sara"].active = False
    s.commit()
    with app.test_request_context():
        url = portal.make_link(s, portal_people["sara"], minutes=30, created_by="owner@example.com")
    s.commit()
    assert c.get(url.replace("https://ops.example.com", "")).status_code == 410        # access removed


def test_with_sign_in_switched_off_the_portal_still_needs_a_link(app, s, portal_people):
    app.config["APP"].LOGIN_REQUIRED = False
    c = app.test_client()
    assert c.get("/portal").headers["Location"].endswith("/portal/login")
    assert c.get("/bookings").status_code == 200                                      # the team side is open


# ── What a client sees ──────────────────────────────────────────────────────────

def _two_clients(s, people, small, portal_people, media):
    mine = confirmed(s, people, small)
    c1 = creatives.add_file(s, mine, filename="acme.mp4", content_type="video/mp4", data=read(media["long.mp4"]))
    creatives.check(s, c1)
    theirs = svc.create(s, people["sales"], portal_people["globex"],
                        [svc.LineIn(small["c"].id, future(30), future(40), ad_seconds=10, plays_per_hour=12)],
                        title="Globex secret launch")
    svc.confirm(s, people["manager"], theirs)
    c2 = creatives.add_file(s, theirs, filename="globex.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    p2 = proof.add(s, people["ops"], theirs, filename="g.jpg", content_type="image/jpeg", data=read(media["good.png"]))
    s.commit()
    return mine, theirs, c1, c2, p2


def test_each_client_sees_only_their_own(app, s, people, small, portal_people, media, ai_reads_ads):
    mine, theirs, c1, c2, p2 = _two_clients(s, people, small, portal_people, media)
    c = as_client(app, portal_people["sara"])
    home = c.get("/portal").get_data(as_text=True)
    assert mine.code in home and theirs.code not in home and "Globex" not in home
    page = c.get(f"/portal/bookings/{mine.code}").get_data(as_text=True)
    assert "Changes needed to your files" in page and "The video is 15.0s long" in page
    assert c.get(f"/portal/files/{c1.id}").status_code == 200
    # Every page and file link of the other client is "not found", whatever is tried.
    for url in (f"/portal/bookings/{theirs.code}", f"/portal/files/{c2.id}", f"/portal/proof/{p2.id}"):
        assert c.get(url).status_code == 404, url
    for url, data in ((f"/portal/bookings/{theirs.code}/accept", {}),
                      (f"/portal/bookings/{theirs.code}/upload", {"files": (io.BytesIO(b"x"), "x.mp4")}),
                      (f"/portal/bookings/{theirs.code}/plan", {"decision": "approve"}),
                      (f"/portal/files/{c2.id}/decide", {"decision": "approve"})):
        assert c.post(url, data=form(**data), content_type="multipart/form-data").status_code == 404, url
    assert s.scalars(select(Creative).where(Creative.booking_id == theirs.id)).all() == [c2]   # nothing added
    # The team's own links don't open for a client either.
    for url in (f"/creatives/{c1.id}/file", f"/proof/{p2.id}", f"/bookings/{mine.code}", f"/bookings/{mine.code}/load.zip",
                f"/playlists?booking={mine.code}"):
        assert c.get(url).headers.get("Location", "").endswith("/portal"), url


def test_a_hidden_proof_isnt_shown(app, s, people, small, portal_people, media, ai_reads_ads):
    mine = confirmed(s, people, small)
    p = proof.add(s, people["ops"], mine, filename="inside.jpg", content_type="image/jpeg",
                  data=read(media["good.png"]), shared=False)
    s.commit()
    c = as_client(app, portal_people["sara"])
    assert c.get(f"/portal/proof/{p.id}").status_code == 404


def test_accepting_a_hold_with_a_signed_po(app, s, people, small, portal_people):
    b = svc.create(s, people["sales"], small["client"],
                   [svc.LineIn(small["a"].id, future(10), future(16), ad_seconds=10, plays_per_hour=30)])
    s.commit()
    c = as_client(app, portal_people["sara"])
    assert "Accept the hold and send the signed proposal" in c.get("/portal").get_data(as_text=True)
    r = c.post(f"/portal/bookings/{b.code}/accept", data=form(document=(io.BytesIO(b"%PDF-1.4"), "po.pdf")),
               content_type="multipart/form-data")
    assert r.status_code == 302
    s.expire_all()
    b = s.get(Booking, b.id)
    assert b.client_accepted_at is not None and b.status == "hold"                  # the team still confirms
    doc = s.scalars(select(BookingDocument)).one()
    assert doc.filename == "po.pdf" and doc.uploaded_by == "sara@acme.example"
    assert "accepted the hold in the portal and sent a document" in s.scalars(
        select(Activity).where(Activity.action == "client_accepted")).one().summary
    emails = [j.payload for j in s.scalars(select(Job).where(Job.kind == "send_email"))]
    assert any("accepted hold" in e["subject"] and "owner@example.com" in e["to"] for e in emails)


def test_upload_and_see_the_result(app, s, people, small, portal_people, media, ai_reads_ads):
    b = confirmed(s, people, small)
    c = as_client(app, portal_people["sara"])
    r = c.post(f"/portal/bookings/{b.code}/upload",
               data=form(files=[(io.BytesIO(read(media["good.mp4"])), "spring.mp4"), (io.BytesIO(b"hi"), "notes.txt")]),
               content_type="multipart/form-data")
    assert r.status_code == 302
    s.expire_all()
    cr = s.scalars(select(Creative)).one()
    assert cr.source == "portal" and cr.filename == "spring.mp4"
    creatives.check(s, cr)
    s.commit()
    page = c.get(f"/portal/bookings/{b.code}").get_data(as_text=True)
    assert "Passed our checks" in page


def test_approving_the_final_preview_when_asked(app, s, people, small, portal_people, media, ai_reads_ads):
    settings.put(s, "client_approves_final", True)
    b = confirmed(s, people, small)
    cr = creatives.add_file(s, b, filename="ad.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, cr)
    creatives.approve(s, people["ops"], cr)
    s.commit()
    assert b.status == "checking" or b.status in ("awaiting_creative", "checking")   # not approved until the client says so
    assert {x["state"] for x in creatives.coverage(s, b)} == {"waiting_client"}
    m = s.scalars(select(Message).where(Message.kind == "preview_review")).one()
    assert "approve it in your portal" in m.body
    c = as_client(app, portal_people["sara"])
    page = c.get(f"/portal/bookings/{b.code}").get_data(as_text=True)
    assert "Approve the final preview of your ad" in page and "Approved by us. Please watch it" in page
    r = c.post(f"/portal/files/{cr.id}/decide", data=form(decision="approve"))
    assert r.status_code == 302
    s.expire_all()
    b = s.get(Booking, b.id)
    assert b.status == "creative_approved" and s.get(Creative, cr.id).client_approved_at is not None


def test_asking_for_changes_to_the_preview(app, s, people, small, portal_people, media, ai_reads_ads):
    settings.put(s, "client_approves_final", True)
    b = confirmed(s, people, small)
    cr = creatives.add_file(s, b, filename="ad.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, cr)
    creatives.approve(s, people["ops"], cr)
    s.commit()
    c = as_client(app, portal_people["sara"])
    c.post(f"/portal/files/{cr.id}/decide", data=form(decision="changes", note="Logo is the old one"))
    s.expire_all()
    cr = s.get(Creative, cr.id)
    assert cr.status == "rejected" and cr.client_note == "Logo is the old one"
    assert s.get(Booking, b.id).status == "changes_requested"


def test_approving_the_play_plan(app, s, people, small, portal_people):
    settings.put(s, "client_approves_plan", True)
    b = confirmed(s, people, small)
    plans.confirm(s, people["ops"], b)
    s.commit()
    c = as_client(app, portal_people["sara"])
    page = c.get(f"/portal/bookings/{b.code}").get_data(as_text=True)
    assert "Please check the play plan" in page and "about 540 plays a day" in page
    c.post(f"/portal/bookings/{b.code}/plan", data=form(decision="approve"))
    s.expire_all()
    assert s.get(Booking, b.id).plan_status == "confirmed"


def test_a_new_request_lands_in_the_inbox(app, s, people, small, portal_people):
    c = as_client(app, portal_people["sara"])
    assert c.post("/portal/request", data=form()).status_code == 400
    r = c.post("/portal/request", data=form(campaign="Ramadan", screen_id=[str(small["a"].id)], start="2026-12-01",
                                            end="2026-12-14", frequency="2 slots", notes="Evenings matter most"))
    assert r.status_code == 302
    p = s.scalars(select(Proposal)).one()
    assert p.channel == "portal" and p.client_id == small["client"].id and p.sender == "sara@acme.example"
    body = p.messages[0].body
    assert "Campaign or brand: Ramadan" in body and "SZR-01" in body and "2026-12-01 to 2026-12-14" in body
    assert s.scalars(select(Job).where(Job.kind == "read_proposal")).first() is not None
    from billboard_ops import intake
    assert intake._recipient(s, p) == ("email", "sara@acme.example")


def test_arabic(app, s, people, small, portal_people, media, ai_reads_ads):
    omar = portal_people["omar"]
    b = svc.create(s, people["sales"], portal_people["globex"],
                   [svc.LineIn(small["a"].id, future(30), future(36), ad_seconds=10, plays_per_hour=12)])
    svc.confirm(s, people["manager"], b)
    s.commit()
    c = as_client(app, omar)
    page = c.get("/portal").get_data(as_text=True)
    assert 'dir="rtl"' in page and "حجوزاتي" in page and "بانتظار ملفاتكم" in page
    page = c.get(f"/portal/bookings/{b.code}").get_data(as_text=True)
    assert "ملفات إعلانكم" in page and "يجب أن تكون جميع النصوص بالعربية" in page
    c.post("/portal/language", data=form(next="/portal"))
    assert 'dir="ltr"' in c.get("/portal").get_data(as_text=True)


# ── The team gives access ───────────────────────────────────────────────────────

def test_the_team_gives_and_removes_access(app, s, people, small, login):
    cid = small["client"].id
    c = login(people["sales"])
    r = c.post(f"/clients/{cid}/portal", data=form(action="add", email="New@Acme.example", name="Nadia",
                                                     language="ar", send="1"))
    assert r.status_code == 302
    u = s.scalars(select(User).where(User.email == "new@acme.example")).one()
    assert u.role == "client" and u.client_id == cid and u.language == "ar"
    _, mail = _link_from_mail(s)
    assert mail["to"] == ["new@acme.example"] and "رابط" in mail["subject"]
    page = c.get(f"/clients/{cid}").get_data(as_text=True)
    assert "new@acme.example" in page and "Email a sign-in link" in page
    r = c.post(f"/clients/{cid}/portal", data=form(action="copy", user_id=str(u.id)), follow_redirects=True)
    assert "/portal/link/" in r.get_data(as_text=True)
    r = c.post(f"/clients/{cid}/portal", data=form(action="add", email="ops@example.com"))
    assert "already belongs to someone else" in c.get(f"/clients/{cid}").get_data(as_text=True)
    c.post(f"/clients/{cid}/portal", data=form(action="remove", user_id=str(u.id)))
    s.expire_all()
    assert s.get(User, u.id).active is False
    ops = login(people["ops"])
    assert ops.post(f"/clients/{cid}/portal", data=form(action="restore", user_id=str(u.id))).status_code == 403


def test_links_behind_railways_proxy_are_https(app, s, portal_people):
    app.config["APP"].APP_URL = ""                     # no APP_URL: the link is built from the request
    fwd = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "ops.example.org"}
    c = app.test_client()
    c.get("/portal/login")                             # a plain http request inside, as Railway's proxy sends it
    with c.session_transaction() as sess:
        tok = sess["csrf"]
    c.post("/portal/login", data={"csrf_token": tok, "email": "sara@acme.example"}, headers=fwd)
    job = s.scalars(select(Job).where(Job.kind == "send_email")).one()
    assert "https://ops.example.org/portal/link/" in job.payload["body"]
