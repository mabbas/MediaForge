"""add_commercial_plans_and_entitlements

Revision ID: 9c4d3e2f1a5b
Revises: 8b3c2d1e0f4a
Create Date: 2026-10-29 12:00:00.000000

"""
from typing import Sequence, Union
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c4d3e2f1a5b'
down_revision: Union[str, None] = '8b3c2d1e0f4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create commercial_plans table
    op.create_table(
        'commercial_plans',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('plan_code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='draft'),
        sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('tier_type', sa.String(length=32), nullable=False, server_default='paid'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('billing_interval', sa.String(length=32), nullable=False, server_default='monthly'),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
        sa.Column('price_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('retired_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_commercial_plans')),
        sa.UniqueConstraint('plan_code', 'version', name='uq_commercial_plans_code_version'),
        sa.CheckConstraint("status IN ('draft', 'active', 'retired')", name='ck_commercial_plans_status'),
        sa.CheckConstraint("tier_type IN ('free', 'trial', 'paid')", name='ck_commercial_plans_tier_type'),
        sa.CheckConstraint('version >= 1', name='ck_commercial_plans_version'),
        schema='gid',
    )
    op.create_index(
        'ix_commercial_plans_status_public',
        'commercial_plans',
        ['status', 'is_public'],
        unique=False,
        schema='gid',
    )

    # 2. Create commercial_plan_entitlements table
    op.create_table(
        'commercial_plan_entitlements',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('plan_id', sa.String(length=36), nullable=False),
        sa.Column('entitlement_key', sa.String(length=64), nullable=False),
        sa.Column('value_type', sa.String(length=32), nullable=False),
        sa.Column('value_bool', sa.Boolean(), nullable=True),
        sa.Column('value_int', sa.BigInteger(), nullable=True),
        sa.Column('is_unlimited', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(
            ['plan_id'],
            ['gid.commercial_plans.id'],
            name=op.f('fk_commercial_plan_entitlements_plan_id_commercial_plans'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_commercial_plan_entitlements')),
        sa.UniqueConstraint('plan_id', 'entitlement_key', name='uq_commercial_plan_entitlements_plan_key'),
        sa.CheckConstraint("value_type IN ('boolean', 'count', 'bytes', 'seconds', 'concurrency')", name='ck_plan_entitlements_value_type'),
        sa.CheckConstraint('value_int IS NULL OR value_int >= 0', name='ck_plan_entitlements_value_int_nonneg'),
        schema='gid',
    )
    op.create_index(
        'ix_commercial_plan_entitlements_plan_id',
        'commercial_plan_entitlements',
        ['plan_id'],
        unique=False,
        schema='gid',
    )

    # 3. Create tenant_subscriptions table
    op.create_table(
        'tenant_subscriptions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('plan_id', sa.String(length=36), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='trial'),
        sa.Column('trial_started_at', sa.DateTime(), nullable=True),
        sa.Column('trial_expires_at', sa.DateTime(), nullable=True),
        sa.Column('current_period_start', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('current_period_end', sa.DateTime(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(
            ['tenant_id'],
            ['gid.tenants.id'],
            name=op.f('fk_tenant_subscriptions_tenant_id_tenants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['plan_id'],
            ['gid.commercial_plans.id'],
            name=op.f('fk_tenant_subscriptions_plan_id_commercial_plans'),
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tenant_subscriptions')),
        sa.UniqueConstraint('tenant_id', name='uq_tenant_subscriptions_tenant_id'),
        sa.CheckConstraint("status IN ('trial', 'active', 'cancelled', 'expired')", name='ck_tenant_subscriptions_status'),
        schema='gid',
    )
    op.create_index(
        'ix_tenant_subscriptions_status',
        'tenant_subscriptions',
        ['status'],
        unique=False,
        schema='gid',
    )

    # 4. Create tenant_entitlement_overrides table
    op.create_table(
        'tenant_entitlement_overrides',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('entitlement_key', sa.String(length=64), nullable=False),
        sa.Column('value_type', sa.String(length=32), nullable=False),
        sa.Column('value_bool', sa.Boolean(), nullable=True),
        sa.Column('value_int', sa.BigInteger(), nullable=True),
        sa.Column('is_unlimited', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('granted_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(
            ['tenant_id'],
            ['gid.tenants.id'],
            name=op.f('fk_tenant_entitlement_overrides_tenant_id_tenants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['granted_by_user_id'],
            ['gid.users.id'],
            name=op.f('fk_tenant_entitlement_overrides_granted_by_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tenant_entitlement_overrides')),
        sa.UniqueConstraint('tenant_id', 'entitlement_key', name='uq_tenant_entitlement_overrides_tenant_key'),
        sa.CheckConstraint("value_type IN ('boolean', 'count', 'bytes', 'seconds', 'concurrency')", name='ck_tenant_overrides_value_type'),
        sa.CheckConstraint('value_int IS NULL OR value_int >= 0', name='ck_tenant_overrides_value_int_nonneg'),
        schema='gid',
    )
    op.create_index(
        'ix_tenant_entitlement_overrides_tenant',
        'tenant_entitlement_overrides',
        ['tenant_id'],
        unique=False,
        schema='gid',
    )

    # 5. Create commercial_audit_logs table
    op.create_table(
        'commercial_audit_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('actor_user_id', sa.String(length=36), nullable=True),
        sa.Column('actor_role', sa.String(length=32), nullable=False, server_default='system_admin'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('details_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(
            ['tenant_id'],
            ['gid.tenants.id'],
            name=op.f('fk_commercial_audit_logs_tenant_id_tenants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['actor_user_id'],
            ['gid.users.id'],
            name=op.f('fk_commercial_audit_logs_actor_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_commercial_audit_logs')),
        schema='gid',
    )
    op.create_index(
        'ix_commercial_audit_logs_tenant',
        'commercial_audit_logs',
        ['tenant_id', 'created_at'],
        unique=False,
        schema='gid',
    )
    op.create_index(
        'ix_commercial_audit_logs_action',
        'commercial_audit_logs',
        ['action', 'created_at'],
        unique=False,
        schema='gid',
    )

    # 6. Grant runtime permissions for PostgreSQL roles
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gid_app') THEN "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.commercial_plans TO gid_app; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.commercial_plan_entitlements TO gid_app; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.tenant_subscriptions TO gid_app; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.tenant_entitlement_overrides TO gid_app; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.commercial_audit_logs TO gid_app; "
            "END IF; "
            "IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gid_user') THEN "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.commercial_plans TO gid_user; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.commercial_plan_entitlements TO gid_user; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.tenant_subscriptions TO gid_user; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.tenant_entitlement_overrides TO gid_user; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE gid.commercial_audit_logs TO gid_user; "
            "END IF; END $$;"
        )
    )

    # 7. Seed standard published plan catalog version 1
    bind = op.get_bind()
    plan_table = sa.table(
        'commercial_plans',
        sa.column('id', sa.String),
        sa.column('plan_code', sa.String),
        sa.column('name', sa.String),
        sa.column('description', sa.Text),
        sa.column('status', sa.String),
        sa.column('is_public', sa.Boolean),
        sa.column('tier_type', sa.String),
        sa.column('version', sa.Integer),
        sa.column('billing_interval', sa.String),
        sa.column('currency', sa.String),
        sa.column('price_cents', sa.Integer),
        sa.column('metadata_json', sa.JSON),
        sa.column('published_at', sa.DateTime),
        schema='gid',
    )
    ent_table = sa.table(
        'commercial_plan_entitlements',
        sa.column('id', sa.String),
        sa.column('plan_id', sa.String),
        sa.column('entitlement_key', sa.String),
        sa.column('value_type', sa.String),
        sa.column('value_bool', sa.Boolean),
        sa.column('value_int', sa.BigInteger),
        sa.column('is_unlimited', sa.Boolean),
        schema='gid',
    )

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # Plan definitions
    plans_data = [
        {
            "id": "plan-free-v1",
            "plan_code": "free",
            "name": "MediaForge Free",
            "description": "Standard free tier for evaluation and light media downloading.",
            "status": "active",
            "is_public": True,
            "tier_type": "free",
            "version": 1,
            "billing_interval": "monthly",
            "currency": "USD",
            "price_cents": 0,
            "metadata_json": {"tier_level": 1},
            "published_at": now_utc,
            "entitlements": [
                ("desktop_access", "boolean", True, None, False),
                ("api_access", "boolean", False, None, False),
                ("max_active_api_keys", "count", None, 0, False),
                ("monthly_download_count", "count", None, 50, False),
                ("monthly_download_bytes", "bytes", None, 5 * 1024 * 1024 * 1024, False),  # 5 GB
                ("monthly_clip_count", "count", None, 20, False),
                ("monthly_clip_seconds", "seconds", None, 600, False),  # 10 min
                ("monthly_merge_count", "count", None, 10, False),
                ("max_concurrent_jobs", "concurrency", None, 1, False),
                ("storage_bytes", "bytes", None, 5 * 1024 * 1024 * 1024, False),  # 5 GB
                ("artifact_retention_days", "count", None, 7, False),
                ("ai_transcription_access", "boolean", False, None, False),
                ("max_users", "count", None, 1, False),
            ],
        },
        {
            "id": "plan-creator-monthly-v1",
            "plan_code": "creator_monthly",
            "name": "MediaForge Creator",
            "description": "For content creators needing robust downloading, editing, and API access.",
            "status": "active",
            "is_public": True,
            "tier_type": "paid",
            "version": 1,
            "billing_interval": "monthly",
            "currency": "USD",
            "price_cents": 1900,  # $19.00
            "metadata_json": {"tier_level": 2},
            "published_at": now_utc,
            "entitlements": [
                ("desktop_access", "boolean", True, None, False),
                ("api_access", "boolean", True, None, False),
                ("max_active_api_keys", "count", None, 5, False),
                ("monthly_download_count", "count", None, 500, False),
                ("monthly_download_bytes", "bytes", None, 50 * 1024 * 1024 * 1024, False),  # 50 GB
                ("monthly_clip_count", "count", None, 200, False),
                ("monthly_clip_seconds", "seconds", None, 7200, False),  # 2 hours
                ("monthly_merge_count", "count", None, 50, False),
                ("max_concurrent_jobs", "concurrency", None, 2, False),
                ("storage_bytes", "bytes", None, 50 * 1024 * 1024 * 1024, False),  # 50 GB
                ("artifact_retention_days", "count", None, 30, False),
                ("ai_transcription_access", "boolean", True, None, False),
                ("max_users", "count", None, 5, False),
            ],
        },
        {
            "id": "plan-pro-monthly-v1",
            "plan_code": "pro_monthly",
            "name": "MediaForge Pro",
            "description": "Professional tier with unlimited downloading, high concurrency, and large storage.",
            "status": "active",
            "is_public": True,
            "tier_type": "paid",
            "version": 1,
            "billing_interval": "monthly",
            "currency": "USD",
            "price_cents": 4900,  # $49.00
            "metadata_json": {"tier_level": 3},
            "published_at": now_utc,
            "entitlements": [
                ("desktop_access", "boolean", True, None, False),
                ("api_access", "boolean", True, None, False),
                ("max_active_api_keys", "count", None, 25, False),
                ("monthly_download_count", "count", None, None, True),
                ("monthly_download_bytes", "bytes", None, None, True),
                ("monthly_clip_count", "count", None, None, True),
                ("monthly_clip_seconds", "seconds", None, None, True),
                ("monthly_merge_count", "count", None, None, True),
                ("max_concurrent_jobs", "concurrency", None, 5, False),
                ("storage_bytes", "bytes", None, 200 * 1024 * 1024 * 1024, False),  # 200 GB
                ("artifact_retention_days", "count", None, 90, False),
                ("ai_transcription_access", "boolean", True, None, False),
                ("max_users", "count", None, 20, False),
            ],
        },
        {
            "id": "plan-trial-v1",
            "plan_code": "enterprise_trial",
            "name": "MediaForge Pro Trial",
            "description": "14-day fully featured evaluation plan for new commercial tenants.",
            "status": "active",
            "is_public": False,
            "tier_type": "trial",
            "version": 1,
            "billing_interval": "monthly",
            "currency": "USD",
            "price_cents": 0,
            "metadata_json": {"trial_duration_days": 14},
            "published_at": now_utc,
            "entitlements": [
                ("desktop_access", "boolean", True, None, False),
                ("api_access", "boolean", True, None, False),
                ("max_active_api_keys", "count", None, 10, False),
                ("monthly_download_count", "count", None, 1000, False),
                ("monthly_download_bytes", "bytes", None, 100 * 1024 * 1024 * 1024, False),  # 100 GB
                ("monthly_clip_count", "count", None, 500, False),
                ("monthly_clip_seconds", "seconds", None, 18000, False),  # 5 hours
                ("monthly_merge_count", "count", None, 100, False),
                ("max_concurrent_jobs", "concurrency", None, 3, False),
                ("storage_bytes", "bytes", None, 100 * 1024 * 1024 * 1024, False),  # 100 GB
                ("artifact_retention_days", "count", None, 14, False),
                ("ai_transcription_access", "boolean", True, None, False),
                ("max_users", "count", None, 10, False),
            ],
        },
    ]

    for p in plans_data:
        op.bulk_insert(
            plan_table,
            [
                {
                    "id": p["id"],
                    "plan_code": p["plan_code"],
                    "name": p["name"],
                    "description": p["description"],
                    "status": p["status"],
                    "is_public": p["is_public"],
                    "tier_type": p["tier_type"],
                    "version": p["version"],
                    "billing_interval": p["billing_interval"],
                    "currency": p["currency"],
                    "price_cents": p["price_cents"],
                    "metadata_json": p["metadata_json"],
                    "published_at": p["published_at"],
                }
            ],
        )
        ent_rows = []
        for key, vtype, vbool, vint, is_unlim in p["entitlements"]:
            ent_rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "plan_id": p["id"],
                    "entitlement_key": key,
                    "value_type": vtype,
                    "value_bool": vbool,
                    "value_int": vint,
                    "is_unlimited": is_unlim,
                }
            )
        op.bulk_insert(ent_table, ent_rows)

    # 8. Assign existing tenants to appropriate default subscription
    # Non-destructive: read tenants from gid.tenants and map basic -> free, pro -> creator, platinum -> pro
    sub_table = sa.table(
        'tenant_subscriptions',
        sa.column('id', sa.String),
        sa.column('tenant_id', sa.String),
        sa.column('plan_id', sa.String),
        sa.column('status', sa.String),
        sa.column('current_period_start', sa.DateTime),
        schema='gid',
    )
    tenants_res = bind.execute(sa.text("SELECT id, tier FROM gid.tenants")).fetchall()
    sub_rows = []
    for t_id, t_tier in tenants_res:
        plan_id = "plan-free-v1"
        if t_tier == "pro":
            plan_id = "plan-creator-monthly-v1"
        elif t_tier in ("platinum", "enterprise"):
            plan_id = "plan-pro-monthly-v1"
        sub_rows.append(
            {
                "id": str(uuid.uuid4()),
                "tenant_id": t_id,
                "plan_id": plan_id,
                "status": "active",
                "current_period_start": now_utc,
            }
        )
    if sub_rows:
        op.bulk_insert(sub_table, sub_rows)


def downgrade() -> None:
    op.drop_table('commercial_audit_logs', schema='gid')
    op.drop_table('tenant_entitlement_overrides', schema='gid')
    op.drop_table('tenant_subscriptions', schema='gid')
    op.drop_table('commercial_plan_entitlements', schema='gid')
    op.drop_table('commercial_plans', schema='gid')
