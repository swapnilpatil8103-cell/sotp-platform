"""Shared pytest fixtures.

Points DATABASE_URL at a throwaway SQLite file for the duration of the test
session so the suite never requires a live Postgres/Supabase instance, then
creates all tables via SQLModel metadata (equivalent to running the Alembic
migrations, without needing alembic installed at test time).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

from backend.tests.fixtures.auth import TEST_JWT_SECRET, seed_test_roster

# Set at import time (not in a fixture) because test modules mint tokens at
# module level, which happens during collection, before any fixture runs.
# Unconditional on purpose: a real AUTH_JWT_SECRET exported from .env must not
# leak into, or be required by, the test suite.
os.environ["AUTH_JWT_SECRET"] = TEST_JWT_SECRET


def _postgres_test_url() -> str | None:
    """Opt-in: run the suite against a real Postgres instead of per-test SQLite.

    Set TEST_DATABASE_URL (NOT DATABASE_URL, which this fixture always
    overrides). Every test drops and recreates ALL tables in that database, so it
    must be a throwaway database whose name ends in "_test"; anything else is
    refused rather than wiped.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return None
    from sqlalchemy.engine import make_url

    name = make_url(url).database or ""
    if not name.endswith("_test"):
        raise RuntimeError(
            f"TEST_DATABASE_URL points at database {name!r}; its name must end in '_test' "
            "because the test suite drops every table in it."
        )
    return url


@pytest.fixture(autouse=True, scope="function")
def _test_database(monkeypatch, tmp_path, request) -> Iterator[None]:
    pg_url = _postgres_test_url()
    if pg_url:
        monkeypatch.setenv("DATABASE_URL", pg_url)
    else:
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    # Same dummy value CI uses; keeps tests that build the SEC client independent
    # of the developer's shell. Tests of the missing-variable path delenv it.
    # Integration tests call the real SEC and need the developer's real value.
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")

    from backend import db as db_module

    db_module.reset_engine()
    if pg_url:
        from sqlmodel import SQLModel

        SQLModel.metadata.drop_all(db_module.get_engine())  # clean slate between tests
    db_module.create_all()
    from sqlmodel import Session

    with Session(db_module.get_engine()) as seed_session:
        seed_test_roster(seed_session)

    yield

    db_module.reset_engine()


@pytest.fixture()
def session():
    from backend.db import get_engine
    from sqlmodel import Session

    with Session(get_engine()) as s:
        yield s
