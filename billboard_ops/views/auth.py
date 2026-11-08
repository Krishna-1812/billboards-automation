"""Signing in.

The team signs in with Google: the browser gets a signed ID token from Google, and we check its signature against
our client ID. Only people an owner has added (Settings → People), plus the first owners named in OWNER_EMAILS,
are let in. Clients sign in to their portal with a one-time emailed link instead (views/portal.py).
"""

import logging

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, select

from ..activity import log
from ..models import User, utcnow
from ..web import current_user, f_str, get_db

bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)


def _verify_google(credential, client_id):
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token
    return id_token.verify_oauth2_token(credential, greq.Request(), client_id)


def find_or_bootstrap(s, email):
    """The active user with this email. An address in OWNER_EMAILS is made an owner on first sign-in."""
    email = (email or "").strip().lower()
    if not email:
        return None
    user = s.scalars(select(User).where(func.lower(User.email) == email)).first()
    cfg = current_app.config["APP"]
    if user is None and email in cfg.OWNER_EMAILS:
        user = User(email=email, role="owner", name=email.split("@")[0])
        s.add(user)
        log(s, email, "created", "user", email, "First owner, from OWNER_EMAILS")
    if user is None or not user.active:
        return None
    return user


def _sign_in(s, user, name=""):
    if name and not user.name:
        user.name = name
    user.last_login_at = utcnow()
    s.commit()
    nxt = session.get("next_url")
    session.clear()
    if nxt:
        session["next_url"] = nxt
    session["uid"] = user.id
    session.permanent = True


@bp.route("/login")
def login():
    if current_user() is not None:
        return redirect(url_for("main.dashboard"))
    cfg = current_app.config["APP"]
    return render_template("login.html", google_client_id=cfg.GOOGLE_CLIENT_ID, dev_login=cfg.DEV_LOGIN)


@bp.route("/auth/google", methods=["POST"])
def google_sign_in():
    cfg = current_app.config["APP"]
    if not cfg.GOOGLE_CLIENT_ID:
        return jsonify(ok=False, error="Google sign-in isn't set up (GOOGLE_CLIENT_ID)."), 503
    credential = (request.get_json(silent=True) or {}).get("credential", "")
    try:
        info = _verify_google(credential, cfg.GOOGLE_CLIENT_ID)
    except Exception as e:  # noqa: BLE001 - any bad token is a refusal
        logger.info("google sign-in refused: %s", e)
        return jsonify(ok=False, error="Google couldn't confirm this sign-in. Try again."), 401
    if info.get("email_verified") is False:
        return jsonify(ok=False, error="Google hasn't verified this email address."), 403
    s = get_db()
    user = find_or_bootstrap(s, info.get("email"))
    if user is None:
        return jsonify(ok=False, error=f"{info.get('email')} doesn't have access. Ask the owner to add you."), 403
    _sign_in(s, user, info.get("name", ""))
    return jsonify(ok=True, next=_next())


@bp.route("/auth/dev", methods=["POST"])
def dev_sign_in():
    """Local development only: refused whenever Google sign-in is configured."""
    if not current_app.config["APP"].DEV_LOGIN:
        return ("Not found", 404)
    s = get_db()
    user = find_or_bootstrap(s, f_str("email"))
    if user is None:
        flash("That address doesn't have access.", "error")
        return redirect(url_for("auth.login"))
    _sign_in(s, user)
    return redirect(_next())


def _next():
    nxt = session.pop("next_url", None)
    if isinstance(nxt, str) and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return url_for("main.dashboard")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

