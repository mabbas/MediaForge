"""subscription_lifecycle_and_scheduled_transitions

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-30 06:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    schema = 'gid' if is_pg else None

    # 1. Update check constraint on tenant_subscriptions.status to include lifecycle states
    if is_pg:
        op.drop_constraint('ck_tenant_subscriptions_status', 'tenant_subscriptions', schema=schema, type_='check')
        op.create_check_constraint(
            'ck_tenant_subscriptions_status',
            'tenant_subscriptions',
            "status IN ('trial', 'active', 'grace_period', 'scheduled_cancellation', 'cancelled', 'expired', 'suspended', 'legacy_active', 'reconciliation_required', 'pending_activation')",
            schema=schema,
        )

    # 2. Create table gid.scheduled_subscription_transitions
    op.create_table(
        'scheduled_subscription_transitions',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey(f"{schema + '.' if schema else ''}tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column('target_plan_id', sa.String(length=36), sa.ForeignKey(f"{schema + '.' if schema else ''}commercial_plans.id", ondelete="RESTRICT"), nullable=True),
        sa.Column('transition_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending'),
        sa.Column('scheduled_at', sa.DateTime(), nullable=False),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('actor_user_id', sa.String(length=36), sa.ForeignKey(f"{schema + '.' if schema else ''}users.id", ondelete="SET NULL"), nullable=True),
        sa.Column('actor_role', sa.String(length=64), nullable=False, server_default='system_admin'),
        sa.Column('metadata_json', postgresql.JSON(astext_type=sa.Text()) if is_pg else sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "transition_type IN ('upgrade', 'downgrade', 'cancel', 'renewal')",
            name='ck_sched_trans_type'
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'executed', 'cancelled', 'failed')",
            name='ck_sched_trans_status'
        ),
        schema=schema,
    )

    op.create_index(
        'ix_sched_transitions_tenant_status',
        'scheduled_subscription_transitions',
        ['tenant_id', 'status'],
        schema=schema,
    )
    op.create_index(
        'ix_sched_transitions_due',
        'scheduled_subscription_transitions',
        ['status', 'scheduled_at'],
        schema=schema,
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    schema = 'gid' if is_pg else None

    # 1. Drop scheduled_subscription_transitions table and indexes
    op.drop_index('ix_sched_transitions_due', table_name='scheduled_subscription_transitions', schema=schema)
    op.drop_index('ix_sched_transitions_tenant_status', table_name='scheduled_subscription_transitions', schema=schema)
    op.drop_table('scheduled_subscription_transitions', schema=schema)

    # 2. Revert check constraint on tenant_subscriptions.status
    if is_pg:
        op.drop_constraint('ck_tenant_subscriptions_status', 'tenant_subscriptions', schema=schema, type_='check')
        op.create_check_constraint(
            'ck_tenant_subscriptions_status',
            'tenant_subscriptions',
            "status IN ('trial', 'active', 'legacy_active', 'cancelled', 'expired', 'reconciliation_required')",
            schema=schema,
        )
