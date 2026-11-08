"""Hardening for the web app (Phase 5): safe response headers and limits on how fast the public doors can be tried.

The limits are kept in memory in each web process: enough to stop someone guessing upload links or sign-in links,
or hammering the sign-in page, without needing anything else running.
"""

import threading
import time
from collections import defaultdict, deque

from flask import render_template, request

# endpoint: (requests, per seconds, which methods count)
LIMITS = {
    "portal.login": (10, 600, ("POST",)),
    "public.signup": (6, 600, ("POST",)),
    "portal.link": (30, 600, ("GET", "POST")),
    "auth.google_sign_in": (20, 600, ("POST",)),
    "auth.dev_sign_in": (20, 600, ("POST",)),
    "creatives.client_upload": (60, 600, ("GET", "POST")),
    "webhooks.whatsapp_receive": (600, 60, ("POST",)),
    "webhooks.whatsapp_verify": (60, 60, ("GET",)),
}

_hits = defaultdict(deque)
_lock = threading.Lock()


def allowed(key, limit, per, now=None):
    now = now if now is not None else time.monotonic()
    with _lock:
        q = _hits[key]
        while q and q[0] <= now - per:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset():
    with _lock:
        _hits.clear()


def check_rate():
    rule = LIMITS.get(request.endpoint or "")
    if rule is None or request.method not in rule[2]:
        return None
    if not allowed(f"{request.endpoint}:{request.remote_addr}", rule[0], rule[1]):
        return render_template("error.html", code=429,
                               message="Too many tries from your connection. Wait a few minutes and try again."), 429
    return None


def headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
    # Sign-in links and upload links carry their token in the address: never send it to another site.
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if (request.endpoint or "").startswith(("portal.", "creatives.client_upload")) or request.path.startswith("/upload/"):
        resp.headers.setdefault("Cache-Control", "private, no-store")
    return resp


def init(app):
    app.before_request_funcs.setdefault(None, []).insert(0, check_rate)
    app.after_request(headers)
