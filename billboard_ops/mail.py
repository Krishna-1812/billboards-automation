"""Email: hold reminders and error alerts. Sent by the worker through SMTP; logged when SMTP isn't set up."""

import logging
import smtplib
from email.message import EmailMessage

from . import runtime
from .jobs import Skip, enqueue, handler

log = logging.getLogger(__name__)
_sent = []          # tests read what would have gone out


def queue(session, to, subject, body):
    to = [t for t in ([to] if isinstance(to, str) else to) if t]
    if to:
        enqueue(session, "send_email", {"to": to, "subject": subject, "body": body})


@handler("send_email")
def _send(session, payload):
    cfg = runtime.config
    if cfg is None or not cfg.SMTP_HOST:
        _sent.append(payload)
        log.info("email not sent (SMTP not set up): %s → %s", payload.get("subject"), payload.get("to"))
        raise Skip("SMTP isn't set up: set SMTP_HOST, SMTP_USER, SMTP_PASSWORD and MAIL_FROM.")
    msg = EmailMessage()
    msg["From"] = cfg.MAIL_FROM
    msg["To"] = ", ".join(payload["to"])
    msg["Subject"] = payload["subject"]
    msg.set_content(payload["body"])
    with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=20) as smtp:
        smtp.starttls()
        if cfg.SMTP_USER:
            smtp.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
        smtp.send_message(msg)
    _sent.append(payload)
