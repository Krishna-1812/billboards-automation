"""Billboard Ops: bookings, creative checks and the owner's dashboard for a digital-screen network.

`create_app()` builds the web app (gunicorn "billboard_ops:create_app()"); `python -m billboard_ops.worker` runs the
background worker.
"""

import logging
import time
from datetime import timedelta

from flask import Flask, g, render_template, request, session

from . import db, runtime
from .config import Config
from .i18n import direction, t
from .models import STATUSES, Base, User
from .permissions import can
from .web import check_csrf, close_db, csrf_token, get_db

log = logging.getLogger(__name__)


def create_app(config=None):
    cfg = config or Config()
    runtime.init(cfg)
    db.init_engine(cfg.DATABASE_URL)
    if cfg.DATABASE_URL.startswith("sqlite"):
        # Local and test databases are made on the spot; Postgres is built by the migrations (alembic upgrade head).
        Base.metadata.create_all(db.engine())
    if not cfg.SECRET_KEY:
        if cfg.TESTING or cfg.DEV_LOGIN or cfg.DATABASE_URL.startswith("sqlite"):
            cfg.SECRET_KEY = "dev-only-not-secret"
        else:
            cfg.SECRET_KEY, cfg.SECRET_KEY_STORED = derived_secret(cfg.DATABASE_URL), True
    if cfg.LOGIN_REQUIRED and not cfg.TESTING and not has_real_owner(cfg):
        # Switching sign-in on with nobody allowed in would lock everyone out: keep it off and say why.
        cfg.LOGIN_REQUIRED = False
        cfg.LOGIN_BLOCKED = True
        log.warning("Sign-in stays off: set OWNER_EMAILS to the owner's Google address first.")

    app = Flask(__name__)
    # Railway (like most hosts) serves the app behind one proxy that ends HTTPS: trust its X-Forwarded-Proto and
    # Host headers, so links the app builds (sign-in links, upload links) start with https:// and the right host.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        APP=cfg, SECRET_KEY=cfg.SECRET_KEY, TESTING=cfg.TESTING,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not (cfg.TESTING or cfg.DEV_LOGIN),
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    )

    # Every job handler registers itself on import.
    from . import backup, creative_eval, creatives, evaluate, intake, loading, mail, outbox, plans, sync  # noqa: F401
    from .channels import email_in  # noqa: F401

    from .views import admin, auth, bookings, creatives as creative_views, imports, inbox, live, main, portal, \
        quotes, screens, webhooks
    for bp in (auth.bp, main.bp, bookings.bp, screens.bp, imports.bp, admin.bp, inbox.bp, webhooks.bp,
               creative_views.bp, live.bp, portal.bp, quotes.bp):
        app.register_blueprint(bp)

    app.teardown_appcontext(close_db)
    from . import security
    security.init(app)

    @app.before_request
    def _load_user():
        if request.endpoint in ("main.health", "main.health_full", "static"):
            return
        if request.endpoint and request.endpoint.startswith("webhooks."):
            return                          # signed by the sender instead (see views/webhooks.py)
        if request.endpoint in creative_views.BIG_UPLOAD_ENDPOINTS:
            request.max_content_length = creative_views.BIG_UPLOAD    # video files; every other form stays at 20 MB
        check_csrf()
        g.user = None
        uid = session.get("uid")
        if uid:
            u = get_db().get(User, uid)
            if u is not None and u.active:
                g.user = u
            else:
                session.clear()
        if g.user is None and not cfg.LOGIN_REQUIRED:
            g.user = open_access_user(get_db(), cfg)
        g.lang = (g.user.language if g.user else None) or session.get("lang") or "en"

    @app.errorhandler(413)
    def _too_big(_e):
        return render_template("error.html", code=413, message="That file is too large to upload here."), 413

    @app.context_processor
    def _globals():
        lang = g.get("lang", "en")
        return {
            "t": lambda k: t(k, lang), "lang": lang, "dir": direction(lang), "user": g.get("user"),
            "can": lambda cap: can(g.get("user"), cap), "csrf_token": csrf_token, "STATUSES": STATUSES,
            "status_label": lambda s: t(f"status_{s}", lang),
            "login_required": cfg.LOGIN_REQUIRED,
            "login_blocked": cfg.LOGIN_BLOCKED,
        }

    from .views.filters import register as register_filters
    register_filters(app)

    @app.errorhandler(403)
    def _forbidden(_e):
        return render_template("error.html", code=403,
                               message="Your role doesn't allow this. Ask the owner if you need it."), 403

    @app.errorhandler(404)
    def _missing(_e):
        return render_template("error.html", code=404, message="That page doesn't exist."), 404

    @app.errorhandler(500)
    def _broken(e):
        _alert(cfg, e)
        return render_template("error.html", code=500,
                               message="Something went wrong. The team has been told."), 500

    return app


def derived_secret(database_url):
    """No SECRET_KEY was set: derive one from the database address, whose password is just as secret. Every
    process gets the same key without reading anything at start-up. Changing the database password, or setting
    SECRET_KEY on Railway, signs everyone out once."""
    import hashlib
    import hmac
    return hmac.new(database_url.encode(), b"billboard-ops session key", hashlib.sha256).hexdigest()


STAND_IN_OWNER = "owner@billboards.local"


def has_real_owner(cfg):
    """Someone can sign in as an owner: an address in OWNER_EMAILS, or an active owner added in Settings → People."""
    if cfg.OWNER_EMAILS:
        return True
    from sqlalchemy import func, select
    try:
        with db.session_scope() as s:
            return s.scalars(select(User.id).where(User.role == "owner", User.active.is_(True),
                                                   func.lower(User.email) != STAND_IN_OWNER)).first() is not None
    except Exception:  # noqa: BLE001 - the database isn't reachable yet: keep sign-in on rather than open the app
        log.warning("Couldn't check for an owner at start-up; leaving sign-in on.", exc_info=True)
        return True


def open_access_user(s, cfg):
    """While sign-in is off, everyone acts as this owner (the first of OWNER_EMAILS, or a stand-in address)."""
    from sqlalchemy import func, select
    email = (cfg.OWNER_EMAILS or [STAND_IN_OWNER])[0].strip().lower()
    u = s.scalars(select(User).where(func.lower(User.email) == email)).first()
    if u is None:
        u = User(email=email, role="owner", name="Owner")
        s.add(u)
        s.commit()
    elif u.role != "owner" or not u.active:
        u.role, u.active = "owner", True
        s.commit()
    return u


_last_alert = {}


def _alert(cfg, err):
    """Email the alert list about an error, at most once every 10 minutes per kind of error."""
    if not cfg.ALERT_EMAILS:
        return
    key = type(getattr(err, "original_exception", err)).__name__
    if time.time() - _last_alert.get(key, 0) < 600:
        return
    _last_alert[key] = time.time()
    try:
        from .mail import queue
        with db.session_scope() as s:
            queue(s, cfg.ALERT_EMAILS, f"Billboard Ops error: {key}",
                  f"{request.method} {request.path}\n\n{getattr(err, 'original_exception', err)!r}")
    except Exception:  # noqa: BLE001 - an alert must never cause a second error
        log.exception("could not queue the error alert")
