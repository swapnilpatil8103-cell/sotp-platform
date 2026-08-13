"""Database engine + session management.

Reads ``DATABASE_URL`` from the environment (a Supabase-compatible Postgres
connection string in production). For tests, ``DATABASE_URL`` is overridden
to a SQLite file/in-memory DB by the ``backend/tests`` fixtures, so the test
suite never needs a live Postgres instance.

Exposes:
- ``get_engine()`` — lazily-constructed, process-wide SQLAlchemy engine.
- ``get_session()`` — FastAPI dependency yielding a ``Session``.
- ``create_all()`` — dev/test helper that creates all tables from SQLModel
  metadata directly (bypassing Alembic) — convenient for tests and local
  bootstrapping. Real deployments should use the Alembic migrations in
  ``backend/migrations/`` instead.
"""

from __future__ import annotations

import os
from typing import Iterator, Optional

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.engine import Engine

# Ensure all models are registered on SQLModel.metadata before create_all()/
# Alembic autogenerate runs.
import backend.models  # noqa: F401

_engine: Optional[Engine] = None


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL env var must be set (Postgres/Supabase connection string "
            "for real use, or a sqlite:// URL for tests/dev). See .env.example."
        )
    return url


def get_engine(echo: bool = False) -> Engine:
    """Return the process-wide engine, creating it lazily on first use.

    Cached at module level so repeated calls reuse the same connection pool.
    Call ``reset_engine()`` (tests only) to force a fresh engine after
    changing ``DATABASE_URL``.
    """
    global _engine
    if _engine is None:
        url = _database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, echo=echo, connect_args=connect_args)
    return _engine


def reset_engine() -> None:
    """Dispose of and clear the cached engine. Used by tests when swapping DATABASE_URL."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def create_all() -> None:
    """Create all tables from SQLModel metadata. Dev/test convenience only —
    production schema changes should go through Alembic migrations."""
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a SQLModel Session bound to the process engine."""
    with Session(get_engine()) as session:
        yield session
