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


@pytest.fixture(autouse=True, scope="function")
def _test_database(monkeypatch, tmp_path) -> Iterator[None]:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from backend import db as db_module

    db_module.reset_engine()
    db_module.create_all()

    yield

    db_module.reset_engine()


@pytest.fixture()
def session():
    from backend.db import get_engine
    from sqlmodel import Session

    with Session(get_engine()) as s:
        yield s
