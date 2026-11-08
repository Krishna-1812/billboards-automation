"""Gunicorn reads this file on its own (it sits where gunicorn starts).

Before the web processes start, it builds or updates the database tables (`alembic upgrade head`), so a release
never depends on Railway's pre-deploy setting. MIGRATE_ON_START=0 turns this off.

Besides serving the app, the web service also runs the background worker (`python -m billboard_ops.worker`) next to
it, and starts it again if it stops. So one Railway service is enough: no separate worker service to set up.

Set WORKER_IN_WEB=0 on the web service if you do run a separate worker service. Two workers wouldn't do any harm
(jobs are claimed one at a time and the minute-by-minute work is safe to repeat), but one is enough.
"""

import os
import subprocess
import sys
import threading
import time

_proc = None
_stopping = False


_OFF = ("0", "false", "no", "off")


def worker_wanted(environ=None):
    value = (environ if environ is not None else os.environ).get("WORKER_IN_WEB", "1")
    return value.strip().lower() not in _OFF


def migrate_wanted(environ=None):
    env = environ if environ is not None else os.environ
    url = env.get("DATABASE_URL", "")
    return url.startswith(("postgres://", "postgresql")) and env.get("MIGRATE_ON_START", "1").strip().lower() not in _OFF


def on_starting(server):
    if not migrate_wanted():
        return
    here = os.path.dirname(os.path.abspath(globals().get("__file__") or "gunicorn.conf.py"))
    for attempt in range(1, 7):
        if subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=here).returncode == 0:
            server.log.info("The database tables are up to date")
            return
        # The database may not be reachable for the first few seconds of a deploy: wait and try again.
        server.log.warning("Updating the database tables failed (try %s of 6)", attempt)
        time.sleep(5 * attempt)
    raise RuntimeError("Couldn't build or update the database tables; see the errors above.")


def _supervise(server):
    global _proc
    delay = 5
    while not _stopping:
        started = time.monotonic()
        _proc = subprocess.Popen([sys.executable, "-m", "billboard_ops.worker"])
        server.log.info("Background worker started (pid %s)", _proc.pid)
        code = _proc.wait()
        if code == 0:
            time.sleep(2)      # a clean stop usually means the whole service is stopping: give on_exit a moment
        if _stopping:
            break
        # Back off if it keeps failing straight away; reset once it has run for a while.
        delay = 5 if time.monotonic() - started > 300 else min(delay * 2, 300)
        server.log.warning("Background worker stopped (exit code %s); starting it again in %s s", code, delay)
        time.sleep(delay)


def when_ready(server):
    if worker_wanted():
        threading.Thread(target=_supervise, args=(server,), name="worker-supervisor", daemon=True).start()


def on_exit(server):
    global _stopping
    _stopping = True
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            _proc.kill()
