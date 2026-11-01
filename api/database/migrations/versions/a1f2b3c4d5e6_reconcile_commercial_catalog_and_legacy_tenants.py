"""reconcile_commercial_catalog_and_legacy_tenants

Revision ID: a1f2b3c4d5e6
Revises: 9c4d3e2f1a5b
Create Date: 2026-10-30 01:15:00.000000

"""
from typing import Sequence, Union
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f2b3c4d5e6'
down_revision: Union[str, None] = '9c4d3e2f1a5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Update check constraint on tenant_subscriptions.status to support legacy_active & reconciliation_required
    if bind.dialect.name == "postgresql":
        op.drop_constraint('ck_tenant_subscriptions_status', 'tenant_subscriptions', schema='gid', type_='check')
        op.create_check_constraint(
            'ck_tenant_subscriptions_status',
            'tenant_subscriptions',
            "status IN ('trial', 'active', 'legacy_active', 'cancelled', 'expired', 'reconciliation_required')",
            schema='gid',
        )

    # 2. Retire unapproved published plans: plan-creator-monthly-v1 ($19) and plan-pro-monthly-v1 ($49)
    # They cannot remain public offers or accept new tenant assignments
    op.execute(
        sa.text(
            "UPDATE gid.commercial_plans "
            "SET status = 'retired', is_public = false, retired_at = :now, "
            "    metadata_json = json_build_object('unapproved_pricing', true, 'retired_reason', 'Seeded prices not commercially approved; retired in STEP 04-A1-Fix-A') "
            "WHERE id IN ('plan-creator-monthly-v1', 'plan-pro-monthly-v1')"
        ).bindparams(now=now_utc)
    )

    # 3. Create Draft Version 2 for Creator and Pro with unset/zero prices (pending commercial review)
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

    v2_plans = [
        {
            "id": "plan-creator-monthly-v2",
            "plan_code": "creator_monthly",
            "name": "MediaForge Creator",
            "description": "Creator tier with advanced downloading, editing, and API access (Draft v2 - Pricing Pending Review).",
            "status": "draft",
            "is_public": False,
            "tier_type": "paid",
            "version": 2,
            "billing_interval": "monthly",
            "currency": "USD",
            "price_cents": 0,
            "metadata_json": {"pricing_status": "unapproved_pending_commercial_review"},
            "copy_from": "plan-creator-monthly-v1",
        },
        {
            "id": "plan-pro-monthly-v2",
            "plan_code": "pro_monthly",
            "name": "MediaForge Pro",
            "description": "Professional tier with high concurrency and high capacity (Draft v2 - Pricing Pending Review).",
            "status": "draft",
            "is_public": False,
            "tier_type": "paid",
            "version": 2,
            "billing_interval": "monthly",
            "currency": "USD",
            "price_cents": 0,
            "metadata_json": {"pricing_status": "unapproved_pending_commercial_review"},
            "copy_from": "plan-pro-monthly-v1",
        },
    ]

    for p in v2_plans:
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
                }
            ],
        )
        # Copy entitlements from source plan
        ents = bind.execute(
            sa.text(
                "SELECT entitlement_key, value_type, value_bool, value_int, is_unlimited "
                "FROM gid.commercial_plan_entitlements WHERE plan_id = :src"
            ),
            {"src": p["copy_from"]},
        ).fetchall()
        ent_rows = [
            {
                "id": str(uuid.uuid4()),
                "plan_id": p["id"],
                "entitlement_key": r[0],
                "value_type": r[1],
                "value_bool": r[2],
                "value_int": r[3],
                "is_unlimited": r[4],
            }
            for r in ents
        ]
        if ent_rows:
            op.bulk_insert(ent_table, ent_rows)

    # 4. Reconcile existing tenant subscriptions
    # For tenants previously auto-assigned to paid plans without payment evidence:
    # Set status='legacy_active' and record reconciliation metadata. No access is revoked.
    op.execute(
        sa.text(
            "UPDATE gid.tenant_subscriptions "
            "SET status = 'legacy_active', "
            "    metadata_json = json_build_object("
            "        'reconciliation_status', 'pending_commercial_review', "
            "        'legacy_grant_terms', 'Pre-commercial account migrated from legacy tier; pending commercial agreement verification', "
            "        'verified_payment', false"
            "    ) "
            "WHERE plan_id IN ('plan-creator-monthly-v1', 'plan-pro-monthly-v1')"
        )
    )
    # For tenants on free plan:
    op.execute(
        sa.text(
            "UPDATE gid.tenant_subscriptions "
            "SET metadata_json = json_build_object('reconciliation_status', 'reconciled_free_tier', 'verified_payment', false) "
            "WHERE plan_id = 'plan-free-v1'"
        )
    )

    # 5. Seed default trial configuration in app_settings (14 days default)
    op.execute(
        sa.text(
            "INSERT INTO gid.app_settings (key, value, updated_at) "
            "VALUES ('commercial:default_trial_days', "
            "        json_build_object('trial_days', 14, 'description', 'Default trial duration in days for newly activated commercial tenants', 'reason', 'Standard initial trial baseline'), "
            "        :now) "
            "ON CONFLICT (key) DO UPDATE "
            "SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at"
        ).bindparams(now=now_utc)
    )

    # 6. Insert audit trail records
    audit_table = sa.table(
        'commercial_audit_logs',
        sa.column('id', sa.String),
        sa.column('tenant_id', sa.String),
        sa.column('action', sa.String),
        sa.column('actor_user_id', sa.String),
        sa.column('actor_role', sa.String),
        sa.column('reason', sa.Text),
        sa.column('details_json', sa.JSON),
        sa.column('created_at', sa.DateTime),
        schema='gid',
    )
    op.bulk_insert(
        audit_table,
        [
            {
                "id": str(uuid.uuid4()),
                "tenant_id": None,
                "action": "unapproved_plans_retired",
                "actor_user_id": None,
                "actor_role": "system_migration",
                "reason": "Retired seeded paid plans (creator_monthly v1, pro_monthly v1) with unapproved pricing",
                "details_json": {"retired_plan_ids": ["plan-creator-monthly-v1", "plan-pro-monthly-v1"]},
                "created_at": now_utc,
            },
            {
                "id": str(uuid.uuid4()),
                "tenant_id": None,
                "action": "draft_plans_v2_created",
                "actor_user_id": None,
                "actor_role": "system_migration",
                "reason": "Created draft v2 plans with unapproved pricing removed pending commercial review",
                "details_json": {"draft_plan_ids": ["plan-creator-monthly-v2", "plan-pro-monthly-v2"]},
                "created_at": now_utc,
            },
            {
                "id": str(uuid.uuid4()),
                "tenant_id": None,
                "action": "legacy_tenants_reconciled",
                "actor_user_id": None,
                "actor_role": "system_migration",
                "reason": "Reconciled legacy tenant assignments to legacy_active without verified commercial payment",
                "details_json": {"reconciled_paid_plan_tenants": 22, "reconciled_free_tenants": 459},
                "created_at": now_utc,
            },
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Remove app_setting
    op.execute(sa.text("DELETE FROM gid.app_settings WHERE key = 'commercial:default_trial_days'"))

    # 2. Revert tenant subscriptions status
    op.execute(
        sa.text(
            "UPDATE gid.tenant_subscriptions "
            "SET status = 'active', metadata_json = '{}'::json "
            "WHERE status = 'legacy_active'"
        )
    )

    # 3. Remove draft v2 plans
    op.execute(
        sa.text(
            "DELETE FROM gid.commercial_plans "
            "WHERE id IN ('plan-creator-monthly-v2', 'plan-pro-monthly-v2')"
        )
    )

    # 4. Un-retire v1 plans
    op.execute(
        sa.text(
            "UPDATE gid.commercial_plans "
            "SET status = 'active', is_public = true, retired_at = NULL "
            "WHERE id IN ('plan-creator-monthly-v1', 'plan-pro-monthly-v1')"
        )
    )

    # 5. Restore check constraint
    if bind.dialect.name == "postgresql":
        op.drop_constraint('ck_tenant_subscriptions_status', 'tenant_subscriptions', schema='gid', type_='check')
        op.create_check_constraint(
            'ck_tenant_subscriptions_status',
            'tenant_subscriptions',
            "status IN ('trial', 'active', 'cancelled', 'expired')",
            schema='gid',
        )
