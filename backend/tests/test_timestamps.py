"""Timezone handling: aware-UTC login attempts, the Postgres driver URL, and the timestamptz migration.

Regression for fresh installs: sqlmodel >= 0.0.48 maps every ``datetime`` field to a
timezone-aware column type and rejects naive values, which made POST /auth/login
return 500 when the throttle wrote a naive timestamp.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, select

from backend.api.main import app
from backend.db import normalize_database_url
from backend.models.audit_log_entry import AuditLogEntry
from backend.models.login_attempt import LoginAttempt
from backend.services import login_throttle as lt
from backend.services import users
from backend.tests.fixtures.auth import TEST_JWT_SECRET

client = TestClient(app, raise_server_exceptions=False)
PG_URL = os.environ.get("TEST_DATABASE_URL")
needs_postgres = pytest.mark.skipif(not PG_URL, reason="set TEST_DATABASE_URL to a Postgres *_test database")


# --------------------------------------------------------------------------
# login attempts are timezone-aware UTC
# --------------------------------------------------------------------------


def test_a_failed_login_is_recorded_and_the_endpoint_does_not_500(session):
    users.create_user(session, "tz@example.com", "correct horse battery staple", ["analyst"])

    resp = client.post("/auth/login", json={"email": "tz@example.com", "password": "wrong password here"})

    assert resp.status_code == 401  # this was a 500 on a fresh install
    [row] = session.exec(select(LoginAttempt)).all()
    assert row.ts.tzinfo is not None and row.ts.utcoffset() == timedelta(0)


def test_successful_login_and_throttle_checks_work_with_aware_timestamps(session):
    users.create_user(session, "tz2@example.com", "correct horse battery staple", ["analyst"])
    assert client.post("/auth/login", json={"email": "tz2@example.com", "password": "correct horse battery staple"}).status_code == 200
    for _ in range(lt.PAIR.threshold):
        client.post("/auth/login", json={"email": "tz2@example.com", "password": "wrong password here"})
    assert client.post("/auth/login", json={"email": "tz2@example.com", "password": "correct horse battery staple"}).status_code == 429


def test_utcnow_is_aware_utc():
    now = lt.utcnow()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    assert abs((datetime.now(timezone.utc) - now).total_seconds()) < 5


def test_the_stored_time_is_the_utc_instant_even_if_the_caller_passes_another_zone(session):
    ist = timezone(timedelta(hours=5, minutes=30))
    moment = datetime(2026, 1, 1, 5, 30, tzinfo=ist)  # 00:00 UTC
    lt.record_failure(session, "h" * 64, "10.0.0.1", moment)

    [row] = session.exec(select(LoginAttempt)).all()

    assert lt.as_utc(row.ts) == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def test_a_naive_now_from_a_caller_is_treated_as_utc_not_rejected(session):
    naive = datetime(2026, 1, 1, 12, 0, 0)  # e.g. an old caller or test helper
    lt.record_failure(session, "n" * 64, "10.0.0.2", naive)  # must not raise
    for _ in range(lt.PAIR.threshold - 1):
        lt.record_failure(session, "n" * 64, "10.0.0.2", naive)

    with pytest.raises(lt.ThrottledError):
        lt.check(session, "n" * 64, "10.0.0.2", naive)  # naive `now` works in checks too


@pytest.mark.parametrize(
    "value, expected",
    [
        (datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)),  # naive = UTC
        (datetime(2026, 1, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))), datetime(2026, 1, 1, tzinfo=timezone.utc)),
        (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ],
)
def test_as_utc(value, expected):
    assert lt.as_utc(value) == expected and lt.as_utc(value).tzinfo is not None


# --------------------------------------------------------------------------
# the Postgres driver in DATABASE_URL
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg2://u:p@h:5432/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),  # an explicit driver is respected
        ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
        ("sqlite://", "sqlite://"),
    ],
)
def test_normalize_database_url(url, expected):
    assert normalize_database_url(url) == expected


def test_a_bare_postgres_url_is_left_alone_when_psycopg2_is_missing(monkeypatch):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None if name == "psycopg2" else real(name, *a, **k))
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


def test_the_normalised_url_resolves_to_an_installed_driver():
    if importlib_find("psycopg2") is None:
        pytest.skip("psycopg2 not installed")
    # SQLAlchemy 2.1 would otherwise try psycopg 3 for the bare URL and fail on import.
    engine = create_engine(normalize_database_url("postgresql://u:p@localhost:1/db"))
    assert engine.dialect.driver == "psycopg2"


def importlib_find(name):
    import importlib.util

    return importlib.util.find_spec(name)


# --------------------------------------------------------------------------
# the timestamptz migration (SQLite: a no-op; Postgres: converts every column)
# --------------------------------------------------------------------------


def test_timestamptz_migration_is_a_noop_on_sqlite_and_the_head_is_reachable(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'tz.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "f6a7b8c9d0e1")
    engine = create_engine(url)
    before = [(r[1], r[2]) for r in engine.connect().execute(text("PRAGMA table_info('audit_log_entry')")).all()]

    command.upgrade(cfg, "head")

    with engine.connect() as c:
        from alembic.script import ScriptDirectory

        assert c.execute(text("select version_num from alembic_version")).scalar_one() == ScriptDirectory.from_config(cfg).get_current_head()
        assert [(r[1], r[2]) for r in c.execute(text("PRAGMA table_info('audit_log_entry')")).all()] == before
    command.downgrade(cfg, "f6a7b8c9d0e1")  # and the downgrade is a no-op too


def test_the_migration_lists_every_timestamp_column_in_the_models():
    from backend.migrations.versions import a7b8c9d0e1f2_timestamptz as mig

    expected = set()
    for table in SQLModel.metadata.sorted_tables:
        for col in table.columns:
            # sqlmodel wraps its datetime type in a TypeDecorator; look at what it wraps.
            if isinstance(getattr(col.type, "impl", col.type), sa.DateTime):
                expected.add((table.name, col.name))
    assert set(mig.TIMESTAMP_COLUMNS) == expected  # a new datetime column needs a migration too


@needs_postgres
def test_postgres_models_create_timestamptz_columns(session):
    with session.connection() as conn:
        rows = conn.execute(
            text("select data_type from information_schema.columns where table_schema='public' and data_type like 'timestamp%'")
        ).scalars().all()
    assert rows and set(rows) == {"timestamp with time zone"}


def _pg_url_with_tz(zone: str) -> str:
    return make_url(PG_URL).update_query_dict({"options": f"-c timezone={zone}"}).render_as_string(hide_password=False)


@needs_postgres
def test_postgres_instants_survive_a_non_utc_session_timezone():
    """00:00 UTC written and read back under an Asia/Kolkata session is still 00:00 UTC."""
    engine = create_engine(normalize_database_url(_pg_url_with_tz("Asia/Kolkata")))
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    moment = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    with Session(engine) as s:
        s.add(AuditLogEntry(entity_type="X", action="TZ", actor="t", created_at=moment))
        s.commit()
    with Session(engine) as s:
        assert s.exec(select(AuditLogEntry)).one().created_at == moment
    engine.dispose()


@needs_postgres
def test_postgres_migration_converts_legacy_shifted_values_to_the_right_instant(monkeypatch):
    """Rows written under the old naive columns on an IST server hold 05:30 for 00:00 UTC; after the
    migration they must read back as 00:00 UTC, and the downgrade must restore the stored wall time."""
    url = _pg_url_with_tz("Asia/Kolkata")
    engine = create_engine(normalize_database_url(url))
    SQLModel.metadata.drop_all(engine)
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS alembic_version"))
        for t in ("filingtype", "valuationmethod", "assumptionstatus", "datastatus"):
            c.execute(text(f"DROP TYPE IF EXISTS {t}"))
    monkeypatch.setenv("DATABASE_URL", url)  # alembic/env.py reads this (a URL with % can't go in the ini)
    cfg = Config("backend/alembic.ini")

    try:
        command.upgrade(cfg, "f6a7b8c9d0e1")  # the naive-column schema
        with engine.begin() as c:
            # what the old code did: psycopg2 sends an aware value as timestamptz, which PostgreSQL then
            # converts to the IST session's wall time to fit the naive column
            c.execute(text(
                "INSERT INTO audit_log_entry (entity_type, action, actor, detail, created_at) "
                "VALUES ('X', 'LEGACY', 't', '{}', '2026-01-01T00:00:00+00:00'::timestamptz)"
            ))
            assert c.execute(text("select created_at from audit_log_entry")).scalar_one() == datetime(2026, 1, 1, 5, 30)

        command.upgrade(cfg, "head")
        with engine.connect() as c:
            kinds = c.execute(text("select distinct data_type from information_schema.columns "
                                   "where table_schema='public' and table_name <> 'alembic_version' and data_type like 'timestamp%'")).scalars().all()
            assert kinds == ["timestamp with time zone"]
            assert c.execute(text("select created_at from audit_log_entry")).scalar_one() == datetime(2026, 1, 1, tzinfo=timezone.utc)

        command.downgrade(cfg, "f6a7b8c9d0e1")
        with engine.connect() as c:
            assert c.execute(text("select created_at from audit_log_entry")).scalar_one() == datetime(2026, 1, 1, 5, 30)
        command.upgrade(cfg, "head")  # and up again
    finally:
        command.downgrade(cfg, "base")
        engine.dispose()
