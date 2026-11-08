"""The dashboard, notices, the language switch and the health check."""

from datetime import date, timedelta

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import or_, select, text
from sqlalchemy.orm import joinedload

from .. import db, settings
from ..jobs import recent_problems
from ..models import Activity, Booking, Notification, Screen, Setting, SheetChange, utcnow
from ..web import current_user, get_db, login_required, needs

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
    from flask import current_app
    cfg = current_app.config["APP"]
    return jsonify(ok=True, database="ok", database_kind=cfg.DATABASE_URL.split(":", 1)[0].split("+", 1)[0],
                   sign_in="on" if cfg.LOGIN_REQUIRED else "off", worker_seconds_since_heartbeat=age)


def _seen_key(user):
    return f"notices_seen:{user.id}"


def notices_for(s, user, limit=50):
    """The notices this person sees: everyone's and their own, less the topics they switched off."""
    from ..notices import hidden_topics
    q = select(Notification).where(or_(Notification.user_email == "", Notification.user_email == user.email))
    hidden = hidden_topics(user)
    if hidden:
        q = q.where(Notification.topic.not_in(hidden))
    return s.scalars(q.order_by(Notification.id.desc()).limit(limit)).all()


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
    """The owner's one page: how full we are, what's coming, what's at risk, and what needs a decision."""
    from .. import insights
    s = get_db()
    today = date.today()
    fmt = request.args.get("format")
    if fmt:
        return _export(s, request.args.get("export", ""), fmt, today)
    screens = s.scalars(select(Screen).where(Screen.active.is_(True)).order_by(Screen.code)).all()
    w0, _ = insights.week_of(today)
    m0, m1 = insights.month_of(today)
    l0, l1 = insights.last_month(today)
    overrides = s.scalars(select(Booking).options(joinedload(Booking.client))
                          .where(Booking.override_reason != "",
                                 Booking.status.notin_(("cancelled", "expired", "completed")))).all()
    sheet_changes = s.scalars(select(SheetChange).where(SheetChange.resolved_at.is_(None))).all()
    activity = s.scalars(select(Activity).order_by(Activity.id.desc()).limit(10)).all()
    full, from_week = insights.dashboard_fills(s, screens, today)
    weeks = insights.weekly_grid(s, screens, w0, 12, from_week)
    from .. import lifecycle
    local = lifecycle.local_now(s)
    greeting = "good_morning" if local.hour < 12 else ("good_afternoon" if local.hour < 18 else "good_evening")
    return render_template("dashboard.html", screens=screens, top=insights.top_numbers(s, today, full),
                           risks=insights.at_risk(s, today), weeks=weeks,
                           chart=insights.trend_chart(weeks, w0), live=insights.live_now(s),
                           starting=insights.starting_soon(s, today),
                           week0=w0, heat=insights.heat, this_month=insights.pipeline(s, m0, m1),
                           last=insights.pipeline(s, l0, l1), month0=m0, last0=l0, greeting=greeting,
                           map_points=insights.screens_on_map(s, today, full), overrides=overrides,
                           sheet_changes=sheet_changes, activity=activity, problems=recent_problems(s, 5))


def dashboard_table(s, what, today):
    """(headers, rows, title) for a dashboard table, for exports and the weekly summary."""
    from .. import insights
    if what == "risk":
        return (["Severity", "Booking", "Client", "What", "Who owns the next step", "Date"],
                [[{3: "urgent", 2: "soon", 1: "follow up"}[r["severity"]], r["code"], r["client"], r["text"],
                  r["owner"], r["date"]] for r in insights.at_risk(s, today)], f"At risk {today}")
    if what == "grid":
        screens = s.scalars(select(Screen).where(Screen.active.is_(True)).order_by(Screen.code)).all()
        w0, _ = insights.week_of(today)
        weeks = insights.weekly_grid(s, screens, w0, 12)
        heads = ["Screen", "Name"] + [f"w/c {w0 + timedelta(days=7 * i):%d %b}" for i in range(12)]
        rows = [[sc.code, sc.name] + [("closed" if closed else f"{avg:.0f}%") for _, avg, _, closed in weeks[sc.id]]
                for sc in screens]
        return heads, rows, f"Occupancy 12 weeks from {w0}"
    if what == "pipeline":
        m0, m1 = insights.month_of(today)
        l0, l1 = insights.last_month(today)
        a, b = insights.pipeline(s, m0, m1), insights.pipeline(s, l0, l1)
        labels = {"requests": "Requests", "holds": "Holds placed", "confirmed": "Confirmed", "started": "Campaigns started"}
        return (["Stage", f"{m0:%B %Y}", f"{l0:%B %Y}"], [[labels[k], a[k], b[k]] for k in labels],
                f"Pipeline {m0:%Y-%m}")
    if what == "screens":
        screens = s.scalars(select(Screen).where(Screen.active.is_(True)).order_by(Screen.code)).all()
        w0, w1 = insights.week_of(today)
        m0, m1 = insights.month_of(today)
        _, wk = insights.occupancy(s, screens, w0, w1)
        _, mo = insights.occupancy(s, screens, m0, m1)
        return (["Screen", "Name", "Area", "This week", "This month"],
                [[sc.code, sc.name, sc.area, f"{wk.get(sc.id, 0):.0f}%", f"{mo.get(sc.id, 0):.0f}%"] for sc in screens],
                f"Occupancy by screen {today}")
    abort(404)


def _export(s, what, fmt, today):
    from .. import exports
    headers, rows, title = dashboard_table(s, what, today)
    resp = exports.respond(fmt, headers, rows, title)
    if resp is None:
        abort(404)
    return resp


@bp.route("/export/sheet", methods=["POST"])
@needs("insights")
def export_sheet():
    from flask import flash
    from .. import exports
    s = get_db()
    what = request.form.get("what", "")
    headers, rows, title = dashboard_table(s, what, date.today())
    try:
        tab = exports.to_sheet(s, headers, rows, title)
        flash(f"Written to the “{tab}” tab of the mirror sheet.", "ok")
    except Exception as e:  # noqa: BLE001 - Sheets not set up, or Google refused
        flash(f"Couldn't write to Google Sheets: {e}", "error")
    return redirect(url_for("main.dashboard"))


@bp.route("/summary", methods=["GET", "POST"])
@needs("insights")
def weekly_summary():
    """The Monday email, as it would go out now, with a button to send it."""
    from flask import flash
    from .. import summary
    s = get_db()
    if request.method == "POST":
        to = summary.send(s, date.today(), actor=current_user().email)
        s.commit()
        flash(f"Sent to {', '.join(to)}." if to else "Nobody to send it to: add an owner with an email address.",
              "ok" if to else "error")
        return redirect(url_for("main.weekly_summary"))
    subject, body = summary.compose(s, date.today())
    return render_template("summary.html", subject=subject, body=body, to=summary.recipients(s))


@bp.route("/search")
@login_required
def search():
    """One box for everything: bookings, clients, screens and requests."""
    from sqlalchemy import func as f
    from ..models import Client, Proposal
    s = get_db()
    q = (request.args.get("q") or "").strip()
    res = {"bookings": [], "clients": [], "screens": [], "proposals": []}
    if q:
        like = f"%{q.lower()}%"
        res["bookings"] = s.scalars(select(Booking).options(joinedload(Booking.client))
                                    .join(Booking.client)
                                    .where(or_(f.lower(Booking.code).like(like), f.lower(Booking.title).like(like),
                                               f.lower(Client.name).like(like)))
                                    .order_by(Booking.id.desc()).limit(25)).unique().all()
        res["clients"] = s.scalars(select(Client).where(or_(f.lower(Client.name).like(like), f.lower(Client.email).like(like)))
                                   .order_by(Client.name).limit(15)).all()
        res["screens"] = s.scalars(select(Screen).where(or_(f.lower(Screen.code).like(like), f.lower(Screen.name).like(like),
                                                          f.lower(Screen.area).like(like)))
                                   .order_by(Screen.code).limit(15)).all()
        res["proposals"] = s.scalars(select(Proposal).where(or_(f.lower(Proposal.ref).like(like),
                                                              f.lower(Proposal.subject).like(like),
                                                              f.lower(Proposal.sender).like(like),
                                                              f.lower(Proposal.sender_name).like(like)))
                                     .order_by(Proposal.id.desc()).limit(15)).all()
        exact = [b for b in res["bookings"] if b.code.lower() == q.lower()]
        if exact and sum(len(v) for v in res.values()) == 1:
            return redirect(url_for("bookings.booking_detail", code=exact[0].code))
    return render_template("search.html", q=q, res=res, total=sum(len(v) for v in res.values()))


@bp.route("/help")
@login_required
def help_page():
    """One-page guides for each role, in English and Arabic, ready to print for training."""
    from ..help_text import GUIDES, TEAM_ORDER
    lang = "ar" if request.args.get("lang") == "ar" else "en"
    role = request.args.get("role", "")
    chosen = [r for r in TEAM_ORDER if r == role] or list(TEAM_ORDER)
    return render_template("help.html", guides=GUIDES, roles=chosen, all_roles=TEAM_ORDER, glang=lang, role=role)


@bp.route("/performance")
@needs("insights")
def performance():
    from .. import exports, insights
    s = get_db()
    p = insights.performance(s, date.today())
    fmt = request.args.get("format")
    if fmt:
        rows = [["Request to hold (median hours)", p["to_hold"][0], p["to_hold"][1]],
                ["Confirmation to approved files (median hours)", p["to_files"][0], p["to_files"][1]]]
        rows += [[f"File mistake: {k}", n, ""] for k, n in p["mistakes"]]
        rows += [[f"Agent on its own: {k}", n, ""] for k, n in p["agent_work"].items()]
        rows += [[f"Changed by a person: {k}", f"{v['changed']} of {v['n']}", ""] for k, v in p["changed"].items()]
        return exports.respond(fmt, ["Measure", "Value", "Out of"], rows, f"Performance {date.today()}") or abort(404)
    return render_template("performance.html", p=p)


@bp.route("/health/full")
def health_full():
    """For an outside uptime check: the worker's heartbeat and every connection. 503 when something is down."""
    from .. import monitor
    try:
        with db.session_scope() as s:
            s.execute(text("SELECT 1"))
            age = monitor.worker_age(s)
            conns = monitor.status(s)
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, database=str(e)[:200]), 503
    worker_ok = age is not None and age <= monitor.WORKER_LATE
    down = [name for name, _, state, _ in conns if state == "down"]
    ok = worker_ok and not down
    return jsonify(ok=ok, database="ok", worker_ok=worker_ok,
                   worker_seconds_since_heartbeat=int(age.total_seconds()) if age is not None else None,
                   connections={name: state for name, _, state, _ in conns}, down=down), (200 if ok else 503)


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


@bp.route("/notices/settings", methods=["GET", "POST"])
@login_required
def notice_settings():
    """Each person chooses which notices they see in the app and get by email."""
    from ..activity import log
    from ..notices import TOPICS, pref
    s = get_db()
    u = current_user()
    if request.method == "POST":
        u.notify_prefs = {t: {"app": request.form.get(f"app_{t}") == "1", "email": request.form.get(f"email_{t}") == "1"}
                          for t in TOPICS}
        log(s, u.email, "changed", "user", u.email, "Notice settings changed")
        s.commit()
        from flask import flash
        flash("Saved.", "ok")
        return redirect(url_for("main.notice_settings"))
    return render_template("notice_settings.html", topics=TOPICS, pref=lambda t: pref(u, t))


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
