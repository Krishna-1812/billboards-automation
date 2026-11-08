"""Data rules (Phase 5): how long files and messages are kept, and deleting a client's data when they ask.

    purge()          every night: file contents and stills for campaigns that ended more than `keep_files_months`
                     ago, and message text and attachments older than `keep_messages_months`, are removed. The
                     bookings, dates, check results and the activity log stay, so the history still adds up.
                     Files in the client's Google Drive are theirs and aren't touched.
    delete_client()  the owner removes everything about one client, on their request: bookings, files, proof,
                     messages, proposals, portal logins and calendar events. Only a note that it happened is kept.
"""

from datetime import timedelta

from sqlalchemy import delete, func, or_, select, update

from . import settings, sync
from .activity import log
from .bookings import actor_of
from .jobs import enqueue
from .models import (Activity, AiCall, Attachment, Booking, BookingDocument, BookingLine, Creative,
                     CreativeFrame, EvalCase, LoginLink, Message, Notification, Proof, Proposal, User, utcnow)
from .permissions import require


class RetentionError(Exception):
    pass


def _months(n):
    return timedelta(days=int(n) * 30)


def purge(session, today):
    files_before = today - _months(settings.get(session, "keep_files_months") or 12)
    msgs_before = utcnow() - _months(settings.get(session, "keep_messages_months") or 24)
    old = [b.id for b in session.scalars(select(Booking).where(Booking.status.in_(("completed", "cancelled",
                                                                                   "expired"))))
           if b.end_date and b.end_date < files_before]
    counts = {"files": 0, "stills": 0, "proof": 0, "messages": 0, "attachments": 0}
    if old:
        ids = [c for (c,) in session.execute(select(Creative.id).where(Creative.booking_id.in_(old)))]
        if ids:
            counts["stills"] = session.execute(delete(CreativeFrame).where(CreativeFrame.creative_id.in_(ids))).rowcount
            counts["files"] = session.execute(update(Creative).where(Creative.id.in_(ids), Creative.data.is_not(None))
                                              .values(data=None)).rowcount
        counts["proof"] = session.execute(update(Proof).where(Proof.booking_id.in_(old), Proof.data.is_not(None))
                                          .values(data=None)).rowcount
    counts["attachments"] = session.execute(
        update(Attachment).where(Attachment.data.is_not(None),
                                 Attachment.message_id.in_(select(Message.id).where(Message.created_at < msgs_before)))
        .values(data=None)).rowcount
    counts["messages"] = session.execute(update(Message).where(Message.created_at < msgs_before, Message.body != "")
                                         .values(body="")).rowcount
    if any(counts.values()):
        log(session, "agent", "purged", "settings", "retention",
            "Old data removed under the data rules: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v))
    return counts


def delete_client(session, user, client, typed_name):
    """Everything about one client, gone. The owner types the client's name to confirm."""
    require(user, "people")                         # the owner only
    if (typed_name or "").strip().lower() != client.name.strip().lower():
        raise RetentionError("Type the client's name exactly to confirm.")
    bookings = session.scalars(select(Booking).where(Booking.client_id == client.id)).all()
    bids = [b.id for b in bookings]
    codes = [b.code for b in bookings]
    proposals = [p.id for p in session.scalars(select(Proposal).where(
        or_(Proposal.client_id == client.id, Proposal.booking_id.in_(bids) if bids else False)))]
    refs = [r for (r,) in session.execute(select(Proposal.ref).where(Proposal.id.in_(proposals)))] if proposals else []
    # Calendar events go first, while the lines still say where they are.
    for l in session.scalars(select(BookingLine).where(BookingLine.booking_id.in_(bids))) if bids else []:
        if l.calendar_event_id and l.screen.calendar_id:
            enqueue(session, "calendar_delete", {"calendar_id": l.screen.calendar_id, "event_id": l.calendar_event_id})
    creative_ids = [c for (c,) in session.execute(select(Creative.id).where(Creative.booking_id.in_(bids)))] \
        if bids else []
    msg_ids = [m for (m,) in session.execute(select(Message.id).where(
        or_(Message.booking_id.in_(bids) if bids else False, Message.proposal_id.in_(proposals) if proposals else False)))]
    session.execute(update(AiCall).where(or_(AiCall.client_id == client.id,
                                             AiCall.booking_id.in_(bids) if bids else False,
                                             AiCall.proposal_id.in_(proposals) if proposals else False,
                                             AiCall.creative_id.in_(creative_ids) if creative_ids else False))
                    .values(client_id=None, booking_id=None, proposal_id=None, creative_id=None))
    if creative_ids:
        session.execute(delete(CreativeFrame).where(CreativeFrame.creative_id.in_(creative_ids)))
        session.execute(delete(Creative).where(Creative.id.in_(creative_ids)))
    if bids:
        session.execute(delete(Proof).where(Proof.booking_id.in_(bids)))
        session.execute(delete(BookingDocument).where(BookingDocument.booking_id.in_(bids)))
    if msg_ids:
        session.execute(update(Creative).where(Creative.message_id.in_(msg_ids)).values(message_id=None))
        session.execute(delete(Attachment).where(Attachment.message_id.in_(msg_ids)))
        session.execute(delete(Message).where(Message.id.in_(msg_ids)))
    if proposals:
        session.execute(update(Proposal).where(Proposal.duplicate_of_id.in_(proposals)).values(duplicate_of_id=None))
        session.execute(update(EvalCase).where(EvalCase.source_proposal_id.in_(proposals))
                        .values(source_proposal_id=None))
        session.execute(delete(Proposal).where(Proposal.id.in_(proposals)))
    if bids:
        session.execute(delete(BookingLine).where(BookingLine.booking_id.in_(bids)))
        session.execute(delete(Booking).where(Booking.id.in_(bids)))
    users = [u for (u,) in session.execute(select(User.id).where(User.client_id == client.id))]
    if users:
        emails = [e for (e,) in session.execute(select(User.email).where(User.id.in_(users)))]
        session.execute(delete(LoginLink).where(LoginLink.user_id.in_(users)))
        session.execute(delete(User).where(User.id.in_(users)))
    else:
        emails = []
    keys = [str(client.id), client.name] + codes + refs + emails
    session.execute(delete(Activity).where(or_(Activity.entity_id.in_(keys),
                                               Activity.summary.contains(client.name))))
    for code in codes + refs:
        session.execute(delete(Notification).where(Notification.text.contains(code)))
    session.execute(delete(Notification).where(Notification.text.contains(client.name)))
    n = len(bids)
    session.delete(client)
    session.flush()
    log(session, actor_of(user), "deleted_client", "client", "", f"All data for a client deleted on their request "
                                                                    f"({n} booking(s), {len(proposals)} request(s))")
    sync.queue_sheet_mirror(session)
    return {"bookings": n, "proposals": len(proposals), "files": len(creative_ids), "messages": len(msg_ids),
            "logins": len(users)}


def counts_for(session, client):
    bids = [b for (b,) in session.execute(select(Booking.id).where(Booking.client_id == client.id))]
    return {"bookings": len(bids),
            "files": session.scalar(select(func.count()).select_from(Creative).where(Creative.booking_id.in_(bids)))
            if bids else 0,
            "requests": session.scalar(select(func.count()).select_from(Proposal)
                                       .where(Proposal.client_id == client.id)) or 0}
