"""The public pages, open to everyone: the front page about the company, becoming a client, and the way in.

A visitor reads about the company, then either signs in (clients by an emailed link, the team with Google) or asks
to become a client. That request lands in Requests like any email, with channel "web", and the team replies, sends
a proposal and gives the new client portal access from the client's page.
"""

import re
from collections import Counter
from datetime import date

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from sqlalchemy import select

from .. import intake, pricing, settings
from ..models import Screen
from ..notices import alert
from ..public_text import tw
from ..web import current_user, f_str, get_db

bp = Blueprint("public", __name__)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SHOW_SCREENS = 9


def lang():
    wanted = request.args.get("lang")
    if wanted in ("en", "ar"):
        session["portal_lang"] = wanted
    return session.get("portal_lang") or "en"


@bp.app_context_processor
def _public_globals():
    if (request.endpoint or "") not in PUBLIC_PAGES:
        return {}
    lg = lang()
    s = get_db()
    u = current_user()
    cfg = current_app.config["APP"]
    return {"plang": lg, "tw": lambda k, **kw: tw(k, lg, **kw), "company": settings.get(s, "company_name"),
            "public_email": settings.get(s, "public_email"), "public_phone": settings.get(s, "public_phone"),
            "viewer": "client" if u is not None and u.role == "client" else ("team" if u is not None else None),
            "open_access": not cfg.LOGIN_REQUIRED, "year": date.today().year}


def _loop(screens):
    """The loop most screens use, and how often one slot of it plays."""
    if screens:
        (loop_s, slot_s), _ = Counter((x.loop_seconds, x.slot_seconds) for x in screens).most_common(1)[0]
        hours = Counter(pricing.hours_per_day(x.open_hour, x.close_hour) for x in screens).most_common(1)[0][0]
    else:
        loop_s, slot_s, hours = 120, 10, 18
    loop_s, slot_s = max(1, loop_s), max(1, slot_s)
    slots = max(1, loop_s // slot_s)
    per_hour = round(3600 / loop_s)
    return {"loop": loop_s, "slot": slot_s, "slots": slots, "per_hour": per_hour, "per_day": per_hour * hours,
            "hours": hours}


@bp.route("/welcome")
def welcome():
    s = get_db()
    screens = s.scalars(select(Screen).where(Screen.active.is_(True)).order_by(Screen.area, Screen.code)).all()
    show = bool(settings.get(s, "public_show_screens")) and bool(screens)
    areas = sorted({x.area.strip() for x in screens if (x.area or "").strip()})
    loop = _loop(screens)
    stats = None
    if show:
        stats = {"screens": len(screens), "areas": len(areas), "plays": loop["per_day"],
                 "hours": max(pricing.hours_per_day(x.open_hour, x.close_hour) for x in screens)}
    return render_template("welcome.html", screens=screens[:SHOW_SCREENS] if show else [],
                           more=max(0, len(screens) - SHOW_SCREENS) if show else 0, areas=areas if show else [],
                           stats=stats, lp=loop, about=(settings.get(s, "public_about") or "").strip(),
                           hours_per_day=pricing.hours_per_day)


def _signup_body(form):
    kind = "an agency" if form.get("kind") == "agency" else "a brand"
    lines = [f"Request from the website sign-up ({kind})",
             f"Name: {form['name']}", f"Company or brand: {form['company']}", f"Email: {form['email']}"]
    if form.get("phone"):
        lines.append(f"Phone or WhatsApp: {form['phone']}")
    lines += [f"Screens or areas: {form.get('where') or 'Any'}",
              f"Dates: {form.get('start') or '?'} to {form.get('end') or '?'}",
              f"Budget: {form.get('budget') or 'Not given'}"]
    if form.get("notes"):
        lines.append(f"Notes: {form['notes']}")
    return "\n".join(lines)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    lg = lang()
    form = {k: f_str(k) for k in ("name", "company", "email", "phone", "kind", "start", "end", "where", "budget",
                                  "notes")} if request.method == "POST" else {"kind": "brand"}
    if request.method == "GET":
        return render_template("signup.html", form=form, error="")
    if f_str("website"):                     # filled in only by bots: thank them and keep nothing
        session["signup_done"] = {"ref": "", "name": form["name"][:60], "email": form["email"][:120]}
        return redirect(url_for("public.signup_done"))
    error = ""
    if not (form["name"] and form["company"] and form["email"]):
        error = tw("su_need", lg)
    elif not EMAIL.match(form["email"]) or len(form["email"]) > 254:
        error = tw("su_bad_email", lg)
    else:
        try:
            a = date.fromisoformat(form["start"]) if form["start"] else None
            b = date.fromisoformat(form["end"]) if form["end"] else None
        except ValueError:
            a = b = None
            form["start"] = form["end"] = ""
        if a and b and b < a:
            error = tw("su_bad_dates", lg)
    if error:
        return render_template("signup.html", form=form, error=error), 400
    form = {k: v[:2000] if k == "notes" else v[:200] for k, v in form.items()}
    s = get_db()
    p, _, _ = intake.receive(s, channel="web", sender=form["email"].lower(), sender_name=form["name"],
                             subject=f"Website: {form['company']}"[:300], body=_signup_body(form),
                             actor=form["email"].lower())
    if lg == "ar":
        p.language = "ar"
    p.reading = {"signup": form}        # the fields as typed: the draft is filled in from them without Claude
    alert(s, "proposals", f"New client sign-up {p.ref} from {form['company']} on the website.",
          link=f"/inbox/{p.ref}", subject=f"New client sign-up: {form['company']}")
    s.commit()
    session["signup_done"] = {"ref": p.ref, "name": form["name"][:60], "email": form["email"][:120]}
    return redirect(url_for("public.signup_done"))


@bp.route("/signup/thanks")
def signup_done():
    done = session.get("signup_done")
    if not done:
        return redirect(url_for("public.signup"))
    return render_template("signup.html", form={}, error="", done=done)


PUBLIC_PAGES = {"public.welcome", "public.signup", "public.signup_done", "auth.login"}
