"""The activity log: who did what, and when, for every record."""

from datetime import date, datetime

from .models import Activity, Notification


def _plain(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def diff(before, after):
    """{field: [old, new]} for the fields that changed."""
    out = {}
    for k in set(before) | set(after):
        a, b = _plain(before.get(k)), _plain(after.get(k))
        if a != b:
            out[k] = [a, b]
    return out


def snapshot(obj, fields):
    return {f: _plain(getattr(obj, f, None)) for f in fields}


def log(session, actor, action, entity_type, entity_id="", summary="", changes=None):
    session.add(Activity(actor=actor or "system", action=action, entity_type=entity_type,
                         entity_id=str(entity_id or ""), summary=summary[:500], changes=changes or None))


def notify(session, text, kind="info", link="", user_email=""):
    """An in-app notice. An empty user_email shows it to the whole team."""
    session.add(Notification(user_email=user_email, kind=kind, text=text[:500], link=link))
