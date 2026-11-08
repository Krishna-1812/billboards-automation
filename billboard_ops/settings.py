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
    "auto_hold": False,                   # the agent places holds on its own (only while the accuracy test passes)
    "remind_clients": True,               # draft a reminder to the client before their hold expires
    # Phase 3: creative
    "creative_lead_days": 5,              # working days between the files' deadline and the start date
    "weekend_days": "5,6",                # days off (Mon=0 … Sun=6) when counting working days
    "drive_parent_folder_id": "",         # the Shared Drive folder the booking folders go in
    "black_max_s": 0.5,                   # black at the start or end longer than this fails
    "freeze_max_s": 5.0,                  # a still stretch longer than this is flagged
    "content_rules": "",                  # one rule per line, checked by Claude
    "auto_send_technical": False,         # send feedback on measured faults without waiting for a person
    # Phase 4: play plans, going live, the portal and automation
    "dayparts": ("Morning peak = 6-10 | ذروة الصباح\nDaytime = 10-16 | النهار\nEvening peak = 16-22 | ذروة المساء\n"
                 "Night = 22-6 | الليل"),
    "client_approves_plan": False,        # the client approves the play plan in the portal
    "client_approves_final": False,       # the client approves the final preview of their ad in the portal
    "renewal_days": 7,                    # days before the end date to offer a renewal
    "load_filename": "{screen}_{booking}_{start}_{end}_v{version}{ext}",   # names in the Ready to load folder
    "auto_send_client": False,            # routine client messages (file requests, reminders, live, renewal...)
    "auto_send_content": False,           # feedback that includes Claude's content findings
    "auto_approve_passed": False,         # approve a file that passes every check with nothing to look at
    "auto_confirm_plan": False,           # confirm a play plan the agent drafted with confidence, when it fits
    # Phase 5
    "weekly_summary": True,               # the Monday email to the owner
    "weekly_summary_to": "",              # more addresses for it, comma separated
    "keep_files_months": 12,              # files and proof kept this long after a campaign ends
    "keep_messages_months": 24,           # message text and attachments kept this long
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
    session.flush()        # so a later read in the same step sees it (the sessions don't autoflush)


def all_settings(session):
    return {k: get(session, k) for k in DEFAULTS}
