"""The background worker: `python -m billboard_ops.worker`.

Every few seconds it runs queued jobs (calendar and sheet mirroring, emails, sheet checks). Every minute it expires
holds, sends hold reminders and closes finished campaigns. Every 10 minutes it re-reads the old sheets for direct
edits. It writes a heartbeat, which /health and Settings → Connections show.
"""

import logging
import signal
import time
from datetime import date

from sqlalchemy import select

from . import bookings, db, jobs, runtime, settings
from .config import Config
from .models import SheetWatch, utcnow

log = logging.getLogger("billboard_ops.worker")

POLL_SECONDS = 5
TICK_SECONDS = 60
WATCH_SECONDS = 600

_stop = False


def _on_term(*_):
    global _stop
    _stop = True


def tick(now=None, today=None):
    """The once-a-minute work. Safe to run from several workers at once: each change is one transaction."""
    now = now or utcnow()
    with db.session_scope() as s:
        bookings.expire_holds(s, now)
    with db.session_scope() as s:
        bookings.remind_holds(s, now)
    with db.session_scope() as s:
        bookings.complete_finished(s, today or date.today())
    with db.session_scope() as s:
        settings.put(s, "worker_heartbeat", now.isoformat())


def queue_sheet_checks():
    with db.session_scope() as s:
        for w in s.scalars(select(SheetWatch).where(SheetWatch.active.is_(True))):
            jobs.enqueue(s, "sheet_watch_check", {"watch_id": w.id}, dedupe_key=f"watch:{w.id}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config()
    runtime.init(cfg)
    db.init_engine(cfg.DATABASE_URL)
    if cfg.DATABASE_URL.startswith("sqlite"):
        from .models import Base
        Base.metadata.create_all(db.engine())
    from . import mail, sync  # noqa: F401 - registers the job handlers
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    log.info("worker started")
    last_tick = last_watch = 0.0
    while not _stop:
        t = time.monotonic()
        try:
            if t - last_tick >= TICK_SECONDS:
                tick()
                last_tick = t
            if t - last_watch >= WATCH_SECONDS:
                queue_sheet_checks()
                last_watch = t
            ran = jobs.run_due()
        except Exception:  # noqa: BLE001 - keep going; the next loop tries again
            log.exception("worker loop error")
            ran = 0
        if not ran:
            for _ in range(POLL_SECONDS * 10):
                if _stop:
                    break
                time.sleep(0.1)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
