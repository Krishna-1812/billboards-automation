"""Which steps the agent may take on its own (Settings → Automation).

Every step starts as "ask a person first". The owner switches a step to automatic once the numbers show it can be
trusted (the accuracy tests, and how often people changed the agent's work). Whatever the agent does on its own is
still written to the activity log, under "agent".
"""

from . import settings
from .activity import log

STEPS = [
    ("auto_hold", "Holds",
     "Place a hold when a request is clear, the client is known and every screen is free. Only while the "
     "proposal accuracy test passes."),
    ("auto_send_client", "Client messages",
     "Send routine messages to clients without waiting: asking for files, reminders, the play plan to approve, "
     "the final preview, going live, renewal offers and the wrap-up."),
    ("auto_send_technical", "Technical feedback",
     "Send feedback on measured faults (size, length, format, sound, black frames) as soon as a file is checked."),
    ("auto_send_content", "Content feedback",
     "Send feedback that includes Claude's content findings (language, Arabic, spelling, brand, safe area)."),
    ("auto_approve_passed", "Final approval",
     "Approve a file that passes every check with nothing for a person to look at."),
    ("auto_confirm_plan", "Play plans",
     "Confirm a play plan the agent drafted with high confidence, when it fits every loop."),
]
KEYS = [k for k, _, _ in STEPS]


def on(session, key):
    return bool(settings.get(session, key))


def send_if_automatic(session, msg, key, what=""):
    """Send a draft at once when its step is automatic and the client can be reached. Returns True if sent."""
    if msg is None or not on(session, key) or not msg.recipient or msg.channel == "manual":
        return False
    from . import outbox
    from .intake import AGENT
    outbox.approve(session, AGENT, msg)
    log(session, "agent", "sent_automatically", "message", msg.id,
        f"Sent {what or msg.kind.replace('_', ' ')} to {msg.recipient} automatically")
    return True
