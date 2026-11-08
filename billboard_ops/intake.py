"""The proposals inbox: every request, from any channel, in one place.

    receive()  a message arrives (email, WhatsApp, pasted or uploaded). It joins the conversation it belongs to,
               or starts a new proposal, and a read is queued.
    process()  the worker reads the conversation with Claude, checks vacancy for what it found, spots
               duplicates, and drafts a question to the client if something is missing. With "place holds on
               their own" switched on (and the accuracy test passed), a request that is complete, certain and
               fits is held straight away.
    place_hold / ask_client / offer_alternatives / not_a_request / merge: what the team does from a proposal.

Nothing is ever sent from here: client messages are drafts until a person approves them (see outbox.py).
"""

import logging
import re
from datetime import date, timedelta

from sqlalchemy import or_, select

from . import ai, bookings, drafts, extract, settings, vacancy
from .activity import log, notify
from .jobs import enqueue, handler
from .models import OPEN_PROPOSAL, Attachment, Booking, Client, Message, Proposal, Screen, utcnow

logger = logging.getLogger(__name__)

THREAD_DAYS = {"email": 30, "whatsapp": 3}
READ_DELAY = {"whatsapp": 30, "email": 5, "manual": 0, "portal": 0}   # WhatsApp: wait for the rest of a burst


class AgentActor:
    """The agent itself, when it places a hold on its own. It has Sales' rights and nothing more."""
    email = "agent"
    role = "sales"
    active = True
    name = "Agent"


AGENT = AgentActor()


def _subject_key(subject):
    s = (subject or "").strip().lower()
    while True:
        new = re.sub(r"^(re|fw|fwd|aw|tr|رد|إعادة توجيه)\s*:\s*", "", s)
        if new == s:
            return s
        s = new


def find_thread(session, *, channel, sender, subject, reply_ids):
    """The proposal a new message belongs to, or None."""
    ids = [r for r in reply_ids if r]
    if ids:
        m = session.scalars(select(Message).where(Message.external_id.in_(ids), Message.proposal_id.is_not(None))
                            .order_by(Message.id.desc())).first()
        if m is not None:
            return m.proposal
    if channel not in THREAD_DAYS or not sender:
        return None
    since = utcnow() - timedelta(days=THREAD_DAYS[channel])
    q = (select(Proposal).where(Proposal.channel == channel, Proposal.sender == sender,
                                Proposal.updated_at >= since,
                                Proposal.status.notin_(("merged", "declined", "not_proposal")))
         .order_by(Proposal.updated_at.desc()))
    for p in session.scalars(q):
        if channel == "whatsapp" or _subject_key(p.subject) == _subject_key(subject):
            return p
    return None


def receive(session, *, channel, sender="", sender_name="", subject="", body="", attachments=(), external_id="",
            reply_ids=(), received_at=None, actor="system"):
    """Store an incoming message. Returns (proposal, message, is_new_proposal); a message seen before
    (same email Message-ID or WhatsApp id) is returned as is, never stored twice."""
    if external_id:
        seen = session.scalars(select(Message).where(Message.external_id == external_id)).first()
        if seen is not None:
            return seen.proposal, seen, False
    from . import creatives
    has_media = any(creatives.is_media(a.get("filename"), a.get("content_type")) or a.get("external_ref")
                    for a in attachments)
    booking = creatives.booking_for_reply(session, channel, sender, list(reply_ids), has_media) \
        if channel in ("email", "whatsapp") else None
    if booking is not None:
        return _receive_files(session, booking, channel=channel, sender=sender, subject=subject, body=body,
                              attachments=attachments, external_id=external_id, reply_ids=reply_ids,
                              received_at=received_at)
    p = find_thread(session, channel=channel, sender=sender, subject=subject, reply_ids=list(reply_ids))
    if p is not None and p.status == "held" and has_media and p.booking is not None \
            and p.booking.status in creatives.CREATIVE_STAGES:
        return _receive_files(session, p.booking, channel=channel, sender=sender, subject=subject, body=body,
                              attachments=attachments, external_id=external_id, reply_ids=reply_ids,
                              received_at=received_at, proposal=p)
    new = p is None
    if new:
        p = Proposal(channel=channel, sender=sender, sender_name=sender_name, subject=subject[:300],
                     language=extract.guess_language(body))
        session.add(p)
        session.flush()
        p.ref = f"PR-{p.id:05d}"
    m = Message(proposal=p, direction="in", channel=channel, kind="inbound", status="received", sender=sender,
                subject=subject[:300], body=body or "", external_id=external_id[:300],
                in_reply_to=(list(reply_ids) or [""])[0][:300], language=extract.guess_language(body))
    if received_at is not None:
        m.created_at = received_at
    session.add(m)
    for a in attachments:
        data = a.get("data")
        m.attachments.append(Attachment(filename=(a.get("filename") or "")[:300],
                                        content_type=(a.get("content_type") or "")[:120],
                                        size=len(data) if data else int(a.get("size") or 0), data=data,
                                        external_ref=a.get("external_ref") or ""))
    p.updated_at = utcnow()
    if sender_name and not p.sender_name:
        p.sender_name = sender_name
    session.flush()
    if p.status in ("held", "declined"):
        notify(session, f"{p.sender_name or p.sender} wrote again on {p.ref} ({p.status}).", kind="info",
               link=f"/inbox/{p.ref}")
    else:
        if p.status in ("waiting_client", "needs_review", "failed", "not_proposal"):
            p.status = "new"
        queue_read(session, p)
    log(session, actor, "received" if new else "replied", "proposal", p.ref,
        f"{'New request' if new else 'New message'} by {channel} from {sender_name or sender or 'someone'}"
        + (f", {len(attachments)} attachment(s)" if attachments else ""))
    return p, m, new


def _receive_files(session, booking, *, channel, sender, subject, body, attachments, external_id, reply_ids,
                   received_at, proposal=None):
    """A client's reply carrying their ad files: it joins the booking, and each file becomes a version to check."""
    from . import creatives
    from .channels import whatsapp
    m = Message(proposal=proposal, booking_id=booking.id, direction="in", channel=channel, kind="files",
                status="received", sender=sender, subject=subject[:300], body=body or "",
                external_id=external_id[:300], in_reply_to=(list(reply_ids) or [""])[0][:300],
                language=extract.guess_language(body))
    if received_at is not None:
        m.created_at = received_at
    session.add(m)
    for a in attachments:
        data = a.get("data")
        m.attachments.append(Attachment(filename=(a.get("filename") or "")[:300],
                                        content_type=(a.get("content_type") or "")[:120],
                                        size=len(data) if data else int(a.get("size") or 0), data=data,
                                        external_ref=a.get("external_ref") or ""))
    session.flush()
    if any(a.external_ref and not a.data for a in m.attachments):
        whatsapp.fetch_message_media(session, m)
    made = creatives.files_from_message(session, booking, m)
    if not made:
        notify(session, f"{sender} wrote about {booking.code}: “{(body or '')[:80]}”", kind="info",
               link=f"/bookings/{booking.code}")
    return proposal, m, False


def queue_read(session, p, actor="agent"):
    enqueue(session, "read_proposal", {"proposal_id": p.id, "actor": actor}, dedupe_key=f"read:{p.id}",
            delay_seconds=READ_DELAY.get(p.channel, 0))


@handler("read_proposal")
def _read_job(session, payload):
    p = session.get(Proposal, payload["proposal_id"])
    if p is not None:
        process(session, p, actor=payload.get("actor") or "agent")


def conversation(p):
    """Messages Claude reads: everything in, and what was actually sent out (drafts are not part of it)."""
    return [m for m in p.messages if m.direction == "in" or m.status == "sent"]


def process(session, p, actor="agent", today=None):
    if p.status in ("held", "declined", "merged"):
        return p
    from .channels import whatsapp
    whatsapp.fetch_pending_media(session, p)
    today = today or extract.today_for(session)
    p.status = "reading"
    try:
        raw, draft = extract.read(session, messages=conversation(p), channel=p.channel, sender=p.sender,
                                  subject=p.subject, today=today, actor=actor, client_id=p.client_id,
                                  proposal_id=p.id)
    except ai.AITransient:
        raise                                   # the job is tried again later
    except (ai.AIFailed, ai.AIUnavailable) as e:
        p.status, p.error = "failed", str(e)
        notify(session, f"{p.ref} couldn't be read automatically: {e} Fill it in by hand.", kind="warning",
               link=f"/inbox/{p.ref}")
        return p
    p.reading, p.draft, p.error = raw, draft, ""
    p.read_count += 1
    p.last_read_at = utcnow()
    p.language = draft["language"] if draft["language"] in ("en", "ar", "mixed") else p.language
    p.client_id = draft["client_id"]
    if not draft["is_booking_request"]:
        p.status = "not_proposal"
        log(session, actor, "read", "proposal", p.ref, "Read: not a booking request")
        return p
    p.checks = check(session, draft["lines"], today)
    _supersede_drafts(session, p, ("clarify",))
    p.duplicate_of_id = None
    dup = find_duplicate(session, p)
    if dup is not None:
        if _same_request(p, dup) and dup.channel != p.channel and dup.status != "held":
            merge(session, p, dup, actor)
            return p
        p.duplicate_of_id = dup.id
    p.ready = bool(draft["ready"])
    fits = bool(p.checks) and all(c["status"] == "fits" for c in p.checks)
    client = session.get(Client, draft["client_id"]) if draft["client_id"] else None
    if p.ready and fits and client is not None and sender_is_client(p, client) and auto_hold_allowed(session):
        try:
            place_hold(session, AGENT, p, client=client,
                       lines=lines_from_draft(draft["lines"]), title=draft["title"], notes=draft["notes"])
            p.auto_held = True
            notify(session, f"The agent placed a hold for {p.ref}. Its confirmation to the client is waiting in "
                            "the Outbox.", kind="info", link=f"/inbox/{p.ref}")
            return p
        except Exception as e:  # noqa: BLE001 - anything unexpected falls back to a person
            logger.info("auto hold for %s didn't go through: %s", p.ref, e)
    p.status = "needs_review"
    if draft["missing"] or draft["questions"] or draft["unmatched"]:
        _draft_clarify(session, p)
    log(session, actor, "read", "proposal", p.ref,
        f"Read: {len(draft['lines'])} screen line(s)"
        + (f", missing {', '.join(draft['missing'])}" if draft["missing"] else "")
        + (", ready to hold" if p.ready and fits else ""))
    return p


def check(session, lines, today):
    """Vacancy for each draft line, with alternatives when it doesn't fit."""
    out = []
    for ln in lines:
        screen = session.get(Screen, ln.get("screen_id")) if ln.get("screen_id") else None
        if screen is None or not ln.get("start_date"):
            out.append({"screen_code": ln.get("screen_code", ""), "status": "unknown", "reason": "incomplete"})
            continue
        li = line_from_draft(ln)
        r = vacancy.check_many(session, [screen], li.request(), today=today)[0]
        out.append({"screen_code": screen.code, "status": r.status, "reason": r.reason, "blocking": r.blocking,
                    "ok_days": r.ok_days, "days": len(r.days),
                    "alt_dates": [[a.isoformat(), b.isoformat()] for a, b in r.alt_dates],
                    "alt_screens": [s.code for s in r.alt_screens]})
    return out


def line_from_draft(ln):
    def h(v):
        return None if v in ("", None) else int(v)
    return bookings.LineIn(screen_id=int(ln["screen_id"]), start_date=date.fromisoformat(ln["start_date"]),
                           end_date=date.fromisoformat(ln.get("end_date") or ln["start_date"]),
                           start_hour=h(ln.get("start_hour")), end_hour=h(ln.get("end_hour")),
                           ad_seconds=int(ln.get("ad_seconds") or 10),
                           plays_per_hour=float(ln.get("plays_per_hour") or 0),
                           exclusive=bool(ln.get("exclusive")))


def lines_from_draft(lines):
    return [line_from_draft(ln) for ln in lines if ln.get("screen_id") and ln.get("start_date")]


# ── Duplicates ──────────────────────────────────────────────────────────────────

def _spans(p):
    return {(ln.get("screen_id"), ln.get("start_date"), ln.get("end_date")) for ln in (p.draft or {}).get("lines", [])}


def _same_request(a, b):
    return bool(_spans(a)) and _spans(a) == _spans(b)


def find_duplicate(session, p):
    """An open proposal from the same client (or sender) for overlapping screens within two days of the dates."""
    draft = p.draft or {}
    mine = draft.get("lines") or []
    if not mine:
        return None
    since = utcnow() - timedelta(days=14)
    who = []
    if draft.get("client_id"):
        who.append(Proposal.client_id == draft["client_id"])
    if p.sender:
        who.append(Proposal.sender == p.sender)
    if not who:
        return None
    q = (select(Proposal).where(Proposal.id != p.id, Proposal.created_at >= since, or_(*who),
                                Proposal.status.in_(OPEN_PROPOSAL + ("held",)))
         .order_by(Proposal.id))
    for other in session.scalars(q):
        for a in mine:
            for b in (other.draft or {}).get("lines") or []:
                if a.get("screen_id") != b.get("screen_id") or not a.get("start_date") or not b.get("start_date"):
                    continue
                if abs((date.fromisoformat(a["start_date"]) - date.fromisoformat(b["start_date"])).days) <= 2:
                    return other
    return None


def merge(session, p, into, actor="agent"):
    """Fold proposal `p` into `into`: its messages move across, `into` is read again."""
    for m in list(p.messages):
        m.proposal = into
    p.status = "merged"
    p.duplicate_of_id = into.id
    into.updated_at = utcnow()
    if into.status not in ("held", "declined"):
        into.status = "new"
        queue_read(session, into, actor)
    log(session, actor, "merged", "proposal", p.ref, f"Merged into {into.ref}: the same request on another channel")
    return into


# ── What the team does ──────────────────────────────────────────────────────────

def _recipient(session, p):
    """Where a reply to this proposal goes: back the way it came; for pasted requests, the client's email."""
    if p.channel in ("email", "whatsapp") and p.sender:
        return p.channel, p.sender
    if p.channel == "portal" and "@" in (p.sender or ""):
        return "email", p.sender
    contact = ((p.draft or {}).get("contact") or {}).get("email") or ""
    client = p.client
    if contact or (client and client.email):
        return "email", contact or client.email
    return "manual", ""


def _last_inbound_id(p):
    ids = [m.external_id for m in p.messages if m.direction == "in" and m.external_id]
    return ids[-1] if ids else ""


def _name(p):
    contact = ((p.draft or {}).get("contact") or {}).get("name") or ""
    return contact or p.sender_name or ""


def _lang(p):
    """Reply in the language the client wrote in; their saved preference only when that isn't known."""
    if p.language in ("en", "ar", "mixed"):
        return p.language
    return p.client.language if p.client is not None and p.client.language in ("en", "ar") else "en"


def _supersede_drafts(session, p, kinds):
    for m in p.messages:
        if m.direction == "out" and m.status == "draft" and m.kind in kinds:
            m.status = "discarded"


def _draft_clarify(session, p):
    d = p.draft or {}
    subject, body = drafts.clarify(_lang(p), _name(p), settings.get(session, "company_name"),
                                   d.get("missing") or [], d.get("questions") or [])
    channel, to = _recipient(session, p)
    return drafts.new_draft(session, kind="clarify", channel=channel, recipient=to, subject=subject, body=body,
                            language=_lang(p), proposal=p, in_reply_to=_last_inbound_id(p))


def place_hold(session, user, p, *, client, lines, title="", notes="", override_reason=""):
    if p.status in ("held", "merged"):
        raise bookings.BookingError(f"{p.ref} is already {p.status}.")
    b = bookings.create(session, user, client, lines, title=title, notes=notes, override_reason=override_reason,
                        source="proposal", source_ref=p.ref)
    p.booking_id = b.id
    p.client_id = client.id
    p.status = "held"
    p.decided_by, p.decided_at = bookings.actor_of(user), utcnow()
    _supersede_drafts(session, p, ("clarify", "decline"))
    subject, body = drafts.hold_confirmation(_lang(p), _name(p), settings.get(session, "company_name"), b)
    channel, to = _recipient(session, p)
    drafts.new_draft(session, kind="hold_confirmation", channel=channel, recipient=to, subject=subject, body=body,
                     language=_lang(p), proposal=p, booking=b, in_reply_to=_last_inbound_id(p))
    log(session, bookings.actor_of(user), "held", "proposal", p.ref, f"Hold {b.code} placed from {p.ref}")
    return b


def ask_client(session, user, p):
    """The clarifying question as a draft (the existing one, or a new one)."""
    for m in p.messages:
        if m.direction == "out" and m.status == "draft" and m.kind == "clarify":
            return m
    m = _draft_clarify(session, p)
    log(session, bookings.actor_of(user), "asked", "proposal", p.ref, "Drafted a question to the client")
    return m


def offer_alternatives(session, user, p):
    """Decline politely, offering the nearest free dates and screens, as a draft."""
    problems = []
    for ln, c in zip((p.draft or {}).get("lines") or [], p.checks or []):
        if c.get("status") == "fits" or not ln.get("start_date"):
            continue
        problems.append({"screen": drafts.screen_label(session, ln["screen_id"]), "start": ln["start_date"],
                         "end": ln.get("end_date") or ln["start_date"],
                         "alt_dates": [(a, b) for a, b in c.get("alt_dates") or []],
                         "alt_screens": [drafts.screen_label(session, s.id) for s in session.scalars(
                             select(Screen).where(Screen.code.in_(c.get("alt_screens") or [])))]})
    if not problems:
        raise bookings.BookingError("Everything asked for fits, so there is nothing to decline.")
    subject, body = drafts.alternatives(_lang(p), _name(p), settings.get(session, "company_name"), problems)
    channel, to = _recipient(session, p)
    _supersede_drafts(session, p, ("clarify", "decline"))
    m = drafts.new_draft(session, kind="decline", channel=channel, recipient=to, subject=subject, body=body,
                         language=_lang(p), proposal=p, in_reply_to=_last_inbound_id(p))
    p.status = "declined"
    p.decided_by, p.decided_at = bookings.actor_of(user), utcnow()
    log(session, bookings.actor_of(user), "declined", "proposal", p.ref, "Declined, offering alternatives")
    return m


def not_a_request(session, user, p):
    p.status = "not_proposal"
    _supersede_drafts(session, p, ("clarify",))
    log(session, bookings.actor_of(user), "dismissed", "proposal", p.ref, "Marked as not a booking request")


def reopen(session, user, p):
    if p.status in ("not_proposal", "declined", "failed"):
        p.status = "needs_review" if p.draft else "new"
        log(session, bookings.actor_of(user), "reopened", "proposal", p.ref, "Reopened")


# ── Settings gate ───────────────────────────────────────────────────────────────

def _digits(v):
    return re.sub(r"\D", "", v or "")


def sender_is_client(p, client):
    """Whether the message really comes from this client: their email address or email domain, or their
    WhatsApp or phone number. Anyone can write "this is Acme" — the agent only acts alone for known senders."""
    sender = (p.sender or "").strip().lower()
    if not sender:
        return False
    if "@" in sender:
        known = (client.email or "").strip().lower()
        if not known:
            return False
        if sender == known:
            return True
        domain = known.split("@")[-1]
        public = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "live.com"}
        return domain not in public and sender.endswith("@" + domain)
    digits = _digits(sender)
    return bool(digits) and digits in {_digits(client.whatsapp), _digits(client.phone)} - {""}


def auto_hold_allowed(session):
    """The agent holds on its own only when the owner switched it on and the latest accuracy test passed."""
    if not settings.get(session, "auto_hold"):
        return False
    from .evaluate import gate
    return gate(session)["passed"]


def client_contact(session, booking):
    """How to reach a booking's client: back through the proposal it came from, else the client's email.
    Returns dict(channel, to, lang, name, reply, proposal), or None when there's no way to reach them."""
    p = session.scalars(select(Proposal).where(Proposal.booking_id == booking.id)).first()
    c = booking.client
    if p is not None:
        channel, to = _recipient(session, p)
        out = {"channel": channel, "to": to, "lang": _lang(p), "name": _name(p), "reply": _last_inbound_id(p),
               "proposal": p}
        # The client said how they'd rather hear from us (Phase 4): that wins over the way the request came in.
        pref = (c.preferred_channel if c else "") or ""
        target = {"email": c.email, "whatsapp": c.whatsapp}.get(pref, "") if c else ""
        if target and pref != channel:
            out.update(channel=pref, to=target, reply="")
        return out
    if c and c.preferred_channel == "whatsapp" and c.whatsapp:
        return {"channel": "whatsapp", "to": c.whatsapp, "lang": c.language if c.language in ("en", "ar") else "en",
                "name": c.contact_name, "reply": "", "proposal": None}
    if c and (c.email or c.whatsapp):
        return {"channel": "email" if c.email else "whatsapp", "to": c.email or c.whatsapp,
                "lang": c.language if c.language in ("en", "ar") else "en", "name": c.contact_name, "reply": "",
                "proposal": None}
    return {"channel": "manual", "to": "", "lang": (c.language if c else "en") or "en",
            "name": c.contact_name if c else "", "reply": "", "proposal": None}


def hold_reminder_drafts(session, booking):
    """A reminder to the client that their hold expires soon, as a draft for the team to approve."""
    if not settings.get(session, "remind_clients"):
        return None
    who = client_contact(session, booking)
    if not who["to"] and who["proposal"] is None:
        return None
    subject, body = drafts.hold_reminder(who["lang"], who["name"], settings.get(session, "company_name"), booking)
    return drafts.new_draft(session, kind="hold_reminder", channel=who["channel"], recipient=who["to"],
                            subject=subject, body=body, language=who["lang"], proposal=who["proposal"],
                            booking=booking, in_reply_to=who["reply"])


def counts(session):
    from sqlalchemy import func
    rows = dict(session.execute(select(Proposal.status, func.count()).group_by(Proposal.status)).all())
    drafts_waiting = session.scalar(select(func.count()).select_from(Message)
                                    .where(Message.direction == "out", Message.status.in_(("draft", "failed"))))
    return {"review": rows.get("needs_review", 0) + rows.get("failed", 0) + rows.get("new", 0)
            + rows.get("reading", 0), "outbox": drafts_waiting or 0, **rows}


def booking_for(session, p):
    return session.get(Booking, p.booking_id) if p.booking_id else None
