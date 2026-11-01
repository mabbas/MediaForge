"""add_password_reset_tokens

Revision ID: 3f371fba5a49
Revises: 16f22eb0a094
Create Date: 2026-10-18 15:26:17.571908

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f371fba5a49'
down_revision: Union[str, None] = '16f22eb0a094'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('consumed_at', sa.DateTime(), nullable=True),
        sa.Column('client_ip', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['gid.users.id'],
            name=op.f('fk_password_reset_tokens_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_password_reset_tokens')),
        schema='gid',
    )
    op.create_index(
        'ix_password_reset_tokens_user_id',
        'password_reset_tokens',
        ['user_id'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_password_reset_tokens_token_hash',
        'password_reset_tokens',
        ['token_hash'],
        unique=True,
        schema='gid',
    )
    op.create_index(
        'ix_password_reset_tokens_expires_at',
        'password_reset_tokens',
        ['expires_at'],
        unique=False,
        schema='gid',
    )


def downgrade() -> None:
    op.drop_index(
        'ix_password_reset_tokens_expires_at',
        table_name='password_reset_tokens',
        schema='gid',
    )
    op.drop_index(
        'ix_password_reset_tokens_token_hash',
        table_name='password_reset_tokens',
        schema='gid',
    )
    op.drop_index(
        'ix_password_reset_tokens_user_id',
        table_name='password_reset_tokens',
        schema='gid',
    )
    op.drop_table('password_reset_tokens', schema='gid')
