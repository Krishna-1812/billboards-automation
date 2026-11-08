"""Watching the app's connections (Phase 5): Drive, Sheets and Calendar, email in and out, WhatsApp and Claude.

Every background job reports how its connection did. When one keeps failing for 30 minutes, the team is told in
the app and by email (at most every 6 hours per connection). `/health/full` reports all of it, plus the worker's
heartbeat, for an outside uptime check: it answers 503 when the worker has stopped or a connection is down.
"""

from datetime import datetime, timedelta

from . import runtime, settings
from .models import utcnow

# job kind -> connection
CONNECTION = {
    "send_email": "email_out", "poll_email": "email_in", "send_message": "messages_out",
    "calendar_line": "calendar", "calendar_delete": "calendar",
    "sheet_mirror": "sheets", "sheet_watch_check": "sheets",
    "poll_drive": "drive", "ready_to_load": "drive", "creative_request": "drive",
    "read_proposal": "claude", "check_creative": "claude", "draft_plan": "claude",
    "backup": "backups",
}
LABELS = {"email_out": "Sending email", "email_in": "Reading the proposals mailbox", "messages_out":
          "Sending client messages", "calendar": "Google Calendar", "sheets": "Google Sheets", "drive": "Google Drive",
          "claude": "Claude", "backups": "Nightly backup"}
FAILING_FOR = timedelta(minutes=30)
ALERT_EVERY = timedelta(hours=6)
WORKER_LATE = timedelta(minutes=5)
KEY = "connection_status"


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _dt(v):
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def record(session, kind, ok, error=""):
    name = CONNECTION.get(kind)
    if name is None:
        return
    status = dict(settings.get(session, KEY) or {})
    row = dict(status.get(name) or {})
    now = _iso(utcnow())
    if ok:
        row.update(ok_at=now, failing_since=None, error="")
    else:
        row.update(error_at=now, error=(error or "").splitlines()[0][:300] if error else "failed")
        row.setdefault("failing_since", None)
        if not row.get("failing_since"):
            row["failing_since"] = now
    status[name] = row
    settings.put(session, KEY, status)


def status(session, now=None):
    """[(name, label, state, detail)] where state is ok | failing | down | unknown."""
    now = now or utcnow()
    out = []
    for name, row in sorted((settings.get(session, KEY) or {}).items()):
        since = _dt(row.get("failing_since"))
        if since is None:
            state = "ok" if row.get("ok_at") else "unknown"
        else:
            state = "down" if now - since >= FAILING_FOR else "failing"
        detail = row.get("error", "") if since else (f"last worked {row.get('ok_at', '')[:16].replace('T', ' ')} UTC"
                                                     if row.get("ok_at") else "")
        out.append((name, LABELS.get(name, name), state, detail))
    return out


def worker_age(session, now=None):
    seen = _dt(settings.get(session, "worker_heartbeat"))
    return None if seen is None else (now or utcnow()) - seen


def check(session, now=None):
    """Once a minute: tell the team about connections that have been down for 30 minutes."""
    from .notices import alert
    now = now or utcnow()
    sent = dict(settings.get(session, "connection_alerted") or {})
    told = []
    for name, label, state, detail in status(session, now):
        last = _dt(sent.get(name))
        if state == "down" and (last is None or now - last >= ALERT_EVERY):
            sent[name] = _iso(now)
            text = f"{label} has been failing for over 30 minutes: {detail}"
            alert(session, "system", text, link="/settings/connections", kind="error",
                  subject=f"Billboard Ops: {label} is down")
            cfg = runtime.config
            if cfg is not None and cfg.ALERT_EMAILS:
                from .mail import queue
                queue(session, cfg.ALERT_EMAILS, f"Billboard Ops: {label} is down", text)
            told.append(name)
        elif state == "ok" and name in sent:
            sent.pop(name)
    settings.put(session, "connection_alerted", sent)
    return told
