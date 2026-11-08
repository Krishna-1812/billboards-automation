"""The calendar grid, the vacancy check, bookings and clients."""

import calendar as cal
from datetime import date, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from .. import bookings as svc
from .. import settings, vacancy
from ..activity import log
from ..models import Activity, Booking, BookingLine, Client, Screen, Spec, STATUSES
from ..permissions import Forbidden, can
from ..web import a_date, current_user, done, f_str, get_db, login_required, needs

bp = Blueprint("bookings", __name__)

VIEWS = {"week": 7, "month": None, "quarter": 91}


def _active_screens(s, area="", spec_id=None):
    q = select(Screen).where(Screen.active.is_(True))
    if area:
        q = q.where(Screen.area == area)
    if spec_id:
        q = q.where(Screen.spec_id == spec_id)
    return s.scalars(q.order_by(Screen.area, Screen.code)).all()


def _areas(s):
    return [a for (a,) in s.execute(select(Screen.area).where(Screen.area != "").distinct().order_by(Screen.area))]


# ── Calendar ────────────────────────────────────────────────────────────────────

@bp.route("/calendar")
@login_required
def calendar_grid():
    s = get_db()
    view = request.args.get("view", "month")
    if view not in VIEWS:
        view = "month"
    today = date.today()
    start = a_date("start")
    if view == "week":
        start = start or today - timedelta(days=today.weekday())
        days, step = 7, timedelta(days=7)
    elif view == "month":
        start = (start or today).replace(day=1)
        days = cal.monthrange(start.year, start.month)[1]
        step = None
    else:
        start = start or today.replace(day=1)
        days, step = 91, timedelta(days=91)
    if step is None:
        prev_start = (start - timedelta(days=1)).replace(day=1)
        next_start = (start + timedelta(days=days))
    else:
        prev_start, next_start = start - step, start + step
    area = request.args.get("area", "")
    spec_id = request.args.get("spec", type=int)
    screens = _active_screens(s, area, spec_id)
    grid = vacancy.fill_grid(s, screens, start, days)
    dates = [start + timedelta(days=i) for i in range(days)]
    return render_template("calendar.html", view=view, start=start, dates=dates, screens=screens, grid=grid,
                           prev_start=prev_start, next_start=next_start, areas=_areas(s), area=area,
                           specs=s.scalars(select(Spec).order_by(Spec.name)).all(), spec_id=spec_id)


@bp.route("/calendar/<int:screen_id>/<day>")
@login_required
def calendar_day(screen_id, day):
    s = get_db()
    screen = s.get(Screen, screen_id) or abort(404)
    try:
        d = date.fromisoformat(day)
    except ValueError:
        abort(404)
    hours = vacancy.hourly_detail(s, screen, d)
    codes = sorted({u.booking_code for _, _, _, uses in hours for u in uses})
    found = s.scalars(select(Booking).options(joinedload(Booking.client), joinedload(Booking.lines))
                      .where(Booking.code.in_(codes))).unique().all() if codes else []
    return render_template("calendar_day.html", screen=screen, day=d, hours=hours, bookings=found,
                           prev_day=d - timedelta(days=1), next_day=d + timedelta(days=1))


# ── Vacancy check ───────────────────────────────────────────────────────────────

def _request_from(args, screen):
    """A vacancy request from form or query values, for one screen (frequency can be given in loop slots)."""
    start = date.fromisoformat(args.get("start_date"))
    end = date.fromisoformat(args.get("end_date") or args.get("start_date"))
    sh = args.get("start_hour", "").strip()
    eh = args.get("end_hour", "").strip()
    sh = int(sh) if sh != "" else None
    eh = int(eh) if eh != "" else None
    mode = args.get("freq_mode", "slots")
    value = float(args.get("freq_value") or 1)
    ad = int(float(args.get("ad_seconds") or screen.slot_seconds))
    exclusive = mode == "whole"
    pph = value * screen.plays_per_hour_per_slot if mode == "slots" else value
    return vacancy.Request(start, end, sh, eh, ad, pph, exclusive)


@bp.route("/availability", methods=["GET", "POST"])
@login_required
def availability():
    s = get_db()
    screens = _active_screens(s)
    results, form, error = None, request.values, None
    chosen = [int(x) for x in request.values.getlist("screen_id") if x.isdigit()]
    if request.method == "POST" or request.args.get("start_date"):
        try:
            if not chosen:
                raise ValueError("Choose at least one screen.")
            picked = [sc for sc in screens if sc.id in chosen]
            results = []
            for sc in picked:
                req = _request_from(form, sc)
                if req.end_date < req.start_date:
                    raise ValueError("The end date is before the start date.")
                if (req.start_hour is None) != (req.end_hour is None):
                    raise ValueError("Give both a from and a to hour, or neither.")
                r = vacancy.check_many(s, [sc], req, today=date.today())[0]
                results.append((sc, req, r))
        except (ValueError, TypeError) as e:
            error = str(e) if str(e) else "Check the dates and numbers."
            results = None
    return render_template("availability.html", screens=screens, areas=_areas(s), form=form, chosen=chosen,
                           results=results, error=error)


# ── Bookings ────────────────────────────────────────────────────────────────────

@bp.route("/bookings")
@login_required
def booking_list():
    s = get_db()
    q = (select(Booking).options(joinedload(Booking.client), joinedload(Booking.lines).joinedload(BookingLine.screen))
         .order_by(Booking.id.desc()))
    status = request.args.get("status", "")
    if status == "open":
        q = q.where(Booking.status.notin_(("cancelled", "expired", "completed")))
    elif status in STATUSES:
        q = q.where(Booking.status == status)
    if request.args.get("flag") == "override":
        q = q.where(Booking.override_reason != "")
    client_id = request.args.get("client", type=int)
    if client_id:
        q = q.where(Booking.client_id == client_id)
    screen_id = request.args.get("screen", type=int)
    if screen_id:
        q = q.where(Booking.lines.any(BookingLine.screen_id == screen_id))
    term = request.args.get("q", "").strip()
    if term:
        like = f"%{term.lower()}%"
        q = q.join(Client).where(or_(func.lower(Booking.code).like(like), func.lower(Booking.title).like(like),
                                     func.lower(Client.name).like(like)))
    items = s.scalars(q.limit(500)).unique().all()
    return render_template("bookings.html", items=items, status=status, term=term,
                           clients=s.scalars(select(Client).order_by(Client.name)).all(),
                           screens=s.scalars(select(Screen).order_by(Screen.code)).all(),
                           client_id=client_id, screen_id=screen_id)


def _lines_from_form(s):
    """LineIn list from the booking form's repeated fields."""
    f = request.form
    rows = zip(f.getlist("line_id"), f.getlist("screen_id"), f.getlist("start_date"), f.getlist("end_date"),
               f.getlist("start_hour"), f.getlist("end_hour"), f.getlist("ad_seconds"), f.getlist("freq_mode"),
               f.getlist("freq_value"))
    lines, errors = [], []
    for lid, sid, sd, ed, sh, eh, ad, mode, val in rows:
        if not sid:
            if sd or ed:
                errors.append("Choose a screen for every line, or remove the lines you don't want.")
            continue
        screen = s.get(Screen, int(sid))
        if screen is None:
            errors.append("Unknown screen.")
            continue
        try:
            start = date.fromisoformat(sd)
            end = date.fromisoformat(ed or sd)
        except ValueError:
            errors.append(f"{screen.code}: give a start and an end date.")
            continue
        try:
            value = float(val or 1)
            ad_s = int(float(ad)) if ad else screen.slot_seconds
            pph = value * screen.plays_per_hour_per_slot if mode == "slots" else value
            lines.append(svc.LineIn(screen_id=screen.id, start_date=start, end_date=end,
                                    start_hour=int(sh) if sh != "" else None, end_hour=int(eh) if eh != "" else None,
                                    ad_seconds=ad_s, plays_per_hour=pph, exclusive=(mode == "whole"),
                                    id=int(lid) if lid else None))
        except ValueError:
            errors.append(f"{screen.code}: check the hours, ad length and frequency.")
    return lines, errors


def _client_from_form(s):
    cid = request.form.get("client_id", "")
    if cid == "new" or not cid:
        name = f_str("new_client")
        if not name:
            return None
        existing = s.scalars(select(Client).where(func.lower(Client.name) == name.lower())).first()
        if existing:
            return existing
        c = Client(name=name, email=f_str("new_client_email"))
        s.add(c)
        s.flush()
        log(s, current_user().email, "created", "client", c.id, f"New client {name}")
        return c
    return s.get(Client, int(cid))


def _form_lines_for(booking):
    out = []
    for l in booking.lines:
        per_slot = l.screen.plays_per_hour_per_slot
        slots = l.plays_per_hour / per_slot if per_slot else 0
        if l.exclusive:
            mode, value = "whole", 1
        elif abs(slots - round(slots)) < 1e-6 and slots >= 1:
            mode, value = "slots", int(round(slots))
        else:
            mode, value = "pph", l.plays_per_hour
        out.append({"id": l.id, "screen_id": l.screen_id, "start_date": l.start_date.isoformat(),
                    "end_date": l.end_date.isoformat(), "start_hour": "" if l.start_hour is None else l.start_hour,
                    "end_hour": "" if l.end_hour is None else l.end_hour, "ad_seconds": l.ad_seconds,
                    "freq_mode": mode, "freq_value": f"{value:g}"})
    return out


def _render_form(s, booking=None, lines=None, overbooked=None, status=400):
    return render_template("booking_form.html", booking=booking, lines=lines or [{}],
                           clients=s.scalars(select(Client).order_by(Client.name)).all(),
                           screens=_active_screens(s), overbooked=overbooked, form=request.form), status


@bp.route("/bookings/new", methods=["GET", "POST"])
@needs("hold")
def booking_new():
    s = get_db()
    if request.method == "GET":
        a = request.args
        prefill = [{"screen_id": int(x), "start_date": a.get("start_date", ""), "end_date": a.get("end_date", ""),
                    "start_hour": a.get("start_hour", ""), "end_hour": a.get("end_hour", ""),
                    "ad_seconds": a.get("ad_seconds", ""), "freq_mode": a.get("freq_mode", "slots"),
                    "freq_value": a.get("freq_value", "1")}
                   for x in a.getlist("screen_id") if x.isdigit()]
        return _render_form(s, lines=prefill or [{}], status=200)
    lines, errors = _lines_from_form(s)
    client = _client_from_form(s)
    if client is None:
        errors.append("Choose a client, or type a new client's name.")
    if errors:
        for e in dict.fromkeys(errors):
            flash(e, "error")
        s.rollback()
        return _render_form(s, lines=[_line_echo(l) for l in lines] or [{}])
    status = "confirmed" if request.form.get("status") == "confirmed" else "hold"
    try:
        if status == "confirmed" and not can(current_user(), "confirm"):
            raise Forbidden("confirm")
        b = svc.create(s, current_user(), client, lines, title=f_str("title"), status="hold",
                       override_reason=f_str("override_reason"), notes=f_str("notes"))
        if status == "confirmed":       # straight to confirmed: the same rule as confirming a hold (the PO)
            svc.confirm(s, current_user(), b, po_number=f_str("po_number"), no_po_reason=f_str("no_po_reason"))
    except svc.Overbooked as e:
        s.rollback()
        flash("There isn't room for all of this. See below.", "error")
        return _render_form(s, lines=[_line_echo(l) for l in lines], overbooked=e.results)
    except (svc.BookingError, Forbidden) as e:
        s.rollback()
        flash(_why(e), "error")
        return _render_form(s, lines=[_line_echo(l) for l in lines])
    s.commit()
    return done(f"{b.code} saved as a {'hold' if status == 'hold' else 'confirmed booking'}.",
                "bookings.booking_detail", code=b.code)


def _why(e):
    if isinstance(e, Forbidden):
        return ("Only a Manager or the owner can do that." if str(e) != "override"
                else "Only a Manager or the owner can book past a screen's capacity.")
    return str(e)


def _line_echo(li):
    return {"id": li.id, "screen_id": li.screen_id, "start_date": li.start_date.isoformat(),
            "end_date": li.end_date.isoformat(), "start_hour": "" if li.start_hour is None else li.start_hour,
            "end_hour": "" if li.end_hour is None else li.end_hour, "ad_seconds": li.ad_seconds,
            "freq_mode": "whole" if li.exclusive else "pph", "freq_value": f"{li.plays_per_hour:g}"} if li else {}


def _booking(s, code):
    b = s.scalars(select(Booking).options(joinedload(Booking.client),
                                          joinedload(Booking.lines).joinedload(BookingLine.screen))
                  .where(Booking.code == code)).unique().first()
    if b is None:
        abort(404)
    return b


@bp.route("/bookings/<code>")
@login_required
def booking_detail(code):
    s = get_db()
    b = _booking(s, code)
    checks = []
    if b.status not in ("cancelled", "expired", "completed"):
        checks = [(l, vacancy.check_screen(s, l.screen,
                                           vacancy.Request(l.start_date, l.end_date, l.start_hour, l.end_hour,
                                                           l.ad_seconds, l.plays_per_hour, l.exclusive),
                                           exclude_booking_id=b.id, alternatives=False)) for l in b.lines]
    history = s.scalars(select(Activity).where(Activity.entity_type == "booking", Activity.entity_id == b.code)
                        .order_by(Activity.id.desc())).all()
    from ..models import BookingDocument, Message, Proposal
    docs = s.scalars(select(BookingDocument).where(BookingDocument.booking_id == b.id)).all()
    proposal = s.scalars(select(Proposal).where(Proposal.booking_id == b.id)).first()
    messages = s.scalars(select(Message).where(Message.booking_id == b.id).order_by(Message.id)).all()
    from .creatives import booking_panel
    from .live import booking_panel as live_panel
    from ..models import Quote
    quote = s.scalars(select(Quote).where(Quote.booking_id == b.id)).first()
    return render_template("booking_detail.html", b=b, checks=checks, history=history, docs=docs,
                           proposal=proposal, messages=messages, files=booking_panel(s, b), live=live_panel(s, b),
                           quote=quote, po_gap=svc.po_mismatch(b), po_required=settings.get(s, "po_required"),
                           currency=settings.get(s, "currency"))


@bp.route("/bookings/<code>/edit", methods=["GET", "POST"])
@login_required
def booking_edit(code):
    s = get_db()
    b = _booking(s, code)
    if not can(current_user(), "hold" if b.status == "hold" else "edit_confirmed"):
        abort(403)
    if request.method == "GET":
        return _render_form(s, booking=b, lines=_form_lines_for(b), status=200)
    lines, errors = _lines_from_form(s)
    if errors:
        for e in dict.fromkeys(errors):
            flash(e, "error")
        return _render_form(s, booking=b, lines=[_line_echo(l) for l in lines] or [{}])
    try:
        svc.update(s, current_user(), b, lines, title=f_str("title"), notes=f_str("notes"),
                   override_reason=f_str("override_reason"))
    except svc.Overbooked as e:
        s.rollback()
        flash("There isn't room for this change. See below.", "error")
        return _render_form(s, booking=_booking(s, code), lines=[_line_echo(l) for l in lines], overbooked=e.results)
    except (svc.BookingError, Forbidden) as e:
        s.rollback()
        flash(_why(e), "error")
        return _render_form(s, booking=_booking(s, code), lines=[_line_echo(l) for l in lines])
    s.commit()
    return done(f"{b.code} updated.", "bookings.booking_detail", code=b.code)


@bp.route("/bookings/<code>/<action>", methods=["POST"])
@login_required
def booking_action(code, action):
    s = get_db()
    b = _booking(s, code)
    u = current_user()
    try:
        if action in ("confirm", "po"):
            f = request.files.get("document")
            doc = (f.filename, f.mimetype or "", f.read()) if f and f.filename else None
            amount = request.form.get("po_amount", "").replace(",", "").strip()
            amount = float(amount) if amount else None
            if action == "po":
                if not can(u, "hold"):
                    abort(403)
                svc.record_po(s, u.email, b, f_str("po_number"), amount, doc)
                gap = svc.po_mismatch(b)
                msg = "Purchase order added." + (f" {gap}" if gap else "")
            else:
                svc.confirm(s, u, b, document=doc, po_number=f_str("po_number") if doc or f_str("po_number") != b.po_number
                            else "", po_amount=amount, no_po_reason=f_str("no_po_reason"))
                msg = f"{b.code} is confirmed."
        elif action == "extend":
            days = max(1, min(30, int(request.form.get("days") or 1)))
            svc.extend_hold(s, u, b, days)
            msg = f"Hold extended by {days} day(s)."
        elif action == "cancel":
            svc.cancel(s, u, b, f_str("reason"))
            msg = f"{b.code} is cancelled and its screen time is free."
        else:
            abort(404)
    except (svc.BookingError, Forbidden) as e:
        s.rollback()
        flash(_why(e), "error")
        return redirect(url_for("bookings.booking_detail", code=code))
    except ValueError:
        s.rollback()
        flash("The amount should be a number, for example 120000.", "error")
        return redirect(url_for("bookings.booking_detail", code=code) + "#po")
    s.commit()
    return done(msg, "bookings.booking_detail", code=code)


# ── Clients ─────────────────────────────────────────────────────────────────────

@bp.route("/clients")
@login_required
def client_list():
    s = get_db()
    counts = dict(s.execute(select(Booking.client_id, func.count())
                            .where(Booking.status.notin_(("cancelled", "expired")))
                            .group_by(Booking.client_id)).all())
    return render_template("clients.html", items=s.scalars(select(Client).order_by(Client.name)).all(),
                           counts=counts)


CLIENT_FIELDS = ("name", "contact_name", "email", "phone", "whatsapp", "language", "notes")


@bp.route("/clients/new", methods=["GET", "POST"])
@bp.route("/clients/<int:client_id>", methods=["GET", "POST"])
@login_required
def client_edit(client_id=None):
    s = get_db()
    c = s.get(Client, client_id) if client_id else Client(language="en")
    if client_id and c is None:
        abort(404)
    if request.method == "POST":
        if not can(current_user(), "edit_clients"):
            abort(403)
        name = f_str("name")
        if not name:
            flash("A client needs a name.", "error")
        elif s.scalars(select(Client).where(func.lower(Client.name) == name.lower(),
                                            Client.id != (c.id or 0))).first():
            flash(f"There is already a client called {name}.", "error")
        else:
            for f in CLIENT_FIELDS:
                setattr(c, f, f_str(f))
            if c.language not in ("en", "ar"):
                c.language = "en"
            c.preferred_channel = f_str("preferred_channel") if f_str("preferred_channel") in ("email", "whatsapp") \
                else ""
            if not c.id:
                s.add(c)
            s.flush()
            log(s, current_user().email, "saved", "client", c.id, f"Client {c.name} saved")
            s.commit()
            return done(f"{c.name} saved.", "bookings.client_edit", client_id=c.id)
    history = []
    if c.id:
        history = s.scalars(select(Booking).options(joinedload(Booking.lines))
                            .where(Booking.client_id == c.id).order_by(Booking.id.desc())).unique().all()
    from .portal import access_panel
    from .. import insights
    from .. import quotes as qs
    from ..models import Quote
    client_quotes = s.scalars(select(Quote).where(Quote.client_id == c.id).order_by(Quote.id.desc()).limit(20)).all() \
        if c.id else []
    return render_template("client_form.html", c=c, history=history, portal_users=access_panel(s, c) if c.id else [],
                           client_quotes=client_quotes, quote_totals=qs.totals,
                           stats=insights.client_stats(s, c) if c.id else None)
