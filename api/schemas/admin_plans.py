"""Pydantic schemas for administrative commercial plan management (STEP 04-A1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class EntitlementInput(BaseModel):
    entitlement_key: str = Field(..., description="Canonical entitlement key")
    value_type: str = Field(..., description="boolean, count, bytes, seconds, concurrency")
    value_bool: Optional[bool] = None
    value_int: Optional[int] = None
    is_unlimited: bool = False


class PlanCreateRequest(BaseModel):
    plan_code: str = Field(..., min_length=2, max_length=64, description="Unique alphanumeric identifier (e.g. creator_monthly)")
    name: str = Field(..., min_length=2, max_length=128)
    description: Optional[str] = None
    is_public: bool = True
    tier_type: str = Field("paid", description="free, trial, paid")
    billing_interval: str = Field("monthly", description="monthly, annual, lifetime")
    currency: str = Field("USD", min_length=3, max_length=3)
    price_cents: Optional[int] = Field(None, ge=0, description="Base pricing metadata in integer cents (e.g. 1900 = $19.00), None for unapproved draft")
    metadata_json: Optional[dict[str, Any]] = None
    entitlements: list[EntitlementInput] = Field(default_factory=list)


class PlanResponse(BaseModel):
    id: str
    plan_code: str
    name: str
    description: Optional[str]
    status: str
    is_public: bool
    tier_type: str
    version: int
    billing_interval: str
    currency: str
    price_cents: Optional[int] = None
    metadata_json: dict[str, Any]
    created_at: str
    updated_at: str
    published_at: Optional[str] = None
    retired_at: Optional[str] = None
    entitlements: dict[str, Any] = Field(default_factory=dict)


class AssignPlanRequest(BaseModel):
    plan_id: str
    status: str = Field("active", description="active, trial")
    trial_days: Optional[int] = Field(None, ge=0, le=365)
    reason: Optional[str] = Field(None, description="Administrative audit justification")


class ExtendTrialRequest(BaseModel):
    additional_days: int = Field(..., gt=0, le=365, description="Days to add to trial duration")
    reason: str = Field(..., min_length=3, description="Mandatory audit justification")


class EntitlementOverrideRequest(BaseModel):
    entitlement_key: str
    value_type: str
    value_bool: Optional[bool] = None
    value_int: Optional[int] = None
    is_unlimited: bool = False
    reason: str = Field(..., min_length=3, description="Mandatory audit justification")
    expires_at: Optional[datetime] = None


class RevokeOverrideRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="Mandatory audit justification")


class TrialSettingResponse(BaseModel):
    trial_days: int
    description: Optional[str] = None
    reason: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by_user_id: Optional[str] = None


class UpdateDefaultTrialRequest(BaseModel):
    trial_days: int = Field(..., ge=0, le=365, description="Default trial duration in days (0-365)")
    reason: str = Field(..., min_length=3, description="Mandatory administrative justification")


class PlanPricingUpdateRequest(BaseModel):
    price_cents: int = Field(..., ge=0, description="Price in minor currency units (cents)")
    pricing_status: str = Field("approved", description="Pricing approval state (e.g. approved)")
    reason: str = Field(..., min_length=3, description="Mandatory audit justification")


class AuditLogResponse(BaseModel):
    id: str
    tenant_id: Optional[str]
    action: str
    actor_user_id: Optional[str]
    actor_role: str
    reason: Optional[str]
    details_json: dict[str, Any]
    created_at: str


class EnforcementSettingResponse(BaseModel):
    mode: str = Field(..., description="'disabled' (advisory), 'shadow' (audit would-block), or 'enforce' (allowlisted test tenants)")
    tenant_allowlist: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    reason: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by_user_id: Optional[str] = None


class UpdateEnforcementSettingRequest(BaseModel):
    mode: str = Field(..., pattern="^(disabled|shadow|enforce)$", description="Rollout mode: disabled, shadow, or enforce")
    tenant_allowlist: list[str] = Field(default_factory=list, description="Explicit test tenants eligible for hard enforcement")
    reason: str = Field(..., min_length=3, description="Mandatory administrative justification")


class ReservationResponse(BaseModel):
    id: str
    tenant_id: str
    operation_type: str
    resource_id: Optional[str] = None
    idempotency_key: str
    entitlement_key: str
    quantity: int
    status: str
    estimated_bytes: Optional[int] = None
    estimated_duration_seconds: Optional[float] = None
    lease_expires_at: Optional[str] = None
    created_at: str
    updated_at: str
    finalized_at: Optional[str] = None
    released_at: Optional[str] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ScheduleTransitionRequest(BaseModel):
    target_plan_id: Optional[str] = Field(None, description="Target commercial plan ID for upgrade/downgrade")
    transition_type: str = Field(..., pattern="^(upgrade|downgrade|cancel|renewal)$", description="Type of transition")
    scheduled_at: Optional[datetime] = Field(None, description="Effective UTC timestamp, or None for next period end")
    reason: str = Field(..., min_length=3, description="Mandatory administrative reason")


class ScheduledTransitionResponse(BaseModel):
    id: str
    tenant_id: str
    target_plan_id: Optional[str] = None
    target_plan_code: Optional[str] = None
    target_plan_name: Optional[str] = None
    transition_type: str
    status: str
    scheduled_at: str
    executed_at: Optional[str] = None
    reason: str
    actor_user_id: Optional[str] = None
    actor_role: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class GracePeriodRequest(BaseModel):
    grace_days: int = Field(..., ge=1, le=90, description="Duration of grace period in days (1-90)")
    reason: str = Field(..., min_length=3, description="Mandatory reason for entering grace period")


class ExitGracePeriodRequest(BaseModel):
    new_status: str = Field("active", pattern="^(active|cancelled|expired)$", description="Target status upon exiting grace")
    reason: str = Field(..., min_length=3, description="Mandatory reason for exiting grace period")


class SuspendSubscriptionRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="Mandatory reason for suspending commercial subscription")


class UnsuspendSubscriptionRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="Mandatory reason for lifting suspension")


class CancelSubscriptionRequest(BaseModel):
    immediate: bool = Field(False, description="True to cancel immediately, False to schedule at period end")
    reason: str = Field(..., min_length=3, description="Mandatory reason for subscription cancellation")


class RenewSubscriptionRequest(BaseModel):
    interval: str = Field("monthly", pattern="^(monthly|annual|lifetime|custom)$", description="Renewal interval")
    custom_days: Optional[int] = Field(None, ge=1, le=365, description="Days to extend if interval is custom")
    payment_verification: str = Field("manual_grant", description="Payment verification status: manual_grant, deferred, external_verified")
    reason: str = Field(..., min_length=3, description="Mandatory reason for administrative renewal")


class PlanChangePreviewResponse(BaseModel):
    tenant_id: str
    current_plan: dict[str, Any]
    target_plan: dict[str, Any]
    transition_type: str
    recommended_effective_at: str
    next_period_end: Optional[str] = None
    entitlement_diff: dict[str, Any]
    pricing_display: str


class QuotationPreviewRequest(BaseModel):
    plan_id: str = Field(..., description="Target commercial plan ID")
    custom_overrides: Optional[dict[str, Any]] = Field(None, description="Proposed tenant entitlement overrides")
    discount_percent: Optional[float] = Field(None, ge=0.0, le=100.0, description="Discount percentage (0-100)")
    notes: Optional[str] = Field(None, description="Quotation annotations")


class QuotationPreviewResponse(BaseModel):
    tenant_id: str
    plan_id: str
    plan_code: str
    plan_name: str
    tier_type: str
    billing_interval: str
    currency: str
    base_price_cents: Optional[int] = None
    discount_percent: Optional[float] = None
    effective_amount_cents: Optional[int] = None
    pricing_display: str
    is_approved_pricing: bool
    effective_entitlements: dict[str, Any]
    notes: Optional[str] = None
    disclaimer: str


class SubscriptionHistoryResponse(BaseModel):
    tenant_id: str
    current_subscription: Optional[dict[str, Any]] = None
    scheduled_transitions: list[ScheduledTransitionResponse] = Field(default_factory=list)
    audit_events: list[AuditLogResponse] = Field(default_factory=list)


class DashboardStatsResponse(BaseModel):
    total_tenants: int
    free_tenants: int
    trial_tenants: int
    paid_tenants: int
    legacy_tenants: int
    suspended_tenants: int
    grace_period_tenants: int
    pending_transitions: int
    active_plans: int
    enforcement: dict[str, Any]
    scheduler: dict[str, Any]


class TenantListItemResponse(BaseModel):
    tenant_id: str
    name: str
    slug: str
    tier: str
    created_at: str
    plan_id: Optional[str] = None
    plan_code: Optional[str] = None
    plan_name: Optional[str] = None
    tier_type: Optional[str] = None
    subscription_status: str
    current_period_start: Optional[str] = None
    current_period_end: Optional[str] = None
    trial_days_remaining: Optional[int] = None
    trial_expires_at: Optional[str] = None
    grace_period_remaining_days: Optional[int] = None
    grace_period_expires_at: Optional[str] = None
    is_suspended: bool = False
    suspension_reason: Optional[str] = None
    payment_verification_state: str = "none"
    has_pending_transition: bool = False


class TenantListResponse(BaseModel):
    items: list[TenantListItemResponse]
    total: int
    limit: int
    offset: int


class ScheduledTransitionListResponse(BaseModel):
    items: list[ScheduledTransitionResponse]
    total: int
    limit: int
    offset: int


class SchedulerStatusResponse(BaseModel):
    enabled: bool
    paused: bool
    rollout_mode: str
    test_tenant_prefixes: list[str] = Field(default_factory=list)
    tenant_allowlist: list[str] = Field(default_factory=list)
    poll_interval_seconds: int
    last_scan_at: Optional[str] = None
    last_executed_count: int = 0
    worker_id: Optional[str] = None
    cluster_status: Optional[str] = None
    active_worker_count: int = 1
    last_successful_scan_at: Optional[str] = None
    last_failed_scan_at: Optional[str] = None
    pending_due_count: int = 0
    total_pending_count: int = 0
    failed_count: int = 0
    updated_at: Optional[str] = None


class PauseSchedulerRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="Mandatory administrative justification for pausing the transition scheduler")


class ResumeSchedulerRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="Mandatory administrative justification for resuming the transition scheduler")


class RetryFailedTransitionsRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="Mandatory administrative justification for resetting failed transitions")


class TriggerSchedulerResponse(BaseModel):
    executed_count: int
    executed_transitions: list[dict[str, Any]]
    message: str
