"""Launch setup: the stored secret key, sign-in that can't lock everyone out, and the worker inside the web service."""

import importlib.util
from pathlib import Path

from billboard_ops import create_app, db, derived_secret, has_real_owner
from billboard_ops.models import User

from conftest import _config


def test_the_secret_comes_from_the_database_password_alone():
    a = derived_secret("postgresql://u:pw1@db.internal:5432/railway")
    assert len(a) == 64 and a == derived_secret("postgresql://u:pw1@db.internal:5432/railway")
    assert a != derived_secret("postgresql://u:pw2@db.internal:5432/railway")
    assert "pw1" not in a


def test_a_real_owner_is_needed_before_sign_in_switches_on(tmp_path):
    cfg = _config(TESTING=False, OWNER_EMAILS=[], DATABASE_URL=f"sqlite:///{tmp_path}/a.db")
    app = create_app(cfg)
    assert cfg.LOGIN_BLOCKED and not cfg.LOGIN_REQUIRED
    page = app.test_client().get("/").get_data(as_text=True)
    assert "Sign-in can't switch on yet" in page and "OWNER_EMAILS" in page
    db.engine().dispose()


def test_an_owner_added_in_people_is_enough(tmp_path):
    cfg = _config(TESTING=False, OWNER_EMAILS=[], DATABASE_URL=f"sqlite:///{tmp_path}/b.db")
    create_app(_config(TESTING=False, OWNER_EMAILS=["boss@client.example"], DATABASE_URL=cfg.DATABASE_URL))
    with db.session_scope() as s:
        s.add(User(email="boss@client.example", role="owner", name="Boss"))
        s.add(User(email="owner@billboards.local", role="owner", name="Owner"))   # the stand-in doesn't count
    assert has_real_owner(cfg)
    create_app(cfg)
    assert cfg.LOGIN_REQUIRED and not cfg.LOGIN_BLOCKED
    db.engine().dispose()


def test_only_the_stand_in_owner_is_not_enough(tmp_path):
    cfg = _config(TESTING=False, OWNER_EMAILS=[], DATABASE_URL=f"sqlite:///{tmp_path}/c.db")
    create_app(_config(TESTING=False, DATABASE_URL=cfg.DATABASE_URL))
    with db.session_scope() as s:
        s.add(User(email="owner@billboards.local", role="owner", name="Owner"))
    assert not has_real_owner(cfg)
    db.engine().dispose()


def _gunicorn_conf():
    path = Path(__file__).resolve().parent.parent / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_conf", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_web_service_runs_the_worker_unless_told_not_to():
    conf = _gunicorn_conf()
    assert conf.worker_wanted({})
    assert conf.worker_wanted({"WORKER_IN_WEB": "1"})
    for off in ("0", "false", "no", "OFF"):
        assert not conf.worker_wanted({"WORKER_IN_WEB": off})
