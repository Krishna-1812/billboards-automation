"""The dashboard, notices, the language switch and the health check."""

from datetime import date, timedelta

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import or_, select, text
from sqlalchemy.orm import joinedload

from .. import db, settings, vacancy
from ..jobs import recent_problems
from ..models import (BOOKED, Activity, Booking, BookingLine, Notification, Screen, Setting, SheetChange, utcnow)
from ..web import current_user, get_db, login_required

bp = Blueprint("main", __name__)


@bp.route("/health")
def health():
    """Railway's health check. 200 while the database answers; also reports the worker's last heartbeat."""
    try:
        with db.session_scope() as s:
            s.execute(text("SELECT 1"))
            beat = s.get(Setting, "worker_heartbeat")
            seen = (beat.value or {}).get("v") if beat else None
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, database=str(e)[:200]), 503
    age = None
    if seen:
        from datetime import datetime
        age = int((utcnow() - datetime.fromisoformat(seen)).total_seconds())
    return jsonify(ok=True, database="ok", worker_seconds_since_heartbeat=age)


def _seen_key(user):
    return f"notices_seen:{user.id}"


def notices_for(s, user, limit=50):
    return s.scalars(select(Notification).where(or_(Notification.user_email == "",
                                                    Notification.user_email == user.email))
                     .order_by(Notification.id.desc()).limit(limit)).all()


def unread_count(s, user):
    seen = settings.get(s, _seen_key(user)) or 0
    return sum(1 for n in notices_for(s, user, 100) if n.id > seen)


@bp.app_context_processor
def _notice_count():
    u = g.get("user")
    if u is None or u.role == "client":
        return {"unread_notices": 0}
    return {"unread_notices": unread_count(get_db(), u)}


@bp.route("/")
@login_required
def dashboard():
    s = get_db()
    today = date.today()
    now = utcnow()
    screens = s.scalars(select(Screen).where(Screen.active.is_(True)).order_by(Screen.code)).all()
    grid = vacancy.fill_grid(s, screens, today, 7) if screens else {}
    week_avg = (sum(avg for cells in grid.values() for _, _, avg, closed in cells if not closed) /
                max(1, sum(1 for cells in grid.values() for c in cells if not c[3]))) if grid else 0.0
    today_peak = {sid: cells[0][1] for sid, cells in grid.items()}

    holds = s.scalars(select(Booking).options(joinedload(Booking.client))
                      .where(Booking.status == "hold").order_by(Booking.hold_expires_at)).all()
    expiring = [b for b in holds if b.hold_expires_at and b.hold_expires_at <= now + timedelta(hours=48)]
    starting = s.scalars(select(Booking).join(BookingLine).options(joinedload(Booking.client))
                         .where(Booking.status.in_(("hold",) + BOOKED),
                                BookingLine.start_date >= today,
                                BookingLine.start_date <= today + timedelta(days=7))
                         .order_by(BookingLine.start_date)).unique().all()
    running = s.scalars(select(Booking).join(BookingLine)
                        .where(Booking.status.in_(BOOKED),
                               BookingLine.start_date <= today, BookingLine.end_date >= today)).unique().all()
    overrides = s.scalars(select(Booking).options(joinedload(Booking.client))
                          .where(Booking.override_reason != "",
                                 Booking.status.notin_(("cancelled", "expired", "completed")))).all()
    sheet_changes = s.scalars(select(SheetChange).where(SheetChange.resolved_at.is_(None))).all()
    activity = s.scalars(select(Activity).order_by(Activity.id.desc()).limit(12)).all()
    return render_template("dashboard.html", screens=screens, today_peak=today_peak, week_avg=week_avg,
                           holds=holds, expiring=expiring, starting=starting, running=running,
                           overrides=overrides, sheet_changes=sheet_changes, activity=activity,
                           problems=recent_problems(s, 5))


@bp.route("/notices")
@login_required
def notices():
    s = get_db()
    u = current_user()
    items = notices_for(s, u)
    seen = settings.get(s, _seen_key(u)) or 0
    if items:
        settings.put(s, _seen_key(u), items[0].id)
        s.commit()
    return render_template("notices.html", items=items, seen=seen)


@bp.route("/language", methods=["POST"])
def language():
    lang = "ar" if g.get("lang", "en") == "en" else "en"
    session["lang"] = lang
    u = current_user()
    if u is not None:
        u.language = lang
        get_db().commit()
    nxt = request.form.get("next") or "/"
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
