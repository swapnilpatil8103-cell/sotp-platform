"""add market_data_snapshot

Revision ID: a1b2c3d4e5f6
Revises: d590d6e06ac2
Create Date: 2026-08-11 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'd590d6e06ac2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'market_data_snapshot',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('currency', sqlmodel.sql.sqltypes.AutoString(length=8), nullable=True),
        sa.Column('market_cap', sa.Float(), nullable=True),
        sa.Column('shares_outstanding', sa.Float(), nullable=True),
        sa.Column('beta', sa.Float(), nullable=True),
        sa.Column('dividend_yield', sa.Float(), nullable=True),
        sa.Column('last_dividend_value', sa.Float(), nullable=True),
        sa.Column('last_dividend_date', sa.Date(), nullable=True),
        sa.Column('as_of', sa.Date(), nullable=False),
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('fetched_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'as_of', name='uq_market_data_snapshot_company_as_of'),
    )
    op.create_index(op.f('ix_market_data_snapshot_company_id'), 'market_data_snapshot', ['company_id'], unique=False)
    op.create_index(op.f('ix_market_data_snapshot_as_of'), 'market_data_snapshot', ['as_of'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_market_data_snapshot_as_of'), table_name='market_data_snapshot')
    op.drop_index(op.f('ix_market_data_snapshot_company_id'), table_name='market_data_snapshot')
    op.drop_table('market_data_snapshot')
