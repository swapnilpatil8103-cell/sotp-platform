"""add login_attempt table and app_user.token_version

login_attempt backs the shared (multi-worker, restart-proof) login throttle.
token_version lets a password change or an admin "revoke sessions" invalidate
every token issued before it.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'login_attempt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('ip', sqlmodel.sql.sqltypes.AutoString(length=45), nullable=False),
        sa.Column('ts', sa.DateTime(), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_login_attempt_email_hash'), 'login_attempt', ['email_hash'], unique=False)
    op.create_index(op.f('ix_login_attempt_ip'), 'login_attempt', ['ip'], unique=False)
    op.create_index(op.f('ix_login_attempt_ts'), 'login_attempt', ['ts'], unique=False)

    with op.batch_alter_table('app_user') as batch_op:
        batch_op.add_column(sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('app_user') as batch_op:
        batch_op.drop_column('token_version')
    op.drop_index(op.f('ix_login_attempt_ts'), table_name='login_attempt')
    op.drop_index(op.f('ix_login_attempt_ip'), table_name='login_attempt')
    op.drop_index(op.f('ix_login_attempt_email_hash'), table_name='login_attempt')
    op.drop_table('login_attempt')
