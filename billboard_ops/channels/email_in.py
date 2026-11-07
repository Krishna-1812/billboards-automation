"""The proposals mailbox, read over IMAP every minute.

Each new email (and its attachments) goes into the inbox; replies join the proposal they answer, found by the email
headers or, failing that, by sender and subject. Our own messages and automatic replies (out-of-office, bounces)
are skipped, so the app never talks to itself. Messages are marked as read in the mailbox once stored.
"""

import email
import imaplib
import logging
import re
from email import policy
from datetime import timezone
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from .. import runtime
from ..jobs import Skip, handler

log = logging.getLogger(__name__)

MAX_PER_POLL = 50
_imap_factory = None


def set_imap_factory(factory):
    """Tests: a callable (host, port) -> an object with the imaplib.IMAP4_SSL methods used here."""
    global _imap_factory
    _imap_factory = factory


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")
        if tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html):
    p = _Text()
    p.feed(html or "")
    text = "".join(p.parts)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


REPLY_MARKERS = [r"^On .{5,200} wrote:\s*$", r"^-{2,}\s*Original Message\s*-{2,}", r"^From: .+\n(Sent|Date): ",
                 r"^في .{5,200} كتب.*:\s*$", r"^_{10,}\s*$"]


def strip_quoted(text):
    """Cut the quoted earlier conversation from a reply (it's already stored as earlier messages)."""
    for pat in REPLY_MARKERS:
        m = re.search(pat, text, flags=re.M)
        if m and m.start() > 20:
            text = text[:m.start()]
    return "\n".join(l for l in text.rstrip().splitlines() if not l.startswith(">")).strip()


def parse(raw):
    """A dict of what we keep from one raw email."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    name, addr = (getaddresses([msg.get("From", "")]) or [("", "")])[0]
    body, html, atts = "", "", []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disp = (part.get_content_disposition() or "").lower()
        ctype = part.get_content_type()
        if filename or disp == "attachment" or (disp == "inline" and not ctype.startswith("text/")):
            data = part.get_payload(decode=True) or b""
            atts.append({"filename": filename or f"attachment.{ctype.split('/')[-1]}", "content_type": ctype,
                         "data": data})
        elif ctype == "text/plain" and not body:
            body = part.get_content()
        elif ctype == "text/html" and not html:
            html = part.get_content()
    text = body or html_to_text(html)
    refs = (msg.get("References", "") or "").split()
    reply_ids = [x for x in [msg.get("In-Reply-To", "").strip()] + list(reversed(refs)) if x]
    try:
        when = (parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).replace(tzinfo=None)
                if msg.get("Date") else None)
    except (TypeError, ValueError):
        when = None
    auto = (msg.get("Auto-Submitted", "no").lower() != "no" or msg.get("X-Autoreply") or
            msg.get("Precedence", "").lower() in ("bulk", "auto_reply", "junk") or
            "mailer-daemon" in addr.lower())
    return {"from": addr.lower(), "name": name, "subject": str(msg.get("Subject", "") or ""),
            "body": strip_quoted(text or ""), "attachments": atts, "message_id": msg.get("Message-ID", "").strip(),
            "reply_ids": reply_ids, "date": when, "auto": bool(auto)}


def store(session, raw):
    """Put one raw email into the inbox. Returns the proposal, or None when it was skipped."""
    from ..intake import receive
    e = parse(raw)
    own = (runtime.config.MAIL_FROM or runtime.config.IMAP_USER or "").lower() if runtime.config else ""
    if e["auto"] or (own and own in e["from"]):
        return None
    p, _, _ = receive(session, channel="email", sender=e["from"], sender_name=e["name"], subject=e["subject"],
                      body=e["body"], attachments=e["attachments"], external_id=e["message_id"],
                      reply_ids=e["reply_ids"], received_at=e["date"])
    return p


def _connect(cfg):
    if _imap_factory is not None:
        return _imap_factory(cfg.IMAP_HOST, cfg.IMAP_PORT)
    return imaplib.IMAP4_SSL(cfg.IMAP_HOST, cfg.IMAP_PORT, timeout=30)


@handler("poll_email")
def poll(session, payload=None):
    cfg = runtime.config
    if not cfg or not cfg.email_in:
        raise Skip("The proposals mailbox isn't set up: IMAP_HOST, IMAP_USER and IMAP_PASSWORD.")
    box = _connect(cfg)
    stored = 0
    try:
        box.login(cfg.IMAP_USER, cfg.IMAP_PASSWORD)
        box.select(cfg.IMAP_FOLDER)
        typ, data = box.uid("search", None, "UNSEEN")
        uids = (data[0] or b"").split()[:MAX_PER_POLL]
        done = []
        for uid in uids:
            typ, parts = box.uid("fetch", uid, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw is None:
                continue
            store(session, raw)
            done.append(uid)
        # Saved first, then marked as read: a crash in between means a re-read, which the Message-ID check
        # turns into a no-op, never a lost email.
        session.commit()
        for uid in done:
            box.uid("store", uid, "+FLAGS", "(\\Seen)")
        stored = len(done)
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass
    return stored
