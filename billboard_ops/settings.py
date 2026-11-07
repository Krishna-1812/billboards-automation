"""Settings the owner changes in the app (Settings → General), with their defaults."""

from .models import Setting

DEFAULTS = {
    "hold_days": 3,                       # how long a hold lasts before it expires
    "hold_reminder_hours": 24,            # remind this long before a hold expires
    "date_order": "dmy",                  # how to read 03/04/2026 in imported sheets: dmy or mdy
    "default_timezone": "Asia/Dubai",
    "mirror_spreadsheet_id": "",          # where the app writes its Bookings tab
    "mirror_tab": "Bookings (from app)",
    "company_name": "Billboard Ops",
}


def get(session, key):
    row = session.get(Setting, key)
    if row is None or row.value is None:
        return DEFAULTS.get(key)
    return row.value.get("v", DEFAULTS.get(key))


def put(session, key, value):
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value={"v": value}))
    else:
        row.value = {"v": value}


def all_settings(session):
    return {k: get(session, k) for k in DEFAULTS}
