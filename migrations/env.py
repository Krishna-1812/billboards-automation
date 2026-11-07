"""Alembic: the database URL comes from DATABASE_URL, the tables from billboard_ops.models."""

from alembic import context
from sqlalchemy import create_engine, pool

from billboard_ops import models  # noqa: F401 - registers every table
from billboard_ops.config import database_url
from billboard_ops.db import Base

target_metadata = Base.metadata


def run_offline():
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_online():
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
