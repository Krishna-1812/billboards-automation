"""Play plans: how often an ad plays on each booked screen, by part of the day, weekday and date (Phase 4).

Frequency is agreed with each client, so a plan starts from the booking and the conversation that led to it:

    draft()      a draft for every screen of a booking. Claude reads the conversation for day parts ("double in
                 the evening peak", "weekends only", "nothing during Ramadan nights"); without Claude, or when
                 nothing was said, every day part plays the booked frequency.
    evaluate()   plays per day and in total, how full each screen's loop becomes, and the hours it would overfill.
    save()       the team's edits (a draft until confirmed).
    confirm()    a person confirms; a plan that overfills a loop is refused (a Manager may override, with a reason).
                 When the owner asks for it, the client approves the plan in the portal before it counts.

A plan is stored on each booking line as plain data:

    {"parts": [{"name": "Morning peak", "from": 6, "to": 10, "plays": 30}, ...],
     "hours": {"0": 0, ..., "23": 30},           # plays per clock hour, worked out from the parts
     "weekdays": [0, 1, 2, 3, 4, 5, 6],          # Monday = 0
     "exceptions": [{"from": "2026-12-01", "to": "2026-12-03", "plays": 0, "note": "National Day"}]}

`line.plan` is the working copy; `line.confirmed_plan` is what the screen's loop is reserved for, and only changes
when a plan is confirmed, so editing a draft can never quietly take loop time from another booking.
"""

import hashlib
import json
import logging
from datetime import date

from sqlalchemy import select

from . import ai, settings, vacancy
from .activity import log
from .bookings import actor_of
from .jobs import handler
from .models import Booking, Message, Proposal, utcnow
from .notices import alert
from .permissions import can, require

logger = logging.getLogger(__name__)

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ALL_DAYS = list(range(7))
DEFAULT_PARTS = ("Morning peak = 6-10 | ذروة الصباح\nDaytime = 10-16 | النهار\nEvening peak = 16-22 | ذروة المساء\n"
                 "Night = 22-6 | الليل")


class PlanError(Exception):
    pass


# ── Day parts ───────────────────────────────────────────────────────────────────

def label(name, lang="en"):
    """A day part's name in one language: names are written "English | العربية" (the Arabic is optional)."""
    en, _, ar = (name or "").partition("|")
    return (ar.strip() or en.strip()) if lang == "ar" else en.strip()


def part_hours(frm, to):
    """The clock hours of a day part. `to` <= `from` runs past midnight; 0-24 (or equal ends) is all day."""
    frm, to = int(frm) % 24, int(to)
    if to % 24 == frm:
        return list(range(24))
    if to > frm:
        return list(range(frm, min(to, 24)))
    return list(range(frm, 24)) + list(range(0, to))


def format_dayparts(parts):
    """The settings text for these parts, the Arabic name last so mixed text reads correctly."""
    out = []
    for p in parts:
        en, ar = label(p["name"]), label(p["name"], "ar")
        out.append(f"{en} = {p['from']}-{p['to']}" + (f" | {ar}" if ar != en else ""))
    return "\n".join(out)


def parse_dayparts(text):
    """'Name = 6-10 | الاسم' per line (the Arabic name is optional). Returns (parts, errors); every hour of the
    day must be in exactly one part."""
    parts, errors, seen = [], [], {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "=" not in line or "-" not in line.split("=", 1)[1]:
            errors.append(f"“{line}”: write it as Name = from-to, for example Morning peak = 6-10.")
            continue
        name, span = (x.strip() for x in line.split("=", 1))
        if "|" in span:
            span, _, arabic = span.partition("|")
            name = f"{name} | {arabic.strip()}" if arabic.strip() else name
        a, _, b = span.partition("-")
        try:
            frm, to = int(a.strip()), int(b.strip())
        except ValueError:
            errors.append(f"“{line}”: the hours must be whole numbers from 0 to 24.")
            continue
        if not name or not (0 <= frm <= 23 and 0 <= to <= 24):
            errors.append(f"“{line}”: the hours must be whole numbers from 0 to 24.")
            continue
        if name.lower() in {p["name"].lower() for p in parts}:
            errors.append(f"“{name}” is listed twice.")
            continue
        for h in part_hours(frm, to):
            if h in seen:
                errors.append(f"{h}:00 is in both {seen[h]} and {name}.")
            seen[h] = name
        parts.append({"name": name, "from": frm, "to": to})
    missing = [h for h in range(24) if h not in seen]
    if parts and missing:
        errors.append("These hours aren't in any day part: " + ", ".join(f"{h}:00" for h in missing) + ".")
    if not parts:
        errors.append("Give at least one day part.")
    return parts, errors


def dayparts(session):
    parts, errors = parse_dayparts(settings.get(session, "dayparts"))
    if errors:
        parts, _ = parse_dayparts(DEFAULT_PARTS)
    return parts


# ── Building plans ──────────────────────────────────────────────────────────────

def build(parts, plays, weekdays=None, exceptions=None):
    """A plan from plays per part (a list in the order of `parts`, or {name: plays})."""
    if isinstance(plays, dict):
        lookup = {k.lower(): v for k, v in plays.items()}
        plays = [lookup.get(p["name"].lower(), lookup.get(label(p["name"]).lower(), 0)) for p in parts]
    out_parts, hours = [], {}
    for p, n in zip(parts, plays):
        n = max(0.0, float(n or 0))
        out_parts.append({"name": p["name"], "from": p["from"], "to": p["to"], "plays": n})
        for h in part_hours(p["from"], p["to"]):
            hours[str(h)] = n
    days = sorted({int(d) for d in (weekdays if weekdays is not None else ALL_DAYS) if 0 <= int(d) <= 6})
    return {"parts": out_parts, "hours": {str(h): hours.get(str(h), 0.0) for h in range(24)}, "weekdays": days,
            "exceptions": [dict(e) for e in (exceptions or [])]}


def flat(line, parts):
    """The booked frequency in every day part, every day."""
    return build(parts, [line.plays_per_hour] * len(parts))


def working_plan(line, parts):
    return line.plan or line.confirmed_plan or flat(line, parts)


def signature(plan):
    if not plan:
        return ""
    return hashlib.sha1(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()[:16]


def describe(plan, lang="en"):
    """'Morning peak 30/h, Evening peak 60/h; Fri, Sat' in plain words, for messages and the portal."""
    from .drafts import t
    if not plan:
        return ""
    comma, semi = ("، ", "؛ ") if lang == "ar" else (", ", "; ")
    unit = t("per_hour", lang)
    bits = [f"{label(p['name'], lang)} {p['plays']:g}{unit}" for p in plan.get("parts", []) if p.get("plays")]
    days = plan.get("weekdays", ALL_DAYS)
    when = t("every_day", lang) if len(days) == 7 else comma.join(t(f"wd{d}", lang) for d in days)
    text = (comma.join(bits) or t("no_plays", lang)) + semi + when
    for e in plan.get("exceptions") or []:
        text += semi + t("plan_exception", lang, start=e["from"], end=e["to"], n=f"{float(e.get('plays') or 0):g}")
    return text


# ── Checking a plan against the loop ────────────────────────────────────────────

def _use(line, plan, booking):
    return vacancy.Use(booking_id=booking.id, booking_code=booking.code, status=booking.status,
                       start_date=line.start_date, end_date=line.end_date, start_hour=line.start_hour,
                       end_hour=line.end_hour, ad_seconds=line.ad_seconds, plays_per_hour=line.plays_per_hour,
                       exclusive=line.exclusive, plan=plan)


def evaluate(session, booking, proposed=None):
    """For each line: plays per day and in total, the fullest hour, and the hours the plan would overfill.
    `proposed` is {line_id: plan}; lines not in it use their working plan."""
    parts = dayparts(session)
    plans = {l.id: (proposed or {}).get(l.id) or working_plan(l, parts) for l in booking.lines}
    out = []
    for l in booking.lines:
        row = {"line": l, "plan": plans[l.id], "fits": True, "over": [], "over_count": 0, "total": 0.0,
               "per_day": 0.0, "peak_pct": 0.0, "days": 0, "exclusive": l.exclusive}
        if l.exclusive:
            out.append(row)
            continue
        rules = vacancy.load_rules(session, l.screen)
        uses = vacancy.load_uses(session, l.screen_id, l.start_date, l.end_date, exclude_booking_id=booking.id)
        uses += [_use(o, plans[o.id], booking) for o in booking.lines if o.id != l.id and o.screen_id == l.screen_id]
        used, who = vacancy.usage(rules, uses, l.start_date, l.end_date)
        mine = _use(l, plans[l.id], booking)
        cap = rules.capacity or 1.0
        played_days = set()
        for day in vacancy.days_between(l.start_date, l.end_date):
            for slot in sorted(vacancy.playing_slots(rules, mine, day)):
                need = vacancy.slot_demand(mine, rules, day, slot)
                if need <= 0:
                    continue
                played_days.add(day)
                row["total"] += need / max(1, l.ad_seconds)
                fill = used.get(slot, 0.0) + need
                row["peak_pct"] = max(row["peak_pct"], 100.0 * fill / cap)
                if fill > cap + vacancy.EPS:
                    row["over_count"] += 1
                    if len(row["over"]) < 12:
                        row["over"].append({"date": slot[0], "hour": slot[1], "pct": round(100.0 * fill / cap),
                                            "with": sorted({u.booking_code for u in who.get(slot, [])})})
        row["fits"] = row["over_count"] == 0
        row["days"] = len(played_days)
        row["per_day"] = row["total"] / row["days"] if row["days"] else 0.0
        out.append(row)
    return out


def fits(rows):
    return all(r["fits"] for r in rows)


# ── Claude's draft ──────────────────────────────────────────────────────────────

SCHEMA = {
    "type": "object",
    "properties": {
        "said_anything": {"type": "boolean"},
        "lines": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "screen_code": {"type": "string"},
                "parts": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "plays_per_hour": {"type": "number"}},
                    "required": ["name", "plays_per_hour"], "additionalProperties": False}},
                "weekdays": {"type": "array", "items": {"type": "integer"}},
                "exceptions": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                                   "plays_per_hour": {"type": "number"}, "note": {"type": "string"}},
                    "required": ["from", "to", "plays_per_hour", "note"], "additionalProperties": False}},
                "evidence": {"type": "string"}},
            "required": ["screen_code", "parts", "weekdays", "exceptions", "evidence"],
            "additionalProperties": False}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"}},
    "required": ["said_anything", "lines", "confidence", "notes"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You turn what a client agreed with {company}, a digital billboard company, into a play plan.

Each booked screen plays a loop shared by several advertisers. The booking already says how often the client's ad
plays on average (plays per hour). The plan says how that frequency is spread over the day parts below, the
weekdays, and any dates with special terms.

Day parts (clock hours, local time):
{parts}

Read the conversation. For each booked screen, give plays per hour for EVERY day part, the weekdays it plays on
(0 = Monday ... 6 = Sunday), and any dates with different terms (plays_per_hour 0 means it doesn't play then).
- If the conversation says nothing about day parts, weekdays or dates, use the booked plays per hour in every day
  part, every day, no exceptions, and set said_anything to false.
- Only change the spread where the client and the company clearly agreed it. Quote the words in `evidence`.
- Keep numbers practical: plays per hour can't go above what the screen's loop holds (given per screen).
- confidence is "high" only when the conversation states the plan clearly or says nothing at all.
- Write `notes` for the team in one or two short sentences: what you based the plan on, or what is unclear."""


def _ask_claude(session, booking, parts, proposal):
    msgs = session.scalars(select(Message).where(Message.proposal_id == proposal.id,
                                                 Message.status.in_(("received", "sent")))
                           .order_by(Message.id)).all()
    company = settings.get(session, "company_name")
    system = INSTRUCTIONS.format(company=company, parts="\n".join(
        f"- {label(p['name'])}: {p['from']}:00 to {p['to']}:00" for p in parts))
    booked = []
    for l in booking.lines:
        most = int(vacancy.SECONDS_PER_HOUR * l.screen.sellable_pct / 100 // max(1, l.ad_seconds))
        booked.append(f"- {l.screen.code} ({l.screen.name}): {l.start_date} to {l.end_date}, "
                      f"{'whole screen' if l.exclusive else f'{l.plays_per_hour:g} plays per hour'}, "
                      f"{l.ad_seconds}s ad, at most {most} plays per hour")
    content = [{"type": "text", "text": f"Booking {booking.code} for {booking.client.name}. Booked screens:\n"
                                        + "\n".join(booked) + (f"\nTeam notes: {booking.notes}" if booking.notes else "")}]
    for i, m in enumerate(msgs, 1):
        who = "from the client" if m.direction == "in" else "sent by the company"
        content.append({"type": "text", "text": f"--- Message {i}, {who} ---\n{(m.body or '').strip() or '(no text)'}"})
    content.append({"type": "text", "text": "Fill in the play plan."})
    return ai.complete_json(session, purpose="play_plan", system=system, content=content, schema=SCHEMA,
                            booking_id=booking.id, client_id=booking.client_id, proposal_id=proposal.id,
                            max_tokens=8000)


def _date_ok(v, line):
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
    return d if line.start_date <= d <= line.end_date else None


def _from_answer(answer, booking, parts):
    plans = {}
    by_code = {}
    for row in answer.get("lines") or []:
        by_code.setdefault(str(row.get("screen_code", "")).strip().upper(), row)
    for l in booking.lines:
        row = by_code.get(l.screen.code.upper())
        if row is None or l.exclusive:
            continue
        most = vacancy.SECONDS_PER_HOUR * l.screen.sellable_pct / 100 / max(1, l.ad_seconds)
        named = {str(p.get("name", "")).lower(): p.get("plays_per_hour") for p in row.get("parts") or []}
        plays = [min(most, max(0.0, float(named.get(label(p["name"]).lower(), named.get(p["name"].lower(),
                                                                                    l.plays_per_hour)) or 0)))
                 for p in parts]
        days = [d for d in (row.get("weekdays") or []) if isinstance(d, int) and 0 <= d <= 6] or ALL_DAYS
        exceptions = []
        for e in row.get("exceptions") or []:
            a, b = _date_ok(e.get("from"), l), _date_ok(e.get("to"), l)
            if a and b and a <= b:
                exceptions.append({"from": a.isoformat(), "to": b.isoformat(),
                                   "plays": min(most, max(0.0, float(e.get("plays_per_hour") or 0))),
                                   "note": str(e.get("note", ""))[:200]})
        plans[l.id] = build(parts, plays, days, exceptions)
        if row.get("evidence"):
            plans[l.id]["evidence"] = str(row["evidence"])[:500]
    return plans


def draft(session, booking, actor="agent"):
    """A draft plan for every screen of the booking, from the conversation when there is one."""
    parts = dayparts(session)
    plans = {l.id: flat(l, parts) for l in booking.lines}
    note, confidence = "Every day part plays the booked frequency, every day.", "medium"
    proposal = session.scalars(select(Proposal).where(Proposal.booking_id == booking.id)).first()
    if proposal is not None and ai.available():
        try:
            answer = _ask_claude(session, booking, parts, proposal)
            confidence = answer.get("confidence") or "low"
            if answer.get("said_anything"):
                plans.update(_from_answer(answer, booking, parts))
            note = (answer.get("notes") or "").strip() or note
        except ai.AIFailed as e:
            note, confidence = f"Claude couldn't read the conversation ({e}). {note}", "low"
    elif proposal is None:
        note = "No conversation to read. " + note
    else:
        note = "Claude isn't connected. " + note
    for l in booking.lines:
        l.plan = plans[l.id]
    booking.plan_status = "draft"
    booking.plan_note, booking.plan_confidence = note[:2000], confidence
    log(session, actor, "drafted_plan", "booking", booking.code, f"Play plan drafted ({confidence}): {note}"[:500])
    rows = evaluate(session, booking)
    from . import automation
    if automation.on(session, "auto_confirm_plan") and confidence == "high" and fits(rows):
        confirm(session, None, booking, automatic=True)
    else:
        alert(session, "plans", f"{booking.code}: a play plan is drafted. Check it and confirm it.",
              link=f"/bookings/{booking.code}/plan")
    return rows


@handler("draft_plan")
def _draft_job(session, payload):
    b = session.get(Booking, payload["booking_id"])
    if b is not None and b.plan_status == "none" and b.status not in ("cancelled", "expired", "completed", "hold"):
        draft(session, b)


# ── Editing and confirming ──────────────────────────────────────────────────────

def save(session, user, booking, plans):
    """Store the team's edits as the working plan. A confirmed plan goes back to draft until confirmed again."""
    require(user, "edit_plan")
    for l in booking.lines:
        p = plans.get(l.id)
        if p is None:
            continue
        if not l.exclusive and not p.get("weekdays"):
            raise PlanError(f"{l.screen.code}: choose at least one weekday.")
        l.plan = p
    if booking.plan_status in ("confirmed", "waiting_client"):
        booking.plan_status = "draft"
    elif booking.plan_status == "none":
        booking.plan_status = "draft"
    log(session, actor_of(user), "edited_plan", "booking", booking.code, "Play plan edited")
    return evaluate(session, booking)


def confirm(session, user, booking, override_reason="", automatic=False):
    from . import client_messages, lifecycle
    if not automatic:
        require(user, "confirm_plan")
    if booking.status in ("hold", "enquiry", "cancelled", "expired", "completed"):
        raise PlanError(f"{booking.code} is {booking.status}; a plan is confirmed once the booking is confirmed.")
    parts = dayparts(session)
    for l in booking.lines:
        if l.plan is None:
            l.plan = flat(l, parts)
    rows = evaluate(session, booking)
    who = "agent" if automatic else actor_of(user)
    if not fits(rows):
        bad = "; ".join(f"{r['line'].screen.code}: {r['over_count']} hour(s) over" for r in rows if not r["fits"])
        if automatic or not (override_reason.strip() and can(user, "override")):
            raise PlanError(f"The plan would overfill the loop ({bad}). Lower the plays in those hours"
                            + (", or a Manager can confirm it anyway with a reason." if not automatic else "."))
        booking.plan_override_reason = override_reason.strip()[:500]
    else:
        booking.plan_override_reason = ""
    for l in booking.lines:
        l.confirmed_plan = l.plan
    booking.plan_confirmed_by, booking.plan_confirmed_at = who, utcnow()
    booking.plan_client_note = ""
    total = sum(r["total"] for r in rows)
    if settings.get(session, "client_approves_plan"):
        booking.plan_status = "waiting_client"
        booking.plan_client_approved_at = None
        client_messages.plan_review(session, booking, rows)
        log(session, who, "confirmed_plan", "booking", booking.code,
            f"Play plan confirmed{' automatically' if automatic else ''} ({total:,.0f} plays); sent to the client "
            "to approve")
    else:
        booking.plan_status = "confirmed"
        log(session, who, "confirmed_plan", "booking", booking.code,
            f"Play plan confirmed{' automatically' if automatic else ''} ({total:,.0f} plays)"
            + (f", past capacity: {booking.plan_override_reason}" if booking.plan_override_reason else ""))
    if automatic:
        alert(session, "plans", f"{booking.code}: the agent confirmed the play plan it drafted.",
              link=f"/bookings/{booking.code}/plan")
    lifecycle.advance(session, booking, actor=who)
    return rows


def client_decide(session, user, booking, approve, note=""):
    """The client approves the plan in the portal, or asks for changes."""
    from . import lifecycle
    if booking.plan_status != "waiting_client":
        raise PlanError("There's no play plan waiting for your approval.")
    who = user.email
    if approve:
        booking.plan_status = "confirmed"
        booking.plan_client_approved_at = utcnow()
        log(session, who, "client_approved_plan", "booking", booking.code, "The client approved the play plan")
        alert(session, "client", f"{booking.client.name} approved the play plan for {booking.code}.",
              link=f"/bookings/{booking.code}/plan")
        lifecycle.advance(session, booking, actor=who)
    else:
        if not (note or "").strip():
            raise PlanError("Tell us what you'd like changed.")
        booking.plan_status = "draft"
        booking.plan_client_note = note.strip()[:2000]
        log(session, who, "client_changes_plan", "booking", booking.code, f"The client asked for changes: {note}"[:500])
        alert(session, "client", f"{booking.client.name} asked for changes to the play plan for {booking.code}: "
                                 f"{note.strip()}", link=f"/bookings/{booking.code}/plan", kind="warning")


def reset_for_edit(booking):
    """The booking's screens or dates changed: the plan has to be confirmed again, and the loop goes back to
    being reserved at the booked frequency (which the edit was checked against)."""
    if booking.plan_status != "none":
        booking.plan_status = "draft"
    for l in booking.lines:
        l.confirmed_plan = None
