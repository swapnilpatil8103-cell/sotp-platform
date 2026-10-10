"""The shared, database-backed login throttle (backend/services/login_throttle.py)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend import db as db_module
from backend.api.main import app
from backend.models.app_user import AppUser
from backend.models.login_attempt import LoginAttempt
from backend.services import login_throttle as lt
from backend.services import users
from backend.tests.fixtures.auth import TEST_JWT_SECRET

client = TestClient(app, raise_server_exceptions=False)
PASSWORD = "correct horse battery staple"
EMAIL = "victim@example.com"


def eh(email: str = EMAIL) -> str:
    return lt.email_hash(email, TEST_JWT_SECRET)


def fail(session, n: int, *, email: str = EMAIL, ip: str = "10.0.0.1", now=None):
    for _ in range(n):
        lt.record_failure(session, eh(email), ip, now)


def locked(session, *, email: str = EMAIL, ip: str = "10.0.0.1", now=None) -> int:
    """Seconds of lock remaining, or 0."""
    try:
        lt.check(session, eh(email), ip, now)
    except lt.ThrottledError as exc:
        return exc.retry_after
    return 0


def _login(email: str, password: str, headers=None):
    return client.post("/auth/login", json={"email": email, "password": password}, headers=headers or {})


@pytest.fixture()
def user(session):
    return users.create_user(session, EMAIL, PASSWORD, ["analyst"])


# --------------------------------------------------------------------------
# back-off arithmetic
# --------------------------------------------------------------------------


def test_lock_length_is_zero_below_threshold_then_doubles_and_is_capped():
    s = lt.PAIR
    assert [lt.lock_seconds(s, n) for n in range(0, s.threshold)] == [0] * s.threshold
    seq = [lt.lock_seconds(s, s.threshold + i) for i in range(6)]
    assert seq == [60, 120, 240, 480, 900, 900]  # doubles, then pinned at the 15-minute cap
    assert lt.lock_seconds(s, 10_000) == s.cap_seconds  # no overflow
    assert lt.lock_seconds(lt.EMAIL, 1_000) == 5 * 60  # the email-wide scope is capped lower


# --------------------------------------------------------------------------
# scopes
# --------------------------------------------------------------------------


def test_pair_scope_locks_only_that_email_from_that_ip(session):
    now = lt.utcnow()
    fail(session, lt.PAIR.threshold, ip="6.6.6.6", now=now)

    assert 0 < locked(session, ip="6.6.6.6", now=now) <= 60
    # The victim, from their own address, is not locked out by someone else's guessing.
    assert locked(session, ip="203.0.113.7", now=now) == 0
    # The attacker's address is not locked for a different account (below the IP threshold).
    assert locked(session, email="other@example.com", ip="6.6.6.6", now=now) == 0


def test_below_threshold_is_not_locked(session):
    now = lt.utcnow()
    fail(session, lt.PAIR.threshold - 1, now=now)
    assert locked(session, now=now) == 0


def test_ip_scope_stops_one_address_spraying_many_emails(session):
    now = lt.utcnow()
    for i in range(lt.IP.threshold):
        fail(session, 1, email=f"user{i}@example.com", ip="6.6.6.6", now=now)  # one failure each: no pair lock

    assert locked(session, email="fresh@example.com", ip="6.6.6.6", now=now) > 0
    assert locked(session, email="fresh@example.com", ip="7.7.7.7", now=now) == 0


def test_email_scope_stops_a_distributed_guess_against_one_account(session):
    now = lt.utcnow()
    for i in range(lt.EMAIL.threshold):
        fail(session, 1, ip=f"198.51.100.{i}", now=now)  # one failure per address: no pair/ip lock

    remaining = locked(session, ip="198.51.100.200", now=now)
    assert 0 < remaining <= 60


def test_a_known_good_ip_bypasses_the_email_wide_lock(session):
    """An attacker spreading failures over many addresses cannot lock the real user out of where they sign in."""
    now = lt.utcnow()
    lt.record_success(session, eh(), "203.0.113.7", now - timedelta(days=2))  # the user's usual address
    for i in range(lt.EMAIL.threshold + 5):
        fail(session, 1, ip=f"198.51.100.{i}", now=now)

    assert locked(session, ip="198.51.100.250", now=now) > 0  # a stranger is locked
    assert locked(session, ip="203.0.113.7", now=now) == 0  # the user's own address is not


def test_a_trusted_ip_is_still_subject_to_its_own_pair_lock(session):
    now = lt.utcnow()
    lt.record_success(session, eh(), "203.0.113.7", now - timedelta(days=1))
    fail(session, lt.PAIR.threshold, ip="203.0.113.7", now=now)  # e.g. the user forgot their password
    assert locked(session, ip="203.0.113.7", now=now) > 0


def test_trust_in_an_ip_expires(session):
    now = lt.utcnow()
    lt.record_success(session, eh(), "203.0.113.7", now - timedelta(days=lt.TRUSTED_IP_DAYS + 1))
    for i in range(lt.EMAIL.threshold):
        fail(session, 1, ip=f"198.51.100.{i}", now=now)
    assert locked(session, ip="203.0.113.7", now=now) > 0


def test_back_off_grows_with_each_failure_after_the_lock_expires(session):
    t0 = lt.utcnow()
    fail(session, lt.PAIR.threshold, now=t0)
    assert locked(session, now=t0) == 60

    t1 = t0 + timedelta(seconds=61)  # lock over
    assert locked(session, now=t1) == 0
    fail(session, 1, now=t1)  # one more wrong guess
    assert locked(session, now=t1) == 120  # twice as long

    t2 = t1 + timedelta(seconds=121)
    fail(session, 1, now=t2)
    assert locked(session, now=t2) == 240


def test_the_lock_lifts_by_itself_and_failures_age_out_of_the_window(session):
    t0 = lt.utcnow()
    fail(session, lt.PAIR.threshold, now=t0)
    assert locked(session, now=t0) > 0
    assert locked(session, now=t0 + timedelta(seconds=61)) == 0
    # After the whole window the slate is clean: the next failure starts from 1 again.
    t_late = t0 + lt.WINDOW + timedelta(seconds=1)
    fail(session, 1, now=t_late)
    assert locked(session, now=t_late) == 0


def test_a_success_forgives_that_pair_but_not_other_addresses(session):
    now = lt.utcnow()
    fail(session, 3, ip="10.0.0.1", now=now)
    fail(session, 3, ip="10.0.0.2", now=now)

    lt.record_success(session, eh(), "10.0.0.1", now)

    remaining = session.exec(select(LoginAttempt).where(LoginAttempt.success == False)).all()  # noqa: E712
    assert {a.ip for a in remaining} == {"10.0.0.2"} and len(remaining) == 3


def test_old_rows_are_cleaned_up_on_the_next_write(session):
    now = lt.utcnow()
    fail(session, 2, ip="10.0.0.9", now=now - lt.WINDOW - timedelta(minutes=1))  # stale failures
    lt.record_success(session, eh(), "10.0.0.8", now - timedelta(days=lt.TRUSTED_IP_DAYS + 1))  # stale success
    lt.record_success(session, eh(), "10.0.0.7", now - timedelta(days=3))  # still useful

    fail(session, 1, ip="10.0.0.1", now=now)

    ips = {a.ip for a in session.exec(select(LoginAttempt)).all()}
    assert ips == {"10.0.0.7", "10.0.0.1"}


# --------------------------------------------------------------------------
# persistence / sharing
# --------------------------------------------------------------------------


def test_lock_survives_a_restart_and_is_visible_to_another_worker(session):
    now = lt.utcnow()
    fail(session, lt.PAIR.threshold, now=now)

    db_module.reset_engine()  # a process restart: every in-memory structure is gone
    with Session(db_module.get_engine()) as other_worker:  # a second worker with its own session
        assert locked(other_worker, now=now) > 0


def test_the_table_never_stores_an_email_address(session, user):
    _login(EMAIL, "wrong password here")
    _login("ghost@example.com", "wrong password here")

    rows = session.exec(select(LoginAttempt)).all()
    assert len(rows) == 2  # the unknown email is recorded too, so the table doesn't reveal who exists
    for row in rows:
        assert "@" not in row.email_hash and len(row.email_hash) == 64
        assert row.email_hash != EMAIL


def test_email_hash_is_keyed_and_case_insensitive():
    assert lt.email_hash("A@B.com", "k" * 32) == lt.email_hash(" a@b.COM ", "k" * 32)
    assert lt.email_hash("a@b.com", "k" * 32) != lt.email_hash("a@b.com", "j" * 32)


# --------------------------------------------------------------------------
# through the API
# --------------------------------------------------------------------------


def test_api_locks_after_repeated_failures_even_for_the_right_password(user):
    for _ in range(lt.PAIR.threshold):
        assert _login(EMAIL, "wrong password here").status_code == 401

    resp = _login(EMAIL, PASSWORD)

    assert resp.status_code == 429
    assert 0 < int(resp.headers["retry-after"]) <= 60
    assert "access_token" not in resp.text


def test_hammering_a_locked_account_does_not_extend_the_lock(session, user):
    for _ in range(lt.PAIR.threshold):
        _login(EMAIL, "wrong password here")
    first = int(_login(EMAIL, PASSWORD).headers["retry-after"])
    rows_before = len(session.exec(select(LoginAttempt)).all())

    for _ in range(25):  # the attacker keeps going
        assert _login(EMAIL, "still wrong here").status_code == 429

    assert len(session.exec(select(LoginAttempt)).all()) == rows_before  # nothing was recorded
    assert int(_login(EMAIL, PASSWORD).headers["retry-after"]) <= first  # never longer than before


def test_the_real_user_is_not_locked_out_by_an_attacker_on_another_ip(monkeypatch, user):
    monkeypatch.setenv("AUTH_TRUST_FORWARDED_FOR", "true")
    attacker = {"X-Forwarded-For": "6.6.6.6"}
    for _ in range(lt.PAIR.threshold):
        assert _login(EMAIL, "wrong password here", attacker).status_code == 401
    assert _login(EMAIL, PASSWORD, attacker).status_code == 429  # attacker is locked...

    assert _login(EMAIL, PASSWORD, {"X-Forwarded-For": "203.0.113.7"}).status_code == 200  # ...the user is not


def test_a_successful_login_resets_the_pair_counter(user):
    for _ in range(lt.PAIR.threshold - 1):
        _login(EMAIL, "wrong password here")
    assert _login(EMAIL, PASSWORD).status_code == 200
    for _ in range(lt.PAIR.threshold - 1):
        assert _login(EMAIL, "wrong password here").status_code == 401


def test_api_per_ip_limit_across_many_emails(monkeypatch):
    monkeypatch.setenv("AUTH_TRUST_FORWARDED_FOR", "true")
    sprayer = {"X-Forwarded-For": "6.6.6.6"}
    for i in range(lt.IP.threshold):
        assert _login(f"user{i}@example.com", "wrong password here", sprayer).status_code == 401

    assert _login("fresh@example.com", "wrong password here", sprayer).status_code == 429
    assert _login("fresh@example.com", "wrong password here", {"X-Forwarded-For": "7.7.7.7"}).status_code == 401


# --------------------------------------------------------------------------
# client IP handling
# --------------------------------------------------------------------------


def _recorded_ip(session) -> str:
    return session.exec(select(LoginAttempt)).one().ip


def test_forwarded_header_is_ignored_unless_explicitly_trusted(session, monkeypatch):
    monkeypatch.delenv("AUTH_TRUST_FORWARDED_FOR", raising=False)
    _login("a@example.com", "wrong password here", {"X-Forwarded-For": "9.9.9.9"})
    assert _recorded_ip(session) == "testclient"  # the socket peer; a client cannot choose its own address


def test_rightmost_forwarded_entry_is_used_when_trusted(session, monkeypatch):
    monkeypatch.setenv("AUTH_TRUST_FORWARDED_FOR", "true")
    # Anything left of the last hop is client-supplied and spoofable; the proxy-appended one is not.
    _login("a@example.com", "wrong password here", {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
    assert _recorded_ip(session) == "5.6.7.8"


@pytest.mark.parametrize("junk", ["not-an-ip", "", "1.2.3.4, <script>"])
def test_unparseable_forwarded_value_falls_back_to_the_socket_peer(session, monkeypatch, junk):
    monkeypatch.setenv("AUTH_TRUST_FORWARDED_FOR", "true")
    _login("a@example.com", "wrong password here", {"X-Forwarded-For": junk})
    assert _recorded_ip(session) == "testclient"


def test_ipv6_forwarded_address_is_normalised(session, monkeypatch):
    monkeypatch.setenv("AUTH_TRUST_FORWARDED_FOR", "true")
    _login("a@example.com", "wrong password here", {"X-Forwarded-For": "2001:DB8:0:0:0:0:0:1"})
    assert _recorded_ip(session) == "2001:db8::1"


# --------------------------------------------------------------------------
# the table / migration shape
# --------------------------------------------------------------------------


def test_login_attempt_columns_fit_an_ipv6_address_and_a_sha256_hex():
    assert LoginAttempt.__table__.c.ip.type.length >= 45
    assert LoginAttempt.__table__.c.email_hash.type.length == 64
    assert AppUser.__table__.c.token_version.default.arg == 0
