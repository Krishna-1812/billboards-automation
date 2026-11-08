"""Proposals (the team side): drafting from a request, editing the options, sending, and the client's answer."""

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from werkzeug.exceptions import Forbidden

from .. import bookings as svc
from .. import quotes as qs
from .. import settings
from ..models import QUOTE_OPEN, Client, Quote, QuoteOption, Screen
from ..web import current_user, f_str, get_db, login_required, needs

bp = Blueprint("quotes", __name__)


def _quote(s, ref):
    q = s.scalars(select(Quote).options(selectinload(Quote.options).selectinload(QuoteOption.lines))
                  .where(Quote.ref == ref)).first()
    if q is None:
        abort(404)
    return q


def _num(v):
    v = (v or "").replace(",", "").strip()
    return float(v) if v else None


def _why(e):
    return str(e.description if isinstance(e, Forbidden) else e)


@bp.route("/quotes")
@login_required
def quote_list():
    s = get_db()
    show = request.args.get("show", "open")
    q = select(Quote).options(selectinload(Quote.options).selectinload(QuoteOption.lines), selectinload(Quote.client))
    if show == "open":
        q = q.where(Quote.status.in_(QUOTE_OPEN))
    elif show == "accepted":
        q = q.where(Quote.status == "accepted")
    elif show == "closed":
        q = q.where(Quote.status.in_(("declined", "expired", "withdrawn")))
    rows = s.scalars(q.order_by(Quote.id.desc()).limit(300)).all()
    return render_template("quotes.html", rows=rows, show=show, counts=qs.counts(s), totals=qs.totals,
                           clients=s.scalars(select(Client).order_by(Client.name)).all())


@bp.route("/quotes/new", methods=["POST"])
@needs("hold")
def quote_new():
    s = get_db()
    client = s.get(Client, int(request.form.get("client_id") or 0))
    if client is None:
        flash("Choose the client.", "error")
        return redirect(url_for("quotes.quote_list"))
    q = qs.create(s, current_user(), client, title=f_str("title"))
    qs.add_option(s, q)
    s.commit()
    return redirect(url_for("quotes.quote_page", ref=q.ref))


@bp.route("/quotes/<ref>")
@login_required
def quote_page(ref):
    s = get_db()
    q = _quote(s, ref)
    checks = {}
    if q.status in QUOTE_OPEN:
        for o in q.options:
            for l in o.lines:
                checks[l.id] = qs.fits(s, l, exclude_booking_id=q.request.booking_id if q.request else None)
    screens = s.scalars(select(Screen).where(Screen.active.is_(True)).order_by(Screen.code)).all()
    return render_template("quote.html", q=q, totals=qs.totals, line_price=qs.line_price,
                           list_price=qs.line_list_price, plays=qs.line_plays, checks=checks, screens=screens,
                           editable=q.status in QUOTE_OPEN, limit=float(settings.get(s, "max_discount_pct") or 0))


@bp.route("/quotes/<ref>/details", methods=["POST"])
@needs("hold")
def quote_details(ref):
    s = get_db()
    q = _quote(s, ref)
    try:
        qs.editable(q)
        q.title = f_str("title")[:200]
        q.message = request.form.get("message", "").strip()
        q.internal_note = request.form.get("internal_note", "").strip()
        q.currency = (f_str("currency") or q.currency)[:3].upper()
        q.budget = _num(request.form.get("budget"))
        vu = request.form.get("valid_until", "")
        q.valid_until = date.fromisoformat(vu) if vu else None
    except (qs.QuoteError, ValueError) as e:
        s.rollback()
        flash(str(e) if isinstance(e, qs.QuoteError) else "Check the date and the budget.", "error")
        return redirect(url_for("quotes.quote_page", ref=ref))
    s.commit()
    flash("Saved.", "ok")
    return redirect(url_for("quotes.quote_page", ref=ref))


def _rows_from_form(s):
    f = request.form
    rows, errors = [], []
    fields = zip(f.getlist("screen_id"), f.getlist("start_date"), f.getlist("end_date"), f.getlist("start_hour"),
                 f.getlist("end_hour"), f.getlist("ad_seconds"), f.getlist("freq_mode"), f.getlist("freq_value"),
                 f.getlist("price"), f.getlist("bonus"))
    for sid, sd, ed, sh, eh, ad, mode, val, price, bonus in fields:
        if not sid:
            continue
        screen = s.get(Screen, int(sid))
        if screen is None:
            errors.append("Unknown screen.")
            continue
        try:
            start = date.fromisoformat(sd)
            end = date.fromisoformat(ed or sd)
            value = float(val or 1)
            rows.append({"screen_id": screen.id, "start_date": start, "end_date": end,
                         "start_hour": int(sh) if sh != "" else None, "end_hour": int(eh) if eh != "" else None,
                         "ad_seconds": int(float(ad)) if ad else screen.slot_seconds,
                         "plays_per_hour": value * screen.plays_per_hour_per_slot if mode == "slots" else value,
                         "exclusive": mode == "whole", "price": _num(price), "bonus": bonus == "1"})
        except ValueError:
            errors.append(f"{screen.code}: check the dates, hours, frequency and price.")
    return rows, errors


@bp.route("/quotes/<ref>/options/<int:oid>", methods=["POST"])
@needs("hold")
def option_save(ref, oid):
    s = get_db()
    q = _quote(s, ref)
    o = next((x for x in q.options if x.id == oid), None)
    if o is None:
        abort(404)
    rows, errors = _rows_from_form(s)
    try:
        if errors:
            raise qs.QuoteError(" ".join(dict.fromkeys(errors)))
        o.name = f_str("name")[:120] or o.name
        o.discount_pct = max(0.0, min(100.0, _num(request.form.get("discount_pct")) or 0.0))
        o.agreed_total = _num(request.form.get("agreed_total"))
        o.value_adds = request.form.get("value_adds", "").strip()
        qs.set_lines(s, current_user(), o, rows)
    except (qs.QuoteError, ValueError, Forbidden) as e:
        s.rollback()
        flash(_why(e), "error")
        return redirect(url_for("quotes.quote_page", ref=ref) + f"#opt{oid}")
    s.commit()
    flash(f"{o.name} saved.", "ok")
    return redirect(url_for("quotes.quote_page", ref=ref) + f"#opt{oid}")


@bp.route("/quotes/<ref>/options/<int:oid>/<action>", methods=["POST"])
@needs("hold")
def option_action(ref, oid, action):
    s = get_db()
    q = _quote(s, ref)
    o = next((x for x in q.options if x.id == oid), None)
    if o is None or action not in ("copy", "delete"):
        abort(404)
    try:
        qs.editable(q)
        if action == "copy":
            new = qs.add_option(s, q, f"{o.name} (copy)", [
                {"screen_id": l.screen_id, "start_date": l.start_date, "end_date": l.end_date,
                 "start_hour": l.start_hour, "end_hour": l.end_hour, "ad_seconds": l.ad_seconds,
                 "plays_per_hour": l.plays_per_hour, "exclusive": l.exclusive, "bonus": l.bonus, "price": l.price}
                for l in o.lines], o.discount_pct, o.value_adds)
            anchor = f"#opt{new.id}"
        else:
            if len(q.options) == 1:
                raise qs.QuoteError("A proposal needs at least one option.")
            q.options.remove(o)
            for i, x in enumerate(q.options):
                x.position = i
            anchor = ""
    except qs.QuoteError as e:
        s.rollback()
        flash(str(e), "error")
        return redirect(url_for("quotes.quote_page", ref=ref))
    s.commit()
    return redirect(url_for("quotes.quote_page", ref=ref) + anchor)


@bp.route("/quotes/<ref>/options/new", methods=["POST"])
@needs("hold")
def option_new(ref):
    s = get_db()
    q = _quote(s, ref)
    try:
        qs.editable(q)
        o = qs.add_option(s, q)
    except qs.QuoteError as e:
        flash(str(e), "error")
        return redirect(url_for("quotes.quote_page", ref=ref))
    s.commit()
    return redirect(url_for("quotes.quote_page", ref=ref) + f"#opt{o.id}")


@bp.route("/quotes/<ref>/send", methods=["POST"])
@needs("hold")
def quote_send(ref):
    s = get_db()
    q = _quote(s, ref)
    try:
        m = qs.send(s, current_user(), q, note=f_str("note"))
    except (qs.QuoteError, Forbidden) as e:
        s.rollback()
        flash(_why(e), "error")
        return redirect(url_for("quotes.quote_page", ref=ref))
    s.commit()
    flash(f"Version {q.version} is ready in the Outbox with its PDF. Check it and send it.", "ok")
    return redirect(url_for("inbox.outbox_message", message_id=m.id))


@bp.route("/quotes/<ref>/answer", methods=["POST"])
@needs("hold")
def quote_answer(ref):
    """The client answered by phone, email or WhatsApp: the team records it."""
    s = get_db()
    q = _quote(s, ref)
    u = current_user()
    kind = request.form.get("kind")
    try:
        if kind == "accept":
            o = next((x for x in q.options if x.id == int(request.form.get("option_id") or 0)), None)
            if o is None:
                raise qs.QuoteError("Choose the option the client accepted.")
            b = qs.accept(s, u, q, o)
            s.commit()
            flash(f"{o.name} accepted. Hold {b.code} is placed and waiting for the client's purchase order.", "ok")
            return redirect(url_for("bookings.booking_detail", code=b.code))
        if kind == "changes":
            qs.ask_changes(s, u.email, q, f_str("text"))
        elif kind == "decline":
            qs.decline(s, u.email, q, f_str("text"))
        elif kind == "withdraw":
            qs.withdraw(s, u, q)
        else:
            abort(400)
    except svc.Overbooked as e:
        s.rollback()
        flash(f"There's no longer room for this option: {e}. Change it and send it again.", "error")
        return redirect(url_for("quotes.quote_page", ref=ref))
    except (qs.QuoteError, svc.BookingError, Forbidden) as e:
        s.rollback()
        flash(_why(e), "error")
        return redirect(url_for("quotes.quote_page", ref=ref))
    s.commit()
    flash("Saved.", "ok")
    return redirect(url_for("quotes.quote_page", ref=ref))


@bp.route("/quotes/<ref>/pdf")
@login_required
def quote_pdf(ref):
    s = get_db()
    q = _quote(s, ref)
    v = request.args.get("v", type=int)
    snap = next((x.snapshot for x in q.versions if x.version == v), None) if v else None
    import io
    pdf = qs.to_pdf(q, snap or qs.snapshot(q), settings.get(s, "company_name"))
    name = f"{q.ref}-v{v}.pdf" if snap else f"{q.ref}-draft.pdf"
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", download_name=name, as_attachment=False)
