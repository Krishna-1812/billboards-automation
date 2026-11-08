"""Collecting and checking the client's creative files (Phase 3).

    request_files()   on confirmation: a Drive folder, a private upload link, a deadline, and a message to the
                      client listing each screen's exact spec (a draft, approved by a person).
    add_file()        a file arrives (Drive folder, upload link, email or WhatsApp reply, or the team): it becomes
                      the next numbered version, and a check is queued.
    check()           the worker measures the file against every booked screen it is for, has Claude read its
                      stills, writes the report, compares with the previous version, and drafts feedback.
    approve/reject    a person decides. When every booked screen has an approved file, the booking moves to
                      Creative approved.
    tick()            reminders before the deadline, and alerts when a start date is at risk.
    poll_drive()      new files in the booking folders.
"""

import logging
import os
import secrets
import shutil
import tempfile
from collections import OrderedDict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from . import creative_checks as cc
from . import creative_store, drafts, runtime, settings, sync
from .activity import log, notify
from .bookings import actor_of
from .google_client import folder_url
from .jobs import enqueue, handler
from .models import (CREATIVE_STAGES, FILE_STAGES, Booking, BookingLine, Creative, CreativeFrame, Message, utcnow)
from .permissions import require

log_ = logging.getLogger(__name__)

MEDIA = ("video", "image")
OPEN = ("received", "checking", "passed", "warnings", "failed", "error")


class CreativeError(Exception):
    pass


# ── What's needed ───────────────────────────────────────────────────────────────

def targets(booking, lines=None):
    """The distinct things to check against: (spec, ad length) with the screens that need them."""
    out = OrderedDict()
    for l in (lines if lines is not None else booking.lines):
        spec = l.screen.spec
        key = (spec.id if spec else None, l.ad_seconds)
        out.setdefault(key, {"spec": spec, "ad_seconds": l.ad_seconds, "lines": []})["lines"].append(l)
    return list(out.values())


def working_days_before(day, n, weekend):
    d, left = day, n
    while left > 0:
        d -= timedelta(days=1)
        if d.weekday() not in weekend:
            left -= 1
    return d


def weekend_days(session):
    return {int(x) for x in str(settings.get(session, "weekend_days") or "").split(",") if x.strip().isdigit()}


def deadline_for(session, booking, today=None):
    today = today or date.today()
    d = working_days_before(booking.start_date, int(settings.get(session, "creative_lead_days")),
                            weekend_days(session))
    return max(d, today)


def upload_url(booking):
    base = runtime.config.APP_URL if runtime.config else ""
    return f"{base}/upload/{booking.upload_token}" if booking.upload_token else ""


def coverage(session, booking):
    """For each booked screen: the file that covers it best, and where it stands."""
    files = session.scalars(select(Creative).where(Creative.booking_id == booking.id)
                            .order_by(Creative.version.desc())).all()
    rank = {"approved": 0, "passed": 1, "warnings": 2, "checking": 3, "received": 3, "failed": 4, "error": 5}
    need_client = settings.get(session, "client_approves_final")
    out = []
    for l in booking.lines:
        mine = [c for c in files if l.id in (c.line_ids or []) and c.status in rank]
        best = min(mine, key=lambda c: (rank[c.status], -c.version)) if mine else None
        latest = mine[0] if mine else None
        state = "missing" if best is None else ("approved" if best.status == "approved" else latest.status)
        if state == "approved" and need_client and best.client_approved_at is None:
            # Phase 4: the owner asked clients to approve the final preview too.
            ok = [c for c in mine if c.status == "approved" and c.client_approved_at is not None]
            if ok:
                best = max(ok, key=lambda c: c.version)
            else:
                state = "waiting_client"
        out.append({"line": l, "screen": l.screen, "spec": l.screen.spec, "creative": best if state == "approved"
                    else latest, "state": state})
    return out


# ── Asking for the files ────────────────────────────────────────────────────────

def ensure_folder(session, booking):
    """The booking's Drive folder (inside a folder per client), shared with the client's email."""
    if booking.drive_folder_id:
        return booking.drive_folder_id
    g = creative_store.drive(session)
    if g is None:
        return ""
    parent = settings.get(session, "drive_parent_folder_id")
    client_folder = g.drive_folder(booking.client.name, parent)
    booking.drive_folder_id = g.drive_folder(f"{booking.code} {booking.title}".strip(), client_folder)
    if booking.client.email:
        try:
            g.drive_share(booking.drive_folder_id, booking.client.email, "writer")
        except Exception as e:  # noqa: BLE001 - the upload link still works
            notify(session, f"Couldn't share the Drive folder for {booking.code} with {booking.client.email}: {e}",
                   kind="warning", link=f"/bookings/{booking.code}")
    return booking.drive_folder_id


def request_files(session, booking, actor="agent", today=None):
    """Ask the client for the files: folder, upload link, deadline, and a draft message with every spec."""
    from .intake import client_contact
    if booking.status not in CREATIVE_STAGES:
        raise CreativeError(f"{booking.code} is {booking.status}; files are collected once it's confirmed.")
    missing_spec = [l.screen.code for l in booking.lines if l.screen.spec is None]
    if missing_spec:
        notify(session, f"{booking.code}: screens without a spec ({', '.join(missing_spec)}); the file request "
                        "can't list their rules until a spec is set.", kind="warning", link=f"/bookings/{booking.code}")
    try:
        ensure_folder(session, booking)
    except Exception as e:  # noqa: BLE001 - carry on with the upload link
        log_.warning("Drive folder for %s failed: %s", booking.code, e)
        notify(session, f"Couldn't make the Drive folder for {booking.code}: {e}", kind="warning",
               link=f"/bookings/{booking.code}")
    booking.upload_token = booking.upload_token or secrets.token_urlsafe(24)
    booking.creative_deadline = deadline_for(session, booking, today)
    booking.creative_requested_at = utcnow()
    if booking.status == "confirmed":
        booking.status = "awaiting_creative"
    who = client_contact(session, booking)
    rows = [(t["spec"], t["ad_seconds"], [l.screen.code for l in t["lines"]]) for t in targets(booking)
            if t["spec"] is not None]
    subject, body = drafts.creative_request(who["lang"], who["name"], settings.get(session, "company_name"), booking,
                                            rows, booking.creative_deadline, upload_url(booking),
                                            folder_url(booking.drive_folder_id))
    m = drafts.new_draft(session, kind="creative_request", channel=who["channel"], recipient=who["to"],
                         subject=subject, body=body, language=who["lang"], proposal=who["proposal"], booking=booking,
                         in_reply_to=who["reply"])
    session.flush()
    from .automation import send_if_automatic
    if send_if_automatic(session, m, "auto_send_client"):
        log(session, actor, "requested_files", "booking", booking.code,
            f"Files requested, due {booking.creative_deadline:%d %b %Y}; sent to the client automatically")
    else:
        log(session, actor, "requested_files", "booking", booking.code,
            f"Files requested, due {booking.creative_deadline:%d %b %Y}; the message is waiting in the Outbox")
        notify(session, f"The file request for {booking.code} is ready to approve in the Outbox.", kind="info",
               link="/outbox", topic="files")
    sync.queue_booking(session, booking)
    return m


@handler("creative_request")
def _request_job(session, payload):
    b = session.get(Booking, payload["booking_id"])
    if b is not None and b.status == "confirmed" and b.creative_requested_at is None:
        request_files(session, b)


# ── Receiving files ─────────────────────────────────────────────────────────────

def add_file(session, booking, *, filename, content_type="", data=None, path=None, source="upload",
             drive_file_id="", message=None, actor="client", line_ids=None):
    if booking.status not in FILE_STAGES:
        raise CreativeError(f"{booking.code} isn't collecting files ({booking.status}).")
    version = (session.scalar(select(func.max(Creative.version)).where(Creative.booking_id == booking.id)) or 0) + 1
    c = Creative(booking=booking, version=version, filename=(filename or "file")[:300],
                 content_type=(content_type or "")[:120], source=source, drive_file_id=drive_file_id,
                 message_id=message.id if message is not None else None, line_ids=list(line_ids or []),
                 status="received")
    session.add(c)
    if not drive_file_id:
        creative_store.save(session, booking, c, path=path, data=data)
    session.flush()
    if booking.status in ("confirmed", "awaiting_creative", "changes_requested"):
        booking.status = "checking"
    enqueue(session, "check_creative", {"creative_id": c.id}, dedupe_key=f"check:{c.id}")
    log(session, actor, "file_received", "booking", booking.code, f"v{version} {c.filename} received by {source}")
    return c


def is_media(filename, content_type):
    from .documents import kind_of
    ct = (content_type or "").lower()
    ext = os.path.splitext(filename or "")[1].lower()
    return ct.startswith("video/") or kind_of(filename, content_type) == "image" or \
        ext in (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


def booking_for_reply(session, channel, sender, reply_ids, has_media):
    """The booking a client's email or WhatsApp is sending files for, if it is one."""
    ids = [r for r in reply_ids if r]
    if ids:
        m = session.scalars(select(Message).where(Message.external_id.in_(ids), Message.booking_id.is_not(None))
                            .order_by(Message.id.desc())).first()
        if m is not None:
            b = session.get(Booking, m.booking_id)
            if b is not None and b.status in FILE_STAGES:
                return b
    if not has_media or not sender:
        return None
    from .intake import sender_is_client
    from types import SimpleNamespace
    open_ = session.scalars(select(Booking).where(Booking.status.in_(("awaiting_creative", "checking",
                                                                       "changes_requested")))).all()
    mine = [b for b in open_ if sender_is_client(SimpleNamespace(sender=sender), b.client)]
    return mine[0] if len(mine) == 1 else None


def files_from_message(session, booking, message, actor="client"):
    made = []
    for a in message.attachments:
        if a.data and is_media(a.filename, a.content_type):
            made.append(add_file(session, booking, filename=a.filename, content_type=a.content_type, data=a.data,
                                 source=message.channel, message=message, actor=actor))
    if made:
        notify(session, f"{len(made)} file(s) for {booking.code} arrived by {message.channel} and are being checked.",
               kind="info", link=f"/bookings/{booking.code}")
    return made


def _auto_lines(booking, info):
    """Which booked screens a file is for: those whose spec has its exact size; if none, all of them."""
    w, h = info.get("width"), info.get("height")
    match = [l.id for l in booking.lines if l.screen.spec and (l.screen.spec.width_px, l.screen.spec.height_px) == (w, h)]
    return match or [l.id for l in booking.lines]


# ── Checking ────────────────────────────────────────────────────────────────────

@handler("check_creative")
def _check_job(session, payload):
    c = session.get(Creative, payload["creative_id"])
    if c is not None and c.status in ("received", "checking", "error"):
        check(session, c)


def check(session, c, actor="agent"):
    b = c.booking
    c.status = "checking"
    work = tempfile.mkdtemp(prefix="creative-")
    try:
        try:
            path = creative_store.materialise(session, c, work)
        except creative_store.StoreError as e:
            c.status, c.error = "error", str(e)
            return c
        info = cc.probe(path, c.filename)
        c.probe = {k: v for k, v in info.items() if k != "error"}
        if not c.line_ids and info.get("width"):
            c.line_ids = _auto_lines(b, info)
        elif not c.line_ids:
            c.line_ids = [l.id for l in b.lines]
        scan = cc.scan_video(path, info.get("duration")) if info["kind"] == "video" else None
        frames = cc.sample_frames(path, info, os.path.join(work, "frames")) if info.get("readable") else []
        c.frames.clear()
        session.flush()
        for t, jpeg in frames:
            c.frames.append(CreativeFrame(at_seconds=t, jpeg=jpeg))
        lines = [l for l in b.lines if l.id in c.line_ids]
        blk, frz = float(settings.get(session, "black_max_s")), float(settings.get(session, "freeze_max_s"))
        tech = []
        for tg in targets(b, lines):
            if tg["spec"] is None:
                tech.append({"spec_id": None, "spec_name": "no spec", "ad_seconds": tg["ad_seconds"],
                             "screens": [l.screen.code for l in tg["lines"]],
                             "checks": [cc.check("file_type", "warn", "", "", "The screen has no spec to check against.")]})
                continue
            tech.append({"spec_id": tg["spec"].id, "spec_name": tg["spec"].name, "ad_seconds": tg["ad_seconds"],
                         "screens": [l.screen.code for l in tg["lines"]],
                         "checks": cc.technical(info, scan, tg["spec"], tg["ad_seconds"], blk, frz)})
        specs = [tg["spec"] for tg in targets(b, lines) if tg["spec"] is not None]
        content, raw = [], None
        if specs and info.get("readable"):
            strict = max(specs, key=lambda s: s.safe_margin_pct or 0)
            rules = [r.strip() for r in (settings.get(session, "content_rules") or "").splitlines() if r.strip()]
            content, raw = cc.content(session, frames, strict, client_name=b.client.name, rules=rules,
                                      company=settings.get(session, "company_name"), actor=actor, booking_id=b.id,
                                      creative_id=c.id, client_id=b.client_id)
        all_checks = [x for t in tech for x in t["checks"]] + content
        c.results = {"targets": tech, "content": content, "content_raw": raw, "verdict": cc.verdict(all_checks),
                     "fixed": _fixed_since(session, c, all_checks)}
        c.status = c.results["verdict"]
        c.checked_at = utcnow()
        c.error = ""
        c.team_summary = team_summary(c, all_checks)
        _supersede(session, c)
        _after_check(session, c, all_checks)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c


def failures(c):
    r = c.results or {}
    seen, out = set(), []
    for t in r.get("targets", []):
        for x in t["checks"]:
            if x["status"] == "fail" and (x["key"], x["measured"]) not in seen:
                seen.add((x["key"], x["measured"]))
                out.append(x)
    return out + [x for x in r.get("content", []) if x["status"] == "fail"]


def all_checks(c):
    r = c.results or {}
    return [x for t in r.get("targets", []) for x in t["checks"]] + list(r.get("content", []))


def team_summary(c, checks):
    fails = [x for x in checks if x["status"] == "fail"]
    counted = [x for x in checks if x["status"] != "skip"]
    head = f"v{c.version} {c.filename} for {c.booking.code}"
    if not fails:
        warns = [x for x in checks if x["status"] == "warn"]
        return head + (f": passes, with {len(warns)} thing(s) to look at." if warns else ": passes every check.")
    parts = []
    for x in fails[:6]:
        bit = f"{x['label'].lower()}: {x['measured']}"
        if x["required"]:
            bit += f", needs {x['required']}"
        if x.get("at") is not None:
            bit += f" (at {cc.mmss(x['at'])})"
        parts.append(bit)
    return f"{head}: fails {len(fails)} of {len(counted)} checks: " + "; ".join(parts) + "."


def _fixed_since(session, c, checks):
    """The problems the previous version had that this one no longer has."""
    prev = session.scalars(select(Creative).where(Creative.booking_id == c.booking_id, Creative.id != c.id,
                                                  Creative.version < c.version, Creative.results.is_not(None))
                           .order_by(Creative.version.desc())).first()
    if prev is None or not set(prev.line_ids or []) & set(c.line_ids or []):
        return None
    before = {x["key"] for x in all_checks(prev) if x["status"] == "fail"}
    now = {x["key"] for x in checks if x["status"] == "fail"}
    return {"previous": prev.version, "had": len(before), "fixed": sorted(before - now), "still": sorted(before & now),
            "new": sorted(now - before)}


def _supersede(session, c):
    """Older open versions for the same screens are replaced by this one."""
    for old in session.scalars(select(Creative).where(Creative.booking_id == c.booking_id, Creative.id != c.id,
                                                      Creative.version < c.version, Creative.status.in_(OPEN))):
        if set(old.line_ids or []) <= set(c.line_ids or []):
            old.status = "superseded"


def _after_check(session, c, checks):
    b = c.booking
    fails = [x for x in checks if x["status"] == "fail"]
    fixed = (c.results or {}).get("fixed")
    progress = f" ({len(fixed['fixed'])} of {fixed['had']} earlier problems fixed)" if fixed and fixed["had"] else ""
    if fails:
        b.status = "changes_requested"
        m = draft_feedback(session, c, fails)
        technical = any(x["group"] == "technical" for x in fails)
        content = any(x["group"] != "technical" for x in fails)
        allowed = (not technical or settings.get(session, "auto_send_technical")) and \
                  (not content or settings.get(session, "auto_send_content"))
        if allowed and m.recipient and m.channel != "manual":
            from . import outbox
            from .intake import AGENT
            session.flush()
            outbox.approve(session, AGENT, m)
            log(session, "agent", "sent_automatically", "message", m.id,
                f"Feedback on v{c.version} sent to {m.recipient} automatically")
            note = " Feedback was sent to the client automatically."
        else:
            note = " Feedback for the client is waiting in the Outbox."
        notify(session, c.team_summary + progress + note, kind="warning", link=f"/creatives/{c.id}", topic="files")
    else:
        # Feedback about an earlier version that hasn't gone out yet is out of date now.
        for old in session.scalars(select(Message).where(Message.booking_id == b.id, Message.status == "draft",
                                                         Message.kind == "creative_feedback")):
            old.status = "discarded"
        log(session, "agent", "checked", "booking", b.code, c.team_summary + progress)
        if c.status == "passed" and settings.get(session, "auto_approve_passed"):
            approve(session, None, c, automatic=True)
            notify(session, c.team_summary + progress + " The agent approved it.", kind="info",
                   link=f"/creatives/{c.id}", topic="files")
        else:
            notify(session, c.team_summary + progress + " Watch it and approve it.", kind="info",
                   link=f"/creatives/{c.id}", topic="files")
        return
    log(session, "agent", "checked", "booking", b.code, c.team_summary + progress)


def draft_feedback(session, c, fails, note=""):
    from .intake import client_contact
    b = c.booking
    who = client_contact(session, b)
    if note:
        fails = list(fails) + [{"key": "note", "measured": "", "required": "", "detail": note}]
    subject, body = drafts.creative_feedback(who["lang"], who["name"], settings.get(session, "company_name"), b,
                                             c.filename, fails, b.creative_deadline)
    for old in session.scalars(select(Message).where(Message.booking_id == b.id, Message.kind == "creative_feedback",
                                                     Message.status == "draft")):
        old.status = "discarded"
    return drafts.new_draft(session, kind="creative_feedback", channel=who["channel"], recipient=who["to"],
                            subject=subject, body=body, language=who["lang"], proposal=who["proposal"], booking=b,
                            in_reply_to=who["reply"])


# ── Deciding ────────────────────────────────────────────────────────────────────

def approve(session, user, c, note="", automatic=False):
    if automatic:
        if c.status != "passed":
            raise CreativeError("The agent only approves files that pass every check.")
    elif c.status in ("failed", "error"):
        require(user, "override")
        if not note.strip():
            raise CreativeError("This file failed checks: say why it's approved anyway.")
    else:
        require(user, "approve_creative")
    if c.status not in ("passed", "warnings", "failed", "error"):
        raise CreativeError(f"Version {c.version} is {c.status}; only a checked file can be approved.")
    who = "agent" if automatic else actor_of(user)
    c.status, c.decided_by, c.decided_at, c.decision_note = "approved", who, utcnow(), note.strip()
    for other in session.scalars(select(Creative).where(Creative.booking_id == c.booking_id, Creative.id != c.id,
                                                        Creative.status == "approved")):
        if set(other.line_ids or []) <= set(c.line_ids or []):
            other.status = "superseded"
    session.flush()
    b = c.booking
    log(session, who, "approved_file", "booking", b.code,
        f"v{c.version} {c.filename} approved" + (" automatically" if automatic else "")
        + (f": {note.strip()}" if note.strip() else ""))
    if settings.get(session, "client_approves_final") and c.client_approved_at is None:
        from . import client_messages
        client_messages.preview_review(session, c)
        notify(session, f"{b.code}: v{c.version} is approved; the client is asked to approve the final preview.",
               kind="info", link=f"/creatives/{c.id}", topic="files")
    _after_decision(session, b, who)
    return c


def _after_decision(session, b, who):
    """Every screen has an approved file: the booking moves on (and on through the Phase 4 stages)."""
    if b.status in CREATIVE_STAGES and all(x["state"] == "approved" for x in coverage(session, b)):
        b.status = "creative_approved"
        log(session, who, "creative_approved", "booking", b.code, "Every screen has an approved file")
        notify(session, f"{b.code}: every screen has an approved file.", kind="info", link=f"/bookings/{b.code}",
               topic="files")
        sync.queue_booking(session, b)
    from . import lifecycle
    lifecycle.advance(session, b, actor=who)


def client_decide(session, user, c, approve_it, note=""):
    """The client approves the final preview in the portal, or asks for changes (Phase 4)."""
    from .notices import alert
    b = c.booking
    if c.status != "approved" or c.client_approved_at is not None:
        raise CreativeError("This file isn't waiting for your approval.")
    if approve_it:
        c.client_approved_at = utcnow()
        log(session, user.email, "client_approved_file", "booking", b.code, f"The client approved v{c.version}")
        alert(session, "client", f"{b.client.name} approved the final preview of v{c.version} for {b.code}.",
              link=f"/creatives/{c.id}")
        _after_decision(session, b, user.email)
    else:
        if not (note or "").strip():
            raise CreativeError("Tell us what you'd like changed.")
        c.status, c.client_note = "rejected", note.strip()[:2000]
        c.decided_by, c.decided_at, c.decision_note = user.email, utcnow(), f"Client: {note.strip()}"[:2000]
        if b.status in CREATIVE_STAGES + ("creative_approved",):
            b.status = "changes_requested"
        log(session, user.email, "client_changes_file", "booking", b.code,
            f"The client asked for changes to v{c.version}: {note.strip()}"[:500])
        alert(session, "client", f"{b.client.name} asked for changes to v{c.version} for {b.code}: {note.strip()}",
              link=f"/creatives/{c.id}", kind="warning")
    return c


def reject(session, user, c, note):
    require(user, "approve_creative")
    if not (note or "").strip():
        raise CreativeError("Say what's wrong; it goes to the client.")
    if c.status in ("approved", "superseded", "rejected"):
        raise CreativeError(f"Version {c.version} is {c.status}.")
    c.status, c.decided_by, c.decided_at, c.decision_note = "rejected", actor_of(user), utcnow(), note.strip()
    c.booking.status = "changes_requested"
    m = draft_feedback(session, c, failures(c), note=note.strip())
    log(session, actor_of(user), "rejected_file", "booking", c.booking.code, f"v{c.version} rejected: {note.strip()}")
    return m


def recheck(session, user, c):
    require(user, "approve_creative")
    if c.status in ("approved", "superseded"):
        raise CreativeError(f"Version {c.version} is {c.status}.")
    c.status = "received"
    enqueue(session, "check_creative", {"creative_id": c.id}, dedupe_key=f"check:{c.id}")


def assign(session, user, c, line_ids):
    require(user, "approve_creative")
    valid = {l.id for l in c.booking.lines}
    ids = [i for i in line_ids if i in valid]
    if not ids:
        raise CreativeError("Choose at least one screen.")
    c.line_ids = ids
    recheck(session, user, c)


# ── Every minute / every few minutes ────────────────────────────────────────────

def tick(session, today=None):
    """Reminders before the deadline, and alerts when a start date is at risk."""
    from .intake import client_contact
    today = today or date.today()
    lead = int(settings.get(session, "creative_lead_days"))
    for b in session.scalars(select(Booking).options(selectinload(Booking.lines).selectinload(BookingLine.screen))
                             .where(Booking.status.in_(("awaiting_creative", "checking", "changes_requested")))):
        states = {x["state"] for x in coverage(session, b)}
        if states <= {"approved"}:
            continue
        ready = states <= {"approved", "passed", "warnings"}
        if (b.creative_deadline and not ready and b.creative_reminded_at is None
                and b.creative_deadline - today <= timedelta(days=2)):
            who = client_contact(session, b)
            subject, body = drafts.creative_reminder(who["lang"], who["name"], settings.get(session, "company_name"),
                                                     b, b.creative_deadline)
            m = drafts.new_draft(session, kind="creative_reminder", channel=who["channel"], recipient=who["to"],
                                 subject=subject, body=body, language=who["lang"], proposal=who["proposal"],
                                 booking=b, in_reply_to=who["reply"])
            b.creative_reminded_at = utcnow()
            session.flush()
            from .automation import send_if_automatic
            if not send_if_automatic(session, m, "auto_send_client"):
                notify(session, f"{b.code}: files are due {b.creative_deadline:%d %b}. A reminder to the client is "
                                "waiting in the Outbox.", kind="reminder", link="/outbox", topic="files")
        at_risk = working_days_before(b.start_date, lead, weekend_days(session)) <= today
        if at_risk and b.creative_alerted_on != today and b.start_date >= today:
            b.creative_alerted_on = today
            days = (b.start_date - today).days
            notify(session, f"At risk: {b.code} for {b.client.name} starts in {days} day(s) and its files aren't "
                            "approved yet.", kind="error", link=f"/bookings/{b.code}")
            from .mail import queue as queue_email
            from .models import User
            to = [u.email for u in session.scalars(select(User).where(User.active.is_(True),
                                                                      User.role.in_(("owner", "manager", "ops"))))]
            queue_email(session, to, f"At risk: {b.code} starts in {days} day(s) without approved files",
                        f"{b.code} for {b.client.name} starts on {b.start_date:%d %b %Y} and its files aren't approved."
                        f" Open it: {(runtime.config.APP_URL if runtime.config else '')}/bookings/{b.code}")


@handler("poll_drive")
def poll_drive(session, payload=None):
    """New files in the booking folders become new versions."""
    g = creative_store.drive(session)
    if g is None:
        return 0
    found = 0
    for b in session.scalars(select(Booking).where(Booking.drive_folder_id != "",
                                                   Booking.status.in_(FILE_STAGES))):
        known = {fid for (fid,) in session.execute(select(Creative.drive_file_id)
                                                   .where(Creative.booking_id == b.id, Creative.drive_file_id != ""))}
        for f in g.drive_list(b.drive_folder_id):
            if f["id"] in known or not is_media(f.get("name", ""), f.get("mimeType", "")):
                continue
            add_file(session, b, filename=f.get("name", "file"), content_type=f.get("mimeType", ""),
                     source="drive", drive_file_id=f["id"])
            found += 1
    return found
