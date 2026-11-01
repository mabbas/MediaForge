"""add_pending_registrations

Revision ID: 0ec360091316
Revises: dd1604fa6959
Create Date: 2026-10-18 14:25:32.487954

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0ec360091316'
down_revision: Union[str, None] = 'dd1604fa6959'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pending_registrations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('tenant_name', sa.String(length=200), nullable=False),
        sa.Column('tenant_slug', sa.String(length=100), nullable=False),
        sa.Column('token_hash', sa.String(length=128), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_sent_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_pending_registrations')),
        sa.UniqueConstraint('email', name=op.f('uq_pending_registrations_email')),
        sa.UniqueConstraint('tenant_slug', name=op.f('uq_pending_registrations_tenant_slug')),
        schema='gid',
    )
    op.create_index(
        op.f('ix_pending_registrations_token_hash'),
        'pending_registrations',
        ['token_hash'],
        unique=True,
        schema='gid',
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_pending_registrations_token_hash'),
        table_name='pending_registrations',
        schema='gid',
    )
    op.drop_table('pending_registrations', schema='gid')
