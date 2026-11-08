"""Where creative files are kept: the booking's Google Drive folder when Drive is set up, otherwise the database.

Drive is the right home (videos are large, and the team and client can open the folder). The database is the
fallback so the app works before Drive is connected; it takes files up to 150 MB.
"""

import os
import tempfile

from . import runtime, settings
from .google_client import NotConnected, get_client

DB_LIMIT = 150 * 1024 * 1024


class StoreError(Exception):
    pass


def drive(session):
    """The Google client, if Drive is set up (a service account and a Shared Drive folder), else None."""
    if not settings.get(session, "drive_parent_folder_id"):
        return None
    try:
        return get_client(runtime.config)
    except NotConnected:
        return None


def save(session, booking, creative, path=None, data=None):
    """Keep the file: upload it to the booking's Drive folder, or hold it in the database."""
    size = os.path.getsize(path) if path else len(data or b"")
    creative.size = size
    g = drive(session) if booking.drive_folder_id else None
    if g is not None:
        tmp = None
        if path is None:
            fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(creative.filename)[1])
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            path = tmp
        try:
            creative.drive_file_id = g.drive_upload(booking.drive_folder_id, creative.filename, path,
                                                    creative.content_type)
        finally:
            if tmp:
                os.remove(tmp)
        return
    if size > DB_LIMIT:
        raise StoreError(f"The file is {size // 1_000_000} MB. Without Google Drive set up the app keeps files up to "
                         f"{DB_LIMIT // 1_000_000} MB; set up Drive for larger files.")
    if path is not None:
        with open(path, "rb") as fh:
            data = fh.read()
    creative.data = data


def materialise(session, creative, work_dir):
    """A local copy of the file to check. The caller deletes `work_dir`."""
    path = os.path.join(work_dir, "file" + (os.path.splitext(creative.filename)[1] or ""))
    if creative.data is not None:
        with open(path, "wb") as fh:
            fh.write(creative.data)
        return path
    if creative.drive_file_id:
        get_client(runtime.config).drive_download(creative.drive_file_id, path)
        return path
    raise StoreError("The file isn't stored anywhere.")


CACHE = os.path.join(tempfile.gettempdir(), "billboard-ops-preview")
CACHE_LIMIT = 2 * 1024 * 1024 * 1024


def _prune(limit=CACHE_LIMIT):
    """Keep the preview cache under its limit by removing the copies watched longest ago."""
    files = []
    for name in os.listdir(CACHE):
        p = os.path.join(CACHE, name)
        if os.path.isfile(p) and not name.endswith(".part"):
            st = os.stat(p)
            files.append((st.st_atime, st.st_size, p))
    total = sum(f[1] for f in files)
    for _, size, p in sorted(files):
        if total <= limit:
            break
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass


def preview_path(session, creative):
    """A local copy for the preview player (kept in a small cache, since videos are watched more than once)."""
    os.makedirs(CACHE, exist_ok=True)
    # Keyed by more than the id, so a copy cached for another file can never be shown in its place.
    stamp = int(creative.received_at.timestamp()) if creative.received_at else 0
    kind = type(creative).__name__.lower()          # creatives and proof of play share the cache
    key = f"{kind}-{creative.id}-{creative.size}-{stamp}-{creative.drive_file_id or 'db'}"
    path = os.path.join(CACHE, f"{key}{os.path.splitext(creative.filename)[1]}")
    if not os.path.exists(path):
        tmp = path + ".part"
        if creative.data is not None:
            with open(tmp, "wb") as fh:
                fh.write(creative.data)
        else:
            get_client(runtime.config).drive_download(creative.drive_file_id, tmp)
        os.replace(tmp, path)
        _prune()
    return path
