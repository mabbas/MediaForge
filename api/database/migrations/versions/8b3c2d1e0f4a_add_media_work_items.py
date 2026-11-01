"""add_media_work_items

Revision ID: 8b3c2d1e0f4a
Revises: 7e2b1c85a49d
Create Date: 2026-10-23 05:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b3c2d1e0f4a'
down_revision: Union[str, None] = '7e2b1c85a49d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create media_work_items table
    op.create_table(
        'media_work_items',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('requester_user_id', sa.String(length=36), nullable=True),
        sa.Column('requester_api_key_id', sa.String(length=36), nullable=True),
        sa.Column('operation_type', sa.String(length=32), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('request_payload', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='queued'),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('claimed_by', sa.String(length=128), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_heartbeat_at', sa.DateTime(), nullable=True),
        sa.Column('available_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('progress_percent', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('cancellation_requested_at', sa.DateTime(), nullable=True),
        sa.Column('result_artifact_id', sa.String(length=36), nullable=True),
        sa.Column('sanitized_error_code', sa.String(length=64), nullable=True),
        sa.Column('sanitized_error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(
            ['tenant_id'],
            ['gid.tenants.id'],
            name=op.f('fk_media_work_items_tenant_id_tenants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['requester_user_id'],
            ['gid.users.id'],
            name=op.f('fk_media_work_items_requester_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['requester_api_key_id'],
            ['gid.api_keys.id'],
            name=op.f('fk_media_work_items_requester_api_key_id_api_keys'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['result_artifact_id'],
            ['gid.media_artifacts.id'],
            name=op.f('fk_media_work_items_result_artifact_id_media_artifacts'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_media_work_items')),
        sa.UniqueConstraint('tenant_id', 'idempotency_key', name='uq_media_work_items_tenant_idempotency'),
        sa.CheckConstraint(
            'attempt_count >= 0 AND attempt_count <= max_attempts + 1',
            name='ck_media_work_items_attempts',
        ),
        sa.CheckConstraint(
            'progress_percent >= 0.0 AND progress_percent <= 100.0',
            name='ck_media_work_items_progress',
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')",
            name='ck_media_work_items_status',
        ),
        sa.CheckConstraint(
            "operation_type IN ('clip_extract', 'merge')",
            name='ck_media_work_items_operation',
        ),
        schema='gid',
    )
    op.create_index(
        'ix_media_work_items_claimable',
        'media_work_items',
        ['status', 'available_at'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_media_work_items_lease_expiry',
        'media_work_items',
        ['status', 'lease_expires_at'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_media_work_items_tenant_created',
        'media_work_items',
        ['tenant_id', 'created_at'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_media_work_items_tenant_user',
        'media_work_items',
        ['tenant_id', 'requester_user_id'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_media_work_items_status',
        'media_work_items',
        ['status'],
        unique=False,
        schema='gid',
    )

    # 2. Grant permissions
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gid_app') THEN "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.media_work_items TO gid_app; "
            "END IF; "
            "IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gid_user') THEN "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.media_work_items TO gid_user; "
            "END IF; END $$;"
        )
    )


def downgrade() -> None:
    op.drop_index('ix_media_work_items_status', table_name='media_work_items', schema='gid')
    op.drop_index('ix_media_work_items_tenant_user', table_name='media_work_items', schema='gid')
    op.drop_index('ix_media_work_items_tenant_created', table_name='media_work_items', schema='gid')
    op.drop_index('ix_media_work_items_lease_expiry', table_name='media_work_items', schema='gid')
    op.drop_index('ix_media_work_items_claimable', table_name='media_work_items', schema='gid')
    op.drop_table('media_work_items', schema='gid')
