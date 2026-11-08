"""The Monday summary: a short email to the owner with the numbers, the risks and the open decisions (Phase 5).

The worker sends it once a week, on Monday from 07:00 in the business's time zone, to every owner and to anyone
listed in Settings → Automation. It can also be previewed and sent from the dashboard.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from . import insights, runtime, settings
from .activity import log
from .mail import queue
from .models import Booking, Creative, Message, Proposal, Screen, SheetChange, User


def decisions(session):
    """What is waiting for a person, as [(what, how many, where)]."""
    review = session.scalar(select(func.count()).select_from(Proposal)
                            .where(Proposal.status.in_(("needs_review", "failed")))) or 0
    outbox = session.scalar(select(func.count()).select_from(Message)
                            .where(Message.direction == "out", Message.status.in_(("draft", "failed")))) or 0
    files = session.scalar(select(func.count()).select_from(Creative)
                           .where(Creative.status.in_(("passed", "warnings")))) or 0
    plans = session.scalar(select(func.count()).select_from(Booking)
                           .where(Booking.plan_status == "draft",
                                  Booking.status.not_in(("cancelled", "expired", "completed", "hold")))) or 0
    over = session.scalar(select(func.count()).select_from(Booking)
                          .where(Booking.override_reason != "",
                                 Booking.status.not_in(("cancelled", "expired", "completed")))) or 0
    sheet = session.scalar(select(func.count()).select_from(SheetChange).where(SheetChange.resolved_at.is_(None))) or 0
    rows = [("Proposals to review", review, "/inbox"), ("Messages to approve", outbox, "/outbox"),
            ("Files to approve", files, "/creatives"), ("Play plans to confirm", plans, "/bookings?status=open"),
            ("Bookings over capacity", over, "/"), ("Edits to the old sheet", sheet, "/settings/sheet-changes")]
    return [r for r in rows if r[1]]


def compose(session, today):
    base = runtime.config.APP_URL if runtime.config else ""
    top = insights.top_numbers(session, today)
    m0, m1 = insights.month_of(today)
    l0, l1 = insights.last_month(today)
    now, before = insights.pipeline(session, m0, m1), insights.pipeline(session, l0, l1)
    risks = insights.at_risk(session, today)
    w0, w1 = insights.week_of(today)
    screens = session.scalars(select(Screen).where(Screen.active.is_(True))).all()
    _, per = insights.occupancy(session, screens, w0, w1)
    emptiest = sorted(screens, key=lambda sc: per.get(sc.id, 0.0))[:5]
    company = settings.get(session, "company_name")
    lines = [f"{company}: the week of {w0:%d %B %Y}", "",
             "THE NUMBERS",
             f"- Loop time sold: {top['week']:.0f}% this week, {top['month']:.0f}% in {m0:%B}",
             f"- Live now: {top['live']}; starting in the next 7 days: {top['starting']}",
             f"- Open holds: {top['holds']} ({top['holds_expiring']} expire this week)",
             f"- Files waiting for a decision: {top['files_waiting']}; failed and waiting for the client: "
             f"{top['files_failing']}",
             f"- Claude this month: ${top['spend']:.2f}", "",
             f"PIPELINE ({m0:%B} so far, {l0:%B} in brackets)",
             f"- Requests {now['requests']} ({before['requests']}), proposals sent {now['proposals']} "
             f"({before['proposals']}), holds {now['holds']} ({before['holds']}), "
             f"confirmed {now['confirmed']} ({before['confirmed']}), campaigns started {now['started']} "
             f"({before['started']})", ""]
    lines.append(f"AT RISK ({len(risks)})")
    lines += [f"- {r['code']} {r['client']}: {r['text']}. Next step: {r['owner']}." for r in risks[:10]] or \
        ["- Nothing at risk."]
    if len(risks) > 10:
        lines.append(f"- and {len(risks) - 10} more on the dashboard")
    lines.append("")
    waiting = decisions(session)
    lines.append("WAITING FOR A DECISION")
    lines += [f"- {what}: {n} ({base}{where})" for what, n, where in waiting] or ["- Nothing waiting."]
    if emptiest:
        lines += ["", "GAPS WORTH SELLING THIS WEEK"]
        lines += [f"- {sc.code} {sc.name}: {per.get(sc.id, 0.0):.0f}% sold" for sc in emptiest]
    lines += ["", f"The dashboard: {base}/"]
    subject = (f"{company} weekly: {top['week']:.0f}% sold, {len(risks)} at risk, "
               f"{sum(n for _, n, _ in waiting)} waiting for you")
    return subject, "\n".join(lines)


def recipients(session):
    owners = [u.email for u in session.scalars(select(User).where(User.role == "owner", User.active.is_(True)))
              if "@" in u.email]
    extra = [e.strip() for e in (settings.get(session, "weekly_summary_to") or "").replace(";", ",").split(",")
             if "@" in e]
    return sorted(set(owners + extra))


def send(session, today, actor="agent"):
    to = recipients(session)
    if not to:
        return None
    subject, body = compose(session, today)
    queue(session, to, subject, body)
    log(session, actor, "weekly_summary", "settings", "summary", f"Weekly summary sent to {', '.join(to)}")
    return to


def maybe_send(session, now_utc=None):
    """Called every minute by the worker: sends the summary once each week, on Monday from 07:00 local time."""
    if not settings.get(session, "weekly_summary"):
        return None
    tz = settings.get(session, "default_timezone") or "Asia/Dubai"
    try:
        local = (now_utc or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz))
    except Exception:  # noqa: BLE001
        local = now_utc or datetime.utcnow()
    week = f"{local.isocalendar()[0]}-W{local.isocalendar()[1]:02d}"
    if local.weekday() != 0 or local.hour < 7 or settings.get(session, "weekly_summary_last") == week:
        return None
    settings.put(session, "weekly_summary_last", week)
    return send(session, local.date())

