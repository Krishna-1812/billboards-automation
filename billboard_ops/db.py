"""Database engine and sessions.

One engine per process. Web requests get a session from `get_session()`, closed at the end of the request;
the worker opens its own with `session_scope()`.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def init_engine(url):
    global _engine, _Session
    kwargs = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=5)
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _fk_on(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def engine():
    return _engine


def new_session():
    return _Session()


def is_postgres():
    return _engine is not None and _engine.dialect.name == "postgresql"


@contextmanager
def session_scope():
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
