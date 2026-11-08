"""Getting ready to launch (Phase 5): one checklist that checks what it can by itself, the two-week parallel run,
and cut-over day.

Most items are read from the app: is the database Postgres, is the worker running, is sign-in on, did last night's
backup run. A few can only be confirmed by a person (training done, Railway's backups switched on); those are
ticked by the owner, and the tick records who and when.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from . import monitor, settings
from .activity import log
from .bookings import actor_of
from .models import CreativeRun, Screen, SheetChange, SheetWatch, User, utcnow
from .permissions import require

PARALLEL_DAYS = 14

MANUAL = {
    "railway_backups": "Railway's Postgres backups are switched on (Postgres service → Backups)",
    "restore_test": "Restoring a backup was tested",
    "uptime": "An uptime check watches /health/full and emails the owner when it fails",
    "security_review": "The security review (SECURITY.md) was read and its open points accepted",
    "training_team": "The owner, Sales and Ops have had their training session",
    "training_clients": "The test clients were shown the portal",
    "arabic_checked": "A native speaker checked the Arabic in the messages and the portal",
    "sheet_protected": "The old sheet is protected in Google Sheets so nobody edits it by mistake",
}


def _ok(state, label, detail="", fix="", must=True):
    return {"state": state, "label": label, "detail": detail, "fix": fix, "must": must}


def checks(session, cfg, today=None):
    today = today or date.today()
    ticks = settings.get(session, "launch_ticks") or {}

    def manual(key, must=True):
        t = ticks.get(key)
        return _ok("ok" if t else "todo", MANUAL[key],
                   f"Ticked by {t['by']} on {t['at'][:10]}" if t else "", f"tick:{key}", must)

    groups = []
    age = monitor.worker_age(session)
    beat_ok = age is not None and age <= monitor.WORKER_LATE
    groups.append(("The service", [
        _ok("ok" if cfg.DATABASE_URL.startswith("postgresql") else "todo", "The app uses the Postgres database",
            "" if cfg.DATABASE_URL.startswith("postgresql") else "It's on a temporary SQLite file, wiped on every deploy",
            "Railway: add Postgres and set DATABASE_URL on the web service (README → Launch, in order)"),
        _ok("ok" if cfg.SECRET_KEY and cfg.SECRET_KEY not in ("dev-only-not-secret", "test") else "todo",
            "Sign-in cookies are signed with a secret key",
            "Made by the app and kept in the database (a SECRET_KEY variable on Railway would replace it)"
            if getattr(cfg, "SECRET_KEY_STORED", False) else "", "Railway → web service → Variables → SECRET_KEY"),
        _ok("ok" if beat_ok else "todo", "The worker is running",
            "" if beat_ok else ("never seen" if age is None else f"last seen {int(age.total_seconds() // 60)} min ago"),
            "It starts with the web service; check the web service's logs for \"worker started\""),
        _ok("ok" if cfg.LOGIN_REQUIRED and cfg.GOOGLE_CLIENT_ID else "todo", "Sign-in with Google is on",
            "" if cfg.LOGIN_REQUIRED else ("Waiting for OWNER_EMAILS: without it nobody could sign in as an owner"
                                           if getattr(cfg, "LOGIN_BLOCKED", False)
                                           else "Sign-in is switched off: anyone with the link can use the app"),
            "Set GOOGLE_CLIENT_ID (and remove LOGIN_REQUIRED=0)"),
        _ok("ok" if cfg.APP_URL else "todo", "APP_URL is set, for links in emails and calendar events", "",
            "Railway → Variables"),
        _ok("ok" if cfg.SMTP_HOST else "todo", "Email can be sent (SMTP)",
            "" if cfg.SMTP_HOST else "Sign-in links, alerts and the weekly summary aren't going out", "SMTP_* variables"),
        _ok("ok" if cfg.ALERT_EMAILS else "todo", "Error alerts go to someone (ALERT_EMAILS)", "", "Railway → Variables",
            must=False),
    ]))
    down = [label for _, label, state, _ in monitor.status(session) if state == "down"]
    groups.append(("Connections", [
        _ok("ok" if cfg.ANTHROPIC_API_KEY_SET else "todo", "Claude is connected", "", "ANTHROPIC_API_KEY"),
        _ok("ok" if cfg.service_account_info() else "todo", "Google (Sheets, Calendar, Drive) is connected", "",
            "GOOGLE_SERVICE_ACCOUNT_JSON"),
        _ok("ok" if settings.get(session, "drive_parent_folder_id") else "todo", "The Shared Drive folder is set", "",
            "Settings → General → Creative"),
        _ok("ok" if settings.get(session, "mirror_spreadsheet_id") else "todo", "The mirror sheet is set", "",
            "Settings → General"),
        _ok("ok" if cfg.email_in else "todo", "The proposals mailbox is connected", "", "IMAP_* variables", must=False),
        _ok("ok" if cfg.whatsapp else "todo", "WhatsApp is connected", "", "WHATSAPP_* variables", must=False),
        _ok("ok" if not down else "todo", "No connection is down", ", ".join(down), "Settings → Connections"),
    ]))
    screens = session.scalars(select(Screen).where(Screen.active.is_(True))).all()
    no_spec = [sc.code for sc in screens if sc.spec_id is None]
    no_cal = [sc.code for sc in screens if not sc.calendar_id]
    from .evaluate import gate
    g = gate(session)
    run = session.scalars(select(CreativeRun).where(CreativeRun.status == "done")
                          .order_by(CreativeRun.id.desc())).first()
    creative_ok = bool(run and (run.summary or {}).get("passed"))
    groups.append(("The records and the agent", [
        _ok("ok" if screens else "todo", "Every screen is in the app", f"{len(screens)} screens", "Import → screens"),
        _ok("ok" if screens and not no_spec else "todo", "Every screen has a spec", ", ".join(no_spec[:10]), "Screens"),
        _ok("ok" if screens and not no_cal else "todo", "Every screen has its Google Calendar", ", ".join(no_cal[:10]),
            "Screens", must=False),
        _ok("ok" if g["passed"] else "todo", "The proposal accuracy test passes", g["why"], "Accuracy test"),
        _ok("ok" if creative_ok else "todo", "The creative check test passes",
            "" if run else "Not run yet", "Creative → Check test set"),
    ]))
    last = settings.get(session, "last_backup") or {}
    fresh = last.get("ok") and last.get("at") and utcnow() - datetime.fromisoformat(last["at"]) <= timedelta(hours=26)
    restored = settings.get(session, "restore_tested")
    groups.append(("Safety", [
        _ok("ok" if fresh else "todo", "Last night's backup ran", last.get("error", "") if not fresh else last.get("file", ""),
            "Settings → Connections → Backups"),
        _ok("ok", MANUAL["restore_test"], f"{restored['at'][:10]}, {restored['rows']:,} records")
        if restored else manual("restore_test"),
        manual("railway_backups"), manual("uptime"), manual("security_review"),
    ]))
    team = session.scalar(select(func.count()).select_from(User).where(User.role != "client", User.active.is_(True))) or 0
    clients = session.scalar(select(func.count()).select_from(User).where(User.role == "client",
                                                                       User.active.is_(True))) or 0
    groups.append(("People", [
        _ok("ok" if team >= 2 else "todo", "The team is added", f"{team} {'person' if team == 1 else 'people'}", "Settings → People"),
        _ok("ok" if clients else "todo", "Test clients have portal access", f"{clients} client login{'' if clients == 1 else 's'}",
            "A client's page → Client portal"),
        manual("training_team"), manual("training_clients"), manual("arabic_checked"),
    ]))
    started = settings.get(session, "parallel_started_on")
    days = (today - date.fromisoformat(started)).days if started else None
    open_changes = session.scalar(select(func.count()).select_from(SheetChange)
                                  .where(SheetChange.resolved_at.is_(None))) or 0
    cut = settings.get(session, "cutover_at")
    groups.append(("Parallel run and cut-over", [
        _ok("ok" if days is not None and days >= PARALLEL_DAYS else "todo",
            f"The team has worked in the app for {PARALLEL_DAYS} days, with the old sheet still there",
            "Not started" if days is None else f"Day {days} of {PARALLEL_DAYS}", "start_parallel"),
        _ok("ok" if not open_changes else "todo", "Every difference with the old sheet is explained",
            f"{open_changes} edit(s) to the old sheet still to review" if open_changes else "",
            "Settings → Sheet changes"),
        manual("sheet_protected"),
        _ok("ok" if cut else "todo", "Cut-over: the app is the single source of truth",
            f"Done {cut[:10]}" if cut else "", "cutover"),
    ]))
    return groups


def summary(groups):
    items = [c for _, g in groups for c in g]
    must = [c for c in items if c["must"]]
    return {"done": sum(1 for c in items if c["state"] == "ok"), "total": len(items),
            "must_left": [c["label"] for c in must if c["state"] != "ok"]}


def tick(session, user, key, on=True):
    require(user, "settings")
    if key not in MANUAL:
        raise KeyError(key)
    ticks = dict(settings.get(session, "launch_ticks") or {})
    if on:
        ticks[key] = {"by": actor_of(user), "at": utcnow().isoformat()}
    else:
        ticks.pop(key, None)
    settings.put(session, "launch_ticks", ticks)
    log(session, actor_of(user), "launch_tick", "settings", key, f"{'Ticked' if on else 'Unticked'}: {MANUAL[key]}")


def start_parallel(session, user, day=None):
    require(user, "settings")
    day = day or date.today()
    settings.put(session, "parallel_started_on", day.isoformat())
    log(session, actor_of(user), "parallel_run", "settings", "launch", f"Parallel run started on {day:%d %b %Y}")


def cutover(session, user):
    """The app becomes the single source of truth: the old sheets stop being watched (they're read-only now)."""
    require(user, "settings")
    watches = session.scalars(select(SheetWatch).where(SheetWatch.active.is_(True))).all()
    for w in watches:
        w.active = False
    settings.put(session, "cutover_at", utcnow().isoformat())
    log(session, actor_of(user), "cutover", "settings", "launch",
        f"Cut-over: the app is the single source of truth; {len(watches)} old sheet(s) no longer watched")
    return len(watches)
