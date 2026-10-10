"""User administration: create users, change roles, deactivate/reactivate, revoke sessions.

Every operation writes AuditLogEntry rows (entity_type "AppUser") IN THE SAME
TRANSACTION as the change, so a change cannot exist without its audit entry or the
reverse. The actor is the acting admin (``human:<email>``) for the API and
``system`` for the command-line tools. Audit details record role lists and flags,
never passwords or hashes.

The "last admin" rule: a change that would leave the system with no active admin
is refused (``LastAdminError``). The rule counts the admins that remain AFTER the
change, so it protects against a self-demotion, a self-deactivation, and any
combination in one request. The admin rows are read with ``FOR UPDATE`` (a no-op
on SQLite) so two admins demoting each other at the same moment on Postgres
serialise instead of both succeeding.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from backend.audit.logger import (
    ACTION_USER_CREATED,
    ACTION_USER_DEACTIVATED,
    ACTION_USER_REACTIVATED,
    ACTION_USER_ROLES_CHANGED,
    ACTION_USER_SESSIONS_REVOKED,
    ACTOR_SYSTEM,
    actor_human,
    log_event,
)
from backend.models.app_user import AppUser
from backend.services import users

ENTITY_TYPE = "AppUser"


class UserNotFoundError(LookupError):
    pass


class LastAdminError(RuntimeError):
    """The change would leave no active admin."""


def actor_for(subject: Optional[str]) -> str:
    """Audit actor string: the acting admin, or ``system`` for CLI/bootstrap work."""
    return actor_human(subject) if subject else ACTOR_SYSTEM


def _is_active_admin(user: AppUser) -> bool:
    return user.is_active and users.ROLE_ADMIN in users.parse_roles(user.roles)


def _active_admin_ids(session: Session) -> set[int]:
    rows = session.exec(select(AppUser).where(AppUser.roles.contains(users.ROLE_ADMIN)).with_for_update()).all()
    return {u.id for u in rows if _is_active_admin(u)}


def list_users(session: Session, *, is_active: Optional[bool], limit: int, offset: int) -> tuple[list[AppUser], int]:
    stmt = select(AppUser)
    if is_active is not None:
        stmt = stmt.where(AppUser.is_active == is_active)
    total = len(session.exec(stmt).all())
    rows = session.exec(stmt.order_by(AppUser.email).offset(offset).limit(limit)).all()
    return list(rows), total


def get(session: Session, user_id: int) -> AppUser:
    user = session.get(AppUser, user_id)
    if user is None:
        raise UserNotFoundError(f"No user with id {user_id}.")
    return user


def create(
    session: Session, *, actor: Optional[str], email: str, password: str, roles: list[str], via: Optional[str] = None
) -> AppUser:
    """Create a user and audit it. Raises ``users.UserError`` (``DuplicateUserError`` for an existing email)."""
    try:
        user = users.create_user(session, email, password, roles, commit=False)
        detail = {"email": user.email, "roles": users.parse_roles(user.roles)}
        if via:
            detail["via"] = via
        log_event(
            session, entity_type=ENTITY_TYPE, entity_id=user.id, action=ACTION_USER_CREATED,
            actor=actor_for(actor), detail=detail, commit=False,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(user)
    return user


def _bump(user: AppUser) -> None:
    user.token_version += 1


def update(
    session: Session,
    *,
    actor: Optional[str],
    user_id: int,
    roles: Optional[list[str]] = None,
    is_active: Optional[bool] = None,
    revoke_sessions: bool = False,
    via: Optional[str] = None,
) -> AppUser:
    """Apply any combination of a role change, (de)activation and a session revoke, atomically.

    Deactivating also revokes the user's sessions: otherwise reactivating them later
    would silently resurrect tokens issued before the deactivation. Raises
    ``UserNotFoundError``, ``users.UserError`` (unknown role) or ``LastAdminError``;
    on any error nothing is changed and nothing is audited.
    """
    who = actor_for(actor)
    try:
        # Lock the admin rows first, then re-read the target: both reads see current data.
        admin_ids_before = _active_admin_ids(session)
        user = get(session, user_id)
        was_active_admin = user.id in admin_ids_before

        before_roles = users.parse_roles(user.roles)
        new_roles = users.validate_roles(roles) if roles is not None else before_roles
        new_active = user.is_active if is_active is None else is_active

        # Would anyone still be an active admin afterwards?
        remains_admin = new_active and users.ROLE_ADMIN in new_roles
        if was_active_admin and not remains_admin and not (admin_ids_before - {user.id}):
            raise LastAdminError(
                "This would leave no active admin: the last active admin cannot lose the admin role "
                "or be deactivated. Make another user an admin first."
            )

        events: list[tuple[str, dict]] = []
        if new_roles != before_roles:
            user.roles = ",".join(new_roles)
            events.append((ACTION_USER_ROLES_CHANGED, {"before": before_roles, "after": new_roles}))
        revoked_because: Optional[str] = None
        if new_active != user.is_active:
            user.is_active = new_active
            events.append((ACTION_USER_REACTIVATED if new_active else ACTION_USER_DEACTIVATED, {}))
            if not new_active:
                revoked_because = "deactivated"
        if revoke_sessions and revoked_because is None:
            revoked_because = "requested"
        if revoked_because:
            _bump(user)
            events.append((ACTION_USER_SESSIONS_REVOKED, {"reason": revoked_because, "token_version": user.token_version}))

        session.add(user)
        for action, detail in events:
            if via:
                detail = {**detail, "via": via}
            log_event(
                session, entity_type=ENTITY_TYPE, entity_id=user.id, action=action, actor=who,
                detail={"email": user.email, **detail}, commit=False,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(user)
    return user


def revoke_sessions(session: Session, *, actor: Optional[str], user: AppUser) -> AppUser:
    """Invalidate all of ``user``'s tokens and audit it (used by /auth/revoke-sessions and the CLI)."""
    return update(session, actor=actor, user_id=user.id, revoke_sessions=True)
