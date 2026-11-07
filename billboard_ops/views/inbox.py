"""Proposals, the Outbox, the accuracy test and the cost of reading."""

from datetime import date, timedelta

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import bookings as svc
from .. import evaluate, extract, intake, outbox
from ..activity import log
from ..jobs import enqueue
from ..models import (AiCall, Attachment, Booking, BookingDocument, Client, EvalCase, EvalRun, Message, Proposal,
                      Screen)
from ..permissions import Forbidden, can
from ..web import current_user, done, f_str, get_db, login_required, needs
from .bookings import _active_screens, _client_from_form, _lines_from_form, _why

bp = Blueprint("inbox", __name__)

TABS = {
    "review": ("To review", ("new", "reading", "needs_review", "failed")),
    "waiting": ("Waiting on client", ("waiting_client",)),
    "held": ("Held", ("held",)),
    "closed": ("Declined", ("declined",)),
    "other": ("Not requests", ("not_proposal", "merged")),
}


@bp.app_context_processor
def _counts():
    from flask import g
    u = g.get("user")
    if u is None or u.role == "client":
        return {"inbox_counts": {}}
    return {"inbox_counts": intake.counts(get_db())}


def _proposal(s, ref):
    p = s.scalars(select(Proposal).options(selectinload(Proposal.messages).selectinload(Message.attachments))
                  .where(Proposal.ref == ref)).first()
    if p is None:
        abort(404)
    return p


@bp.route("/inbox")
@login_required
def inbox():
    s = get_db()
    tab = request.args.get("tab", "review")
    if tab not in TABS and tab != "all":
        tab = "review"
    q = select(Proposal).order_by(Proposal.updated_at.desc())
    if tab != "all":
        q = q.where(Proposal.status.in_(TABS[tab][1]))
    items = s.scalars(q.limit(300)).all()
    counts = dict(s.execute(select(Proposal.status, func.count()).group_by(Proposal.status)).all())
    tab_counts = {k: sum(counts.get(st, 0) for st in v[1]) for k, v in TABS.items()}
    cfg = current_app.config["APP"]
    return render_template("inbox.html", items=items, tab=tab, tabs=TABS, tab_counts=tab_counts, cfg=cfg)


@bp.route("/inbox/new", methods=["GET", "POST"])
@needs("hold")
def inbox_new():
    """Anything that didn't come by email or WhatsApp: paste it, or upload the file or screenshot."""
    s = get_db()
    if request.method == "POST":
        text = f_str("body")
        files = [f for f in request.files.getlist("files") if f and f.filename]
        if not text and not files:
            flash("Paste the request or attach a file.", "error")
            return render_template("inbox_new.html"), 400
        atts = [{"filename": f.filename, "content_type": f.mimetype or "", "data": f.read()} for f in files]
        p, _, _ = intake.receive(s, channel="manual", sender=f_str("sender"), sender_name=f_str("sender_name"),
                                 subject=f_str("subject"), body=text, attachments=atts,
                                 actor=current_user().email)
        s.commit()
        return done(f"{p.ref} added. The agent is reading it.", "inbox.proposal_page", ref=p.ref)
    return render_template("inbox_new.html")


def _form_lines(p):
    """The draft's lines in the booking form's shape, then any the agent couldn't tie to a screen."""
    d = p.draft or {}
    out = []
    for ln in d.get("lines") or []:
        out.append({"id": "", "screen_id": ln.get("screen_id"), "start_date": ln.get("start_date", ""),
                    "end_date": ln.get("end_date", ""), "start_hour": ln.get("start_hour", ""),
                    "end_hour": ln.get("end_hour", ""), "ad_seconds": ln.get("ad_seconds", ""),
                    "freq_mode": ln.get("freq_mode", "slots"), "freq_value": ln.get("freq_value", "1"),
                    "confidence": ln.get("confidence") or {}, "evidence": ln.get("evidence", ""),
                    "alternatives": ln.get("alternatives") or []})
    for ln in d.get("unmatched") or []:
        # Without a screen, only frequencies that don't depend on the screen's loop carry over.
        unit, value = ln.get("freq_unit"), ln.get("freq_value")
        mode, val = {"slots": ("slots", value), "plays_per_hour": ("pph", value),
                     "whole_screen": ("whole", 1)}.get(unit, ("slots", 1))
        out.append({"id": "", "screen_id": None, "start_date": ln.get("start_date", ""),
                    "end_date": ln.get("end_date", ""), "start_hour": ln.get("start_hour", ""),
                    "end_hour": ln.get("end_hour", ""), "ad_seconds": ln.get("ad_seconds", ""),
                    "freq_mode": mode, "freq_value": f"{float(val or 1):g}",
                    "confidence": ln.get("confidence") or {}, "evidence": ln.get("evidence", ""),
                    "hint": ln.get("hint", ""), "alternatives": ln.get("alternatives") or []})
    return out or [{}]


def _render(s, p, lines=None, overbooked=None, status=200):
    d = p.draft or {}
    dup = s.get(Proposal, p.duplicate_of_id) if p.duplicate_of_id else None
    cost = s.scalar(select(func.coalesce(func.sum(AiCall.usd), 0.0)).where(AiCall.proposal_id == p.id)) or 0.0
    return render_template(
        "proposal.html", p=p, d=d, lines=lines or _form_lines(p), checks=p.checks or [],
        clients=s.scalars(select(Client).order_by(Client.name)).all(), screens=_active_screens(s),
        overbooked=overbooked, form=request.form, dup=dup, cost=cost, booking=intake.booking_for(s, p),
        reading_now=p.status in ("new", "reading"),
        case=s.scalars(select(EvalCase).where(EvalCase.source_proposal_id == p.id)).first()), status


@bp.route("/inbox/<ref>")
@login_required
def proposal_page(ref):
    s = get_db()
    return _render(s, _proposal(s, ref))


def _draft_from_form(s, p, lines, client):
    """Save the team's edits as the draft (marked as checked by a person) and re-check vacancy."""
    d = dict(p.draft or {})
    out = []
    for li in lines:
        sc = s.get(Screen, li.screen_id)
        per_slot = sc.plays_per_hour_per_slot
        slots = li.plays_per_hour / per_slot if per_slot else 0
        mode, value = ("whole", 1) if li.exclusive else \
            (("slots", round(slots)) if abs(slots - round(slots)) < 1e-6 and slots >= 1 else ("pph", li.plays_per_hour))
        out.append({"screen_id": sc.id, "screen_code": sc.code, "start_date": li.start_date.isoformat(),
                    "end_date": li.end_date.isoformat(),
                    "start_hour": "" if li.start_hour is None else li.start_hour,
                    "end_hour": "" if li.end_hour is None else li.end_hour, "ad_seconds": li.ad_seconds,
                    "freq_mode": mode, "freq_value": f"{value:g}", "plays_per_hour": li.plays_per_hour,
                    "exclusive": li.exclusive, "confidence": {"screen": "checked", "dates": "checked",
                                                              "frequency": "checked"},
                    "evidence": "", "hint": "", "alternatives": []})
    d.update(lines=out, unmatched=[], title=f_str("title"), notes=f_str("notes"))
    if client is not None:
        d.update(client_id=client.id, client_name=client.name, client_confidence="checked")
    d["missing"] = [m for m in d.get("missing") or [] if m not in ("screens", "dates", "frequency", "client")
                    or (m == "client" and client is None)]
    p.draft = d
    p.checks = intake.check(s, out, extract.today_for(s))
    if client is not None:
        p.client_id = client.id


@bp.route("/inbox/<ref>/<action>", methods=["POST"])
@login_required
def proposal_action(ref, action):
    s = get_db()
    p = _proposal(s, ref)
    u = current_user()
    need = {"save": "hold", "hold": "hold", "ask": "hold", "decline": "hold", "dismiss": "hold", "reopen": "hold",
            "read": "hold", "merge": "hold", "case": "accuracy"}.get(action)
    if need is None:
        abort(404)
    if not can(u, need):
        abort(403)
    try:
        if action in ("save", "hold"):
            s.autoflush = False
            lines, errors = _lines_from_form(s)
            client = _client_from_form(s)
            if errors or (action == "hold" and client is None):
                for e in dict.fromkeys(errors or ["Choose a client, or type a new client's name."]):
                    flash(e, "error")
                s.rollback()
                return _render(s, _proposal(s, ref), lines=[_echo(l) for l in lines], status=400)
            if action == "save":
                _draft_from_form(s, p, lines, client)
                s.commit()
                return done("Saved, and vacancy checked again.", "inbox.proposal_page", ref=ref)
            try:
                b = intake.place_hold(s, u, p, client=client, lines=lines, title=f_str("title"),
                                      notes=f_str("notes"), override_reason=f_str("override_reason"))
            except svc.Overbooked as e:
                s.rollback()
                flash("There isn't room for all of this. See below.", "error")
                return _render(s, _proposal(s, ref), lines=[_echo(l) for l in lines], overbooked=e.results,
                               status=400)
            s.commit()
            return done(f"Hold {b.code} placed. The confirmation to the client is waiting in the Outbox.",
                        "inbox.proposal_page", ref=ref)
        if action == "ask":
            m = intake.ask_client(s, u, p)
            s.commit()
            return redirect(url_for("inbox.outbox_message", message_id=m.id))
        if action == "decline":
            m = intake.offer_alternatives(s, u, p)
            s.commit()
            return redirect(url_for("inbox.outbox_message", message_id=m.id))
        if action == "dismiss":
            intake.not_a_request(s, u, p)
        elif action == "reopen":
            intake.reopen(s, u, p)
        elif action == "read":
            if p.status in ("held", "merged"):
                raise svc.BookingError(f"{p.ref} is {p.status}; it isn't read again.")
            p.status = "new"
            intake.queue_read(s, p, actor=u.email)
            log(s, u.email, "reread", "proposal", p.ref, "Asked the agent to read it again")
        elif action == "merge":
            target = s.get(Proposal, p.duplicate_of_id) if p.duplicate_of_id else None
            if target is None:
                raise svc.BookingError("There's nothing to merge it into.")
            intake.merge(s, p, target, u.email)
            s.commit()
            return done(f"Merged into {target.ref}.", "inbox.proposal_page", ref=target.ref)
        elif action == "case":
            case = evaluate.case_from_proposal(s, p, u.email)
            s.commit()
            return done(f"Saved as test case '{case.name}'.", "inbox.accuracy")
    except (svc.BookingError, Forbidden, ValueError) as e:
        s.rollback()
        flash(_why(e), "error")
        return redirect(url_for("inbox.proposal_page", ref=ref))
    s.commit()
    return redirect(url_for("inbox.proposal_page", ref=ref))


def _echo(li):
    return {"id": "", "screen_id": li.screen_id, "start_date": li.start_date.isoformat(),
            "end_date": li.end_date.isoformat(), "start_hour": "" if li.start_hour is None else li.start_hour,
            "end_hour": "" if li.end_hour is None else li.end_hour, "ad_seconds": li.ad_seconds,
            "freq_mode": "whole" if li.exclusive else "pph", "freq_value": f"{li.plays_per_hour:g}"}


@bp.route("/attachments/<int:att_id>")
@login_required
def attachment(att_id):
    a = get_db().get(Attachment, att_id) or abort(404)
    if not a.data:
        abort(404)
    inline = (a.content_type or "").startswith(("image/", "application/pdf"))
    return Response(a.data, mimetype=a.content_type or "application/octet-stream", headers={
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename=\"{_safe(a.filename)}\"",
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@bp.route("/bookings/<code>/documents/<int:doc_id>")
@login_required
def booking_document(code, doc_id):
    d = get_db().get(BookingDocument, doc_id) or abort(404)
    return Response(d.data, mimetype=d.content_type or "application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename=\"{_safe(d.filename)}\"", "X-Content-Type-Options": "nosniff"})


def _safe(name):
    return "".join(ch for ch in (name or "file") if ch.isalnum() or ch in "._- ")[:120] or "file"


# ── Outbox ──────────────────────────────────────────────────────────────────────

@bp.route("/outbox")
@login_required
def outbox_list():
    s = get_db()
    sent = s.scalars(select(Message).where(Message.direction == "out", Message.status == "sent")
                     .order_by(Message.sent_at.desc()).limit(30)).all()
    return render_template("outbox.html", waiting=outbox.waiting(s), sent=sent)


@bp.route("/outbox/<int:message_id>", methods=["GET", "POST"])
@login_required
def outbox_message(message_id):
    s = get_db()
    m = s.get(Message, message_id)
    if m is None or m.direction != "out":
        abort(404)
    if request.method == "POST":
        if not can(current_user(), "send_messages"):
            abort(403)
        action = request.form.get("action")
        try:
            if action == "approve":
                outbox.approve(s, current_user(), m, subject=f_str("subject"), body=request.form.get("body", ""),
                               recipient=f_str("recipient"), channel=f_str("channel") or None)
                msg = ("Approved. It goes out within a minute." if m.channel != "manual" and m.recipient
                       else "Approved. Copy it, send it yourself, then mark it sent.")
            elif action == "save":
                m.subject, m.body, m.recipient = f_str("subject"), request.form.get("body", ""), f_str("recipient")
                msg = "Saved."
            elif action == "sent":
                outbox.mark_sent(s, current_user(), m)
                msg = "Marked as sent."
            elif action == "discard":
                outbox.discard(s, current_user(), m)
                msg = "Discarded."
            else:
                abort(400)
        except outbox.OutboxError as e:
            s.rollback()
            flash(str(e), "error")
            return redirect(url_for("inbox.outbox_message", message_id=m.id))
        s.commit()
        flash(msg, "ok")
        if m.proposal is not None:
            return redirect(url_for("inbox.proposal_page", ref=m.proposal.ref))
        return redirect(url_for("inbox.outbox_list"))
    b = s.get(Booking, m.booking_id) if m.booking_id else None
    return render_template("outbox_message.html", msg=m, booking=b,
                           cfg=current_app.config["APP"])


# ── Accuracy ────────────────────────────────────────────────────────────────────

@bp.route("/inbox/accuracy")
@needs("accuracy")
def accuracy():
    s = get_db()
    cases = s.scalars(select(EvalCase).order_by(EvalCase.id.desc())).all()
    runs = s.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(10)).all()
    return render_template("accuracy.html", cases=cases, runs=runs, gate=evaluate.gate(s), min_cases=evaluate.MIN_CASES,
                           pass_pct=evaluate.PASS_PCT, fields=evaluate.FIELDS)


@bp.route("/inbox/accuracy/run", methods=["POST"])
@needs("accuracy")
def accuracy_run():
    s = get_db()
    if not s.scalar(select(func.count()).select_from(EvalCase).where(EvalCase.active.is_(True))):
        flash("Add some test cases first.", "error")
        return redirect(url_for("inbox.accuracy"))
    run = EvalRun(started_by=current_user().email)
    s.add(run)
    s.flush()
    enqueue(s, "eval_run", {"run_id": run.id})
    log(s, current_user().email, "started", "eval_run", run.id, "Started the accuracy test")
    s.commit()
    return done("The test is running. It reads every case again; this page shows the result when it's done.",
                "inbox.accuracy_run_page", run_id=run.id)


@bp.route("/inbox/accuracy/runs/<int:run_id>")
@needs("accuracy")
def accuracy_run_page(run_id):
    s = get_db()
    run = s.get(EvalRun, run_id) or abort(404)
    cases = {c.id: c for c in s.scalars(select(EvalCase))}
    return render_template("accuracy_run.html", run=run, cases=cases, fields=evaluate.FIELDS)


def _expected_from_form():
    """The right answer, typed in: one row per screen."""
    f = request.form
    lines = []
    for code, start, end, sh, eh, pph, whole in zip(f.getlist("e_screen"), f.getlist("e_start"), f.getlist("e_end"),
                                                    f.getlist("e_start_hour"), f.getlist("e_end_hour"),
                                                    f.getlist("e_pph"), f.getlist("e_whole")):
        if not code.strip():
            continue
        date.fromisoformat(start)
        date.fromisoformat(end or start)
        lines.append({"screen": code.strip().upper(), "start": start, "end": end or start,
                      "start_hour": int(sh) if sh.strip() else None, "end_hour": int(eh) if eh.strip() else None,
                      "plays_per_hour": float(pph) if pph.strip() and whole != "1" else None,
                      "exclusive": whole == "1"})
    if not lines:
        raise ValueError("Give at least one screen with its dates.")
    return {"client": f_str("e_client"), "lines": lines}


@bp.route("/inbox/accuracy/new", methods=["GET", "POST"])
@bp.route("/inbox/accuracy/<int:case_id>", methods=["GET", "POST"])
@needs("accuracy")
def accuracy_case(case_id=None):
    s = get_db()
    case = s.get(EvalCase, case_id) if case_id else EvalCase(name="", channel="email", expected={"lines": []})
    if case_id and case is None:
        abort(404)
    if request.method == "POST":
        if request.form.get("action") == "toggle" and case.id:
            case.active = not case.active
            s.commit()
            return done("Updated.", "inbox.accuracy")
        try:
            case.expected = _expected_from_form()
            received = f_str("received_on")
            case.received_on = date.fromisoformat(received) if received else None
        except ValueError as e:
            flash(str(e) if "Give" in str(e) else "Check the dates in the answer (yyyy-mm-dd).", "error")
            return render_template("accuracy_case.html", case=case, screens=_active_screens(s)), 400
        case.name = f_str("name") or "Untitled case"
        case.channel = f_str("channel") or "email"
        case.subject = f_str("subject")
        case.text = request.form.get("text", "")
        if not case.id:
            case.created_by = current_user().email
            s.add(case)
        for f in request.files.getlist("files"):
            if f and f.filename:
                data = f.read()
                case.attachments.append(Attachment(filename=f.filename, content_type=f.mimetype or "", size=len(data),
                                                   data=data))
        s.commit()
        return done("Test case saved.", "inbox.accuracy")
    return render_template("accuracy_case.html", case=case, screens=_active_screens(s))


# ── Costs ───────────────────────────────────────────────────────────────────────

@bp.route("/settings/costs")
@needs("costs")
def costs():
    s = get_db()
    today = date.today()
    month_start = today.replace(day=1)
    rows = s.execute(select(AiCall.purpose, func.count(), func.coalesce(func.sum(AiCall.usd), 0.0))
                     .where(AiCall.at >= month_start).group_by(AiCall.purpose)).all()
    per_client = s.execute(select(Client.name, func.count(), func.coalesce(func.sum(AiCall.usd), 0.0))
                           .join(Client, Client.id == AiCall.client_id).where(AiCall.at >= month_start)
                           .group_by(Client.name).order_by(func.sum(AiCall.usd).desc()).limit(20)).all()
    reads = s.execute(select(func.count(func.distinct(AiCall.proposal_id)), func.coalesce(func.sum(AiCall.usd), 0.0))
                      .where(AiCall.purpose == "read_proposal", AiCall.at >= today - timedelta(days=30))).one()
    recent = s.scalars(select(AiCall).order_by(AiCall.id.desc()).limit(50)).all()
    by_month = {}
    for at, usd in s.execute(select(AiCall.at, AiCall.usd).where(AiCall.at >= month_start - timedelta(days=335))):
        by_month[at.strftime("%Y-%m")] = by_month.get(at.strftime("%Y-%m"), 0.0) + (usd or 0.0)
    months = sorted(by_month.items())
    return render_template("costs.html", rows=rows, per_client=per_client, reads=reads, recent=recent,
                           months=months[-12:], month_start=month_start, model=current_app.config["APP"].PROPOSAL_MODEL)
