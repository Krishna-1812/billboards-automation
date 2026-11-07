"""Calls from outside services. Not signed in and not CSRF-checked: each is checked by the sender's own proof."""

import hmac
import logging

from flask import Blueprint, current_app, jsonify, request

from ..channels import whatsapp
from ..web import get_db

bp = Blueprint("webhooks", __name__)
log = logging.getLogger(__name__)


@bp.route("/webhooks/whatsapp", methods=["GET"])
def whatsapp_verify():
    """Meta's one-time check when the webhook is set up: echo the challenge if the verify token matches."""
    cfg = current_app.config["APP"]
    token = request.args.get("hub.verify_token", "")
    if (request.args.get("hub.mode") == "subscribe" and cfg.WHATSAPP_VERIFY_TOKEN
            and hmac.compare_digest(token, cfg.WHATSAPP_VERIFY_TOKEN)):
        return request.args.get("hub.challenge", ""), 200
    return "Forbidden", 403


@bp.route("/webhooks/whatsapp", methods=["POST"])
def whatsapp_receive():
    cfg = current_app.config["APP"]
    raw = request.get_data()
    if not whatsapp.signature_ok(raw, request.headers.get("X-Hub-Signature-256", ""), cfg.WHATSAPP_APP_SECRET):
        log.warning("WhatsApp webhook with a bad or missing signature was refused")
        return "Forbidden", 403
    s = get_db()
    n = whatsapp.receive_webhook(s, request.get_json(silent=True) or {})
    s.commit()
    return jsonify(ok=True, stored=n)
