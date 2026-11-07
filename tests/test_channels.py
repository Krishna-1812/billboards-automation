"""Email in (IMAP), WhatsApp in and out, and sending approved messages."""

import hashlib
import hmac
import json
from datetime import timedelta
from email.message import EmailMessage
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import select

from billboard_ops import outbox, runtime
from billboard_ops.channels import email_in, whatsapp
from billboard_ops.jobs import run_due
from billboard_ops.models import Message, Proposal, utcnow



def raw_email(body="Two slots on SZR-01 please", subject="Booking", sender="Sara <ads@acme.example>",
              msg_id="<m1@acme>", in_reply_to=None, html=None, attachment=None, headers=None):
    em = EmailMessage()
    em["From"] = sender
    em["To"] = "bookings@ops.example"
    em["Subject"] = subject
    em["Message-ID"] = msg_id
    em["Date"] = "Sat, 07 Nov 2026 10:00:00 +0400"
    if in_reply_to:
        em["In-Reply-To"] = in_reply_to
    for k, v in (headers or {}).items():
        em[k] = v
    if html:
        em.set_content(html, subtype="html")
    else:
        em.set_content(body)
    if attachment:
        em.add_attachment(attachment[1], maintype="application", subtype="pdf", filename=attachment[0])
    return em.as_bytes()


def test_parse_plain_html_quoted_and_attachments():
    e = email_in.parse(raw_email(body="Yes please, 1-14 Dec.\n\nOn Fri, 6 Nov 2026 Ops <b@ops> wrote:\n> old text",
                                 attachment=("brief.pdf", b"%PDF-1.4 x")))
    assert e["from"] == "ads@acme.example" and e["name"] == "Sara" and e["message_id"] == "<m1@acme>"
    assert e["body"] == "Yes please, 1-14 Dec." and e["attachments"][0]["filename"] == "brief.pdf"
    assert e["date"].hour == 6                         # stored in UTC
    e = email_in.parse(raw_email(html="<html><style>x{}</style><p>Hello</p><p>Book <b>SZR-01</b></p></html>"))
    assert e["body"] == "Hello\nBook SZR-01"
    assert email_in.parse(raw_email(headers={"Auto-Submitted": "auto-replied"}))["auto"]


def test_store_skips_auto_replies_and_our_own_mail(s, inventory):
    runtime.config.MAIL_FROM = "bookings@ops.example"
    assert email_in.store(s, raw_email(headers={"Auto-Submitted": "auto-replied"})) is None
    assert email_in.store(s, raw_email(sender="bookings@ops.example", msg_id="<own@ops>")) is None
    p = email_in.store(s, raw_email())
    assert p.channel == "email" and p.sender == "ads@acme.example"


class FakeIMAP:
    def __init__(self, mails):
        self.mails, self.seen, self.log = mails, set(), []

    def __call__(self, host, port):
        return self

    def login(self, u, p):
        self.log.append("login")

    def select(self, folder):
        self.log.append(("select", folder))

    def uid(self, cmd, *args):
        if cmd == "search":
            return "OK", [b" ".join(k for k in self.mails if k not in self.seen)]
        if cmd == "fetch":
            return "OK", [(b"1 (BODY[] {n})", self.mails[args[0]]), b")"]
        if cmd == "store":
            self.seen.add(args[0])
            return "OK", []

    def logout(self):
        self.log.append("logout")


def test_polling_the_mailbox(s, inventory):
    cfg = runtime.config
    cfg.IMAP_HOST, cfg.IMAP_USER, cfg.IMAP_PASSWORD = "imap.example", "bookings@ops.example", "x"
    fake = FakeIMAP({b"1": raw_email(), b"2": raw_email(msg_id="<m2@acme>", subject="Re: Booking", body="From 1 Dec")})
    email_in.set_imap_factory(fake)
    try:
        assert email_in.poll(s) == 2
        assert fake.seen == {b"1", b"2"} and fake.log[-1] == "logout"
        p = s.scalars(select(Proposal)).one()            # the reply joined the first email's proposal
        assert len(p.messages) == 2
        fake.seen.clear()                                 # delivered again: nothing new is stored
        email_in.poll(s)
        assert len(s.scalars(select(Message)).all()) == 2
    finally:
        email_in.set_imap_factory(None)
        cfg.IMAP_HOST = ""


# ── WhatsApp ────────────────────────────────────────────────────────────────────

def wa_payload(*messages, statuses=()):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "971500000001", "profile": {"name": "Omar"}}],
        "messages": list(messages), "statuses": list(statuses)}}]}]}


def wa_text(text, mid="wamid.1", ts=1793000000, context=None):
    m = {"from": "971500000001", "id": mid, "timestamp": str(ts), "type": "text", "text": {"body": text}}
    if context:
        m["context"] = {"id": context}
    return m


def test_parse_whatsapp():
    doc = {"from": "971500000001", "id": "wamid.2", "timestamp": "1793000000", "type": "document",
           "document": {"id": "media-9", "mime_type": "application/pdf", "filename": "brief.pdf", "caption": "brief"}}
    items = whatsapp.parse(wa_payload(wa_text("Hi"), doc, statuses=[{"id": "wamid.out", "status": "failed",
                                                                       "errors": [{"title": "Re-engagement"}]}]))
    assert items[0]["from"] == "+971500000001" and items[0]["name"] == "Omar" and items[0]["text"] == "Hi"
    assert items[1]["media"] == {"id": "media-9", "mime_type": "application/pdf", "filename": "brief.pdf"}
    assert items[2] == {"kind": "status", "id": "wamid.out", "status": "failed", "error": "Re-engagement"}


def _signed(app, body):
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def test_whatsapp_webhook(app, s, inventory):
    cfg = app.config["APP"]
    cfg.WHATSAPP_VERIFY_TOKEN, cfg.WHATSAPP_APP_SECRET = "verify-me", "app-secret"
    c = app.test_client()
    r = c.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=42")
    assert r.status_code == 200 and r.get_data(as_text=True) == "42"
    assert c.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=no&hub.challenge=42").status_code == 403
    body = json.dumps(wa_payload(wa_text("Need SZR-01 next month"))).encode()
    assert c.post("/webhooks/whatsapp", data=body, headers={"Content-Type": "application/json"}).status_code == 403
    r = c.post("/webhooks/whatsapp", data=body, headers=_signed(app, body))     # no CSRF token needed
    assert r.status_code == 200 and r.get_json()["stored"] == 1
    p = s.scalars(select(Proposal)).one()
    assert (p.channel, p.sender, p.sender_name) == ("whatsapp", "+971500000001", "Omar")
    c.post("/webhooks/whatsapp", data=body, headers=_signed(app, body))         # Meta retries: still one message
    assert len(s.scalars(select(Message)).all()) == 1


class FakeHTTP:
    def __init__(self):
        self.sent = []

    def get(self, url, headers=None, timeout=None):
        if url.endswith("/media-9"):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"url": "https://cdn/x",
                                                                                 "mime_type": "application/pdf"})
        return SimpleNamespace(raise_for_status=lambda: None, content=b"%PDF-1.4 brief")

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append((url, json))
        return SimpleNamespace(status_code=200, json=lambda: {"messages": [{"id": "wamid.out1"}]})


@pytest.fixture
def wa(app):
    cfg = app.config["APP"]
    cfg.WHATSAPP_TOKEN, cfg.WHATSAPP_PHONE_NUMBER_ID = "token", "12345"
    fake = FakeHTTP()
    whatsapp.set_http(fake)
    yield fake
    whatsapp.set_http(None)
    cfg.WHATSAPP_TOKEN = cfg.WHATSAPP_PHONE_NUMBER_ID = ""


def test_media_is_fetched_before_reading(s, inventory, wa):
    doc = {"from": "971500000001", "id": "wamid.2", "timestamp": str(int(utcnow().timestamp())), "type": "document",
           "document": {"id": "media-9", "mime_type": "application/pdf", "filename": "brief.pdf"}}
    whatsapp.receive_webhook(s, wa_payload(doc))
    p = s.scalars(select(Proposal)).one()
    whatsapp.fetch_pending_media(s, p)
    att = p.messages[0].attachments[0]
    assert att.data == b"%PDF-1.4 brief" and att.size == 14


def test_whatsapp_replies_respect_the_24_hour_window(s, people, inventory, wa):
    whatsapp.receive_webhook(s, wa_payload(wa_text("Hi", ts=int((utcnow() - timedelta(hours=2)).timestamp()))))
    p = s.scalars(select(Proposal)).one()
    msg = Message(proposal=p, direction="out", channel="whatsapp", kind="clarify", status="draft",
                  recipient="+971500000001", body="Which dates?")
    s.add(msg)
    p.status = "needs_review"
    outbox.approve(s, people["sales"], msg)
    s.commit()
    run_due()
    s.refresh(msg)
    s.refresh(p)
    assert msg.status == "sent" and msg.external_id == "wamid.out1" and p.status == "waiting_client"
    url, body = wa.sent[0]
    assert url.endswith("/12345/messages") and body["to"] == "971500000001" and body["context"]["message_id"] == "wamid.1"
    # A day later the window is closed: not sent, and the team is told to use email.
    s.scalars(select(Message).where(Message.direction == "in")).one().created_at = utcnow() - timedelta(hours=30)
    late = Message(proposal=p, direction="out", channel="whatsapp", kind="other", status="draft",
                   recipient="+971500000001", body="Still there?")
    s.add(late)
    outbox.approve(s, people["sales"], late)
    s.commit()
    run_due()
    s.refresh(late)
    assert late.status == "failed" and "24 hours" in late.error


def test_email_replies_stay_in_the_clients_thread(s, people, inventory):
    runtime.config.SMTP_HOST, runtime.config.MAIL_FROM = "smtp.example", "bookings@ops.example"
    try:
        p = email_in.store(s, raw_email())
        msg = Message(proposal=p, direction="out", channel="email", kind="clarify", status="draft",
                      recipient="ads@acme.example", subject="Re: Booking", body="Which dates?")
        s.add(msg)
        outbox.approve(s, people["sales"], msg)
        s.commit()
        sent = []
        with mock.patch("smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value.send_message.side_effect = sent.append
            run_due()
        s.refresh(msg)
        em = sent[0]
        assert em["In-Reply-To"] == "<m1@acme>" and em["To"] == "ads@acme.example"
        assert msg.status == "sent" and msg.external_id == em["Message-ID"] and "ops.example" in msg.external_id
    finally:
        runtime.config.SMTP_HOST = ""


def test_manual_messages_are_marked_sent_by_hand(s, people, inventory):
    msg = Message(direction="out", channel="manual", kind="clarify", status="draft", recipient="", body="Hi")
    s.add(msg)
    outbox.approve(s, people["sales"], msg)
    assert msg.status == "approved" and not s.scalars(select(msg.__class__).where(False)).all()
    outbox.mark_sent(s, people["sales"], msg)
    assert msg.status == "sent"
    with pytest.raises(outbox.OutboxError):
        outbox.discard(s, people["sales"], msg)
    empty = Message(direction="out", channel="email", kind="other", status="draft", recipient="x@y.z", body=" ")
    s.add(empty)
    with pytest.raises(outbox.OutboxError):
        outbox.approve(s, people["sales"], empty)
