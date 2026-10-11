"""financial_fact.status_note, and clear previously normalized facts

Adds ``status_note`` to financial_fact and segment_financial_fact (why a value is DERIVED /
CONFLICTING / a total chosen over a subset).

It also deletes every row in ``financial_fact``. Those rows are a cache of public SEC data
produced by the normalizer, and the normalizer was fixed (stale prior-year share counts, a
revenue subset tag, a mislabeled long-term debt, derived total debt, restatement detection).
The facts endpoint serves rows already in the table without re-normalizing, and rows are keyed
by XBRL tag, so rows written under the old rules would keep being returned next to (or instead
of) correct ones. They are re-created from SEC on the next request; nothing else references them.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('financial_fact', 'segment_financial_fact'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('status_note', sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=True))
    op.execute('DELETE FROM financial_fact')


def downgrade() -> None:
    # The cleared rows are not restored (they are re-fetched from SEC on demand).
    for table in ('segment_financial_fact', 'financial_fact'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('status_note')
