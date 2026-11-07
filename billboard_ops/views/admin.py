"""Settings: general, people, connections, sheet changes; and the activity log."""

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from .. import settings
from ..activity import log
from ..google_client import get_client, service_account_email, spreadsheet_id_from
from ..jobs import enqueue, recent_problems
from ..models import ROLES, Activity, Client, Job, Screen, SheetChange, SheetWatch, User, utcnow
from ..sync import accept_change, queue_sheet_mirror
from ..web import current_user, done, f_float, f_int, f_str, get_db, login_required, needs

bp = Blueprint("admin", __name__)


@bp.route("/settings", methods=["GET", "POST"])
@needs("settings")
def general():
    s = get_db()
    if request.method == "POST":
        hold_days = f_int("hold_days")
        reminder = f_int("hold_reminder_hours")
        if not hold_days or not 1 <= hold_days <= 60:
            flash("A hold lasts between 1 and 60 days.", "error")
        elif reminder is None or not 0 <= reminder <= 24 * 30:
            flash("The reminder is between 0 hours and 30 days before expiry.", "error")
        else:
            before = settings.all_settings(s)
            settings.put(s, "hold_days", hold_days)
            settings.put(s, "hold_reminder_hours", reminder)
            settings.put(s, "date_order", "mdy" if f_str("date_order") == "mdy" else "dmy")
            settings.put(s, "default_timezone", f_str("default_timezone") or "Asia/Dubai")
            settings.put(s, "mirror_spreadsheet_id", spreadsheet_id_from(f_str("mirror_spreadsheet_id")))
            settings.put(s, "mirror_tab", f_str("mirror_tab") or "Bookings (from app)")
            settings.put(s, "company_name", f_str("company_name") or "Billboard Ops")
            settings.put(s, "auto_hold", request.form.get("auto_hold") == "1")
            if "creative_lead_days" in request.form:      # the Creative section was on the form
                settings.put(s, "creative_lead_days", max(0, min(30, f_int("creative_lead_days", 5))))
                settings.put(s, "weekend_days", ",".join(x.strip() for x in f_str("weekend_days").split(",")
                                                         if x.strip() in list("0123456")))
                settings.put(s, "black_max_s", max(0.0, f_float("black_max_s", 0.5)))
                settings.put(s, "freeze_max_s", max(1.0, f_float("freeze_max_s", 5.0)))
                settings.put(s, "drive_parent_folder_id", spreadsheet_id_from(f_str("drive_parent_folder_id")
                                                                               .replace("/folders/", "/d/")))
                settings.put(s, "content_rules", request.form.get("content_rules", "").strip())
                settings.put(s, "auto_send_technical", request.form.get("auto_send_technical") == "1")
            settings.put(s, "remind_clients", request.form.get("remind_clients") == "1")
            after = settings.all_settings(s)
            log(s, current_user().email, "changed", "settings", "general", "General settings changed",
                {k: [before[k], after[k]] for k in after if before[k] != after[k]})
            if after["mirror_spreadsheet_id"] and after["mirror_spreadsheet_id"] != before["mirror_spreadsheet_id"]:
                queue_sheet_mirror(s)
            s.commit()
            return done("Settings saved.", "admin.general")
    from ..evaluate import gate
    return render_template("settings_general.html", st=settings.all_settings(s), gate=gate(s))


@bp.route("/settings/people", methods=["GET", "POST"])
@needs("people")
def people():
    s = get_db()
    if request.method == "POST":
        email = f_str("email").lower()
        role = f_str("role")
        uid = f_int("user_id")
        user = s.get(User, uid) if uid else None
        if role not in ROLES:
            abort(400)
        if user is None:
            if "@" not in email:
                flash("Give an email address.", "error")
                return redirect(url_for("admin.people"))
            if s.scalars(select(User).where(func.lower(User.email) == email)).first():
                flash(f"{email} is already on the list.", "error")
                return redirect(url_for("admin.people"))
            user = User(email=email, name=f_str("name"), role=role)
            s.add(user)
            msg = f"Added {email} as {role}."
        else:
            if user.id == current_user().id and (role != "owner" or request.form.get("active") != "1"):
                flash("You can't remove your own owner access. Ask another owner.", "error")
                return redirect(url_for("admin.people"))
            user.role = role
            user.active = request.form.get("active") == "1"
            msg = f"Updated {user.email}."
        if role == "client":
            user.client_id = f_int("client_id")
        s.flush()
        log(s, current_user().email, "saved", "user", user.email, f"{user.email}: {user.role}, "
            f"{'active' if user.active else 'removed'}")
        s.commit()
        return done(msg, "admin.people")
    return render_template("settings_people.html", people=s.scalars(select(User).order_by(User.role, User.email)).all(),
                           roles=ROLES, clients=s.scalars(select(Client).order_by(Client.name)).all())


@bp.route("/settings/connections", methods=["GET", "POST"])
@needs("settings")
def connections():
    s = get_db()
    cfg = current_app.config["APP"]
    test = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "test_sheet":
            try:
                tabs = get_client(cfg).sheet_tabs(spreadsheet_id_from(f_str("sheet")))
                test = ("ok", "The app can read this sheet. Tabs: " + ", ".join(tabs))
            except Exception as e:  # noqa: BLE001
                test = ("error", f"Couldn't read it: {str(e)[:300]}")
        elif action == "mirror_now":
            queue_sheet_mirror(s)
            s.commit()
            test = ("ok", "The Bookings tab will be rewritten within a minute.")
        elif action == "retry":
            job = s.get(Job, f_int("job_id"))
            if job is not None:
                job.status, job.attempts, job.run_at, job.last_error = "queued", 0, utcnow(), ""
                s.commit()
                test = ("ok", "Queued to run again.")
    no_calendar = s.scalars(select(Screen).where(Screen.active.is_(True), Screen.calendar_id == "")).all()
    beat = settings.get(s, "worker_heartbeat")
    return render_template("settings_connections.html", cfg=cfg, sa_email=service_account_email(cfg),
                           connected=bool(cfg.service_account_info()), test=test, problems=recent_problems(s, 30),
                           no_calendar=no_calendar, heartbeat=beat,
                           queued=s.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")),
                           mirror=settings.get(s, "mirror_spreadsheet_id"))


@bp.route("/settings/sheet-changes", methods=["GET", "POST"])
@needs("review_sheet")
def sheet_changes():
    s = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "accept":
            ch = s.get(SheetChange, f_int("change_id")) or abort(404)
            accept_change(s, ch, current_user().email)
            log(s, current_user().email, "reviewed", "sheet_change", ch.id,
                f"Reviewed a direct edit to the old sheet: {ch.change} {ch.row_key}")
        elif action == "check_now":
            for w in s.scalars(select(SheetWatch).where(SheetWatch.active.is_(True))):
                enqueue(s, "sheet_watch_check", {"watch_id": w.id}, dedupe_key=f"watch:{w.id}")
            flash("Checking the old sheets now.", "ok")
        elif action == "stop":
            w = s.get(SheetWatch, f_int("watch_id")) or abort(404)
            w.active = False
            log(s, current_user().email, "stopped", "sheet_watch", w.id, f"Stopped watching tab {w.tab}")
        s.commit()
        return redirect(url_for("admin.sheet_changes"))
    open_changes = s.scalars(select(SheetChange).where(SheetChange.resolved_at.is_(None))
                             .order_by(SheetChange.id.desc())).all()
    watches = {w.id: w for w in s.scalars(select(SheetWatch))}
    return render_template("settings_sheet_changes.html", changes=open_changes, watches=watches)


@bp.route("/activity")
@login_required
def activity():
    s = get_db()
    q = select(Activity).order_by(Activity.id.desc())
    who = request.args.get("who", "").strip()
    kind = request.args.get("type", "").strip()
    if who:
        q = q.where(Activity.actor == who)
    if kind:
        q = q.where(Activity.entity_type == kind)
    page = max(1, request.args.get("page", 1, type=int))
    items = s.scalars(q.offset((page - 1) * 100).limit(101)).all()
    types = [t for (t,) in s.execute(select(Activity.entity_type).distinct().order_by(Activity.entity_type))]
    return render_template("activity.html", items=items[:100], more=len(items) > 100, page=page, who=who,
                           kind=kind, types=types)
