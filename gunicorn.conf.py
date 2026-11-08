"""Gunicorn reads this file on its own (it sits where gunicorn starts).

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


def worker_wanted(environ=None):
    value = (environ if environ is not None else os.environ).get("WORKER_IN_WEB", "1")
    return value.strip().lower() not in ("0", "false", "no", "off")


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
