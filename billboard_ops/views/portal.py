"""The client portal (Phase 4): clients sign in with a one-time link and see only their own company's bookings.

They can follow each booking, accept a hold, upload files and see the check results in plain words, approve the
play plan and the final preview, see proof that it played, and send a new request. Every lookup is limited to the
signed-in client's own records; anything else is "not found".

The team side lives here too: giving a client's contacts access, and sending or copying a sign-in link.
"""

import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file,
                   session, url_for)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import creative_store, creatives, drafts, intake, plans, settings
from ..activity import log
from ..mail import queue as queue_email
from ..models import (BOOKED, FILE_STAGES, Booking, BookingDocument, BookingLine, Client, Creative, LoginLink,
                      Proof, User, utcnow)
from ..notices import alert
from ..permissions import can
from ..portal_text import tp
from ..web import current_user, f_int, f_str, get_db, login_required

bp = Blueprint("portal", __name__)

LINK_MINUTES = 30
TEAM_LINK_HOURS = 24
LINKS_PER_HOUR = 5


# ── Sign-in links ───────────────────────────────────────────────────────────────

def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def make_link(s, user, minutes=LINK_MINUTES, created_by="client"):
    token = secrets.token_urlsafe(32)
    s.add(LoginLink(user_id=user.id, token_hash=_hash(token), expires_at=utcnow() + timedelta(minutes=minutes),
                    created_by=created_by))
    base = current_app.config["APP"].APP_URL or request.host_url.rstrip("/")
    return f"{base}{url_for('portal.link', token=token)}"


def _valid_link(s, token):
    if not token or len(token) > 100:
        return None
    row = s.scalars(select(LoginLink).where(LoginLink.token_hash == _hash(token))).first()
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        return None
    u = row.user
    if u is None or not u.active or u.role != "client" or not u.client_id:
        return None
    return row


def send_link(s, user, created_by="client"):
    url = make_link(s, user, LINK_MINUTES, created_by)
    subject, body = drafts.sign_in_link(user.language, settings.get(s, "company_name"), url, LINK_MINUTES)
    queue_email(s, user.email, subject, body)
    return url


# ── Who is signed in ────────────────────────────────────────────────────────────

def client_user():
    u = g.get("user")
    if u is not None and u.role == "client" and u.client_id and u.active:
        return u
    return None


def portal_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if client_user() is None:
            return redirect(url_for("portal.login"))
        return fn(*a, **kw)
    return wrapper


def _lang():
    u = client_user()
    return (u.language if u is not None else session.get("portal_lang")) or "en"


@bp.app_context_processor
def _portal_globals():
    if not (request.endpoint or "").startswith("portal."):
        return {}
    lang = _lang()
    return {"tp": lambda k, **kw: tp(k, lang, **kw), "plang": lang,
            "company": settings.get(get_db(), "company_name")}


def _mine(s, code):
    """A booking of the signed-in client, or 404."""
    u = client_user()
    b = s.scalars(select(Booking).options(selectinload(Booking.lines).selectinload(BookingLine.screen))
                  .where(Booking.code == code, Booking.client_id == u.client_id)).first()
    if b is None or b.status == "enquiry":
        abort(404)
    return b


def _my_creative(s, cid):
    c = s.get(Creative, cid)
    if c is None or c.booking.client_id != client_user().client_id:
        abort(404)
    return c


# ── Signing in and out ──────────────────────────────────────────────────────────

@bp.route("/portal/login", methods=["GET", "POST"])
def login():
    s = get_db()
    if client_user() is not None:
        return redirect(url_for("portal.home"))
    if request.method == "POST":
        email = f_str("email").lower()
        u = s.scalars(select(User).where(func.lower(User.email) == email, User.role == "client",
                                         User.active.is_(True))).first() if "@" in email else None
        if u is not None:
            recent = s.scalar(select(func.count()).select_from(LoginLink).where(
                LoginLink.user_id == u.id, LoginLink.created_by == "client",
                LoginLink.created_at > utcnow() - timedelta(hours=1))) or 0
            if recent >= LINKS_PER_HOUR:
                flash(tp("too_many", _lang()), "error")
                return render_template("portal_login.html", sent=False), 429
            send_link(s, u)
            log(s, email, "asked_link", "user", email, "Asked for a portal sign-in link")
            s.commit()
        return render_template("portal_login.html", sent=True)
    return render_template("portal_login.html", sent=False)


@bp.route("/portal/link/<token>", methods=["GET", "POST"])
def link(token):
    """GET shows a button; POST signs in. Mail scanners that open links in advance can't use up the link."""
    s = get_db()
    row = _valid_link(s, token)
    if row is None:
        return render_template("portal_link.html", bad=True), 410
    if request.method == "POST":
        row.used_at = utcnow()
        u = row.user
        u.last_login_at = utcnow()
        log(s, u.email, "signed_in", "user", u.email, "Signed in to the client portal")
        s.commit()
        session.clear()
        session["uid"] = u.id
        session.permanent = True
        return redirect(url_for("portal.home"))
    return render_template("portal_link.html", bad=False, email=row.user.email)


@bp.route("/portal/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("portal.login"))


@bp.route("/portal/language", methods=["POST"])
def language():
    u = client_user()
    new = "en" if _lang() == "ar" else "ar"
    if u is not None:
        u.language = new
        get_db().commit()
    else:
        session["portal_lang"] = new
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/portal") else url_for("portal.home"))


# ── Bookings ────────────────────────────────────────────────────────────────────

def next_step(s, b, lang):
    if b.status == "hold":
        return tp("ns_accepted" if b.client_accepted_at else "ns_accept", lang)
    if b.plan_status == "waiting_client" and b.status not in ("completed", "cancelled", "expired"):
        return tp("ns_plan", lang)
    if b.status in FILE_STAGES:
        cov = creatives.coverage(s, b)
        if any(x["state"] == "waiting_client" for x in cov):
            return tp("ns_preview", lang)
    if b.status in ("confirmed", "awaiting_creative"):
        return tp("ns_upload", lang, date=drafts._d(b.creative_deadline, lang)) if b.creative_deadline \
            else tp("ns_upload_any", lang)
    if b.status == "changes_requested":
        return tp("ns_fix", lang)
    if b.status == "checking":
        return tp("ns_checking", lang)
    if b.status in ("creative_approved", "plan_set", "scheduled"):
        return tp("ns_ready", lang)
    if b.status == "live":
        return tp("ns_live", lang)
    if b.status == "completed":
        return tp("ns_done", lang)
    return tp("ns_none", lang)


@bp.route("/portal")
@portal_required
def home():
    s = get_db()
    u = client_user()
    lang = _lang()
    rows = s.scalars(select(Booking).options(selectinload(Booking.lines).selectinload(BookingLine.screen))
                     .where(Booking.client_id == u.client_id, Booking.status != "enquiry")
                     .order_by(Booking.id.desc())).all()
    current = [b for b in rows if b.status in ("hold",) + BOOKED]
    past = [b for b in rows if b not in current]
    current.sort(key=lambda b: b.start_date or b.created_at.date())
    return render_template("portal_home.html", current=[(b, next_step(s, b, lang)) for b in current], past=past,
                           client=s.get(Client, u.client_id))


def _file_rows(s, b, lang):
    """The client's files, newest first, with what the checks found in plain words."""
    need_client = settings.get(s, "client_approves_final")
    out = []
    files = s.scalars(select(Creative).where(Creative.booking_id == b.id).order_by(Creative.version.desc())).all()
    codes = {l.id: l.screen.code for l in b.lines}
    for c in files:
        state = {"received": "f_checking", "checking": "f_checking", "passed": "f_passed", "warnings": "f_warnings",
                 "failed": "f_failed", "error": "f_error", "approved": "f_approved", "rejected": "f_rejected",
                 "superseded": "f_superseded"}.get(c.status, "f_checking")
        waiting = c.status == "approved" and need_client and c.client_approved_at is None
        if waiting:
            state = "f_waiting"
        fixes = [drafts.fix_sentence(lang, x, b.client.name) for x in creatives.failures(c)] \
            if c.status == "failed" else []
        out.append({"c": c, "state": state, "waiting": waiting, "fixes": fixes,
                    "screens": ", ".join(codes[i] for i in (c.line_ids or []) if i in codes),
                    "playable": c.status not in ("received", "checking") or c.data is not None})
    return out


@bp.route("/portal/bookings/<code>")
@portal_required
def booking(code):
    s = get_db()
    b = _mine(s, code)
    lang = _lang()
    rows = plans.evaluate(s, b) if b.plan_status != "none" and b.status not in ("hold",) else []
    proofs = s.scalars(select(Proof).where(Proof.booking_id == b.id, Proof.shared.is_(True))
                       .order_by(Proof.id.desc())).all()
    return render_template("portal_booking.html", b=b, next=next_step(s, b, lang), files=_file_rows(s, b, lang),
                           targets=creatives.targets(b), plan_rows=rows, describe=plans.describe, proofs=proofs,
                           can_upload=b.status in FILE_STAGES, lang=lang, fmt=lambda d: drafts._d(d, lang))


@bp.route("/portal/bookings/<code>/accept", methods=["POST"])
@portal_required
def accept(code):
    s = get_db()
    b = _mine(s, code)
    u = client_user()
    if b.status != "hold":
        abort(404)
    if b.client_accepted_at is None:
        b.client_accepted_at = utcnow()
        doc = request.files.get("document")
        if doc and doc.filename:
            data = doc.read()
            if data:
                s.add(BookingDocument(booking_id=b.id, filename=doc.filename[:300], content_type=(doc.mimetype or "")[:120],
                                      data=data, uploaded_by=u.email))
        log(s, u.email, "client_accepted", "booking", b.code,
            "The client accepted the hold in the portal" + (" and sent a document" if doc and doc.filename else ""))
        alert(s, "client", f"{b.client.name} accepted hold {b.code} in the portal. Confirm the booking.",
              link=f"/bookings/{b.code}", subject=f"{b.client.name} accepted hold {b.code}")
        s.commit()
    flash(tp("done_thanks", _lang()), "ok")
    return redirect(url_for("portal.booking", code=code))


@bp.route("/portal/bookings/<code>/upload", methods=["POST"])
@portal_required
def upload(code):
    s = get_db()
    b = _mine(s, code)
    if b.status not in FILE_STAGES:
        abort(404)
    from .creatives import _store_uploads
    made, refused = _store_uploads(s, b, request.files.getlist("files"), "portal", client_user().email)
    s.commit()
    if made:
        flash(tp("uploaded", _lang()), "ok")
    if refused:
        flash(tp("not_taken", _lang(), list=", ".join(refused)), "error")
    return redirect(url_for("portal.booking", code=code) + "#files")


@bp.route("/portal/bookings/<code>/plan", methods=["POST"])
@portal_required
def plan_decision(code):
    s = get_db()
    b = _mine(s, code)
    try:
        plans.client_decide(s, client_user(), b, approve=f_str("decision") == "approve", note=f_str("note"))
    except plans.PlanError as e:
        s.rollback()
        flash(str(e) if _lang() == "en" else tp("what_change", "ar"), "error")
        return redirect(url_for("portal.booking", code=code) + "#plan")
    s.commit()
    flash(tp("done_thanks", _lang()), "ok")
    return redirect(url_for("portal.booking", code=code) + "#plan")


@bp.route("/portal/files/<int:cid>/decide", methods=["POST"])
@portal_required
def file_decision(cid):
    s = get_db()
    c = _my_creative(s, cid)
    try:
        creatives.client_decide(s, client_user(), c, f_str("decision") == "approve", note=f_str("note"))
    except creatives.CreativeError as e:
        s.rollback()
        flash(str(e) if _lang() == "en" else tp("what_change", "ar"), "error")
        return redirect(url_for("portal.booking", code=c.booking.code) + "#files")
    s.commit()
    flash(tp("done_thanks", _lang()), "ok")
    return redirect(url_for("portal.booking", code=c.booking.code) + "#files")


@bp.route("/portal/files/<int:cid>")
@portal_required
def file(cid):
    s = get_db()
    c = _my_creative(s, cid)
    try:
        path = creative_store.preview_path(s, c)
    except Exception:  # noqa: BLE001
        abort(404)
    return send_file(path, mimetype=c.content_type or None, conditional=True, download_name=c.filename)


@bp.route("/portal/proof/<int:proof_id>")
@portal_required
def proof_file(proof_id):
    s = get_db()
    p = s.get(Proof, proof_id)
    if p is None or not p.shared or p.booking.client_id != client_user().client_id:
        abort(404)
    from .live import proof_response
    return proof_response(s, p)


@bp.route("/portal/help")
@portal_required
def help_page():
    from ..help_text import GUIDES
    return render_template("portal_help.html", g=GUIDES["client"], i=1 if _lang() == "ar" else 0)


# ── A new request ───────────────────────────────────────────────────────────────

@bp.route("/portal/request", methods=["GET", "POST"])
@portal_required
def new_request():
    s = get_db()
    u = client_user()
    lang = _lang()
    from ..models import Screen
    screens = s.scalars(select(Screen).where(Screen.active.is_(True)).order_by(Screen.area, Screen.code)).all()
    if request.method == "POST":
        campaign, wanted = f_str("campaign"), f_str("screens")
        picked = [x.code for x in screens if str(x.id) in request.form.getlist("screen_id")]
        if not (campaign or wanted or picked or f_str("notes")):
            flash(tp("req_empty", lang), "error")
            return render_template("portal_request.html", screens=screens, form=request.form), 400
        client = s.get(Client, u.client_id)
        lines = [f"Request from the client portal ({client.name}, {u.email})",
                 f"Campaign or brand: {campaign or '-'}",
                 f"Screens or areas: {', '.join(picked + ([wanted] if wanted else [])) or '-'}",
                 f"Dates: {f_str('start') or '?'} to {f_str('end') or '?'}",
                 f"How often: {f_str('frequency') or '-'}"]
        if f_str("notes"):
            lines.append(f"Notes: {f_str('notes')}")
        atts = [{"filename": f.filename, "content_type": f.mimetype or "", "data": f.read()}
                for f in request.files.getlist("files") if f and f.filename]
        p, _, _ = intake.receive(s, channel="portal", sender=u.email, sender_name=u.name or client.contact_name,
                                 subject=f"Portal request: {campaign or client.name}"[:300], body="\n".join(lines),
                                 attachments=atts, actor=u.email)
        p.client_id = client.id
        if lang == "ar":
            p.language = "ar"
        alert(s, "proposals", f"New request {p.ref} from {client.name} in the portal.", link=f"/inbox/{p.ref}",
              subject=f"New portal request from {client.name}")
        s.commit()
        flash(tp("req_sent", lang, ref=p.ref), "ok")
        return redirect(url_for("portal.home"))
    return render_template("portal_request.html", screens=screens, form={})


# ── The team: who has access ────────────────────────────────────────────────────

def access_panel(s, client):
    return s.scalars(select(User).where(User.client_id == client.id, User.role == "client")
                     .order_by(User.email)).all()


@bp.route("/clients/<int:client_id>/portal", methods=["POST"])
@login_required
def manage_access(client_id):
    s = get_db()
    client = s.get(Client, client_id) or abort(404)
    me = current_user()
    if not can(me, "portal_access"):
        abort(403)
    action = f_str("action")
    back = redirect(url_for("bookings.client_edit", client_id=client.id) + "#portal")
    if action == "add":
        email = f_str("email").lower()
        if "@" not in email:
            flash("Give an email address.", "error")
            return back
        u = s.scalars(select(User).where(func.lower(User.email) == email)).first()
        if u is not None and (u.role != "client" or u.client_id not in (None, client.id)):
            flash(f"{email} already belongs to someone else in the app.", "error")
            return back
        if u is None:
            u = User(email=email, role="client")
            s.add(u)
        u.client_id, u.active, u.role = client.id, True, "client"
        u.name = f_str("name") or u.name
        u.language = "ar" if f_str("language") == "ar" else "en"
        s.flush()
        log(s, me.email, "portal_access", "client", client.name, f"Gave {email} access to the portal")
        msg = f"{email} can now sign in to the portal."
        if request.form.get("send") == "1":
            send_link(s, u, created_by=me.email)
            msg += " A sign-in link is on its way to them."
        s.commit()
        flash(msg, "ok")
        return back
    u = s.get(User, f_int("user_id") or 0)
    if u is None or u.client_id != client.id or u.role != "client":
        abort(404)
    if action == "send":
        send_link(s, u, created_by=me.email)
        log(s, me.email, "sent_link", "user", u.email, "Sent a portal sign-in link")
        flash(f"A sign-in link is on its way to {u.email} (it works once, for {LINK_MINUTES} minutes).", "ok")
    elif action == "copy":
        url = make_link(s, u, TEAM_LINK_HOURS * 60, created_by=me.email)
        log(s, me.email, "copied_link", "user", u.email, "Made a portal sign-in link to send by hand")
        flash(f"Sign-in link for {u.email}, valid once for {TEAM_LINK_HOURS} hours: {url}  "
              "(open it in a private window if you want to try it yourself)", "ok")
    elif action == "remove":
        u.active = False
        log(s, me.email, "portal_access", "client", client.name, f"Removed {u.email}'s portal access")
        flash(f"{u.email} can no longer sign in.", "ok")
    elif action == "restore":
        u.active = True
        log(s, me.email, "portal_access", "client", client.name, f"Restored {u.email}'s portal access")
        flash(f"{u.email} can sign in again.", "ok")
    else:
        abort(404)
    s.commit()
    return back
