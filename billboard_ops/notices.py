"""Telling the team: notices in the app and by email, by topic. Each person chooses what they get (Phase 4).

`alert()` writes one notice for the team and emails the people who asked for that topic by email. A person who
switched a topic off in the app doesn't see its notices in their list. Notices without a topic (problems with the
app itself) always show.
"""

from sqlalchemy import select

from . import runtime
from .activity import notify
from .models import User

# topic: (label, what it covers, roles that get it by email unless they change it)
TOPICS = {
    "proposals": ("New requests", "Proposals by email, WhatsApp and the client portal", ()),
    "holds":     ("Holds", "Holds placed by the agent and holds about to expire", ()),
    "files":     ("Client files", "Files checked and waiting for a decision", ()),
    "plans":     ("Play plans", "Plans drafted, confirmed, or waiting for the client", ()),
    "readiness": ("Not ready to start", "A booking starts tomorrow or today without files, plan or loading",
                  ("owner", "manager", "ops")),
    "live":      ("Going live and ending", "Campaigns that started, need reloading, or ended", ()),
    "renewals":  ("Renewals", "Campaigns ending soon, to offer a renewal", ("sales",)),
    "client":    ("Client actions", "What clients do in the portal: accepting, approving, asking for changes",
                  ("owner", "manager", "sales")),
    "system":    ("Problems with the app", "A connection (Drive, email, WhatsApp, Claude…) down for 30 minutes, or a "
                  "backup that didn't run", ("owner",)),
}


def pref(user, topic):
    """{"app": bool, "email": bool} for one person and topic."""
    mine = (getattr(user, "notify_prefs", None) or {}).get(topic) or {}
    default_email = getattr(user, "role", "") in TOPICS.get(topic, ("", "", ()))[2]
    return {"app": bool(mine.get("app", True)), "email": bool(mine.get("email", default_email))}


def hidden_topics(user):
    return {t for t in TOPICS if not pref(user, t)["app"]}


def alert(session, topic, text, link="", kind="info", subject=None, email_body=None):
    """A notice for the team on `topic`, and an email to everyone who wants this topic by email."""
    notify(session, text, kind=kind, link=link, topic=topic)
    team = session.scalars(select(User).where(User.active.is_(True), User.role != "client")).all()
    to = [u.email for u in team if "@" in u.email and pref(u, topic)["email"]]
    if to:
        from .mail import queue
        base = runtime.config.APP_URL if runtime.config else ""
        queue(session, to, subject or text[:120], (email_body or text) + (f"\n\nOpen it: {base}{link}" if link else ""))
    return to
