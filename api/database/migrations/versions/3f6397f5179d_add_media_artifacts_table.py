"""add_media_artifacts_table

Revision ID: 3f6397f5179d
Revises: 5b89a2d1e4f3
Create Date: 2026-10-18 22:19:27.554046

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f6397f5179d'
down_revision: Union[str, None] = '5b89a2d1e4f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'media_artifacts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column('api_key_id', sa.String(length=36), nullable=True),
        sa.Column('artifact_type', sa.String(length=20), nullable=False),
        sa.Column('source_download_id', sa.String(length=36), nullable=True),
        sa.Column('parent_artifact_id', sa.String(length=36), nullable=True),
        sa.Column('storage_path', sa.String(length=512), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('format', sa.String(length=20), nullable=False, server_default='mp4'),
        sa.Column('duration_seconds', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('file_size_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('sha256_checksum', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('input_sources', sa.JSON(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['tenant_id'],
            ['gid.tenants.id'],
            name=op.f('fk_media_artifacts_tenant_id_tenants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['gid.users.id'],
            name=op.f('fk_media_artifacts_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['api_key_id'],
            ['gid.api_keys.id'],
            name=op.f('fk_media_artifacts_api_key_id_api_keys'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['source_download_id'],
            ['gid.download_jobs.id'],
            name=op.f('fk_media_artifacts_source_download_id_download_jobs'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['parent_artifact_id'],
            ['gid.media_artifacts.id'],
            name=op.f('fk_media_artifacts_parent_artifact_id_media_artifacts'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_media_artifacts')),
        schema='gid',
    )
    op.create_index(
        'ix_media_artifacts_tenant_created',
        'media_artifacts',
        ['tenant_id', 'created_at'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_media_artifacts_tenant_user',
        'media_artifacts',
        ['tenant_id', 'user_id'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_media_artifacts_tenant_type_status',
        'media_artifacts',
        ['tenant_id', 'artifact_type', 'status'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_media_artifacts_storage_path',
        'media_artifacts',
        ['tenant_id', 'storage_path'],
        unique=True,
        schema='gid',
    )
    # Grant DML permissions to runtime role gid_app if role exists
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gid_app') THEN "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.media_artifacts TO gid_app; "
            "END IF; END $$;"
        )
    )


def downgrade() -> None:
    op.drop_index('ix_media_artifacts_storage_path', table_name='media_artifacts', schema='gid')
    op.drop_index('ix_media_artifacts_tenant_type_status', table_name='media_artifacts', schema='gid')
    op.drop_index('ix_media_artifacts_tenant_user', table_name='media_artifacts', schema='gid')
    op.drop_index('ix_media_artifacts_tenant_created', table_name='media_artifacts', schema='gid')
    op.drop_table('media_artifacts', schema='gid')
