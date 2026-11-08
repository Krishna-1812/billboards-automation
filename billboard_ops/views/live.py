"""Phase 4 for the team: the play plan editor, loading the screens, playlists, and proof of play."""

import os
from datetime import date, timedelta

from flask import Blueprint, Response, abort, after_this_request, flash, redirect, render_template, request, \
    send_file, url_for
from sqlalchemy import select

from .. import creative_store, lifecycle, loading, plans, proof, settings, vacancy
from ..models import Booking, Proof, Screen
from ..permissions import Forbidden, can
from ..web import a_date, current_user, done, f_int, f_str, get_db, login_required

bp = Blueprint("live", __name__)


def _booking(s, code):
    b = s.scalars(select(Booking).where(Booking.code == code)).first()
    if b is None:
        abort(404)
    return b


def _max_plays(line):
    return int(vacancy.SECONDS_PER_HOUR * line.screen.sellable_pct / 100 // max(1, line.ad_seconds))


# ── The play plan ───────────────────────────────────────────────────────────────

def _plans_from_form(b, parts):
    out, errors = {}, []
    for l in b.lines:
        if l.exclusive:
            continue
        most = _max_plays(l)
        plays = []
        for i, p in enumerate(parts):
            raw = request.form.get(f"p_{l.id}_{i}", "").strip()
            try:
                n = float(raw) if raw else 0.0
            except ValueError:
                errors.append(f"{l.screen.code}, {plans.label(p['name'])}: “{raw}” isn't a number.")
                n = 0.0
            if n < 0 or n > most:
                errors.append(f"{l.screen.code}, {plans.label(p['name'])}: between 0 and {most} plays an hour "
                              f"(the most a {l.ad_seconds}s ad can play in this loop).")
                n = max(0.0, min(n, most))
            plays.append(n)
        days = sorted({int(d) for d in request.form.getlist(f"wd_{l.id}") if d.isdigit() and int(d) < 7})
        exceptions = []
        for a, z, n, note in zip(request.form.getlist(f"ex_{l.id}_from"), request.form.getlist(f"ex_{l.id}_to"),
                                 request.form.getlist(f"ex_{l.id}_plays"), request.form.getlist(f"ex_{l.id}_note")):
            if not a.strip() and not z.strip():
                continue
            try:
                d1, d2 = date.fromisoformat(a.strip()), date.fromisoformat((z or a).strip())
            except ValueError:
                errors.append(f"{l.screen.code}: give both dates for each special date range.")
                continue
            if d2 < d1 or d1 < l.start_date or d2 > l.end_date:
                errors.append(f"{l.screen.code}: special dates must fall between {l.start_date:%d %b} and "
                              f"{l.end_date:%d %b}, the start first.")
                continue
            try:
                k = max(0.0, min(float(n or 0), most))
            except ValueError:
                k = 0.0
            exceptions.append({"from": d1.isoformat(), "to": d2.isoformat(), "plays": k, "note": note.strip()[:200]})
        plan = plans.build(parts, plays, days, exceptions)
        if (l.plan or {}).get("evidence"):
            plan["evidence"] = l.plan["evidence"]
        out[l.id] = plan
    return out, errors


def _render_plan(s, b, rows=None, status=200):
    parts = plans.dayparts(s)
    rows = rows if rows is not None else plans.evaluate(s, b)
    return render_template("plan.html", b=b, parts=parts, rows=rows, fits=plans.fits(rows),
                           max_plays=_max_plays, weekdays=plans.WEEKDAYS, part_hours=plans.part_hours,
                           describe=plans.describe, label=plans.label,
                           settings_client_approves=settings.get(s, "client_approves_plan")), status


@bp.route("/bookings/<code>/plan", methods=["GET", "POST"])
@login_required
def plan_page(code):
    s = get_db()
    b = _booking(s, code)
    if request.method == "GET":
        return _render_plan(s, b)
    u = current_user()
    action = f_str("action")
    try:
        if action == "draft":
            if not can(u, "edit_plan"):
                abort(403)
            plans.draft(s, b, actor=u.email)
            s.commit()
            return done("The agent drafted the plan. Check it and confirm it.", "live.plan_page", code=code)
        parts = plans.dayparts(s)
        proposed, errors = _plans_from_form(b, parts)
        if errors:
            for e in dict.fromkeys(errors):
                flash(e, "error")
            s.rollback()
            return _render_plan(s, _booking(s, code), plans.evaluate(s, b, proposed), 400)
        try:
            rows = plans.save(s, u, b, proposed)
        except plans.PlanError as e:
            s.rollback()
            flash(str(e), "error")
            b = _booking(s, code)
            return _render_plan(s, b, plans.evaluate(s, b, proposed), 400)
        if action == "confirm":
            plans.confirm(s, u, b, override_reason=f_str("override_reason"))
            s.commit()
            msg = ("Plan confirmed and sent to the client to approve." if b.plan_status == "waiting_client"
                   else "Plan confirmed.")
            return done(msg, "live.plan_page", code=code)
        s.commit()
        flash("Saved as a draft." if plans.fits(rows) else
              "Saved as a draft. It overfills the loop in some hours; see below.", "ok" if plans.fits(rows) else "error")
        return redirect(url_for("live.plan_page", code=code))
    except plans.PlanError as e:
        s.commit()                       # the edits are kept as a draft; only the confirmation was refused
        flash(str(e), "error")
        return redirect(url_for("live.plan_page", code=code))
    except Forbidden:
        s.rollback()
        abort(403)


# ── Loading the screens ─────────────────────────────────────────────────────────

@bp.route("/bookings/<code>/lines/<int:line_id>/loaded", methods=["POST"])
@login_required
def mark_loaded(code, line_id):
    s = get_db()
    b = _booking(s, code)
    line = next((l for l in b.lines if l.id == line_id), None) or abort(404)
    try:
        lifecycle.mark_loaded(s, current_user(), b, line, loaded=f_str("loaded") != "0")
    except lifecycle.LoadError as e:
        s.rollback()
        flash(str(e), "error")
        return redirect(url_for("bookings.booking_detail", code=code))
    except Forbidden:
        abort(403)
    s.commit()
    return done(f"{line.screen.code}: {'loaded' if line.loaded_at else 'marked not loaded'}.",
                "bookings.booking_detail", code=code)


@bp.route("/bookings/<code>/load.zip")
@login_required
def load_zip(code):
    s = get_db()
    b = _booking(s, code)
    path, count = loading.zip_for(s, b)
    if not count:
        os.remove(path)
        flash("No approved files to download yet.", "error")
        return redirect(url_for("bookings.booking_detail", code=code))

    @after_this_request
    def _cleanup(resp):
        try:
            os.remove(path)
        except OSError:
            pass
        return resp
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=f"{b.code}-ready-to-load.zip")


@bp.route("/playlists")
@login_required
def playlists():
    s = get_db()
    screens_all = s.scalars(select(Screen).where(Screen.active.is_(True)).order_by(Screen.code)).all()
    start = a_date("from", date.today())
    days = max(1, min(31, int(request.args.get("days") or 1) if (request.args.get("days") or "1").isdigit() else 1))
    end = start + timedelta(days=days - 1)
    sid = request.args.get("screen", "")
    code = request.args.get("booking", "")
    booking = s.scalars(select(Booking).where(Booking.code == code)).first() if code else None
    chosen = [x for x in screens_all if str(x.id) == sid] if sid else (
        [l.screen for l in booking.lines] if booking else screens_all)
    rows = loading.playlist(s, chosen, start, end, booking_id=booking.id if booking else None)
    fmt = request.args.get("format", "")
    if fmt:
        title = (f"Playlist {booking.code} " if booking else "Playlist ") + \
            (f"{chosen[0].code} " if sid and chosen else "") + f"{start:%d %b %Y}" + \
            (f" to {end:%d %b %Y}" if days > 1 else "")
        name = title.replace(" ", "-")
        if fmt == "csv":
            return Response(loading.export_csv(rows), mimetype="text/csv",
                            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
        if fmt == "xlsx":
            return Response(loading.export_xlsx(rows),
                            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
        if fmt == "pdf":
            return Response(loading.export_pdf(rows, title), mimetype="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
        abort(404)
    return render_template("playlists.html", rows=rows, screens=screens_all, sid=sid, start=start, days=days,
                           end=end, booking=booking)


# ── Proof of play ───────────────────────────────────────────────────────────────

@bp.route("/bookings/<code>/proof", methods=["POST"])
@login_required
def add_proof(code):
    s = get_db()
    b = _booking(s, code)
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Choose a photo, a video or a play report.", "error")
        return redirect(url_for("bookings.booking_detail", code=code) + "#proof")
    taken = None
    try:
        taken = date.fromisoformat(f_str("taken_on")) if f_str("taken_on") else None
    except ValueError:
        pass
    added, refused = 0, []
    for f in files:
        try:
            proof.add(s, current_user(), b, filename=f.filename, content_type=f.mimetype or "", data=f.read(),
                      line_id=f_int("line_id"), taken_on=taken, note=f_str("note"),
                      shared=request.form.get("shared") == "1")
            added += 1
        except proof.ProofError as e:
            refused.append(f"{f.filename}: {e}")
        except Forbidden:
            abort(403)
    s.commit()
    for r in refused:
        flash(r, "error")
    if added:
        flash(f"{added} added.", "ok")
    return redirect(url_for("bookings.booking_detail", code=code) + "#proof")


def proof_response(s, p):
    if p.data is not None:
        return Response(p.data, mimetype=p.content_type or "application/octet-stream",
                        headers={"Content-Disposition": f'inline; filename="{p.filename}"',
                                 "Cache-Control": "private, max-age=3600"})
    try:
        path = creative_store.preview_path(s, p)
    except Exception:  # noqa: BLE001
        abort(404)
    return send_file(path, mimetype=p.content_type or None, conditional=True, download_name=p.filename)


@bp.route("/proof/<int:proof_id>")
@login_required
def proof_file(proof_id):
    s = get_db()
    p = s.get(Proof, proof_id) or abort(404)
    return proof_response(s, p)


@bp.route("/proof/<int:proof_id>/<action>", methods=["POST"])
@login_required
def proof_action(proof_id, action):
    s = get_db()
    p = s.get(Proof, proof_id) or abort(404)
    if not can(current_user(), "load"):
        abort(403)
    code = p.booking.code
    from ..activity import log
    if action == "share":
        p.shared = not p.shared
        log(s, current_user().email, "proof_shared", "booking", code,
            f"{p.filename} {'shown to' if p.shared else 'hidden from'} the client")
    elif action == "delete":
        log(s, current_user().email, "proof_deleted", "booking", code, f"Removed proof {p.filename}")
        s.delete(p)
    else:
        abort(404)
    s.commit()
    return redirect(url_for("bookings.booking_detail", code=code) + "#proof")


def booking_panel(s, b):
    """What the booking page shows about getting ready, going live and proof (Phase 4)."""
    if b.status in ("hold", "enquiry", "cancelled", "expired"):
        return None
    r = lifecycle.readiness(s, b)
    rows = {x["line"].id: x for x in plans.evaluate(s, b)} if b.lines else {}
    lines = []
    for l in b.lines:
        lines.append({"line": l, "files": [(c, loading.load_name(s, l, c)) for c in lifecycle.approved_files(s, l)],
                      "plan": rows.get(l.id)})
    proofs = s.scalars(select(Proof).where(Proof.booking_id == b.id).order_by(Proof.id.desc())).all()
    from ..google_client import folder_url
    return {"ready": r, "lines": lines, "proofs": proofs, "load_folder": folder_url(b.load_folder_id)}
