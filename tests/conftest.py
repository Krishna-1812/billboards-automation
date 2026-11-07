"""Shared fixtures. Each test gets a fresh database: SQLite by default, or Postgres when TEST_DATABASE_URL is set
(CI runs both). Google is replaced by an in-memory fake; nothing leaves the machine."""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from billboard_ops import create_app, db, google_client, mail  # noqa: E402
from billboard_ops.config import Config  # noqa: E402
from billboard_ops.models import Base, Client, Screen, Spec, User  # noqa: E402


class FakeGoogle:
    """Sheets and Calendar in memory."""

    def __init__(self):
        self.sheets = {}        # (spreadsheet_id, tab) -> rows
        self.events = {}        # calendar_id -> {event_id: body}
        self.calls = []
        self._n = 0

    def sheet_tabs(self, sid):
        return [tab for (s, tab) in self.sheets if s == sid]

    def read_values(self, sid, tab):
        self.calls.append(("read", sid, tab))
        if (sid, tab) not in self.sheets:
            raise Exception("400 Unable to parse range")
        return [list(r) for r in self.sheets[(sid, tab)]]

    def write_values(self, sid, tab, rows):
        self.calls.append(("write", sid, tab))
        self.sheets[(sid, tab)] = [list(r) for r in rows]

    def list_events(self, cal, tmin, tmax):
        return [dict(v, id=k) for k, v in self.events.get(cal, {}).items()]

    def upsert_event(self, cal, event_id, body):
        self.calls.append(("upsert", cal, event_id))
        cal_events = self.events.setdefault(cal, {})
        if not event_id or event_id not in cal_events:
            self._n += 1
            event_id = f"ev{self._n}"
        cal_events[event_id] = body
        return event_id

    def delete_event(self, cal, event_id):
        self.calls.append(("delete", cal, event_id))
        self.events.get(cal, {}).pop(event_id, None)


def _config(**over):
    os.environ.pop("GOOGLE_CLIENT_ID", None)
    cfg = Config()
    cfg.TESTING = True
    cfg.SECRET_KEY = "test"
    cfg.DATABASE_URL = os.environ.get("TEST_DATABASE_URL_RESOLVED") or "sqlite://"
    cfg.GOOGLE_CLIENT_ID = ""
    cfg.DEV_LOGIN = True
    cfg.OWNER_EMAILS = ["owner@example.com"]
    cfg.SMTP_HOST = ""
    cfg.ALERT_EMAILS = []
    cfg.APP_URL = "https://ops.example.com"
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture
def app(tmp_path):
    test_pg = os.environ.get("TEST_DATABASE_URL")
    if test_pg:
        from billboard_ops.config import database_url
        os.environ["DATABASE_URL"] = test_pg
        os.environ["TEST_DATABASE_URL_RESOLVED"] = database_url()
    else:
        os.environ["TEST_DATABASE_URL_RESOLVED"] = f"sqlite:///{tmp_path}/test.db"
    cfg = _config()
    application = create_app(cfg)
    if test_pg:
        Base.metadata.drop_all(db.engine())
        Base.metadata.create_all(db.engine())
    yield application
    db.engine().dispose()


@pytest.fixture
def google():
    fake = FakeGoogle()
    google_client.set_client(fake)
    yield fake
    google_client.set_client(None)


@pytest.fixture
def s(app):
    sess = db.new_session()
    yield sess
    sess.close()


@pytest.fixture(autouse=True)
def _clear_mail():
    mail._sent.clear()
    yield


@pytest.fixture
def people(s):
    out = {}
    for role in ("owner", "manager", "sales", "ops"):
        u = User(email=f"{role}@example.com", name=role.title(), role=role)
        s.add(u)
        out[role] = u
    s.commit()
    return out


@pytest.fixture
def inventory(s):
    """Two landscape screens in one area sharing a spec, a portrait one elsewhere, and a client."""
    land = Spec(name="Landscape FHD", width_px=1920, height_px=1080, durations_s="10,15")
    port = Spec(name="Portrait", width_px=1080, height_px=1920, durations_s="10")
    s.add_all([land, port])
    a = Screen(code="SZR-01", name="Sheikh Zayed Rd North", area="SZR", spec=land, loop_seconds=120,
               slot_seconds=10, open_hour=6, close_hour=24, calendar_id="cal-szr01")
    b = Screen(code="SZR-02", name="Sheikh Zayed Rd South", area="SZR", spec=land, loop_seconds=120,
               slot_seconds=10, open_hour=6, close_hour=24, calendar_id="cal-szr02")
    c = Screen(code="MAR-01", name="Marina Walk", area="Marina", spec=port, loop_seconds=60, slot_seconds=10,
               open_hour=18, close_hour=2, calendar_id="")
    client = Client(name="Acme Foods", email="ads@acme.example")
    s.add_all([a, b, c, client])
    s.commit()
    return {"a": a, "b": b, "c": c, "client": client, "spec": land}


def signed_in(app, user_id):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = user_id
        sess["csrf"] = "tok"
    return c


@pytest.fixture
def login(app):
    return lambda user: signed_in(app, user.id)


def future(days):
    return date.today() + timedelta(days=days)
