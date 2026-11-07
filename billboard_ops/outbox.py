"""Messages to clients: drafts wait here until a person approves them, then the worker sends them.

Email goes out through SMTP as a reply in the client's own thread (In-Reply-To / References), so the conversation
stays together in their mailbox and ours. WhatsApp goes through the Cloud API. A pasted request with no email
address can't be sent by the app: the team copies the text, sends it themselves, and marks it sent.
"""

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

from sqlalchemy import select

from . import runtime
from .activity import log, notify
from .bookings import actor_of
from .jobs import Skip, enqueue, handler
from .models import Message, utcnow

AFTER_SEND = {"clarify": "waiting_client"}


class OutboxError(Exception):
    pass


def approve(session, user, msg, *, subject=None, body=None, recipient=None, channel=None):
    if msg.status not in ("draft", "failed"):
        raise OutboxError(f"This message is already {msg.status}.")
    if subject is not None:
        msg.subject = subject
    if body is not None:
        msg.body = body
    if recipient is not None:
        msg.recipient = recipient.strip()
    if channel in ("email", "whatsapp", "manual"):
        msg.channel = channel
    if not msg.body.strip():
        raise OutboxError("The message is empty.")
    msg.approved_by = actor_of(user)
    msg.error = ""
    if msg.channel == "manual" or not msg.recipient:
        msg.status = "approved"           # the team sends it themselves, then marks it sent
        log(session, actor_of(user), "approved", "message", msg.id, "Approved; to be sent by hand")
        return msg
    msg.status = "approved"
    session.flush()                       # the job needs the message's id
    enqueue(session, "send_message", {"message_id": msg.id}, dedupe_key=f"send:{msg.id}")
    log(session, actor_of(user), "approved", "message", msg.id,
        f"Approved {msg.kind.replace('_', ' ')} to {msg.recipient} by {msg.channel}")
    return msg


def mark_sent(session, user, msg):
    _sent(session, msg)
    log(session, actor_of(user), "sent_by_hand", "message", msg.id, f"Marked as sent by hand to {msg.recipient}")


def discard(session, user, msg):
    if msg.status == "sent":
        raise OutboxError("It has already been sent.")
    msg.status = "discarded"
    log(session, actor_of(user), "discarded", "message", msg.id, f"Discarded a {msg.kind.replace('_', ' ')} draft")


def _sent(session, msg):
    msg.status = "sent"
    msg.sent_at = utcnow()
    p = msg.proposal
    if p is not None and msg.kind in AFTER_SEND and p.status in ("needs_review", "new"):
        p.status = AFTER_SEND[msg.kind]


def _thread_ids(msg):
    """The client's message we're replying to, and the whole chain for References."""
    ids = []
    if msg.proposal is not None:
        ids = [m.external_id for m in msg.proposal.messages if m.external_id and m.channel == "email"
               and m.id != msg.id and m.status in ("received", "sent")]
    reply_to = msg.in_reply_to or (ids[-1] if ids else "")
    return reply_to, " ".join(ids[-10:])


def send_email(msg):
    cfg = runtime.config
    if not cfg or not cfg.SMTP_HOST:
        raise Skip("SMTP isn't set up, so the app can't send email (SMTP_HOST, SMTP_USER, SMTP_PASSWORD, MAIL_FROM).")
    em = EmailMessage()
    em["From"] = cfg.MAIL_FROM
    em["To"] = msg.recipient
    em["Subject"] = msg.subject or "Your booking request"
    domain = (cfg.MAIL_FROM.split("@")[-1] if "@" in (cfg.MAIL_FROM or "") else None)
    em["Message-ID"] = make_msgid(domain=domain)
    reply_to, refs = _thread_ids(msg)
    if reply_to:
        em["In-Reply-To"] = reply_to
        em["References"] = refs or reply_to
    em.set_content(msg.body)
    with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        if cfg.SMTP_USER:
            smtp.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
        smtp.send_message(em)
    return em["Message-ID"]


@handler("send_message")
def _send(session, payload):
    from .channels import whatsapp
    msg = session.get(Message, payload["message_id"])
    if msg is None or msg.status != "approved":
        return
    try:
        if msg.channel == "whatsapp":
            reply_to = msg.in_reply_to or next((m.external_id for m in reversed(msg.proposal.messages)
                                                if m.direction == "in" and m.external_id), "") if msg.proposal else ""
            msg.external_id = whatsapp.send_text(session, msg.recipient, msg.body, reply_to)
        else:
            msg.external_id = send_email(msg)
    except (Skip, whatsapp.OutsideWindow) as e:
        msg.status, msg.error = "failed", str(e)
        notify(session, f"A message to {msg.recipient} wasn't sent: {e}", kind="warning", link=f"/outbox/{msg.id}")
        return
    _sent(session, msg)


def waiting(session):
    return session.scalars(select(Message).where(Message.direction == "out",
                                                 Message.status.in_(("draft", "failed", "approved")))
                           .order_by(Message.id.desc())).all()
