"""durable_quota_reservations_and_enforcement

Revision ID: b2c3d4e5f6a7
Revises: a1f2b3c4d5e6
Create Date: 2026-10-30 02:20:00.000000

"""
from typing import Sequence, Union
import json
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1f2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    schema = 'gid' if is_pg else None
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Create table gid.commercial_reservations
    op.create_table(
        'commercial_reservations',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey(f"{schema + '.' if schema else ''}tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column('operation_type', sa.String(length=32), nullable=False),
        sa.Column('resource_id', sa.String(length=64), nullable=True),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('entitlement_key', sa.String(length=64), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='reserved'),
        sa.Column('estimated_bytes', sa.BigInteger(), nullable=True),
        sa.Column('estimated_duration_seconds', sa.Float(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('metadata_json', postgresql.JSON(astext_type=sa.Text()) if is_pg else sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('finalized_at', sa.DateTime(), nullable=True),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'idempotency_key', name='uq_commercial_reservations_tenant_idempotency'),
        sa.CheckConstraint(
            "status IN ('reserved', 'active', 'finalized', 'released', 'expired')",
            name='ck_commercial_reservations_status'
        ),
        schema=schema,
    )

    op.create_index(
        'ix_commercial_reservations_tenant_status',
        'commercial_reservations',
        ['tenant_id', 'status'],
        schema=schema,
    )
    op.create_index(
        'ix_commercial_reservations_resource',
        'commercial_reservations',
        ['resource_id'],
        schema=schema,
    )
    op.create_index(
        'ix_commercial_reservations_lease',
        'commercial_reservations',
        ['status', 'lease_expires_at'],
        schema=schema,
    )

    # 2. Grant DML permissions on commercial_reservations to gid_app
    if is_pg:
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON gid.commercial_reservations TO gid_app")

    # 3. Alter commercial_plans.price_cents to be nullable (distinguish unset draft pricing from $0 free pricing)
    if is_pg:
        op.alter_column('commercial_plans', 'price_cents', nullable=True, schema='gid')
        # Update draft v2 plans to price_cents = NULL
        op.execute(
            sa.text(
                "UPDATE gid.commercial_plans "
                "SET price_cents = NULL "
                "WHERE id IN ('plan-creator-monthly-v2', 'plan-pro-monthly-v2') AND status = 'draft'"
            )
        )
    else:
        with op.batch_alter_table('commercial_plans') as batch_op:
            batch_op.alter_column('price_cents', nullable=True)

    # 4. Seed commercial:enforcement in app_settings (default: disabled / advisory)
    setting_val = json.dumps({
        "mode": "disabled",
        "tenant_allowlist": [],
        "updated_at": now_utc.isoformat(),
        "reason": "STEP 04-A2: Initial default rollout state (enforcement disabled / advisory only)"
    })

    if is_pg:
        op.execute(
            sa.text(
                "INSERT INTO gid.app_settings (key, value, updated_at) "
                "VALUES ('commercial:enforcement', CAST(:val AS jsonb), :now) "
                "ON CONFLICT (key) DO UPDATE SET value = CAST(:val AS jsonb), updated_at = :now"
            ).bindparams(val=setting_val, now=now_utc)
        )
    else:
        app_settings = sa.table(
            'app_settings',
            sa.column('key', sa.String),
            sa.column('value', sa.JSON),
            sa.column('updated_at', sa.DateTime),
        )
        op.bulk_insert(
            app_settings,
            [{
                'key': 'commercial:enforcement',
                'value': json.loads(setting_val),
                'updated_at': now_utc,
            }]
        )

    # 5. Audit log
    audit_id = str(uuid.uuid4())
    if is_pg:
        op.execute(
            sa.text(
                "INSERT INTO gid.commercial_audit_logs (id, tenant_id, action, actor_user_id, actor_role, reason, details_json, created_at) "
                "VALUES (:id, NULL, 'commercial_reservations_and_enforcement_initialized', NULL, 'system_migration', "
                "'Created commercial_reservations table, supported nullable draft pricing, seeded enforcement mode as disabled', "
                "json_build_object('migration', 'b2c3d4e5f6a7', 'mode', 'disabled'), :now)"
            ).bindparams(id=audit_id, now=now_utc)
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    schema = 'gid' if is_pg else None

    # 1. Remove enforcement setting
    if is_pg:
        op.execute("DELETE FROM gid.app_settings WHERE key = 'commercial:enforcement'")
        # Revert draft v2 prices to 0 before setting NOT NULL
        op.execute("UPDATE gid.commercial_plans SET price_cents = 0 WHERE price_cents IS NULL")
        op.alter_column('commercial_plans', 'price_cents', nullable=False, schema='gid')
    else:
        op.execute("DELETE FROM app_settings WHERE key = 'commercial:enforcement'")
        with op.batch_alter_table('commercial_plans') as batch_op:
            batch_op.alter_column('price_cents', nullable=False)

    # 2. Drop table commercial_reservations
    op.drop_index('ix_commercial_reservations_lease', table_name='commercial_reservations', schema=schema)
    op.drop_index('ix_commercial_reservations_resource', table_name='commercial_reservations', schema=schema)
    op.drop_index('ix_commercial_reservations_tenant_status', table_name='commercial_reservations', schema=schema)
    op.drop_table('commercial_reservations', schema=schema)
