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


@pytest.fixture(autouse=True, scope="function")
def _test_database(monkeypatch, tmp_path) -> Iterator[None]:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    # Same dummy value CI uses; keeps tests that build the SEC client independent
    # of the developer's shell. Tests of the missing-variable path delenv it.
    monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")

    from backend import db as db_module

    db_module.reset_engine()
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
