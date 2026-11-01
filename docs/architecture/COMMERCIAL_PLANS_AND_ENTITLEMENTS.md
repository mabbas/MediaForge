# Commercial Plans, Tenant Entitlements, Trial Policies and Accurate Usage Metering Architecture

## Document Metadata
- **System**: MediaForge Commercial Platform
- **Milestones**: STEP 04-A1, STEP 04-A1-Fix-A & STEP 04-A2 — Commercial Catalog Safety, Tenant Reconciliation, Default Trial Configuration, Usage Metering, and Transactional Quota Enforcement
- **Database Schema**: PostgreSQL 16 (`grabitdown`, schema `gid`)
- **Alembic Revision**: `b2c3d4e5f6a7` (Revises `a1f2b3c4d5e6`, `9c4d3e2f1a5b`, `8b3c2d1e0f4a`)
- **Application Boundary**: FastAPI Backend + Electron Desktop + FFmpeg Media Worker

---

## 1. Executive Summary & Business Objective

MediaForge is transitioning toward a commercial multi-tenant SaaS offering video downloading, clip extraction, media merging, subtitle retrieval, and future AI transcript generation. Commercial access is structured through versioned subscription plans, typed entitlements, tenant-specific overrides, trial lifecycles, and billing-grade usage metering.

In **STEP 04-A1** and **STEP 04-A1-Fix-A**, the architectural foundation is established with **STRICTLY ADVISORY** quota evaluations:
- **Zero hard customer blocking**: All limit checks return advisory evaluations (`allowed=true/false` with reason codes) without interrupting existing customer workflows.
- **Zero unapproved commercial pricing**: All seeded plans with unapproved prices were safely retired; new plan versions start in `draft` with zero or unset prices pending executive commercial review.
- **Zero false billing claims**: Historical legacy tenants migrated from paid tiers are assigned `legacy_active` with `pending_commercial_review` to prevent premature claims of paid commercial status while preserving uninterrupted service.
- **Persistent default trial governance**: Default trial duration is stored dynamically in PostgreSQL `gid.app_settings` (`commercial:default_trial_days`), adjustable between 0 and 365 days by system administrators with mandatory audit logging.
- **Zero payment gateway integration**: No external billing charges, mock checkout flows, or premature integrations with Stripe, Paddle, or LemonSqueezy.
- **Zero destructive schema operations**: Historical usage events, existing tenants, and persistent media artifacts remain completely intact.

---

## 2. Current Usage and Metering Audit

The system maintains an append-only event ledger in `gid.usage_events` and advisory counters in `gid.tenant_quotas`. An audit of the codebase established the following authoritative state:

| Commercial Metric | Implementation Path | Authority Mechanism | Idempotency Key | Metering State in STEP 04-A1-Fix-A |
|---|---|---|---|---|
| **Media Downloads** | `api/services/engine_sync.py` | Authoritative disk file stat | `download:{job_id}` | Fully metered & idempotent |
| **Download Bytes** | `api/services/engine_sync.py` | Exact completed file byte size | `download:{job_id}` | Fully metered & verified |
| **Clip Extraction Duration** | `api/workers/media_worker.py` | In-transaction transcode seconds | `clip:{artifact_id}` | Fully metered & atomic |
| **Clip Extraction Output** | `api/workers/media_worker.py` | In-transaction output file size | `clip:{artifact_id}` | Fully metered & atomic |
| **Clip Merge Duration** | `api/workers/media_worker.py` | In-transaction transcode seconds | `merge:{artifact_id}` | Fully metered & atomic |
| **Clip Merge Output** | `api/workers/media_worker.py` | In-transaction output file size | `merge:{artifact_id}` | Fully metered & atomic |
| **Persistent Storage** | `api/services/entitlement_service.py` | Aggregated physical file size | N/A (State query) | Authoritative disk stat sum (downloads + artifacts) |
| **Concurrent Jobs** | `api/services/entitlement_service.py` | Authoritative single-slot capacity formula | N/A (State query) | Admitted outstanding operations (`reserved` + `active` + `legacy_unreserved`) with zero double-counting |
| **AI Transcription Minutes** | `api/services/usage_service.py` | Service helper ready | `transcription:{id}` | Metered when AI STT invoked |
| **Subtitle Extractions** | `api/services/usage_service.py` | Service helper ready | `subtitle:{id}` | Separated from AI transcription; caption extractions |

### Metering Integrity Invariants:
1. **Worker Crash & Retry Safety**: A worker task retrying attempt 1 -> attempt 2 does NOT duplicate usage. Only successful physical publication in `complete_job_and_artifact_atomic` commits a `UsageEventDB` record.
2. **Cancelled / Failed Job Isolation**: Work items cancelled by users or terminating with worker exceptions call `fail_job_item_atomic` and commit zero successful-output usage.
3. **Idempotency Conflict Rejection**: Replaying identical parameters is safely deduplicated; attempting to alter quantities under an existing idempotency key raises `IdempotencyConflictError`.

---

## 3. Commercial Plan Catalog Structure & Governance

Commercial plans are modeled dynamically in `gid.commercial_plans` rather than hardcoded in Python dictionaries or application configuration files:

```sql
CREATE TABLE gid.commercial_plans (
    id VARCHAR(36) PRIMARY KEY,
    plan_code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',    -- 'draft', 'active', 'retired'
    is_public BOOLEAN NOT NULL DEFAULT true,
    tier_type VARCHAR(32) NOT NULL DEFAULT 'paid',  -- 'free', 'trial', 'paid'
    version INTEGER NOT NULL DEFAULT 1,
    billing_interval VARCHAR(32) NOT NULL DEFAULT 'monthly',
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    price_cents INTEGER NOT NULL DEFAULT 0,
    metadata_json JSON,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP,
    retired_at TIMESTAMP,
    CONSTRAINT uq_commercial_plans_code_version UNIQUE (plan_code, version),
    CONSTRAINT ck_commercial_plans_status CHECK (status IN ('draft', 'active', 'retired')),
    CONSTRAINT ck_commercial_plans_tier_type CHECK (tier_type IN ('free', 'trial', 'paid')),
    CONSTRAINT ck_commercial_plans_version CHECK (version >= 1)
);
```

### Plan Lifecycle:
1. **Draft (`draft`)**: Plan is being authored or modified by system administrators. New versions of plans always start in draft. Prices may be unset ($0) and marked `pricing_status: "unapproved_pending_commercial_review"`. **Draft plans cannot be assigned to customer tenants.**
2. **Active (`active`)**: Plan is approved and published (`published_at` recorded). Entitlements and pricing for this version become permanently immutable. Available for tenant assignment.
3. **Retired (`retired`)**: Plan is deprecated or retired (`retired_at` recorded). Existing subscriber agreements are permanently grandfathered and honored, but no new tenant assignments may select this plan.

### Pricing Approval and Retirement of Unapproved Plans:
- In STEP 04-A1, initial seed data contained tentative price points ($19 for Creator, $49 for Pro) that had not received formal executive/commercial approval.
- In **STEP 04-A1-Fix-A (Migration `a1f2b3c4d5e6`)**, these plans (`plan-creator-monthly-v1` and `plan-pro-monthly-v1`) were retired in place:
  - `status = 'retired'`
  - `is_public = false`
  - `retired_at = CURRENT_TIMESTAMP`
  - Annotated in `metadata_json` with `unapproved_pricing: true` and retirement justifications.
- In accordance with plan immutability, published plans are never modified in place. Instead, draft version 2 plans (`plan-creator-monthly-v2` and `plan-pro-monthly-v2`) were created with:
  - `version = 2`
  - `status = 'draft'`
  - `is_public = false`
  - `price_cents = 0`
  - `metadata_json = {"pricing_status": "unapproved_pending_commercial_review"}`
  - Entitlements accurately cloned from v1.
- Future commercial plans must be introduced via draft creation (`POST /api/v1/admin/plans`), reviewed by business leadership, and published (`POST /api/v1/admin/plans/{id}/publish`) only after commercial signoff.

---

## 4. Historical Tenant Assignment Policy & Legacy Reconciliation

### Tenant Backfill Reconciliation:
During STEP 04-A1, existing customer tenants were automatically backfilled into active commercial subscriptions based on their legacy `tier` string. This created two risks:
1. 22 existing tenants were marked as `active` on unapproved paid tiers without verified commercial payment records.
2. Immediate downgrading would cause customer disruption, while claiming active paid status violated billing data integrity.

### Reconciliation Strategy (`a1f2b3c4d5e6`):
1. **Allowed Subscription Statuses**: The check constraint `ck_tenant_subscriptions_status` was expanded:
   ```sql
   CONSTRAINT ck_tenant_subscriptions_status CHECK (
       status IN ('trial', 'active', 'legacy_active', 'cancelled', 'expired', 'reconciliation_required')
   )
   ```
2. **Paid-Tier Reconciliation**: The 22 tenants previously assigned to `creator_monthly` (9 tenants) and `pro_monthly` (13 tenants) were migrated to `legacy_active` status:
   - `status = 'legacy_active'`
   - `metadata_json` updated with:
     ```json
     {
       "reconciliation_status": "pending_commercial_review",
       "legacy_grant_terms": "Pre-commercial account migrated from legacy tier",
       "verified_payment": false
     }
     ```
   - **Grandfathering Guarantee**: Zero customer disruption. Tenants retain full technical allowances of their tier during the advisory phase.
3. **Free-Tier Reconciliation**: The 459 tenants on `plan-free-v1` remain in `active` status, annotated with:
   ```json
   {
     "reconciliation_status": "reconciled_free_tier"
   }
   ```

---

## 5. Persistent Default Trial Configuration Architecture

Rather than hardcoding trial durations or relying on transient application environment variables, the system manages default trial durations dynamically in PostgreSQL:

- **Storage**: Stored in `gid.app_settings` under key `commercial:default_trial_days`.
- **Allowed Range**: 0 to 365 integer days.
- **Default Value**: 14 days.
- **Registration Integration**:
  - `RegistrationService.verify_email_token` fetches `get_system_default_trial_days(session)`.
  - If `trial_days > 0`, the newly verified tenant receives an initial subscription with `status='trial'`, `trial_started_at=now`, and `trial_expires_at=now + trial_days`.
  - If `trial_days == 0`, the tenant receives an initial subscription with `status='expired'`, `trial_started_at=now`, and `trial_expires_at=now` (zero-day trial immediately expired, requiring manual upgrade or admin extension).
- **Administrative Endpoints**:
  - `GET /api/v1/admin/settings/trial`: Read persistent trial setting.
  - `PUT /api/v1/admin/settings/trial`: Update persistent trial setting, requiring integer `trial_days` (0–365) and mandatory audit `reason`. Automatically commits an audit record to `gid.commercial_audit_logs`.
- **Existing Trial Stability**: Mutating the system default trial duration affects only *future* tenant registrations. Existing tenant subscriptions and active trials retain their authoritative timestamps.

---

## 6. Entitlement Types and Explicit Units

Entitlements are explicitly typed in `gid.commercial_plan_entitlements` to eliminate unit ambiguity:

| Entitlement Key | Value Type | Unit | Default Free Tier | Default Creator Tier | Default Pro Tier |
|---|---|---|---|---|---|
| `desktop_access` | `boolean` | boolean | `true` | `true` | `true` |
| `api_access` | `boolean` | boolean | `false` | `true` | `true` |
| `max_active_api_keys` | `count` | integer | `0` | `5` | `25` |
| `monthly_download_count` | `count` | integer | `50` | `500` | **Unlimited** |
| `monthly_download_bytes` | `bytes` | bytes | `5,368,709,120` (5 GB) | `53,687,091,200` (50 GB) | **Unlimited** |
| `monthly_clip_count` | `count` | integer | `20` | `200` | **Unlimited** |
| `monthly_clip_seconds` | `seconds` | transcode s | `600` (10 min) | `7,200` (2 hours) | **Unlimited** |
| `monthly_merge_count` | `count` | integer | `10` | `50` | **Unlimited** |
| `max_concurrent_jobs` | `concurrency` | active jobs | `1` | `2` | `5` |
| `storage_bytes` | `bytes` | bytes | `5,368,709,120` (5 GB) | `53,687,091,200` (50 GB) | `214,748,364,800` (200 GB) |
| `artifact_retention_days`| `count` | days | `7` | `30` | `90` |
| `ai_transcription_access`| `boolean` | boolean | `false` | `true` | `true` |
| `max_users` | `count` | integer | `1` | `5` | `20` |

---

## 7. Storage Accounting & Concurrency Measurement Rules

### Exact Physical Storage Accounting:
1. **Physical Files on Disk**: Quota evaluations measure actual physical byte footprints rather than loose estimates or arbitrary allocations.
2. **Storage Aggregation**:
   - `artifact_storage_bytes`: Sum of `file_size_bytes` of all `ready` records in `gid.media_artifacts` owned by the tenant.
   - `download_storage_bytes`: Sum of `file_size_bytes` of all `completed` records in `gid.download_jobs` owned by the tenant.
   - `storage_bytes`: Authoritative total = `artifact_storage_bytes + download_storage_bytes`.
3. **Artifact Cleanup Reconciliation**: When an artifact is deleted or pruned by retention policies, the physical file is removed and the database record is purged, immediately freeing tenant storage quota.

### Active Concurrency Breakdown:
1. **Definition**: Concurrency represents background processing load on the media engine. It is NOT HTTP connection concurrency, API request rate, or worker thread count.
2. **Calculation**:
   - `queued_jobs`: Work items in `gid.media_work_items` where `tenant_id == :tenant_id` and `status = 'queued'`.
   - `running_jobs`: Work items in `gid.media_work_items` where `tenant_id == :tenant_id` and `status = 'running'`.
   - `max_concurrent_jobs` (active consumption): `queued_jobs + running_jobs`.
   - `global_running_jobs`: Sum of all running jobs across all tenants on the cluster (for worker pool capacity planning).
3. **Immediate Release**: As soon as a work item transitions to `succeeded`, `failed`, or `cancelled`, it is excluded from active concurrency.

---

## 8. Tenant Plan Assignment & Precedence Hierarchy

Tenant entitlements follow a strict three-tier precedence hierarchy:

```
┌────────────────────────────────────────────────────────┐
│  1. Tenant-Specific Authorized Override                │
│     (gid.tenant_entitlement_overrides - active & valid)│
└──────────────────────────┬─────────────────────────────┘
                           │ (if no override exists)
                           ▼
┌────────────────────────────────────────────────────────┐
│  2. Assigned Published Plan Version                    │
│     (gid.tenant_subscriptions -> commercial_plans)     │
└──────────────────────────┬─────────────────────────────┘
                           │ (if tenant unassigned)
                           ▼
┌────────────────────────────────────────────────────────┐
│  3. Documented System Default Tier                     │
│     (Hardened free-tier baseline allowances)           │
└────────────────────────────────────────────────────────┘
```

### System-Admin Trust Boundary:
- Commercial plan catalog management (`POST /admin/plans`, `POST /admin/plans/{id}/publish`), tenant plan assignments, entitlement overrides, trial extensions, and default trial settings operate on cross-tenant commercial contracts.
- **Strict Isolation**: These endpoints require system administrator credentials (`CurrentUser.is_system_admin == True` or `CurrentUser.is_system_owner == True`). Ordinary tenant administrators (`role="admin"`) and members (`role="member"`) are strictly forbidden (`403 Forbidden`).
- **Audit Requirement**: Every administrative mutation requires a mandatory `reason` string and writes an immutable record to `gid.commercial_audit_logs`.

---

## 9. Requirements for Future Atomic Reservations & Hard Enforcement

Currently, all quota evaluations are **strictly advisory** (`is_advisory: True`, `allowed: True` for all operations).

### Explicit Prerequisites Before Hard Enforcement (STEP 04-A2):
1. **Durable Database-Level Concurrency Reservations**:
   - Request-time admission must execute a transaction with `SELECT ... FOR UPDATE` on a tenant quota state row or atomically increment a reservation counter in PostgreSQL/Redis.
   - If concurrency limit is reached, reject with `429 Too Many Requests` (`CONCURRENCY_EXCEEDED`).
   - Leases must have a heartbeat and automatic expiration (e.g. 15 minutes) to prevent leaked reservations if workers crash unexpectedly.
2. **Two-Phase Storage Pre-allocation**:
   - Large downloads and transcode operations must verify remaining `storage_bytes` against projected size before enqueuing.
3. **Verified Billing Integration**:
   - Integration with an approved payment processor (e.g. Stripe Customer Portal, webhook handler for invoice payment, subscription cancellation, and renewal).
4. **Grace Period & Notification Tooling**:
   - Customer warning banners when quotas reach 80% and 95%.
   - Configurable grace period (e.g. 3–5 days) before hard-blocking existing paying subscribers after payment failure.
5. **Customer Support Emergency Overrides**:
   - Operational tooling for support teams to instantly apply 24-hour or 7-day quota overrides to remediate false-positive blocks.

---

## 10. Desktop Visibility & UX Integration

The Electron desktop application exposes commercial plan status in `desktop/src/views/SettingsView.tsx`:
- Current plan name and version badge.
- Subscription status badge supporting `Active`, `Trial`, `Expired`, and `Legacy Active` (blue badge for grandfathered accounts).
- If in trial: Days remaining and expiration date.
- Real-time quota meters with visual progress bars:
  - Monthly Downloads (`X / Y` or `Unlimited`).
  - Download Bandwidth (`X MB / Y GB` or `Unlimited`).
  - Persistent Storage (`X MB / Y GB` or `Unlimited`).
  - Active Concurrent Jobs (`X / Y concurrent`).
- Clear advisory notice: *"Usage limits are currently advisory for testing."*
- Prominent quota exceeded banner in SettingsView when any limit check reports `is_exceeded = true`, clearly displaying metric details and automatic period reset date.
- Zero fake checkout flows, zero unapproved pricing displays, zero simulated payment buttons.

---

## 11. Database Migration History

| Revision | Revises | Purpose | Status |
|---|---|---|---|
| `8b3c2d1e0f4a` | `7a1b2c3d4e5f` | Initial multi-tenant schema & media isolation | Applied |
| `9c4d3e2f1a5b` | `8b3c2d1e0f4a` | Commercial plan catalog, subscriptions, entitlements, audit logs | Applied |
| `a1f2b3c4d5e6` | `9c4d3e2f1a5b` | Reconcile catalog safety, retire unapproved pricing, legacy tenant reconciliation (`legacy_active`), seed persistent default trial setting | Applied |
| `b2c3d4e5f6a7` | `a1f2b3c4d5e6` | Durable quota reservations (`gid.commercial_reservations`), nullable draft plan pricing, seed `commercial:enforcement` rollout setting | Authoritative Head |

Both upgrade and downgrade paths for `b2c3d4e5f6a7` have been verified on disposable PostgreSQL sandboxes, and the authoritative production database `grabitdown` is cleanly migrated to `b2c3d4e5f6a7`. Detailed transactional enforcement architecture is documented in [COMMERCIAL_QUOTA_ENFORCEMENT.md](file:///d:/Dev/MediaForge/docs/architecture/COMMERCIAL_QUOTA_ENFORCEMENT.md).
