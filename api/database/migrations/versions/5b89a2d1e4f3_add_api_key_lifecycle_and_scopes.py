"""add_api_key_lifecycle_and_scopes

Revision ID: 5b89a2d1e4f3
Revises: 3f371fba5a49
Create Date: 2026-10-18 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b89a2d1e4f3'
down_revision: Union[str, None] = '3f371fba5a49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'api_keys',
        sa.Column('key_prefix', sa.String(length=16), nullable=False, server_default=''),
        schema='gid',
    )
    op.add_column(
        'api_keys',
        sa.Column('is_revoked', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        schema='gid',
    )
    op.add_column(
        'api_keys',
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        schema='gid',
    )
    op.add_column(
        'api_keys',
        sa.Column('revocation_reason', sa.String(length=255), nullable=True),
        schema='gid',
    )
    op.add_column(
        'api_keys',
        sa.Column('revoked_by_user_id', sa.String(length=36), nullable=True),
        schema='gid',
    )
    op.add_column(
        'api_keys',
        sa.Column('rotated_to_id', sa.String(length=36), nullable=True),
        schema='gid',
    )
    op.add_column(
        'api_keys',
        sa.Column('rotated_from_id', sa.String(length=36), nullable=True),
        schema='gid',
    )
    op.add_column(
        'api_keys',
        sa.Column('total_requests', sa.Integer(), nullable=False, server_default=sa.text('0')),
        schema='gid',
    )
    op.add_column(
        'api_keys',
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        schema='gid',
    )

    op.create_foreign_key(
        op.f('fk_api_keys_rotated_to_id_api_keys'),
        'api_keys',
        'api_keys',
        ['rotated_to_id'],
        ['id'],
        source_schema='gid',
        referent_schema='gid',
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        op.f('fk_api_keys_rotated_from_id_api_keys'),
        'api_keys',
        'api_keys',
        ['rotated_from_id'],
        ['id'],
        source_schema='gid',
        referent_schema='gid',
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_api_keys_tenant_id',
        'api_keys',
        ['tenant_id'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_api_keys_key_prefix',
        'api_keys',
        ['key_prefix'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_api_keys_user_id',
        'api_keys',
        ['user_id'],
        unique=False,
        schema='gid',
    )


def downgrade() -> None:
    op.drop_index('ix_api_keys_user_id', table_name='api_keys', schema='gid')
    op.drop_index('ix_api_keys_key_prefix', table_name='api_keys', schema='gid')
    op.drop_index('ix_api_keys_tenant_id', table_name='api_keys', schema='gid')
    op.drop_constraint(
        op.f('fk_api_keys_rotated_from_id_api_keys'),
        'api_keys',
        schema='gid',
        type_='foreignkey',
    )
    op.drop_constraint(
        op.f('fk_api_keys_rotated_to_id_api_keys'),
        'api_keys',
        schema='gid',
        type_='foreignkey',
    )
    op.drop_column('api_keys', 'updated_at', schema='gid')
    op.drop_column('api_keys', 'total_requests', schema='gid')
    op.drop_column('api_keys', 'rotated_from_id', schema='gid')
    op.drop_column('api_keys', 'rotated_to_id', schema='gid')
    op.drop_column('api_keys', 'revoked_by_user_id', schema='gid')
    op.drop_column('api_keys', 'revocation_reason', schema='gid')
    op.drop_column('api_keys', 'revoked_at', schema='gid')
    op.drop_column('api_keys', 'is_revoked', schema='gid')
    op.drop_column('api_keys', 'key_prefix', schema='gid')
