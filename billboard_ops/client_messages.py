"""Messages to clients at each stage after confirmation (Phase 4): the play plan to approve, the final preview,
going live, the renewal offer and the wrap-up.

Each is a draft in the client's language, sent the way they prefer, and waits in the Outbox for a person unless
the owner made client messages automatic (Settings → Automation).
"""

from . import automation, drafts, runtime, settings
from .intake import client_contact


def portal_url(booking=None):
    base = runtime.config.APP_URL if runtime.config else ""
    if not base:
        return ""
    return f"{base}/portal" + (f"/bookings/{booking.code}" if booking is not None else "")


def _draft(session, booking, kind, make):
    who = client_contact(session, booking)
    subject, body = make(who["lang"], who["name"], settings.get(session, "company_name"))
    m = drafts.new_draft(session, kind=kind, channel=who["channel"], recipient=who["to"], subject=subject, body=body,
                         language=who["lang"], proposal=who["proposal"], booking=booking, in_reply_to=who["reply"])
    session.flush()
    automation.send_if_automatic(session, m, "auto_send_client")
    return m


def plan_review(session, booking, rows):
    return _draft(session, booking, "plan_review",
                  lambda lg, name, co: drafts.plan_review(lg, name, co, booking, rows, portal_url(booking)))


def preview_review(session, creative):
    b = creative.booking
    return _draft(session, b, "preview_review",
                  lambda lg, name, co: drafts.preview_review(lg, name, co, b, creative.filename, portal_url(b)))


def live_notice(session, booking):
    return _draft(session, booking, "live_notice",
                  lambda lg, name, co: drafts.live_notice(lg, name, co, booking, portal_url(booking)))


def renewal(session, booking):
    return _draft(session, booking, "renewal", lambda lg, name, co: drafts.renewal(lg, name, co, booking))


def wrap_up(session, booking, has_proof):
    return _draft(session, booking, "wrap_up",
                  lambda lg, name, co: drafts.wrap_up(lg, name, co, booking, has_proof, portal_url(booking)))
