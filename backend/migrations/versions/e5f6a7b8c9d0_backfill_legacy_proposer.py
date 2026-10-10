"""backfill assumption_decision.proposed_by with 'unknown-legacy'

Rows created before proposer tracking have proposed_by NULL. Mark them with an
explicit sentinel so "no proposer recorded" is a stored fact rather than an
absence: backend/governance/approval.py refuses to APPROVE or EDIT such a
proposal (the caller must re-propose) because separation of duties cannot be
verified for it. They can still be rejected.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match backend.models.assumption_decision.LEGACY_PROPOSER. Duplicated on
# purpose: a migration must keep its meaning even if application code changes.
SENTINEL = 'unknown-legacy'


def upgrade() -> None:
    op.execute(
        f"UPDATE assumption_decision SET proposed_by = '{SENTINEL}' WHERE proposed_by IS NULL"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE assumption_decision SET proposed_by = NULL WHERE proposed_by = '{SENTINEL}'"
    )
