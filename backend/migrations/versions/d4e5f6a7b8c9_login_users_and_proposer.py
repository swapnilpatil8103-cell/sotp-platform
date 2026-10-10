"""add app_user and assumption_decision.proposed_by

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'app_user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sqlmodel.sql.sqltypes.AutoString(length=254), nullable=False),
        sa.Column('password_hash', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('roles', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=False, server_default=''),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_app_user_email'), 'app_user', ['email'], unique=True)

    # Nullable: rows created before this migration have no recorded proposer,
    # so the separation-of-duties rule cannot be applied to them.
    with op.batch_alter_table('assumption_decision') as batch_op:
        batch_op.add_column(sa.Column('proposed_by', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('assumption_decision') as batch_op:
        batch_op.drop_column('proposed_by')
    op.drop_index(op.f('ix_app_user_email'), table_name='app_user')
    op.drop_table('app_user')
