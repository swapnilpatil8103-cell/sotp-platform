"""PostgreSQL: make every timestamp column timezone-aware (timestamptz)

Why: sqlmodel >= 0.0.48 maps every ``datetime`` field to a timezone-aware type
(``DateTime(timezone=True)``) and the application always writes aware UTC values.
The earlier migrations created these columns as plain ``timestamp without time
zone``. PostgreSQL silently converts an aware value to the SESSION time zone before
dropping the zone when it is stored in such a column, so on a server whose
TimeZone is not UTC every value was shifted (e.g. 00:00 UTC stored as 05:30 on an
``Asia/Kolkata`` server) and read back as that shifted wall time.

Existing rows: they were written through that same conversion, so their wall-clock
values are in the session time zone. ``ALTER COLUMN ... TYPE timestamptz`` without a
``USING`` clause interprets existing values in the session time zone as well, which
recovers the original instants exactly as long as the server's TimeZone is the one
the data was written under (the normal case; on a UTC server it is a no-op in value).

SQLite has no zone-aware type and stores UTC wall time, so nothing changes there.
Rewrites each table once; fine at this project's scale.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every timestamp column created by the earlier migrations (explicit, so the
# downgrade converts back exactly these and nothing a later migration adds).
TIMESTAMP_COLUMNS = [
    ('app_user', 'created_at'),
    ('app_user', 'last_login_at'),
    ('assumption_decision', 'created_at'),
    ('assumption_decision', 'decided_at'),
    ('audit_log_entry', 'created_at'),
    ('company', 'created_at'),
    ('company', 'updated_at'),
    ('filing', 'created_at'),
    ('filing', 'updated_at'),
    ('financial_fact', 'created_at'),
    ('financial_fact', 'updated_at'),
    ('insider_transaction', 'created_at'),
    ('insider_transaction', 'updated_at'),
    ('institutional_holding', 'created_at'),
    ('institutional_holding', 'updated_at'),
    ('login_attempt', 'ts'),
    ('market_data_snapshot', 'created_at'),
    ('market_data_snapshot', 'fetched_at'),
    ('market_data_snapshot', 'updated_at'),
    ('segment', 'created_at'),
    ('segment', 'updated_at'),
    ('segment_financial_fact', 'created_at'),
    ('segment_financial_fact', 'updated_at'),
    ('valuation_run', 'created_at'),
]


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == 'postgresql'


def upgrade() -> None:
    if not _is_postgres():
        return
    for table, column in TIMESTAMP_COLUMNS:
        # No USING clause: existing wall-clock values are read in the session time zone.
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE timestamp with time zone')


def downgrade() -> None:
    if not _is_postgres():
        return
    for table, column in TIMESTAMP_COLUMNS:
        # Inverse of upgrade: back to wall-clock time in the session time zone.
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE timestamp without time zone')
