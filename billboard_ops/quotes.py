"""Proposals to clients: the media owner's answer to a request, then the negotiation, then acceptance.

The steps, as the team works them:

1. A request comes in (inbox). `draft_from_request` makes a proposal with options: what was asked for, other free
   screens or dates when something doesn't fit, and one within the client's budget when they gave one.
2. The team edits the options: screens, dates, frequency, price, discount and value adds (bonus lines, extra
   weeks...). Prices start from the rate card (pricing.py); any price can be changed.
3. `send` puts out a version: the PDF and a portal link, as a draft in the Outbox. The sent version is kept, so the
   negotiation can be followed version by version. A discount above the limit in Settings needs a Manager.
4. The client asks for changes, accepts one option, or declines, in the portal or by telling the team.
5. Accepting makes the hold (or brings the request's hold in line) with the agreed price. The purchase order comes
   next (bookings.record_po), then confirmation.
"""

import io
from datetime import date, timedelta

from sqlalchemy import func, select

from . import bookings, drafts, pricing, settings, vacancy
from .activity import log
from .bookings import LineIn, actor_of
from .models import QUOTE_OPEN, Attachment, Client, Quote, QuoteLine, QuoteOption, QuoteVersion, Screen, utcnow
from .notices import alert
from .permissions import can, require

LETTERS = "ABCDEFGH"


class QuoteError(Exception):
    pass


# ── Numbers ─────────────────────────────────────────────────────────────────────

def next_ref(session, year=None):
    year = year or date.today().year
    prefix = f"QT-{year}-"
    last = session.scalars(select(Quote.ref).where(Quote.ref.like(prefix + "%")).order_by(Quote.id.desc())).first()
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:04d}"


def line_list_price(line):
    return pricing.list_price(line.screen, line.start_date, line.end_date, line.start_hour, line.end_hour,
                              line.ad_seconds, line.plays_per_hour, line.exclusive)


def line_price(line):
    """What the line is charged before the option's discount: its own price, else the rate card. Bonus lines: 0."""
    if line.bonus:
        return 0.0
    return line.price if line.price is not None else line_list_price(line)


def line_plays(line):
    return pricing.plays(line.screen, line.start_date, line.end_date, line.start_hour, line.end_hour,
                         line.plays_per_hour, line.exclusive)


def totals(option):
    """The option's money: gross (lines), discount, net (or the agreed total), and what the bonus lines are worth.
    `missing` lists screens with no price yet: the option can't be sent until each has one."""
    gross, missing, bonus_value, plays = 0.0, [], 0.0, 0
    for l in option.lines:
        plays += line_plays(l)
        if l.bonus:
            bonus_value += line_list_price(l) or 0.0
            continue
        p = line_price(l)
        if p is None:
            missing.append(l.screen.code)
        else:
            gross += p
    discount = round(gross * (option.discount_pct or 0) / 100.0)
    net = option.agreed_total if option.agreed_total is not None else gross - discount
    if option.agreed_total is not None:
        discount = gross - option.agreed_total
    return {"gross": gross, "discount": discount, "net": net, "bonus_value": bonus_value, "missing": missing,
            "plays": plays, "discount_pct": (100.0 * discount / gross) if gross else 0.0}


def fits(session, line, exclude_booking_id=None):
    return vacancy.check_screen(session, line.screen,
                                vacancy.Request(line.start_date, line.end_date, line.start_hour, line.end_hour,
                                                line.ad_seconds, line.plays_per_hour, line.exclusive),
                                exclude_booking_id=exclude_booking_id, alternatives=False)


def is_open(quote):
    return quote.status in QUOTE_OPEN


# ── Making a proposal ───────────────────────────────────────────────────────────

def create(session, user, client, title="", request=None, currency=None, budget=None):
    require(user, "hold")
    days = int(settings.get(session, "quote_valid_days") or 7)
    q = Quote(ref=next_ref(session), client=client, request=request, title=title[:200],
              currency=(currency or settings.get(session, "currency") or "AED")[:3].upper(), budget=budget,
              valid_until=date.today() + timedelta(days=days), created_by=actor_of(user))
    session.add(q)
    session.flush()
    log(session, actor_of(user), "created", "quote", q.ref, f"New proposal for {client.name}")
    return q


def add_option(session, quote, name="", lines=(), discount_pct=0.0, value_adds=""):
    opt = QuoteOption(quote=quote, position=len(quote.options), discount_pct=discount_pct or 0.0,
                      value_adds=value_adds, name=name or f"Option {LETTERS[len(quote.options) % len(LETTERS)]}")
    for li in lines:
        opt.lines.append(QuoteLine(**li))
    session.add(opt)
    session.flush()
    return opt


def _request_lines(session, request):
    out = []
    for ln in (request.draft or {}).get("lines") or []:
        try:
            screen = session.get(Screen, int(ln.get("screen_id") or 0))
            start = date.fromisoformat(ln["start_date"])
            end = date.fromisoformat(ln.get("end_date") or ln["start_date"])
        except (TypeError, ValueError, KeyError):
            continue
        if screen is None or end < start:
            continue
        hours = [None if ln.get(k) in (None, "") else int(ln.get(k)) for k in ("start_hour", "end_hour")]
        out.append({"screen": screen, "start_date": start, "end_date": end,
                    "start_hour": hours[0], "end_hour": hours[1],
                    "ad_seconds": int(ln.get("ad_seconds") or screen.slot_seconds or 10),
                    "plays_per_hour": float(ln.get("plays_per_hour") or 3600.0 / max(1, screen.loop_seconds)),
                    "exclusive": bool(ln.get("exclusive"))})
    return out


def _request_budget(request):
    b = ((request.draft or {}).get("budget") or {}) if request is not None else {}
    try:
        amount = float(b.get("amount")) if b.get("amount") not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    return amount, (b.get("currency") or "").strip().upper()[:3]


def draft_from_request(session, user, request):
    """A proposal for a request, with up to three options: as asked; other free screens or dates for what doesn't
    fit; and one scaled to the budget when the client gave one and the first option is over it."""
    client = request.client or (session.get(Client, request.client_id) if request.client_id else None)
    if client is None:
        raise QuoteError("Choose the client on the request first.")
    asked = _request_lines(session, request)
    if not asked:
        raise QuoteError("The request has no screens and dates yet. Fill them in on the request first.")
    budget, cur = _request_budget(request)
    d = request.draft or {}
    subject = (d.get("title") or d.get("campaign") or request.subject or "").strip()
    q = create(session, user, client, title=subject, request=request,
               currency=cur or None, budget=budget)
    a = add_option(session, q, "Option A: as asked", asked)

    # Option B: for each line that doesn't fit, a free screen nearby or the nearest free dates.
    alt, changed = [], False
    for li, line in zip(asked, a.lines):
        res = fits(session, line)
        if res.status == "fits":
            alt.append(dict(li))
            continue
        others = vacancy.other_screens(session, li["screen"], vacancy.Request(
            li["start_date"], li["end_date"], li["start_hour"], li["end_hour"], li["ad_seconds"],
            li["plays_per_hour"], li["exclusive"]), limit=1)
        if others:
            alt.append(dict(li, screen=others[0]))
            changed = True
            continue
        dates = vacancy.check_screen(session, li["screen"], vacancy.Request(
            li["start_date"], li["end_date"], li["start_hour"], li["end_hour"], li["ad_seconds"],
            li["plays_per_hour"], li["exclusive"])).alt_dates or []
        if dates:
            s, e = dates[0]
            alt.append(dict(li, start_date=s, end_date=e))
            changed = True
    if changed and alt:
        add_option(session, q, "Option B: free screens and dates", alt)

    # Option C: within the budget, by playing less often (never below one play an hour).
    net = totals(a)["net"]
    if budget and net and net > budget and not totals(a)["missing"]:
        scale = budget / net
        within = [dict(li, plays_per_hour=max(1.0, round(li["plays_per_hour"] * scale, 1)), exclusive=False)
                  for li in asked]
        add_option(session, q, "Option C: within your budget", within)
    session.flush()
    log(session, actor_of(user), "drafted", "quote", q.ref,
        f"Drafted {len(q.options)} option(s) from request {request.ref}")
    return q


# ── Editing ─────────────────────────────────────────────────────────────────────

def editable(quote):
    if quote.status not in QUOTE_OPEN:
        raise QuoteError(f"This proposal is {quote.status.replace('_', ' ')} and can't be changed.")


def set_lines(session, user, option, rows):
    """Replace an option's lines with `rows` (dicts with screen_id, dates, hours, frequency, price, bonus)."""
    require(user, "hold")
    editable(option.quote)
    errors = []
    new = []
    for r in rows:
        screen = session.get(Screen, r["screen_id"])
        li = LineIn(r["screen_id"], r["start_date"], r["end_date"], r.get("start_hour"), r.get("end_hour"),
                    r.get("ad_seconds") or 10, r.get("plays_per_hour") or 30, bool(r.get("exclusive")))
        errors += bookings.validate_line(screen, li)
        new.append(QuoteLine(screen_id=r["screen_id"], start_date=r["start_date"], end_date=r["end_date"],
                             start_hour=r.get("start_hour"), end_hour=r.get("end_hour"),
                             ad_seconds=li.ad_seconds, plays_per_hour=li.plays_per_hour, exclusive=li.exclusive,
                             bonus=bool(r.get("bonus")), price=r.get("price")))
    if errors:
        raise QuoteError(" ".join(dict.fromkeys(errors)))
    if not new:
        raise QuoteError("An option needs at least one screen.")
    option.lines.clear()
    session.flush()
    for l in new:
        option.lines.append(l)
    session.flush()
    for l in option.lines:
        session.refresh(l)


def check_discount(session, user, quote):
    """A discount above the limit in Settings needs a Manager (or the owner) to send it."""
    limit = float(settings.get(session, "max_discount_pct") or 0)
    if can(user, "confirm"):
        return
    for o in quote.options:
        t = totals(o)
        if t["gross"] and t["discount_pct"] > limit + 0.01:
            raise QuoteError(f"{o.name} has a {t['discount_pct']:.0f}% discount. Above {limit:.0f}% a Manager "
                             "sends it.")


# ── Sending, with versions ──────────────────────────────────────────────────────

def snapshot(quote):
    opts = []
    for o in quote.options:
        t = totals(o)
        opts.append({"id": o.id, "name": o.name, "discount_pct": o.discount_pct, "value_adds": o.value_adds,
                     "agreed_total": o.agreed_total, "totals": t,
                     "lines": [{"screen": l.screen.code, "screen_name": l.screen.name,
                                "start": l.start_date.isoformat(), "end": l.end_date.isoformat(),
                                "start_hour": l.start_hour, "end_hour": l.end_hour, "ad_seconds": l.ad_seconds,
                                "plays_per_hour": l.plays_per_hour, "exclusive": l.exclusive, "bonus": l.bonus,
                                "price": line_price(l), "list_price": line_list_price(l), "plays": line_plays(l)}
                               for l in o.lines]})
    return {"ref": quote.ref, "currency": quote.currency, "valid_until": quote.valid_until.isoformat()
            if quote.valid_until else None, "message": quote.message, "options": opts}


def contact(session, quote):
    """Where the proposal goes: back the way the request came, unless the client prefers another way."""
    from .intake import _lang, _last_inbound_id, _name, _recipient
    c = quote.client
    p = quote.request
    if p is not None:
        channel, to = _recipient(session, p)
        out = {"channel": channel, "to": to, "lang": _lang(p), "name": _name(p), "reply": _last_inbound_id(p),
               "proposal": p}
        target = {"email": c.email, "whatsapp": c.whatsapp}.get(c.preferred_channel or "", "")
        if target and c.preferred_channel != channel:
            out.update(channel=c.preferred_channel, to=target, reply="")
        return out
    lang = c.language if c.language in ("en", "ar") else "en"
    if c.preferred_channel == "whatsapp" and c.whatsapp:
        return {"channel": "whatsapp", "to": c.whatsapp, "lang": lang, "name": c.contact_name, "reply": "",
                "proposal": None}
    if c.email or c.whatsapp:
        return {"channel": "email" if c.email else "whatsapp", "to": c.email or c.whatsapp, "lang": lang,
                "name": c.contact_name, "reply": "", "proposal": None}
    return {"channel": "manual", "to": "", "lang": lang, "name": c.contact_name, "reply": "", "proposal": None}


def send(session, user, quote, note=""):
    """Put out the next version: kept as sent, with the PDF and the portal link in a draft to the client."""
    require(user, "hold")
    editable(quote)
    if not quote.options or not all(o.lines for o in quote.options):
        raise QuoteError("Every option needs at least one screen.")
    for o in quote.options:
        missing = totals(o)["missing"]
        if missing:
            raise QuoteError(f"{o.name}: set a price for {', '.join(dict.fromkeys(missing))} (the screen has no "
                             "rate on the rate card yet).")
    check_discount(session, user, quote)
    quote.version = (quote.version or 0) + 1
    quote.status = "sent"
    quote.sent_at = utcnow()
    if quote.valid_until is None or quote.valid_until < date.today():
        quote.valid_until = date.today() + timedelta(days=int(settings.get(session, "quote_valid_days") or 7))
    snap = snapshot(quote)
    session.add(QuoteVersion(quote=quote, version=quote.version, snapshot=snap, note=note.strip(),
                             sent_by=actor_of(user)))
    who = contact(session, quote)
    from .client_messages import portal_url
    base = portal_url()
    link = f"{base}/quotes/{quote.ref}" if base else ""
    subject, body = drafts.proposal(who["lang"], who["name"], settings.get(session, "company_name"), quote, snap,
                                    link, revised=quote.version > 1)
    m = drafts.new_draft(session, kind="proposal", channel=who["channel"], recipient=who["to"], subject=subject,
                         body=body, language=who["lang"], proposal=who["proposal"], in_reply_to=who["reply"])
    session.flush()
    pdf = to_pdf(quote, snap, settings.get(session, "company_name"))
    session.add(Attachment(message_id=m.id, filename=f"{quote.ref}-v{quote.version}.pdf",
                           content_type="application/pdf", size=len(pdf), data=pdf))
    if quote.request is not None and quote.request.status in ("new", "reading", "needs_review", "waiting_client"):
        quote.request.status = "proposal_sent"
    log(session, actor_of(user), "sent", "quote", quote.ref,
        f"Version {quote.version} ready to send to {quote.client.name}" + (f": {note.strip()}" if note.strip() else ""))
    return m


# ── The client's answer ─────────────────────────────────────────────────────────

def ask_changes(session, actor, quote, text):
    if quote.status not in ("sent", "changes_requested"):
        raise QuoteError("Only a proposal that was sent can be changed at the client's request.")
    if not (text or "").strip():
        raise QuoteError("Say what should change.")
    quote.status = "changes_requested"
    quote.client_note = text.strip()[:4000]
    log(session, actor, "changes_requested", "quote", quote.ref, f"Changes asked for: {text.strip()[:200]}")
    alert(session, "client", f"{quote.client.name} asked for changes to proposal {quote.ref}: {text.strip()[:160]}",
          link=f"/quotes/{quote.ref}", subject=f"{quote.client.name} asked for changes to {quote.ref}")


def decline(session, actor, quote, reason=""):
    if quote.status not in QUOTE_OPEN:
        raise QuoteError(f"This proposal is already {quote.status.replace('_', ' ')}.")
    quote.status = "declined"
    quote.client_note = (reason or "").strip()[:4000]
    log(session, actor, "declined", "quote", quote.ref, "Declined" + (f": {reason.strip()[:200]}" if reason else ""))
    alert(session, "client", f"{quote.client.name} declined proposal {quote.ref}.", link=f"/quotes/{quote.ref}")


def withdraw(session, user, quote):
    require(user, "hold")
    if quote.status not in QUOTE_OPEN:
        raise QuoteError(f"This proposal is already {quote.status.replace('_', ' ')}.")
    quote.status = "withdrawn"
    log(session, actor_of(user), "withdrawn", "quote", quote.ref, "Withdrawn")


def accept(session, actor_user, quote, option, by_client=False):
    """The client takes one option: it becomes the hold (or the request's hold is brought in line with it), with the
    agreed price. The PO comes next. Returns the booking. Raises bookings.Overbooked when there's no longer room."""
    if quote.status not in ("sent", "changes_requested"):
        raise QuoteError("Only a proposal that was sent can be accepted.")
    if option.quote_id != quote.id:
        raise QuoteError("That option isn't part of this proposal.")
    t = totals(option)
    lines = [LineIn(l.screen_id, l.start_date, l.end_date, l.start_hour, l.end_hour, l.ad_seconds,
                    l.plays_per_hour, l.exclusive) for l in option.lines]
    from .intake import AGENT
    team = actor_user if actor_user is not None and actor_user.role != "client" else AGENT
    held = quote.request.booking if quote.request is not None else None
    if held is not None and held.status == "hold":
        bookings.update(session, team, held, lines, title=quote.title or None)
        b = held
    else:
        b = bookings.create(session, team, quote.client, lines, title=quote.title, source="proposal",
                            source_ref=quote.ref)
    b.agreed_amount, b.currency = t["net"], quote.currency
    if by_client:
        b.client_accepted_at = utcnow()
    quote.status = "accepted"
    quote.accepted_option_id = option.id
    quote.accepted_at = utcnow()
    who = actor_user.email if actor_user is not None else "client"
    quote.accepted_by = who
    quote.booking_id = b.id
    if quote.request is not None:
        quote.request.booking_id = b.id
        quote.request.status = "held"
    for other in session.scalars(select(Quote).where(Quote.request_id == quote.request_id, Quote.id != quote.id,
                                                     Quote.status.in_(QUOTE_OPEN))).all() if quote.request_id else []:
        other.status = "withdrawn"
    log(session, who, "accepted", "quote", quote.ref,
        f"{option.name} accepted ({quote.currency} {t['net']:,.0f}); hold {b.code} placed, waiting for the PO")
    alert(session, "client", f"{quote.client.name} accepted {option.name} of proposal {quote.ref}. Hold {b.code} "
                             "is waiting for their purchase order.", link=f"/bookings/{b.code}",
          subject=f"{quote.client.name} accepted proposal {quote.ref}")
    return b


def expire(session, today=None):
    """Proposals past their date that the client never answered."""
    today = today or date.today()
    n = 0
    for q in session.scalars(select(Quote).where(Quote.status.in_(("sent", "changes_requested")),
                                                 Quote.valid_until.is_not(None), Quote.valid_until < today)).all():
        q.status = "expired"
        log(session, "system", "expired", "quote", q.ref, f"Expired on {q.valid_until.isoformat()} without an answer")
        n += 1
    return n


def for_client(session, client_id):
    return session.scalars(select(Quote).where(Quote.client_id == client_id, Quote.version > 0)
                           .order_by(Quote.id.desc())).all()


def counts(session):
    return dict(session.execute(select(Quote.status, func.count()).group_by(Quote.status)).all())


# ── The PDF ─────────────────────────────────────────────────────────────────────

def money(currency, value):
    return f"{currency} {value:,.0f}" if value is not None else "—"


def hours_text(sh, eh):
    return "All operating hours" if sh is None or eh is None else f"{sh:02d}:00–{eh % 24:02d}:00"


def to_pdf(quote, snap, company):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"{quote.ref} proposal", author=company)
    ss = getSampleStyleSheet()
    ink, accent, soft = colors.HexColor("#17162A"), colors.HexColor("#5A49E6"), colors.HexColor("#F3F2F9")
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=20, textColor=ink,
                        alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=13, textColor=ink,
                        spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=ink)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#5F5E78"))
    cur = snap["currency"]
    story = [Paragraph(f"{company}", ParagraphStyle("c", parent=body, textColor=accent, fontName="Helvetica-Bold")),
             Paragraph(f"Proposal {quote.ref}" + (f" · version {quote.version}" if quote.version > 1 else ""), h1),
             Paragraph(f"For {quote.client.name}" + (f" · {quote.title}" if quote.title else "")
                       + (f" · valid until {date.fromisoformat(snap['valid_until']).strftime('%-d %b %Y')}"
                          if snap.get("valid_until") else ""), body),
             Spacer(1, 6)]
    if snap.get("message"):
        story += [Paragraph(snap["message"].replace("\n", "<br/>"), body), Spacer(1, 4)]
    for o in snap["options"]:
        t = o["totals"]
        rows = [["Screen", "Dates", "Hours", "Frequency", "Plays", "Price"]]
        for l in o["lines"]:
            freq = "Whole screen" if l["exclusive"] else f"{l['ad_seconds']}s × {l['plays_per_hour']:g}/hour"
            d1, d2 = date.fromisoformat(l["start"]), date.fromisoformat(l["end"])
            rows.append([Paragraph(f"<b>{l['screen']}</b> {l['screen_name']}", small),
                         f"{d1.strftime('%-d %b')} – {d2.strftime('%-d %b %Y')}",
                         hours_text(l["start_hour"], l["end_hour"]), freq, f"{l['plays']:,}",
                         "Bonus" if l["bonus"] else money(cur, l["price"])])
        rows.append(["", "", "", "", "Total", money(cur, t["gross"])])
        if t["discount"]:
            rows.append(["", "", "", "", "Discount", "– " + money(cur, t["discount"])])
        rows.append(["", "", "", "", "You pay", money(cur, t["net"])])
        tbl = Table(rows, colWidths=[50 * mm, 34 * mm, 26 * mm, 28 * mm, 16 * mm, 24 * mm], repeatRows=1)
        n = len(rows)
        tbl.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#8A89A3")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E8E6F2")),
            ("ALIGN", (4, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONT", (4, n - 1), (-1, n - 1), "Helvetica-Bold", 10), ("BACKGROUND", (0, n - 1), (-1, n - 1), soft),
        ]))
        part = [Paragraph(o["name"], h2), tbl]
        if t["bonus_value"]:
            part.append(Paragraph(f"Bonus lines included at no charge, worth {money(cur, t['bonus_value'])}.", small))
        if o.get("value_adds"):
            part.append(Paragraph("Also included: " + "; ".join(x.strip() for x in o["value_adds"].splitlines()
                                                                  if x.strip()), small))
        story.append(KeepTogether(part))
    story += [Spacer(1, 10), Paragraph("Prices exclude VAT. Screen time is held only once you accept an option, and "
                                       "the booking is confirmed when we receive your purchase order.", small)]
    doc.build(story)
    return buf.getvalue()

