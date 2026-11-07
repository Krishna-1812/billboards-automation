"""A small job queue kept in the database, run by the worker.

The web service only queues work (mirror this booking to Calendar, rewrite the Bookings tab, send an email);
the worker does it, so a slow or broken Google call never slows down or breaks a page. A job that fails is tried
again after 1, 2, 4 and 8 minutes, then marked failed and shown to the team.
"""

import logging
import traceback
from datetime import timedelta

from sqlalchemy import select

from . import db
from .activity import notify
from .models import Job, utcnow

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
HANDLERS = {}


class Skip(Exception):
    """The job can't run and shouldn't be retried, e.g. Google isn't connected yet."""


def handler(kind):
    def wrap(fn):
        HANDLERS[kind] = fn
        return fn
    return wrap


def enqueue(session, kind, payload=None, dedupe_key="", delay_seconds=0):
    """Queue a job. With a dedupe_key, a job with the same key already waiting is reused, so ten quick edits
    to one booking still mean one calendar update."""
    if dedupe_key:
        waiting = session.scalars(select(Job).where(Job.dedupe_key == dedupe_key, Job.status == "queued")).first()
        if waiting is not None:
            waiting.payload = payload or {}
            return waiting
    job = Job(kind=kind, payload=payload or {}, dedupe_key=dedupe_key,
              run_at=utcnow() + timedelta(seconds=delay_seconds))
    session.add(job)
    return job


def _claim(limit):
    with db.session_scope() as s:
        q = (select(Job).where(Job.status == "queued", Job.run_at <= utcnow())
             .order_by(Job.run_at, Job.id).limit(limit))
        if db.is_postgres():
            q = q.with_for_update(skip_locked=True)
        jobs = s.scalars(q).all()
        for j in jobs:
            j.status = "running"
            j.attempts += 1
        return [(j.id, j.kind, dict(j.payload or {}), j.attempts) for j in jobs]


def run_due(limit=20):
    """Run the jobs that are due. Returns how many ran."""
    claimed = _claim(limit)
    for job_id, kind, payload, attempts in claimed:
        status, error = "done", ""
        fn = HANDLERS.get(kind)
        try:
            if fn is None:
                raise Skip(f"no handler for {kind}")
            with db.session_scope() as s:
                fn(s, payload)
        except Skip as e:
            status, error = "skipped", str(e)
        except Exception as e:  # noqa: BLE001 - a job must never take the worker down
            log.exception("job %s (%s) failed", job_id, kind)
            status, error = "failed", f"{e}\n{traceback.format_exc(limit=3)}"
        with db.session_scope() as s:
            j = s.get(Job, job_id)
            j.last_error = error[:4000]
            if status == "failed" and attempts < MAX_ATTEMPTS:
                j.status = "queued"
                j.run_at = utcnow() + timedelta(minutes=2 ** (attempts - 1))
            else:
                j.status = status
                j.finished_at = utcnow()
                if status == "failed":
                    notify(s, f"A background task failed {attempts} times: {kind}. {error.splitlines()[0][:200]}",
                           kind="error", link="/settings/connections")
    return len(claimed)


def recent_problems(session, limit=20):
    return session.scalars(select(Job).where(Job.status.in_(("failed", "skipped")))
                           .order_by(Job.id.desc()).limit(limit)).all()
