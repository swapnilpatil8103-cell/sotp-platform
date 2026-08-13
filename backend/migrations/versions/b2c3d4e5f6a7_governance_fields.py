"""governance: nullable valuation_run_id + company_id/subject on assumption_decision

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('assumption_decision') as batch_op:
        batch_op.alter_column('valuation_run_id', existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('subject', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True))
        batch_op.create_index(op.f('ix_assumption_decision_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key('fk_assumption_decision_company_id', 'company', ['company_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('assumption_decision') as batch_op:
        batch_op.drop_constraint('fk_assumption_decision_company_id', type_='foreignkey')
        batch_op.drop_index(op.f('ix_assumption_decision_company_id'))
        batch_op.drop_column('subject')
        batch_op.drop_column('company_id')
        batch_op.alter_column('valuation_run_id', existing_type=sa.Integer(), nullable=False)
