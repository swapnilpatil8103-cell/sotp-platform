"""add insider_transaction and institutional_holding

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'insider_transaction',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('reporting_owner_name', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True),
        sa.Column('reporting_owner_cik', sqlmodel.sql.sqltypes.AutoString(length=10), nullable=True),
        sa.Column('is_officer', sa.Boolean(), nullable=True),
        sa.Column('is_director', sa.Boolean(), nullable=True),
        sa.Column('is_ten_percent_owner', sa.Boolean(), nullable=True),
        sa.Column('is_other', sa.Boolean(), nullable=True),
        sa.Column('officer_title', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column('transaction_table', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('security_title', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True),
        sa.Column('transaction_date', sa.Date(), nullable=True),
        sa.Column('transaction_code', sqlmodel.sql.sqltypes.AutoString(length=4), nullable=True),
        sa.Column('shares_transacted', sa.Float(), nullable=True),
        sa.Column('price_per_share', sa.Float(), nullable=True),
        sa.Column('transaction_acquired_disposed_code', sqlmodel.sql.sqltypes.AutoString(length=1), nullable=True),
        sa.Column('shares_owned_after', sa.Float(), nullable=True),
        sa.Column('ownership_type', sqlmodel.sql.sqltypes.AutoString(length=1), nullable=True),
        sa.Column('accession_number', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('source_url', sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=True),
        sa.Column('filing_date', sa.Date(), nullable=True),
        sa.Column('data_status', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'accession_number', 'reporting_owner_cik', 'security_title', 'transaction_date',
            name='uq_insider_transaction_natural_key',
        ),
    )
    op.create_index(op.f('ix_insider_transaction_company_id'), 'insider_transaction', ['company_id'], unique=False)
    op.create_index(op.f('ix_insider_transaction_reporting_owner_cik'), 'insider_transaction', ['reporting_owner_cik'], unique=False)
    op.create_index(op.f('ix_insider_transaction_transaction_date'), 'insider_transaction', ['transaction_date'], unique=False)
    op.create_index(op.f('ix_insider_transaction_accession_number'), 'insider_transaction', ['accession_number'], unique=False)

    op.create_table(
        'institutional_holding',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('filer_cik', sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
        sa.Column('filer_name', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True),
        sa.Column('issuer_name', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True),
        sa.Column('title_of_class', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column('cusip', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True),
        sa.Column('period_of_report', sa.Date(), nullable=True),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('shares_or_principal_amount', sa.Float(), nullable=True),
        sa.Column('shares_or_principal_type', sqlmodel.sql.sqltypes.AutoString(length=4), nullable=True),
        sa.Column('investment_discretion', sqlmodel.sql.sqltypes.AutoString(length=8), nullable=True),
        sa.Column('voting_authority_sole', sa.Float(), nullable=True),
        sa.Column('voting_authority_shared', sa.Float(), nullable=True),
        sa.Column('voting_authority_none', sa.Float(), nullable=True),
        sa.Column('accession_number', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('source_url', sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=True),
        sa.Column('filing_date', sa.Date(), nullable=True),
        sa.Column('data_status', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'filer_cik', 'accession_number', 'cusip', name='uq_institutional_holding_natural_key',
        ),
    )
    op.create_index(op.f('ix_institutional_holding_filer_cik'), 'institutional_holding', ['filer_cik'], unique=False)
    op.create_index(op.f('ix_institutional_holding_cusip'), 'institutional_holding', ['cusip'], unique=False)
    op.create_index(op.f('ix_institutional_holding_period_of_report'), 'institutional_holding', ['period_of_report'], unique=False)
    op.create_index(op.f('ix_institutional_holding_accession_number'), 'institutional_holding', ['accession_number'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_institutional_holding_accession_number'), table_name='institutional_holding')
    op.drop_index(op.f('ix_institutional_holding_period_of_report'), table_name='institutional_holding')
    op.drop_index(op.f('ix_institutional_holding_cusip'), table_name='institutional_holding')
    op.drop_index(op.f('ix_institutional_holding_filer_cik'), table_name='institutional_holding')
    op.drop_table('institutional_holding')

    op.drop_index(op.f('ix_insider_transaction_accession_number'), table_name='insider_transaction')
    op.drop_index(op.f('ix_insider_transaction_transaction_date'), table_name='insider_transaction')
    op.drop_index(op.f('ix_insider_transaction_reporting_owner_cik'), table_name='insider_transaction')
    op.drop_index(op.f('ix_insider_transaction_company_id'), table_name='insider_transaction')
    op.drop_table('insider_transaction')
