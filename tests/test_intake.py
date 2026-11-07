"""The proposals inbox: arriving, threading, reading, duplicates, holds, alternatives and the auto-hold gate."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops import intake, settings
from billboard_ops.jobs import run_due
from billboard_ops.models import Booking, EvalRun, Job, Message, utcnow

from conftest import ai_answer, ai_line, future


def email(s, body="Please hold two slots on SZR-01", sender="ads@acme.example", subject="Booking", **kw):
    return intake.receive(s, channel="email", sender=sender, sender_name="Sara", subject=subject, body=body, **kw)


def read_now(s, p):
    intake.process(s, p)
    s.flush()
    return p


def test_a_new_email_becomes_a_proposal_and_is_queued(s, inventory):
    p, m, new = email(s, external_id="<a1@acme>")
    s.flush()
    assert new and p.ref == "PR-00001" and p.status == "new" and m.direction == "in"
    assert s.scalars(select(Job).where(Job.kind == "read_proposal")).one().payload["proposal_id"] == p.id
    again = email(s, external_id="<a1@acme>")          # the same email again (IMAP re-delivery)
    assert again[2] is False and len(s.scalars(select(Message)).all()) == 1


def test_replies_join_their_thread(s, inventory):
    p, _, _ = email(s, external_id="<a1@acme>")
    out = Message(proposal=p, direction="out", channel="email", kind="clarify", status="sent",
                  external_id="<ours@ops>")
    s.add(out)
    p.status = "waiting_client"
    s.flush()
    p2, _, new = email(s, body="The 1st to the 14th", subject="Something else entirely", external_id="<a2@acme>",
                       reply_ids=["<ours@ops>"])
    assert p2 is p and not new and p.status == "new"           # back in the queue to be read again
    p3, _, new = email(s, body="also", subject="RE: Booking", external_id="<a3@acme>")
    assert p3 is p and not new
    p4, _, new = email(s, body="different campaign", subject="Ramadan", external_id="<a4@acme>")
    assert new and p4 is not p


def test_whatsapp_bursts_join_and_are_read_once(s, inventory):
    p, _, _ = intake.receive(s, channel="whatsapp", sender="+971500000001", body="Hi, want SZR-01", external_id="w1")
    p2, _, _ = intake.receive(s, channel="whatsapp", sender="+971500000001", body="from 1 Dec", external_id="w2")
    s.flush()
    assert p2 is p and len(p.messages) == 2
    jobs = s.scalars(select(Job).where(Job.kind == "read_proposal", Job.status == "queued")).all()
    assert len(jobs) == 1 and jobs[0].run_at > utcnow() + timedelta(seconds=20)


def test_reading_fills_the_draft_and_checks_vacancy(s, inventory, fake_ai):
    p, _, _ = email(s)
    read_now(s, p)
    assert p.status == "needs_review" and p.ready and p.client_id == inventory["client"].id
    assert p.checks[0]["status"] == "fits" and p.draft["lines"][0]["screen_code"] == "SZR-01"
    assert not [m for m in p.messages if m.direction == "out"]          # nothing to ask


def test_missing_details_draft_a_question_in_the_clients_language(s, inventory, fake_ai):
    fake_ai.default = ai_answer(lines=[ai_line(start=None, end=None, conf="low")], missing=["dates"],
                                questions=["ما هي تواريخ الحملة؟"], language="ar")
    p, _, _ = email(s, body="نريد حجز شاشة الشيخ زايد")
    read_now(s, p)
    draft = [m for m in p.messages if m.direction == "out"][0]
    assert p.status == "needs_review" and draft.kind == "clarify" and draft.status == "draft"
    assert "ما هي تواريخ الحملة؟" in draft.body and draft.recipient == "ads@acme.example" and draft.language == "ar"
    # Reading again replaces the old question rather than piling up a second one.
    p.status = "new"
    read_now(s, p)
    assert [m.status for m in p.messages if m.kind == "clarify"] == ["discarded", "draft"]


def test_not_a_request(s, inventory, fake_ai):
    fake_ai.default = ai_answer(is_request=False, lines=[])
    p, _, _ = email(s, body="Invoice 4411 attached")
    assert read_now(s, p).status == "not_proposal"


def test_reading_failures(s, inventory, app):
    p, _, _ = email(s)
    read_now(s, p)                                          # no Claude key: a person fills it in
    assert p.status == "failed" and "isn't connected" in p.error


def test_network_trouble_retries_the_job(s, inventory, fake_ai):
    import anthropic
    import httpx2
    fake_ai.error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    p, _, _ = email(s)
    s.scalars(select(Job)).one().run_at = utcnow() - timedelta(seconds=1)
    s.commit()
    run_due()
    job = s.scalars(select(Job).where(Job.kind == "read_proposal")).one()
    s.refresh(job)
    assert job.status == "queued" and job.attempts == 1


def test_place_a_hold_from_a_proposal(s, people, inventory, fake_ai):
    p, _, _ = email(s, external_id="<a1@acme>")
    read_now(s, p)
    b = intake.place_hold(s, people["sales"], p, client=inventory["client"],
                          lines=intake.lines_from_draft(p.draft["lines"]), title="Spring")
    s.flush()
    assert p.status == "held" and p.booking_id == b.id and b.source == "proposal" and b.source_ref == p.ref
    conf = [m for m in p.messages if m.kind == "hold_confirmation"][0]
    assert conf.status == "draft" and conf.in_reply_to == "<a1@acme>" and b.code in conf.subject
    assert "SZR-01" in conf.body and "signed proposal" in conf.body
    with pytest.raises(svc.BookingError):
        intake.place_hold(s, people["sales"], p, client=inventory["client"], lines=intake.lines_from_draft(p.draft["lines"]))


def test_decline_offers_the_nearest_free_dates_and_screens(s, people, inventory, fake_ai):
    a = inventory["a"]
    svc.create(s, people["manager"], inventory["client"],
               [svc.LineIn(a.id, future(10), future(16), plays_per_hour=360)], status="confirmed")
    p, _, _ = email(s)
    read_now(s, p)
    assert p.checks[0]["status"] == "no" and p.checks[0]["alt_screens"] == ["SZR-02"]
    m = intake.offer_alternatives(s, people["sales"], p)
    assert p.status == "declined" and m.kind == "decline"
    assert "Sheikh Zayed Rd South (SZR-02)" in m.body and "nearest dates" in m.body


def test_the_same_request_on_two_channels_is_merged(s, inventory, fake_ai):
    p1, _, _ = email(s)
    read_now(s, p1)
    p2, _, _ = intake.receive(s, channel="whatsapp", sender="+971500000001", body="Same as my email: SZR-01",
                              external_id="w1")
    read_now(s, p2)
    assert p2.status == "merged" and p2.duplicate_of_id == p1.id
    assert len(p1.messages) == 2 and p1.status == "new"            # read again with both messages


def test_a_near_duplicate_is_only_suggested(s, inventory, fake_ai):
    p1, _, _ = email(s)
    read_now(s, p1)
    fake_ai.default = ai_answer(lines=[ai_line(start=future(11), end=future(18))])
    p2, _, _ = email(s, subject="Second request", body="SZR-01 from a day later")
    read_now(s, p2)
    assert p2.status == "needs_review" and p2.duplicate_of_id == p1.id


def _passing_run(s):
    s.add(EvalRun(status="done", summary={"cases": 40, "core": 97.5, "passed": True}))
    s.flush()


def test_auto_hold_only_for_known_senders(s, inventory, fake_ai):
    settings.put(s, "auto_hold", True)
    _passing_run(s)
    p, _, _ = email(s, sender="someone@spoofed.example")       # claims to be Acme, isn't
    assert read_now(s, p).status == "needs_review"
    c = inventory["client"]
    assert intake.sender_is_client(SimpleNamespace(sender="ads@acme.example"), c)
    assert intake.sender_is_client(SimpleNamespace(sender="media@acme.example"), c)     # same company domain
    c.whatsapp = "+971 50 000 0001"
    assert intake.sender_is_client(SimpleNamespace(sender="+971500000001"), c)
    assert not intake.sender_is_client(SimpleNamespace(sender="+971500000002"), c)
    c.email = "acme.ads@gmail.com"                               # a public mail domain proves nothing
    assert not intake.sender_is_client(SimpleNamespace(sender="other@gmail.com"), c)


def test_auto_hold_needs_the_switch_and_a_passing_test(s, inventory, fake_ai):
    settings.put(s, "auto_hold", True)
    p, _, _ = email(s)
    read_now(s, p)
    assert p.status == "needs_review"                   # no passing accuracy run yet
    _passing_run(s)
    p2, _, _ = email(s, subject="Another", body="SZR-02 please", sender="b@acme.example")   # Acme's own domain
    fake_ai.default = ai_answer(lines=[ai_line(codes=("SZR-02",))])
    read_now(s, p2)
    assert p2.status == "held" and p2.auto_held
    b = s.get(Booking, p2.booking_id)
    assert b.created_by == "agent" and b.status == "hold"


def test_auto_hold_never_overbooks_or_guesses(s, people, inventory, fake_ai):
    settings.put(s, "auto_hold", True)
    _passing_run(s)
    svc.create(s, people["manager"], inventory["client"],
               [svc.LineIn(inventory["a"].id, future(10), future(16), plays_per_hour=360)], status="confirmed")
    p, _, _ = email(s)
    assert read_now(s, p).status == "needs_review"      # doesn't fit
    fake_ai.default = ai_answer(client_conf="medium", lines=[ai_line(codes=("SZR-02",))])
    p2, _, _ = email(s, subject="x", sender="c@acme.example")
    assert read_now(s, p2).status == "needs_review"     # not sure of the client


def test_client_hold_reminders_are_drafted(s, people, inventory, fake_ai):
    p, _, _ = email(s, external_id="<a1@acme>")
    read_now(s, p)
    b = intake.place_hold(s, people["sales"], p, client=inventory["client"],
                          lines=intake.lines_from_draft(p.draft["lines"]))
    s.flush()
    svc.remind_holds(s, b.hold_expires_at - timedelta(hours=10))
    reminder = s.scalars(select(Message).where(Message.kind == "hold_reminder")).one()
    assert reminder.status == "draft" and reminder.recipient == "ads@acme.example" and b.code in reminder.body
    settings.put(s, "remind_clients", False)
    assert intake.hold_reminder_drafts(s, b) is None
