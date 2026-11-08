"""Screens, their specs, blackout days and special hours."""

from datetime import date, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from .. import vacancy
from ..activity import diff, log, snapshot
from ..models import Blackout, Booking, BookingLine, HoursOverride, OCCUPYING, Screen, Spec
from ..web import current_user, done, f_bool, f_date, f_float, f_int, f_str, get_db, login_required, needs

bp = Blueprint("screens", __name__)

SCREEN_FIELDS = ("code", "name", "area", "address", "lat", "lng", "physical_size", "spec_id", "loop_seconds",
                 "slot_seconds", "sellable_pct", "open_hour", "close_hour", "timezone", "calendar_id", "active", "notes")
SPEC_FIELDS = ("name", "width_px", "height_px", "file_types", "video_codecs", "durations_s", "duration_tolerance_s",
               "fps_min", "fps_max", "max_file_mb", "audio_allowed", "safe_margin_pct", "language_rule", "notes")


@bp.route("/screens")
@login_required
def screen_list():
    s = get_db()
    screens = s.scalars(select(Screen).options(joinedload(Screen.spec)).order_by(Screen.area, Screen.code)).all()
    today = date.today()
    grid = vacancy.fill_grid(s, [x for x in screens if x.active], today, 1)
    return render_template("screens.html", screens=screens, today_fill={k: v[0] for k, v in grid.items()},
                           view=request.args.get("view", "list"))


def _screen_from_form(sc):
    errors = []
    sc.code = f_str("code")
    sc.name = f_str("name")
    for f in ("area", "address", "physical_size", "timezone", "calendar_id", "notes"):
        setattr(sc, f, f_str(f))
    sc.lat, sc.lng = f_float("lat"), f_float("lng")
    sc.spec_id = f_int("spec_id")
    sc.loop_seconds = f_int("loop_seconds", 0)
    sc.slot_seconds = f_int("slot_seconds", 0)
    sc.sellable_pct = f_float("sellable_pct", 100.0)
    sc.rate_week = f_float("rate_week")
    sc.open_hour = f_int("open_hour", 6)
    sc.close_hour = f_int("close_hour", 24)
    sc.active = f_bool("active")
    if not sc.code or not sc.name:
        errors.append("A screen needs a code and a name.")
    if sc.loop_seconds <= 0 or sc.slot_seconds <= 0:
        errors.append("Loop and slot lengths must be more than 0 seconds.")
    elif sc.slot_seconds > sc.loop_seconds:
        errors.append("A slot can't be longer than the loop.")
    if not (0 <= sc.open_hour <= 23 and 1 <= sc.close_hour <= 24):
        errors.append("Opening hours run from 0 to 23, closing from 1 to 24.")
    if sc.rate_week is not None and sc.rate_week < 0:
        errors.append("The rate can't be negative.")
    if not 0 < sc.sellable_pct <= 100:
        errors.append("The sellable share is between 1 and 100%.")
    if sc.timezone:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(sc.timezone)
        except Exception:  # noqa: BLE001
            errors.append(f"'{sc.timezone}' isn't a time zone name, e.g. Asia/Dubai or Asia/Riyadh.")
    return errors


@bp.route("/screens/new", methods=["GET", "POST"])
@bp.route("/screens/<int:screen_id>/edit", methods=["GET", "POST"])
@needs("edit_screens")
def screen_edit(screen_id=None):
    s = get_db()
    sc = s.get(Screen, screen_id) if screen_id else Screen(active=True, timezone="Asia/Dubai", loop_seconds=120,
                                                            slot_seconds=10, sellable_pct=100, open_hour=6,
                                                            close_hour=24)
    if screen_id and sc is None:
        abort(404)
    if request.method == "POST":
        s.autoflush = False   # nothing is written until the form checks out
        before = snapshot(sc, SCREEN_FIELDS) if sc.id else {}
        errors = _screen_from_form(sc)
        clash = s.scalars(select(Screen).where(func.lower(Screen.code) == sc.code.lower(),
                                               Screen.id != (sc.id or 0))).first()
        if clash:
            errors.append(f"Screen code {sc.code} is already used.")
        if errors:
            for e in errors:
                flash(e, "error")
            # Show what was typed, then drop the unsaved changes.
            with s.no_autoflush:
                page = render_template("screen_form.html", sc=sc,
                                       specs=s.scalars(select(Spec).order_by(Spec.name)).all())
            s.rollback()
            return page, 400
        else:
            if not sc.id:
                s.add(sc)
            s.flush()
            log(s, current_user().email, "saved", "screen", sc.id, f"Screen {sc.code} saved",
                diff(before, snapshot(sc, SCREEN_FIELDS)))
            s.commit()
            return done(f"{sc.code} saved.", "screens.screen_detail", screen_id=sc.id)
    return render_template("screen_form.html", sc=sc, specs=s.scalars(select(Spec).order_by(Spec.name)).all())


@bp.route("/screens/<int:screen_id>")
@login_required
def screen_detail(screen_id):
    s = get_db()
    sc = s.get(Screen, screen_id) or abort(404)
    today = date.today()
    strip = vacancy.fill_grid(s, [sc], today, 30)[sc.id]
    upcoming = s.scalars(select(BookingLine).join(Booking)
                         .options(joinedload(BookingLine.booking).joinedload(Booking.client))
                         .where(BookingLine.screen_id == sc.id, Booking.status.in_(OCCUPYING),
                                BookingLine.end_date >= today)
                         .order_by(BookingLine.start_date)).all()
    blackouts = s.scalars(select(Blackout).where(Blackout.screen_id == sc.id, Blackout.end_date >= today)
                          .order_by(Blackout.start_date)).all()
    overrides = s.scalars(select(HoursOverride).where(HoursOverride.screen_id == sc.id,
                                                      HoursOverride.end_date >= today)
                          .order_by(HoursOverride.start_date)).all()
    from .. import insights
    return render_template("screen_detail.html", sc=sc, strip=strip, upcoming=upcoming, blackouts=blackouts,
                           overrides=overrides, stats=insights.screen_stats(s, sc, today), heat=insights.heat)


# ── Blackouts and special hours ─────────────────────────────────────────────────

@bp.route("/closures")
@login_required
def closures():
    s = get_db()
    today = date.today() - timedelta(days=30)
    return render_template(
        "closures.html",
        blackouts=s.scalars(select(Blackout).where(Blackout.end_date >= today).order_by(Blackout.start_date)).all(),
        overrides=s.scalars(select(HoursOverride).where(HoursOverride.end_date >= today)
                            .order_by(HoursOverride.start_date)).all(),
        screens={x.id: x for x in s.scalars(select(Screen).order_by(Screen.code))})


@bp.route("/closures/add", methods=["POST"])
@needs("edit_screens")
def closure_add():
    s = get_db()
    kind = f_str("kind")
    screen_id = f_int("screen_id")
    start, end = f_date("start_date"), f_date("end_date") or f_date("start_date")
    back = request.form.get("back") or url_for("screens.closures")
    if not start or not end or end < start:
        flash("Give a start date, and an end date on or after it.", "error")
        return redirect(back)
    reason = f_str("reason")
    if kind == "hours":
        o, c = f_int("open_hour"), f_int("close_hour")
        if o is None or c is None or not (0 <= o <= 23 and 1 <= c <= 24):
            flash("Opening hours run from 0 to 23, closing from 1 to 24.", "error")
            return redirect(back)
        s.add(HoursOverride(screen_id=screen_id, start_date=start, end_date=end, open_hour=o, close_hour=c,
                            reason=reason))
        what = f"Special hours {o:02d}:00–{c:02d}:00"
    else:
        s.add(Blackout(screen_id=screen_id, start_date=start, end_date=end, reason=reason))
        what = "Blackout"
    target = s.get(Screen, screen_id).code if screen_id else "all screens"
    log(s, current_user().email, "added", "closure", screen_id or "", f"{what} for {target}, {start} to {end}: {reason}")
    s.commit()
    _warn_affected(s, screen_id, start, end)
    flash(f"{what} added for {target}.", "ok")
    return redirect(back)


def _warn_affected(s, screen_id, start, end):
    q = (select(Booking.code).join(BookingLine)
         .where(Booking.status.in_(OCCUPYING), BookingLine.start_date <= end, BookingLine.end_date >= start))
    if screen_id:
        q = q.where(BookingLine.screen_id == screen_id)
    codes = sorted(set(s.scalars(q)))
    if codes:
        flash("These bookings run in that period and may lose airtime: " + ", ".join(codes[:20]), "warning")


@bp.route("/closures/<kind>/<int:item_id>/delete", methods=["POST"])
@needs("edit_screens")
def closure_delete(kind, item_id):
    s = get_db()
    model = HoursOverride if kind == "hours" else Blackout
    item = s.get(model, item_id) or abort(404)
    log(s, current_user().email, "removed", "closure", item.screen_id or "",
        f"Removed {'special hours' if kind == 'hours' else 'blackout'} {item.start_date} to {item.end_date}")
    s.delete(item)
    s.commit()
    flash("Removed.", "ok")
    return redirect(request.form.get("back") or url_for("screens.closures"))


# ── Specs ───────────────────────────────────────────────────────────────────────

@bp.route("/specs")
@login_required
def spec_list():
    s = get_db()
    counts = dict(s.execute(select(Screen.spec_id, func.count()).group_by(Screen.spec_id)).all())
    return render_template("specs.html", specs=s.scalars(select(Spec).order_by(Spec.name)).all(), counts=counts)


@bp.route("/specs/new", methods=["GET", "POST"])
@bp.route("/specs/<int:spec_id>", methods=["GET", "POST"])
@login_required
def spec_edit(spec_id=None):
    s = get_db()
    sp = s.get(Spec, spec_id) if spec_id else Spec(width_px=1920, height_px=1080, file_types="mp4,jpg,png",
                                                     video_codecs="h264", durations_s="10", duration_tolerance_s=0.2,
                                                     safe_margin_pct=5, audio_allowed=False,
                                                     language_rule="arabic_or_arabic_english")
    if spec_id and sp is None:
        abort(404)
    if request.method == "POST":
        from ..permissions import can
        if not can(current_user(), "edit_screens"):
            abort(403)
        s.autoflush = False   # nothing is written until the form checks out
        before = snapshot(sp, SPEC_FIELDS) if sp.id else {}
        sp.name = f_str("name")
        sp.width_px, sp.height_px = f_int("width_px", 0), f_int("height_px", 0)
        for f in ("file_types", "video_codecs", "durations_s"):
            setattr(sp, f, f_str(f).replace(" ", "").lower())
        sp.duration_tolerance_s = f_float("duration_tolerance_s", 0.2)
        sp.fps_min, sp.fps_max = f_float("fps_min"), f_float("fps_max")
        sp.max_file_mb = f_float("max_file_mb")
        sp.audio_allowed = f_bool("audio_allowed")
        sp.safe_margin_pct = f_float("safe_margin_pct", 5.0)
        sp.notes = f_str("notes")
        errors = []
        if not sp.name:
            errors.append("A spec needs a name.")
        if sp.width_px <= 0 or sp.height_px <= 0:
            errors.append("Width and height must be more than 0 pixels.")
        if not sp.duration_list:
            errors.append("Give at least one allowed ad length in seconds, e.g. 10 or 10,15.")
        if s.scalars(select(Spec).where(func.lower(Spec.name) == sp.name.lower(), Spec.id != (sp.id or 0))).first():
            errors.append(f"There is already a spec called {sp.name}.")
        if errors:
            for e in errors:
                flash(e, "error")
            page = render_template("spec_form.html", sp=sp, screens=[])
            s.rollback()
            return page, 400
        else:
            if not sp.id:
                s.add(sp)
            s.flush()
            log(s, current_user().email, "saved", "spec", sp.id, f"Spec {sp.name} saved",
                diff(before, snapshot(sp, SPEC_FIELDS)))
            s.commit()
            return done(f"Spec {sp.name} saved.", "screens.spec_list")
    screens = s.scalars(select(Screen).where(Screen.spec_id == sp.id).order_by(Screen.code)).all() if sp.id else []
    return render_template("spec_form.html", sp=sp, screens=screens)
