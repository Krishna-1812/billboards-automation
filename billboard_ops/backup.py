"""Backups (Phase 5): every record as one compressed JSON file, every night, into the client's Shared Drive.

This is the portable copy, on top of Railway's own Postgres backups (which hold everything, files included).
File contents kept in the database (videos, images, attachments, stills) are left out to keep it small; files in
Google Drive stay in Drive. Everything else is in it: screens, specs, clients, bookings, plans, proposals, messages,
check results, the activity log and settings.

    python -m billboard_ops.backup export backup.json.gz       write a backup
    python -m billboard_ops.backup restore backup.json.gz      load one into an EMPTY database
"""

import base64
import gzip
import io
import json
import os
import sys
import tempfile
from datetime import date, datetime

from sqlalchemy import Date, DateTime, LargeBinary, func, select, text

from . import db, settings
from .activity import log
from .jobs import Skip, handler
from .models import Base, Screen, utcnow

FORMAT = 1
KEEP = 30                         # backups kept in Drive


class BackupError(Exception):
    pass


def _value(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bytes):
        return {"b64": base64.b64encode(v).decode()}
    return v


def _revision(session):
    """The migration the database is at ("" for a database made without migrations). Never rolls back the
    caller's work."""
    from sqlalchemy import inspect
    if not inspect(session.connection()).has_table("alembic_version"):
        return ""
    return session.execute(text("SELECT version_num FROM alembic_version")).scalar() or ""


def export(session, include_files=False):
    """Every table as {"columns": [...], "rows": [[...]]}. Large file contents are left out unless asked for."""
    out = {"format": FORMAT, "app": "billboard-ops", "created_at": utcnow().isoformat(),
           "revision": _revision(session), "tables": {}, "files_included": include_files}
    for table in Base.metadata.sorted_tables:
        cols = [c for c in table.columns]
        rows = []
        for row in session.execute(select(table)).all():
            vals = []
            for c, v in zip(cols, row):
                if isinstance(c.type, LargeBinary) and not include_files:
                    v = None
                vals.append(_value(v))
            rows.append(vals)
        out["tables"][table.name] = {"columns": [c.name for c in cols], "rows": rows}
    return out


def dumps(data):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(json.dumps(data, separators=(",", ":")).encode())
    return buf.getvalue()


def loads(raw):
    try:
        data = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    except (OSError, ValueError) as e:
        raise BackupError(f"That isn't a backup file: {e}") from e
    if data.get("app") != "billboard-ops" or data.get("format") != FORMAT:
        raise BackupError("That isn't a Billboard Ops backup.")
    return data


def _parse(col, v):
    if v is None:
        return None
    if isinstance(v, dict) and "b64" in v:
        return base64.b64decode(v["b64"])
    if isinstance(col.type, DateTime):
        return datetime.fromisoformat(v)
    if isinstance(col.type, Date):
        return date.fromisoformat(v)
    return v


def restore(session, data):
    """Load a backup into an empty database (no screens, bookings or clients yet). Returns rows per table."""
    if session.scalar(select(func.count()).select_from(Screen)):
        raise BackupError("This database already has screens. Restore only into an empty database.")
    counts = {}
    for table in Base.metadata.sorted_tables:
        part = data["tables"].get(table.name)
        if not part:
            continue
        cols = {c.name: c for c in table.columns}
        rows = [{n: _parse(cols[n], v) for n, v in zip(part["columns"], r) if n in cols} for r in part["rows"]]
        session.execute(table.delete())             # tables seeded on start (none today) start clean
        if rows:
            session.execute(table.insert(), rows)
        counts[table.name] = len(rows)
    if db.is_postgres():
        for table in Base.metadata.sorted_tables:
            if "id" in table.columns and table.columns["id"].autoincrement in (True, "auto"):
                session.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"))
    return counts


# ── Nightly, into Drive ─────────────────────────────────────────────────────────

@handler("backup")
def nightly(session, payload=None):
    from . import creative_store
    g = creative_store.drive(session)
    if g is None:
        settings.put(session, "last_backup", {"at": utcnow().isoformat(), "ok": False,
                                              "error": "Google Drive isn't set up, so there's nowhere to keep it."})
        raise Skip("Backups go to the Shared Drive; set up Drive (Settings → General → Creative).")
    data = export(session)
    raw = dumps(data)
    name = f"billboard-ops-{date.today():%Y-%m-%d}.json.gz"
    folder = g.drive_folder("Backups", settings.get(session, "drive_parent_folder_id"))
    fd, path = tempfile.mkstemp(suffix=".json.gz")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        fid = g.drive_upload(folder, name, path, "application/gzip")
    finally:
        os.remove(path)
    files = sorted((f for f in g.drive_list(folder) if f.get("name", "").startswith("billboard-ops-")),
                   key=lambda f: f.get("name", ""))
    for old in files[:-KEEP]:
        try:
            g.drive_delete(old["id"])
        except Exception:  # noqa: BLE001 - an old copy left behind is harmless
            pass
            pass
    rows = sum(len(t["rows"]) for t in data["tables"].values())
    settings.put(session, "last_backup", {"at": utcnow().isoformat(), "ok": True, "file": name, "drive_id": fid,
                                          "rows": rows, "bytes": len(raw)})
    log(session, "agent", "backup", "settings", "backup", f"Backup {name}: {rows:,} records, {len(raw) // 1024:,} KB")
    return fid


def maybe_queue(session, local_now):
    """Once a night, from 02:00 local time."""
    from .jobs import enqueue
    day = local_now.date().isoformat()
    if local_now.hour < 2 or settings.get(session, "backup_queued_on") == day:
        return False
    settings.put(session, "backup_queued_on", day)
    enqueue(session, "backup", {}, dedupe_key="backup")
    return True


def main(argv=None):
    from .config import Config
    from . import runtime
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2 or argv[0] not in ("export", "restore"):
        print(__doc__)
        return 2
    cfg = Config()
    runtime.init(cfg)
    db.init_engine(cfg.DATABASE_URL)
    if cfg.DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(db.engine())
    cmd, path = argv
    with db.session_scope() as s:
        if cmd == "export":
            raw = dumps(export(s, include_files=os.environ.get("BACKUP_FILES") == "1"))
            with open(path, "wb") as fh:
                fh.write(raw)
            print(f"Wrote {path} ({len(raw) // 1024:,} KB).")
        else:
            with open(path, "rb") as fh:
                counts = restore(s, loads(fh.read()))
            settings.put(s, "restore_tested", {"at": utcnow().isoformat(), "file": os.path.basename(path),
                                               "rows": sum(counts.values())})
            print(f"Restored {sum(counts.values()):,} records into {len(counts)} tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
