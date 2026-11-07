"""Helpers every page uses: the request's database session, the signed-in person, permission checks, CSRF."""

import secrets
from datetime import date
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

from . import db
from .permissions import can


def get_db():
    if "db" not in g:
        g.db = db.new_session()
    return g.db


def close_db(_exc=None):
    s = g.pop("db", None)
    if s is not None:
        s.close()


def current_user():
    return g.get("user")


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u = current_user()
        if u is None:
            session["next_url"] = request.full_path if request.method == "GET" else None
            return redirect(url_for("auth.login"))
        if u.role == "client":
            return redirect(url_for("auth.portal_soon"))
        return fn(*a, **kw)
    return wrapper


def needs(capability):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if not can(current_user(), capability):
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


# ── CSRF: one token per session, sent with every form and fetch ─────────────────

def csrf_token():
    tok = session.get("csrf")
    if not tok:
        tok = session["csrf"] = secrets.token_urlsafe(24)
    return tok


def check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(400, "This form has expired. Go back, reload the page and try again.")


# ── Reading form values ─────────────────────────────────────────────────────────

def f_str(name, default=""):
    return (request.form.get(name, default) or "").strip()


def f_int(name, default=None):
    v = (request.form.get(name) or "").strip()
    if v == "":
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


def f_float(name, default=None):
    v = (request.form.get(name) or "").strip()
    if v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def f_bool(name):
    return request.form.get(name) in ("1", "on", "true", "yes")


def f_date(name):
    v = (request.form.get(name) or "").strip()
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def a_date(name, default=None):
    v = (request.args.get(name) or "").strip()
    try:
        return date.fromisoformat(v) if v else default
    except ValueError:
        return default


def done(message, endpoint, **kw):
    flash(message, "ok")
    return redirect(url_for(endpoint, **kw))
