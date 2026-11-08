"""Creative: the files clients send, their check reports, approval, the client's upload link, the test set."""

from datetime import date

from flask import (Blueprint, Response, abort, flash, redirect, render_template, request, send_file,
                   url_for)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import creative_checks as cc
from .. import creative_store, creatives, settings
from ..activity import log
from ..google_client import folder_url
from ..jobs import enqueue
from ..models import (CREATIVE_STAGES, FILE_STAGES, Booking, BookingLine, Creative, CreativeCase, CreativeFrame, CreativeRun,
                      Spec)
from ..permissions import Forbidden, can
from ..web import current_user, done, f_int, f_str, get_db, login_required, needs

bp = Blueprint("creatives", __name__)

BIG_UPLOAD = 300 * 1024 * 1024
BIG_UPLOAD_ENDPOINTS = {"creatives.client_upload", "creatives.team_upload", "creatives.case_new"}


@bp.route("/creatives")
@login_required
def creative_list():
    s = get_db()
    today = date.today()
    rows = []
    for b in s.scalars(select(Booking).options(selectinload(Booking.lines).selectinload(BookingLine.screen))
                       .where(Booking.status.in_(CREATIVE_STAGES + ("creative_approved",)))
                       .order_by(Booking.id)).all():
        cov = creatives.coverage(s, b)
        rows.append({"b": b, "cov": cov, "start": b.start_date,
                     "risk": b.status != "creative_approved" and b.start_date and
                     creatives.working_days_before(b.start_date, int(settings.get(s, "creative_lead_days")),
                                                   creatives.weekend_days(s)) <= today})
    rows.sort(key=lambda r: (r["b"].status == "creative_approved", r["start"] or date.max))
    waiting = s.scalars(select(Creative).where(Creative.status.in_(("passed", "warnings")))
                        .order_by(Creative.checked_at)).all()
    return render_template("creatives.html", rows=rows, waiting=waiting, today=today)


def _creative(s, cid):
    c = s.get(Creative, cid)
    if c is None:
        abort(404)
    return c


@bp.route("/creatives/<int:cid>")
@login_required
def creative_page(cid):
    s = get_db()
    c = _creative(s, cid)
    b = c.booking
    versions = s.scalars(select(Creative).where(Creative.booking_id == b.id).order_by(Creative.version.desc())).all()
    spec = next((l.screen.spec for l in b.lines if l.id in (c.line_ids or []) and l.screen.spec), None)
    return render_template("creative.html", c=c, b=b, versions=versions, spec=spec, mmss=cc.mmss,
                           checks=creatives.all_checks(c))


@bp.route("/creatives/<int:cid>/file")
@login_required
def creative_file(cid):
    s = get_db()
    c = _creative(s, cid)
    try:
        path = creative_store.preview_path(s, c)
    except Exception:  # noqa: BLE001
        abort(404)
    return send_file(path, mimetype=c.content_type or None, conditional=True, download_name=c.filename,
                     as_attachment=request.args.get("download") == "1")


@bp.route("/creatives/frames/<int:frame_id>")
@login_required
def creative_frame(frame_id):
    f = get_db().get(CreativeFrame, frame_id) or abort(404)
    return Response(f.jpeg, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@bp.route("/creatives/<int:cid>/<action>", methods=["POST"])
@login_required
def creative_action(cid, action):
    s = get_db()
    c = _creative(s, cid)
    u = current_user()
    try:
        if action == "approve":
            creatives.approve(s, u, c, f_str("note"))
            msg = f"Version {c.version} approved."
        elif action == "reject":
            m = creatives.reject(s, u, c, f_str("note"))
            s.commit()
            flash("Rejected. The message to the client is ready to review.", "ok")
            return redirect(url_for("inbox.outbox_message", message_id=m.id))
        elif action == "recheck":
            creatives.recheck(s, u, c)
            msg = "Checking it again."
        elif action == "assign":
            creatives.assign(s, u, c, [int(x) for x in request.form.getlist("line_id") if x.isdigit()])
            msg = "Screens updated; checking it again."
        elif action == "feedback":
            if not can(u, "approve_creative"):
                abort(403)
            m = creatives.draft_feedback(s, c, creatives.failures(c) +
                                         [x for x in creatives.all_checks(c) if x["status"] == "warn"
                                          and x["key"] in ("safe_area", "readability", "brand", "black_frames")],
                                         note=f_str("note"))
            s.commit()
            return redirect(url_for("inbox.outbox_message", message_id=m.id))
        else:
            abort(404)
    except creatives.CreativeError as e:
        s.rollback()
        flash(str(e), "error")
        return redirect(url_for("creatives.creative_page", cid=cid))
    except Forbidden:
        s.rollback()
        flash("Your role can't do that." if action != "approve" else
              "Only a Manager or the owner can approve a file that failed its checks.", "error")
        return redirect(url_for("creatives.creative_page", cid=cid))
    s.commit()
    return done(msg, "creatives.creative_page", cid=cid)


def _booking(s, code):
    b = s.scalars(select(Booking).where(Booking.code == code)).first()
    if b is None:
        abort(404)
    return b


@bp.route("/bookings/<code>/files/request", methods=["POST"])
@login_required
def request_files(code):
    s = get_db()
    b = _booking(s, code)
    if not (can(current_user(), "approve_creative") or can(current_user(), "confirm")):
        abort(403)
    try:
        m = creatives.request_files(s, b, actor=current_user().email)
    except creatives.CreativeError as e:
        flash(str(e), "error")
        return redirect(url_for("bookings.booking_detail", code=code))
    s.commit()
    flash("The file request is ready to review and send.", "ok")
    return redirect(url_for("inbox.outbox_message", message_id=m.id))


def _store_uploads(s, b, files, source, actor):
    made, refused = [], []
    for f in files:
        if not f or not f.filename:
            continue
        if not creatives.is_media(f.filename, f.mimetype):
            refused.append(f.filename)
            continue
        try:
            made.append(creatives.add_file(s, b, filename=f.filename, content_type=f.mimetype or "", data=f.read(),
                                           source=source, actor=actor))
        except (creatives.CreativeError, creative_store.StoreError) as e:
            refused.append(f"{f.filename} ({e})")
    return made, refused


@bp.route("/bookings/<code>/files/upload", methods=["POST"])
@login_required
def team_upload(code):
    s = get_db()
    b = _booking(s, code)
    if not (can(current_user(), "approve_creative") or can(current_user(), "hold")):
        abort(403)
    made, refused = _store_uploads(s, b, request.files.getlist("files"), "team", current_user().email)
    s.commit()
    if refused:
        flash("Not taken: " + ", ".join(refused) + ". Only videos and images can be checked.", "error")
    if made:
        flash(f"{len(made)} file(s) added; checking now.", "ok")
    return redirect(url_for("bookings.booking_detail", code=code))


# ── The client's private upload link ────────────────────────────────────────────

def _by_token(s, token):
    b = s.scalars(select(Booking).where(Booking.upload_token == token)).first() if token and len(token) > 20 else None
    if b is None or b.status not in FILE_STAGES:
        abort(404)
    return b


@bp.route("/upload/<token>", methods=["GET", "POST"])
def client_upload(token):
    s = get_db()
    b = _by_token(s, token)
    lang = "ar" if (b.client.language == "ar") else "en"
    if request.method == "POST":
        made, refused = _store_uploads(s, b, request.files.getlist("files"), "upload", "client")
        s.commit()
        return render_template("upload.html", b=b, targets=creatives.targets(b), done=made, refused=refused,
                               lang_override=lang, deadline=b.creative_deadline)
    return render_template("upload.html", b=b, targets=creatives.targets(b), done=None, refused=[],
                           lang_override=lang, deadline=b.creative_deadline)


# ── The test set for the checks ─────────────────────────────────────────────────

@bp.route("/creatives/test-set")
@needs("accuracy")
def test_set():
    s = get_db()
    return render_template("creative_test_set.html",
                           cases=s.scalars(select(CreativeCase).order_by(CreativeCase.id.desc())).all(),
                           runs=s.scalars(select(CreativeRun).order_by(CreativeRun.id.desc()).limit(10)).all(),
                           keys=cc.TECHNICAL + cc.CONTENT, labels=cc.LABELS)


@bp.route("/creatives/test-set/new", methods=["GET", "POST"])
@needs("accuracy")
def case_new():
    s = get_db()
    specs = s.scalars(select(Spec).order_by(Spec.name)).all()
    if request.method == "POST":
        f = request.files.get("file")
        spec = s.get(Spec, f_int("spec_id") or 0)
        expected = {k: request.form.get(f"x_{k}") for k in cc.TECHNICAL + cc.CONTENT
                    if request.form.get(f"x_{k}") in ("pass", "fail")}
        if not f or not f.filename or spec is None or not expected:
            flash("Give the file, the spec, and the right answer for at least one check.", "error")
            return render_template("creative_case.html", specs=specs, keys=cc.TECHNICAL + cc.CONTENT,
                                   labels=cc.LABELS), 400
        s.add(CreativeCase(name=f_str("name") or f.filename, filename=f.filename, content_type=f.mimetype or "",
                           data=f.read(), spec_id=spec.id, ad_seconds=f_int("ad_seconds", 10),
                           client_name=f_str("client_name"), expected=expected, created_by=current_user().email))
        s.commit()
        return done("Test file added.", "creatives.test_set")
    return render_template("creative_case.html", specs=specs, keys=cc.TECHNICAL + cc.CONTENT, labels=cc.LABELS)


@bp.route("/creatives/test-set/<int:case_id>/toggle", methods=["POST"])
@needs("accuracy")
def case_toggle(case_id):
    s = get_db()
    case = s.get(CreativeCase, case_id) or abort(404)
    case.active = not case.active
    s.commit()
    return redirect(url_for("creatives.test_set"))


@bp.route("/creatives/test-set/run", methods=["POST"])
@needs("accuracy")
def test_run():
    s = get_db()
    run = CreativeRun(started_by=current_user().email)
    s.add(run)
    s.flush()
    enqueue(s, "creative_eval_run", {"run_id": run.id})
    log(s, current_user().email, "started", "creative_run", run.id, "Started the creative check test")
    s.commit()
    return done("The check test is running.", "creatives.test_run_page", run_id=run.id)


@bp.route("/creatives/test-set/runs/<int:run_id>")
@needs("accuracy")
def test_run_page(run_id):
    s = get_db()
    run = s.get(CreativeRun, run_id) or abort(404)
    return render_template("creative_test_run.html", run=run, labels=cc.LABELS)


def booking_panel(s, b):
    """What the booking page shows about its files."""
    return {"coverage": creatives.coverage(s, b),
            "files": s.scalars(select(Creative).where(Creative.booking_id == b.id)
                               .order_by(Creative.version.desc())).all(),
            "upload_url": url_for("creatives.client_upload", token=b.upload_token, _external=True)
            if b.upload_token else "",
            "drive_url": folder_url(b.drive_folder_id), "deadline": b.creative_deadline,
            "collecting": b.status in FILE_STAGES}
