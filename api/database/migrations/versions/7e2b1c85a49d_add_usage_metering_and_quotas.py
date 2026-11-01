"""add_usage_metering_and_quotas

Revision ID: 7e2b1c85a49d
Revises: 3f6397f5179d
Create Date: 2026-10-20 06:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e2b1c85a49d'
down_revision: Union[str, None] = '3f6397f5179d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create usage_events table
    op.create_table(
        'usage_events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column('api_key_id', sa.String(length=36), nullable=True),
        sa.Column('event_type', sa.String(length=32), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('resource_id', sa.String(length=64), nullable=True),
        sa.Column('quantity', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('unit', sa.String(length=32), nullable=False, server_default='count'),
        sa.Column('bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('duration_seconds', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('auth_method', sa.String(length=20), nullable=False, server_default='jwt'),
        sa.Column('client_ip', sa.String(length=45), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(
            ['tenant_id'],
            ['gid.tenants.id'],
            name=op.f('fk_usage_events_tenant_id_tenants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['gid.users.id'],
            name=op.f('fk_usage_events_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['api_key_id'],
            ['gid.api_keys.id'],
            name=op.f('fk_usage_events_api_key_id_api_keys'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_usage_events')),
        sa.UniqueConstraint('tenant_id', 'idempotency_key', name='uq_usage_events_tenant_idempotency'),
        schema='gid',
    )
    op.create_index(
        'ix_usage_events_tenant_created',
        'usage_events',
        ['tenant_id', 'created_at'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_usage_events_tenant_type_created',
        'usage_events',
        ['tenant_id', 'event_type', 'created_at'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_usage_events_tenant_user_created',
        'usage_events',
        ['tenant_id', 'user_id', 'created_at'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_usage_events_tenant_key_created',
        'usage_events',
        ['tenant_id', 'api_key_id', 'created_at'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_usage_events_resource_id',
        'usage_events',
        ['resource_id'],
        unique=False,
        schema='gid',
    )

    # 2. Create tenant_quotas table
    op.create_table(
        'tenant_quotas',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('metric', sa.String(length=64), nullable=False),
        sa.Column('period', sa.String(length=20), nullable=False, server_default='monthly'),
        sa.Column('limit_value', sa.BigInteger(), nullable=False, server_default='-1'),
        sa.Column('is_hard_limit', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(
            ['tenant_id'],
            ['gid.tenants.id'],
            name=op.f('fk_tenant_quotas_tenant_id_tenants'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tenant_quotas')),
        sa.UniqueConstraint('tenant_id', 'metric', 'period', name='uq_tenant_quotas_tenant_metric_period'),
        schema='gid',
    )
    op.create_index(
        'ix_tenant_quotas_tenant_id',
        'tenant_quotas',
        ['tenant_id'],
        unique=False,
        schema='gid',
    )

    # 3. Grant runtime permissions
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gid_app') THEN "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.usage_events TO gid_app; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.tenant_quotas TO gid_app; "
            "END IF; "
            "IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gid_user') THEN "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.usage_events TO gid_user; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.tenant_quotas TO gid_user; "
            "END IF; END $$;"
        )
    )


def downgrade() -> None:
    op.drop_index('ix_tenant_quotas_tenant_id', table_name='tenant_quotas', schema='gid')
    op.drop_table('tenant_quotas', schema='gid')

    op.drop_index('ix_usage_events_resource_id', table_name='usage_events', schema='gid')
    op.drop_index('ix_usage_events_tenant_key_created', table_name='usage_events', schema='gid')
    op.drop_index('ix_usage_events_tenant_user_created', table_name='usage_events', schema='gid')
    op.drop_index('ix_usage_events_tenant_type_created', table_name='usage_events', schema='gid')
    op.drop_index('ix_usage_events_tenant_created', table_name='usage_events', schema='gid')
    op.drop_table('usage_events', schema='gid')
