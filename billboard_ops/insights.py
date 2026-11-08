"""The numbers behind the owner dashboard (Phase 5).

Everything here reads the records the rest of the app already keeps; nothing is stored twice. Occupancy is loop
time used (seconds sold of the seconds sellable, in the hours each screen is on), not just "booked or empty".
"""

import statistics
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from . import vacancy
from .models import (BOOKED, Activity, AiCall, Booking, BookingLine, Client, Creative, Message, Proposal, Screen,
                     User, utcnow)

AT_RISK_DAYS = 7
NO_REPLY_DAYS = 3
WAITING_KINDS = ("clarify", "hold_confirmation", "decline", "creative_request", "creative_feedback",
                 "creative_reminder", "plan_review", "preview_review", "renewal", "hold_reminder")


# ── Periods ─────────────────────────────────────────────────────────────────────

def week_of(day):
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def month_of(day):
    start = day.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


def last_month(day):
    start, _ = month_of(day)
    return month_of(start - timedelta(days=1))


# ── Occupancy ───────────────────────────────────────────────────────────────────

def _avg(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def occupancy(session, screens, start, end):
    """(average % of loop time sold across screens and open hours, {screen_id: average %})."""
    if not screens:
        return 0.0, {}
    grid = vacancy.fill_grid(session, screens, start, (end - start).days + 1)
    per = {sid: _avg(avg for _, _, avg, closed in cells if not closed) for sid, cells in grid.items()}
    every = [avg for cells in grid.values() for _, _, avg, closed in cells if not closed]
    return _avg(every), per


def weekly_grid(session, screens, start, weeks=12, grid=None):
    """{screen_id: [(week start, average %, busiest hour %, closed all week?)]} from `start` (a Monday).
    `grid` is a fill grid from `start` already worked out, so the dashboard reads the loop only once."""
    if not screens:
        return {}
    grid = grid or vacancy.fill_grid(session, screens, start, weeks * 7)
    out = {}
    for sid, cells in grid.items():
        rows = []
        for w in range(weeks):
            chunk = cells[w * 7:(w + 1) * 7]
            open_ = [c for c in chunk if not c[3]]
            rows.append((start + timedelta(days=7 * w), _avg(c[2] for c in open_),
                         max((c[1] for c in open_), default=0.0), not open_))
        out[sid] = rows
    return out


def heat(pct):
    return 0 if pct < 1 else 1 if pct < 25 else 2 if pct < 50 else 3 if pct < 85 else 4


# ── Top numbers ─────────────────────────────────────────────────────────────────

def _from_grid(grid, start, end):
    """(average %, {screen_id: average %}) for start..end, read from a fill grid that covers it."""
    per, every = {}, []
    for sid, cells in grid.items():
        vals = [avg for d, _, avg, closed in cells if start <= d <= end and not closed]
        per[sid] = _avg(vals)
        every += vals
    return _avg(every), per


def dashboard_fills(session, screens, today, weeks=12):
    """One fill grid covering this month and the next `weeks` weeks, for every number on the dashboard."""
    w0, _ = week_of(today)
    m0, m1 = month_of(today)
    start, end = min(w0, m0), max(w0 + timedelta(days=7 * weeks - 1), m1)
    full = vacancy.fill_grid(session, screens, start, (end - start).days + 1) if screens else {}
    from_week = {sid: [c for c in cells if c[0] >= w0][:7 * weeks] for sid, cells in full.items()}
    return full, from_week


def top_numbers(session, today, fills=None):
    screens = session.scalars(select(Screen).where(Screen.active.is_(True))).all()
    w0, w1 = week_of(today)
    m0, m1 = month_of(today)
    if fills is None:
        week, _ = occupancy(session, screens, w0, w1)
        month, _ = occupancy(session, screens, m0, m1)
    else:
        week, _ = _from_grid(fills, w0, w1)
        month, _ = _from_grid(fills, m0, m1)
    now = utcnow()
    holds = session.scalars(select(Booking).where(Booking.status == "hold")).all()
    expiring = [b for b in holds if b.hold_expires_at and b.hold_expires_at <= now + timedelta(days=7)]
    lines = session.scalars(select(BookingLine).join(Booking).where(Booking.status.in_(BOOKED))).all()
    starting = {l.booking_id for l in lines if today <= l.start_date <= today + timedelta(days=7)}
    live = session.scalar(select(func.count()).select_from(Booking).where(Booking.status == "live")) or 0
    waiting = session.scalar(select(func.count()).select_from(Creative)
                             .where(Creative.status.in_(("passed", "warnings")))) or 0
    failing = session.scalar(select(func.count()).select_from(Creative).join(Booking)
                             .where(Creative.status == "failed", Booking.status == "changes_requested")) or 0
    spend = session.scalar(select(func.coalesce(func.sum(AiCall.usd), 0.0))
                           .where(AiCall.at >= datetime.combine(m0, datetime.min.time()))) or 0.0
    return {"week": week, "month": month, "holds": len(holds), "holds_expiring": len(expiring),
            "starting": len(starting), "live": live, "files_waiting": waiting, "files_failing": failing,
            "spend": spend, "screens": len(screens)}


# ── The pipeline ────────────────────────────────────────────────────────────────

def pipeline(session, start, end):
    """Requests → proposals sent → holds → confirmed → campaigns started, in a period, and what was confirmed
    is worth (the agreed price of bookings confirmed in it)."""
    t0, t1 = datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())
    requests = session.scalar(select(func.count()).select_from(Proposal).where(
        Proposal.created_at >= t0, Proposal.created_at < t1,
        Proposal.status.not_in(("not_proposal", "merged")))) or 0
    from .models import QuoteVersion
    proposals = session.scalar(select(func.count()).select_from(QuoteVersion).where(
        QuoteVersion.version == 1, QuoteVersion.sent_at >= t0, QuoteVersion.sent_at < t1)) or 0
    holds = session.scalar(select(func.count()).select_from(Booking).where(
        Booking.created_at >= t0, Booking.created_at < t1, Booking.source != "import")) or 0
    confirmed = session.scalar(select(func.count()).select_from(Booking).where(
        Booking.confirmed_at >= t0, Booking.confirmed_at < t1)) or 0
    # Campaigns that went live in the period; for campaigns from before going live was tracked (imported or
    # completed ones), their start date counts.
    started = session.scalar(select(func.count()).select_from(Booking).where(
        Booking.live_at >= t0, Booking.live_at < t1)) or 0
    for b in session.scalars(select(Booking).options(selectinload(Booking.lines))
                             .where(Booking.live_at.is_(None), Booking.status.in_(("live", "completed")))):
        if b.start_date and start <= b.start_date <= end:
            started += 1
    value = session.scalar(select(func.coalesce(func.sum(Booking.agreed_amount), 0.0)).where(
        Booking.confirmed_at >= t0, Booking.confirmed_at < t1)) or 0.0
    return {"requests": requests, "proposals": proposals, "holds": holds, "confirmed": confirmed, "started": started,
            "value": value}


# ── At risk ─────────────────────────────────────────────────────────────────────

def _names(session):
    return {u.email: (u.name or u.email) for u in session.scalars(select(User))}


def at_risk(session, today):
    """What could go wrong soon, each with who owns the next step and where to fix it."""
    from . import lifecycle
    names = _names(session)
    who = lambda email, fallback: names.get(email, email) if email and "@" in email else fallback  # noqa: E731
    out = []
    starting = select(BookingLine.booking_id).where(
        BookingLine.start_date >= today - timedelta(days=1),
        BookingLine.start_date <= today + timedelta(days=AT_RISK_DAYS))
    soon = session.scalars(select(Booking).options(selectinload(Booking.lines).selectinload(BookingLine.screen),
                                                   joinedload(Booking.client))
                           .where(Booking.status.in_(BOOKED), Booking.status != "live",
                                  Booking.id.in_(starting))).unique().all()
    for b in soon:
        if not b.start_date or not (today - timedelta(days=1) <= b.start_date <= today + timedelta(days=AT_RISK_DAYS)):
            continue
        r = lifecycle.readiness(session, b)
        if r["ready"]:
            continue
        days = (b.start_date - today).days
        when = "today" if days == 0 else ("tomorrow" if days == 1 else (f"in {days} days" if days > 0 else "already"))
        if not r["files"]:
            owner = f"{b.client.name} (files), chased by {who(b.created_by, 'Sales')}"
        elif not r["plan"]:
            owner = "Ops (play plan)" if b.plan_status != "waiting_client" else f"{b.client.name} (plan approval)"
        else:
            owner = "Ops (loading)"
        out.append({"kind": "start", "severity": 3 if days <= 1 else 2, "code": b.code, "client": b.client.name,
                    "text": f"Starts {when} and still needs " + "; ".join(r["missing"]),
                    "owner": owner, "link": f"/bookings/{b.code}", "date": b.start_date})
    now = utcnow()
    for b in session.scalars(select(Booking).options(joinedload(Booking.client))
                             .where(Booking.status == "hold", Booking.hold_expires_at <= now + timedelta(hours=48))):
        hours = max(0, int((b.hold_expires_at - now).total_seconds() // 3600))
        out.append({"kind": "hold", "severity": 2, "code": b.code, "client": b.client.name,
                    "text": f"Hold expires in {hours} hours" + (" (the client accepted it)" if b.client_accepted_at
                                                               else ""),
                    "owner": who(b.created_by, "Sales"), "link": f"/bookings/{b.code}",
                    "date": b.hold_expires_at.date()})
    for m, client, code, link in unreplied(session, now):
        days = (now - m.sent_at).days
        out.append({"kind": "no_reply", "severity": 1, "code": code, "client": client,
                    "text": f"No reply for {days} days to “{m.subject or m.kind.replace('_', ' ')}”",
                    "owner": who(m.approved_by, "Sales"), "link": link, "date": m.sent_at.date()})
    out.sort(key=lambda x: (-x["severity"], x["date"]))
    return out


def unreplied(session, now, days=NO_REPLY_DAYS):
    """Messages we sent that are waiting on the client, with no answer for `days` days (and nothing sent since)."""
    sent = session.scalars(select(Message).where(Message.direction == "out", Message.status == "sent",
                                                 Message.kind.in_(WAITING_KINDS),
                                                 Message.sent_at <= now - timedelta(days=days),
                                                 Message.sent_at >= now - timedelta(days=30))
                           .order_by(Message.sent_at.desc())).all()
    if not sent:
        return []
    pids = {m.proposal_id for m in sent if m.proposal_id}
    bids = {m.booking_id for m in sent if m.booking_id}
    # The latest message each way for every proposal and booking involved, in two queries.
    last_in, last_out = {}, {}
    for direction, when, target in (("in", Message.created_at, last_in), ("out", Message.sent_at, last_out)):
        cond = [Message.direction == direction]
        if direction == "out":
            cond.append(Message.status == "sent")
        for pid, bid, at in session.execute(select(Message.proposal_id, Message.booking_id, func.max(when))
                                            .where(*cond, or_(Message.proposal_id.in_(pids or {0}),
                                                              Message.booking_id.in_(bids or {0})))
                                            .group_by(Message.proposal_id, Message.booking_id)):
            for key in (("p", pid), ("b", bid)):
                if key[1] is not None and at is not None and at > target.get(key, datetime.min):
                    target[key] = at
    bookings = {b.id: b for b in session.scalars(select(Booking).options(joinedload(Booking.client))
                                                 .where(Booking.id.in_(bids or {0})))}
    proposals = {p.id: p for p in session.scalars(select(Proposal).options(joinedload(Proposal.client))
                                                  .where(Proposal.id.in_(pids or {0})))}
    seen, out = set(), []
    for m in sent:
        key = (m.proposal_id, m.booking_id)
        if key in seen:
            continue
        seen.add(key)
        keys = [k for k in (("p", m.proposal_id), ("b", m.booking_id)) if k[1] is not None]
        if any(last_in.get(k, datetime.min) > m.sent_at or last_out.get(k, datetime.min) > m.sent_at for k in keys):
            continue
        b, p = bookings.get(m.booking_id), proposals.get(m.proposal_id)
        if b is not None and b.status in ("cancelled", "expired", "completed"):
            continue
        if b is None and p is not None and p.status in ("declined", "not_proposal", "merged"):
            continue
        client = b.client.name if b is not None else ((p.client.name if p and p.client else "") or
                                                       (p.sender_name or p.sender if p else ""))
        code = b.code if b is not None else (p.ref if p else "")
        link = f"/bookings/{b.code}" if b is not None else (f"/inbox/{p.ref}" if p else "/outbox")
        out.append((m, client, code, link))
    return out


# ── By client and by screen ─────────────────────────────────────────────────────

def planned_plays(session, line, rules_cache=None):
    """Plays a booked screen is planned to get over its whole run (the confirmed plan, else the booked rate)."""
    rules_cache = rules_cache if rules_cache is not None else {}
    rules = rules_cache.get(line.screen_id) or rules_cache.setdefault(line.screen_id,
                                                                      vacancy.load_rules(session, line.screen))
    use = vacancy.use_from_line(line)
    total = 0.0
    for day in vacancy.days_between(line.start_date, line.end_date):
        for slot in vacancy.playing_slots(rules, use, day):
            total += vacancy.slot_demand(use, rules, day, slot) / max(1, line.ad_seconds)
    return total


def file_mistakes(creatives_):
    """The checks files failed most often, as [(label, count)]."""
    from . import creative_checks as cc
    from .creatives import failures
    counts = Counter()
    for c in creatives_:
        if not c.results:
            continue
        for x in failures(c):
            counts[cc.LABELS.get(x["key"], x["key"])] += 1
    return counts.most_common(8)


def client_stats(session, client):
    bookings = session.scalars(select(Booking).options(selectinload(Booking.lines).selectinload(BookingLine.screen))
                               .where(Booking.client_id == client.id)).all()
    real = [b for b in bookings if b.status in BOOKED + ("completed",)]
    cache = {}
    plays = sum(planned_plays(session, l, cache) for b in real for l in b.lines)
    screen_days = sum((l.end_date - l.start_date).days + 1 for b in real for l in b.lines)
    files = session.scalars(select(Creative).where(Creative.booking_id.in_([b.id for b in bookings]))).all() \
        if bookings else []
    checked = [c for c in files if c.results]
    failed = [c for c in checked if c.results.get("verdict") == "failed"]
    ai = session.scalar(select(func.coalesce(func.sum(AiCall.usd), 0.0)).where(AiCall.client_id == client.id)) or 0.0
    return {"bookings": len(bookings), "campaigns": len(real),
            "completed": sum(1 for b in bookings if b.status == "completed"),
            "plays": plays, "screen_days": screen_days, "files": len(checked),
            "fail_rate": (100.0 * len(failed) / len(checked)) if checked else None,
            "mistakes": file_mistakes(failed), "ai_usd": ai,
            "first": min((b.start_date for b in real if b.start_date), default=None),
            "last": max((b.end_date for b in real if b.end_date), default=None)}


def screen_stats(session, screen, today):
    start, _ = week_of(today)
    grid = weekly_grid(session, [screen], start, 12).get(screen.id, [])
    lines = session.scalars(select(BookingLine).join(Booking).options(joinedload(BookingLine.booking)
                                                                      .joinedload(Booking.client))
                            .where(BookingLine.screen_id == screen.id, Booking.status.in_(BOOKED + ("completed",)),
                                   BookingLine.end_date >= today - timedelta(days=365))).all()
    clients = Counter(l.booking.client.name for l in lines).most_common(6)
    ids = {l.id for l in lines}
    files = session.scalars(select(Creative).where(Creative.booking_id.in_({l.booking_id for l in lines}))).all() \
        if lines else []
    mine = [c for c in files if set(c.line_ids or []) & ids and c.results]
    failed = [c for c in mine if c.results.get("verdict") == "failed"]
    return {"weeks": grid, "clients": clients, "campaigns": len({l.booking_id for l in lines}),
            "fail_rate": (100.0 * len(failed) / len(mine)) if mine else None, "mistakes": file_mistakes(failed)}


# ── Team and agent performance ──────────────────────────────────────────────────

def _median_hours(deltas):
    hours = [d.total_seconds() / 3600 for d in deltas if d is not None and d.total_seconds() >= 0]
    return (statistics.median(hours), len(hours)) if hours else (None, 0)


def performance(session, today, days=90):
    since = utcnow() - timedelta(days=days)
    held = session.execute(select(Proposal.created_at, Booking.created_at).join(Booking, Booking.id == Proposal.booking_id)
                           .where(Proposal.created_at >= since)).all()
    to_hold = _median_hours(b - p for p, b in held)
    approved = {}
    for code, at in session.execute(select(Activity.entity_id, func.min(Activity.at))
                                    .where(Activity.action == "creative_approved", Activity.at >= since)
                                    .group_by(Activity.entity_id)):
        approved[code] = at
    confirmed = session.execute(select(Booking.code, Booking.confirmed_at)
                                .where(Booking.code.in_(list(approved)), Booking.confirmed_at.is_not(None))).all() \
        if approved else []
    to_files = _median_hours(approved[c] - at for c, at in confirmed)
    recent = session.scalars(select(Creative).where(Creative.checked_at >= since, Creative.results.is_not(None))).all()
    failed = [c for c in recent if c.results.get("verdict") == "failed"]

    alone = Counter()
    for action, n in session.execute(select(Activity.action, func.count()).where(Activity.actor == "agent",
                                                                                  Activity.at >= since)
                                     .group_by(Activity.action)):
        alone[action] = n
    auto_holds = session.scalar(select(func.count()).select_from(Proposal)
                                .where(Proposal.auto_held.is_(True), Proposal.created_at >= since)) or 0
    agent_work = {
        "Holds placed": auto_holds,
        "Messages sent without waiting": alone.get("sent_automatically", 0),
        "Files approved": alone.get("approved_file", 0),
        "Play plans confirmed": alone.get("confirmed_plan", 0),
        "Files checked": alone.get("checked", 0),
        "Play plans drafted": alone.get("drafted_plan", 0),
        "Renewal offers and wrap-ups": alone.get("renewal_offer", 0) + alone.get("wrapped_up", 0),
    }
    read = session.execute(select(Proposal.edited).where(Proposal.status == "held", Proposal.read_count > 0,
                                                       Proposal.auto_held.is_(False),
                                                       Proposal.decided_at >= since)).scalars().all()
    drafts_ = session.execute(select(Message.edited).where(Message.direction == "out",
                                                         Message.status.in_(("approved", "sent")),
                                                         Message.approved_by != "agent",
                                                         Message.created_at >= since)).scalars().all()
    plans_ = session.execute(select(Booking.plan_edited).where(Booking.plan_confirmed_at >= since,
                                                             Booking.plan_drafted_sig != "",
                                                             Booking.plan_confirmed_by != "agent")).scalars().all()

    def share(flags):
        flags = list(flags)
        return {"n": len(flags), "changed": sum(1 for f in flags if f),
                "pct": (100.0 * sum(1 for f in flags if f) / len(flags)) if flags else None}

    return {"days": days, "to_hold": to_hold, "to_files": to_files, "mistakes": file_mistakes(failed),
            "files_checked": len(recent), "files_failed": len(failed), "agent_work": agent_work,
            "changed": {"Proposals the agent read": share(read), "Messages the agent drafted": share(drafts_),
                        "Play plans the agent drafted": share(plans_)}}


def costs_by_booking(session, since, limit=20):
    return session.execute(select(Booking.code, Client.name, func.count(), func.coalesce(func.sum(AiCall.usd), 0.0))
                           .join(Booking, Booking.id == AiCall.booking_id).join(Client, Client.id == Booking.client_id)
                           .where(AiCall.at >= since).group_by(Booking.code, Client.name)
                           .order_by(func.sum(AiCall.usd).desc()).limit(limit)).all()


def screens_on_map(session, today, fills=None):
    screens = session.scalars(select(Screen).where(Screen.active.is_(True)).order_by(Screen.code)).all()
    w0, w1 = week_of(today)
    _, per = _from_grid(fills, w0, w1) if fills is not None else occupancy(session, screens, w0, w1)
    return [{"id": s.id, "code": s.code, "name": s.name, "area": s.area, "lat": s.lat, "lng": s.lng,
             "pct": round(per.get(s.id, 0.0)), "heat": heat(per.get(s.id, 0.0))}
            for s in screens if s.lat is not None and s.lng is not None]



# ── For the dashboard's tiles ───────────────────────────────────────────────────

def trend_chart(weeks, start, width=600, height=150):
    """The network's average loop fill for each of the next 12 weeks, as SVG paths (line, area, dots)."""
    n = max((len(v) for v in weeks.values()), default=0)
    if not n:
        return None
    avg = []
    for i in range(n):
        vals = [row[i][1] for row in weeks.values() if not row[i][3]]
        avg.append(sum(vals) / len(vals) if vals else 0.0)
    top = max(10.0, max(avg) * 1.25)
    pts = [{"x": round(24 + i * (width - 48) / max(1, n - 1)), "y": round(height - 12 - (v / top) * (height - 34)),
            "v": round(v), "label": (start + timedelta(days=7 * i)).strftime("%-d %b")} for i, v in enumerate(avg)]
    line = " ".join(("L" if i else "M") + f"{p['x']} {p['y']}" for i, p in enumerate(pts))
    area = line + f" L{pts[-1]['x']} {height} L{pts[0]['x']} {height} Z"
    peak = max(pts, key=lambda p: p["v"])
    return {"line": line, "area": area, "dots": pts, "w": width, "h": height, "peak": peak}


def live_now(session):
    """Campaigns playing now, with the file that plays (for the Live now tile)."""
    from .lifecycle import approved_files
    out = []
    for b in session.scalars(select(Booking).options(selectinload(Booking.lines).selectinload(BookingLine.screen),
                                                     joinedload(Booking.client))
                             .where(Booking.status == "live").order_by(Booking.live_at.desc())).unique().all():
        files = approved_files(session, b.lines[0]) if b.lines else []
        f = files[-1] if files else None
        out.append({"b": b, "file": f, "image": bool(f and (f.content_type or "").startswith("image/")),
                    "screens": ", ".join(l.screen.code for l in b.lines)})
    return out


def starting_soon(session, today, days=7):
    from . import lifecycle
    starting = select(BookingLine.booking_id).where(BookingLine.start_date >= today,
                                                    BookingLine.start_date <= today + timedelta(days=days))
    rows = session.scalars(select(Booking).options(selectinload(Booking.lines), joinedload(Booking.client))
                           .where(Booking.status.in_(BOOKED), Booking.status != "live", Booking.id.in_(starting))
                           ).unique().all()
    rows = [b for b in rows if b.start_date and today <= b.start_date <= today + timedelta(days=days)]
    not_ready = sum(1 for b in rows if not lifecycle.readiness(session, b)["ready"])
    initials = []
    for b in rows:
        ini = "".join(w[0] for w in b.client.name.split()[:2]).upper()
        if ini not in initials:
            initials.append(ini)
    return {"count": len(rows), "not_ready": not_ready, "initials": initials[:2], "more": max(0, len(initials) - 2)}
