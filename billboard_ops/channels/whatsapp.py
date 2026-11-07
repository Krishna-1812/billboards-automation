"""WhatsApp Business Cloud API (Meta).

In: Meta calls our webhook (/webhooks/whatsapp) with each message. We check the signature, store the message, and
answer at once; any photo or PDF is downloaded later by the worker, before the proposal is read.
Out: an approved draft is sent as a text message, as a reply to the client's last message.

WhatsApp only allows free-form messages within 24 hours of the client's last message. Outside that window a
message needs a template approved by Meta; until templates are set up, such a message is not sent and the team is
told to send it by email instead.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import runtime
from ..models import Message, utcnow

log = logging.getLogger(__name__)

WINDOW = timedelta(hours=24)
MEDIA_TYPES = ("image", "document", "audio", "video", "sticker")
_http = None


def set_http(fake):
    """Tests: a fake with .get(url, headers=..., timeout=...) and .post(url, json=..., headers=..., timeout=...)."""
    global _http
    _http = fake


def http():
    if _http is not None:
        return _http
    import requests
    return requests


def _base():
    return f"https://graph.facebook.com/{runtime.config.WHATSAPP_API_VERSION}"


def _auth():
    return {"Authorization": f"Bearer {runtime.config.WHATSAPP_TOKEN}"}


def signature_ok(raw_body, header, app_secret):
    """Meta signs each webhook call with the app secret (X-Hub-Signature-256: sha256=<hex>)."""
    if not app_secret:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def parse(payload):
    """The messages and delivery statuses in one webhook call, as plain dicts."""
    out = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
            for m in value.get("messages") or []:
                kind = m.get("type", "")
                item = {"kind": "message", "from": "+" + m.get("from", "").lstrip("+"), "name": names.get(m.get("from"), ""),
                        "id": m.get("id", ""), "timestamp": int(m.get("timestamp") or 0), "type": kind,
                        "text": "", "media": None, "context_id": (m.get("context") or {}).get("id", "")}
                if kind == "text":
                    item["text"] = (m.get("text") or {}).get("body", "")
                elif kind in MEDIA_TYPES:
                    media = m.get(kind) or {}
                    item["text"] = media.get("caption", "")
                    item["media"] = {"id": media.get("id", ""), "mime_type": media.get("mime_type", ""),
                                     "filename": media.get("filename") or f"{kind}-{m.get('id', '')[-8:]}"}
                elif kind == "interactive":
                    i = m.get("interactive") or {}
                    item["text"] = ((i.get("button_reply") or i.get("list_reply") or {}).get("title", ""))
                elif kind == "button":
                    item["text"] = (m.get("button") or {}).get("text", "")
                else:
                    item["text"] = f"[a {kind} message the app can't read]"
                out.append(item)
            for st in value.get("statuses") or []:
                out.append({"kind": "status", "id": st.get("id", ""), "status": st.get("status", ""),
                            "error": "; ".join(e.get("title", "") for e in st.get("errors") or [])})
    return out


def receive_webhook(session, payload):
    """Store what a webhook call carries. Returns how many messages were new."""
    from ..intake import receive
    new = 0
    for item in parse(payload):
        if item["kind"] == "status":
            m = session.scalars(select(Message).where(Message.external_id == item["id"])).first()
            if m is not None and item["status"] == "failed":
                m.status, m.error = "failed", item["error"] or "WhatsApp couldn't deliver it."
            continue
        when = datetime.fromtimestamp(item["timestamp"], timezone.utc).replace(tzinfo=None) if item["timestamp"] \
            else None
        atts = []
        if item["media"]:
            atts.append({"filename": item["media"]["filename"], "content_type": item["media"]["mime_type"],
                         "external_ref": item["media"]["id"], "data": None})
        p, m, _ = receive(session, channel="whatsapp", sender=item["from"], sender_name=item["name"],
                          body=item["text"], attachments=atts, external_id=item["id"],
                          reply_ids=[item["context_id"]] if item["context_id"] else [], received_at=when)
        new += 1
    return new


def fetch_pending_media(session, proposal):
    """Download photos and documents sent on WhatsApp that haven't been fetched yet."""
    for m in proposal.messages:
        for a in m.attachments:
            if a.data or not a.external_ref:
                continue
            if not runtime.config or not runtime.config.whatsapp:
                continue
            try:
                meta = http().get(f"{_base()}/{a.external_ref}", headers=_auth(), timeout=30)
                meta.raise_for_status()
                info = meta.json()
                blob = http().get(info["url"], headers=_auth(), timeout=60)
                blob.raise_for_status()
                a.data = blob.content
                a.size = len(a.data)
                a.content_type = info.get("mime_type") or a.content_type
            except Exception as e:  # noqa: BLE001 - read without it, and say so
                log.warning("couldn't download WhatsApp media %s: %s", a.external_ref, e)


def last_inbound_at(session, phone):
    m = session.scalars(select(Message).where(Message.channel == "whatsapp", Message.direction == "in",
                                              Message.sender == phone).order_by(Message.created_at.desc())).first()
    return m.created_at if m else None


class OutsideWindow(Exception):
    pass


def send_text(session, to, body, reply_to=""):
    """Send a text message; returns its WhatsApp id."""
    last = last_inbound_at(session, to)
    if last is None or utcnow() - last > WINDOW:
        raise OutsideWindow("The client hasn't written on WhatsApp in the last 24 hours, so WhatsApp only allows "
                            "an approved template. Send this one by email instead.")
    data = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to.lstrip("+"),
            "type": "text", "text": {"preview_url": False, "body": body[:4096]}}
    if reply_to:
        data["context"] = {"message_id": reply_to}
    r = http().post(f"{_base()}/{runtime.config.WHATSAPP_PHONE_NUMBER_ID}/messages", json=data,
                    headers=_auth(), timeout=30)
    if getattr(r, "status_code", 200) >= 400:
        raise RuntimeError(f"WhatsApp refused the message ({r.status_code}): {getattr(r, 'text', '')[:300]}")
    return (r.json().get("messages") or [{}])[0].get("id", "")
