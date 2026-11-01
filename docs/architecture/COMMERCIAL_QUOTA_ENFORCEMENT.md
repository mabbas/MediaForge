# MediaForge — Transactional Quota Enforcement, Multi-Slot Concurrency, and Reservation Lifecycle Integrity

## Document Metadata
- **System**: MediaForge Commercial Platform
- **Milestones**: STEP 04-A2 & STEP 04-A2-Fix-A — Transactional Quota Enforcement, Concurrency Deduplication, Durable Job Reservations, and Lifecycle Integrity
- **Authoritative Database**: PostgreSQL 16 (`grabitdown`, schema `gid`)
- **Authoritative Alembic Revision**: `b2c3d4e5f6a7` (Revises `a1f2b3c4d5e6`, `9c4d3e2f1a5b`, `8b3c2d1e0f4a`)
- **Key Modules**:
  - `api/services/enforcement_service.py` (Centralized admission engine, tenant locking, reservation state machine, fingerprinting)
  - `api/services/entitlement_service.py` (Authoritative concurrency formula, typed entitlements, audit trails)
  - `api/services/job_service.py` (Atomic clip/merge submission, worker heartbeat lease renewal, terminal state transitions)
  - `api/routers/downloads.py` (Atomic download submission and batch reservation admission)
  - `api/services/engine_sync.py` (Download reservation finalization/release)
  - `api/routers/api_keys.py` (Commercial API access quota enforcement)
  - `api/routers/admin_plans.py` (Rollout mode governance, reservation reconciliation, plan catalog management)

---

## 1. Executive Summary & Core Principles

MediaForge implements **production-grade transactional quota enforcement** designed to prevent oversubscription under high concurrency, eliminate capacity double-counting, protect long-running background jobs, and guarantee immutable audit trails without prematurely disrupting existing users.

### Architectural Guarantees:
1. **Server-Side Rollout Feature Flag**: Managed in `gid.app_settings` (`commercial:enforcement`). Defaults globally to `mode: "disabled"`, `tenant_allowlist: []`.
2. **Three Operational Rollout Modes**:
   - `disabled`: All admission decisions are advisory. No customer requests are blocked.
   - `shadow`: Evaluates limits and records an audit log entry (`action: "commercial_quota_would_block"`) with exact metric breakdowns, but admits the request.
   - `enforce`: Strictly enforces limits ONLY on tenants explicitly enumerated in `tenant_allowlist`. All other tenants remain completely unrestricted.
3. **Zero Disruption to Existing Customers**: Grandfathered accounts (`status == "legacy_active"`) and the 481 existing tenants are never restricted, even if mistakenly placed on the allowlist.
4. **Row-Level Tenant Serialization Locks**: Admission decisions acquire a row-level lock on `gid.tenants` (`SELECT id FROM gid.tenants WHERE id = :tenant_id FOR UPDATE`). This serializes admission per tenant to prevent race conditions while ensuring zero lock contention across distinct tenants.
5. **Exact Concurrency Invariant (Zero Double-Counting)**: Every logical admitted operation occupies exactly ONE outstanding capacity slot across its entire lifecycle. Work items and reservations are never added together.
6. **Request Fingerprinting and Principal Isolation**: Idempotency keys are bound to a deterministic SHA-256 request payload fingerprint and tenant principal. Conflicting payloads or operations return `HTTP 409 Conflict`.
7. **Heartbeat Lease Renewal**: Active workers renew reservation leases on every heartbeat, preventing long-running media transcodes from expiring during execution.
8. **Automated Stale Lease Reconciliation**: Reconciles abandoned reservations without terminating active, heartbeating, or retrying worker jobs.

---

## 2. Authoritative Concurrency Formula & Resource Accounting

### 2.1 The Single-Slot Invariant
In earlier draft designs, outstanding capacity was informally expressed as the sum of active reservations plus queued work items plus running work items. Because an admitted operation has both a reservation and an associated work item, adding them directly would result in **double-counting**.

Under the authoritative accounting model:
$$\text{Admitted Outstanding Operations} = \text{Reserved Operations} + \text{Active Processing Operations} + \text{Legacy Unreserved Operations}$$

Where:
- **`reserved_operations`**: Count of `gid.commercial_reservations` in `status = 'reserved'` (waiting to be claimed by a worker).
- **`active_processing_operations`**: Count of `gid.commercial_reservations` in `status = 'active'` (currently processing on a worker).
- **`legacy_unreserved_operations`**: Compatibility fallback counting queued/running/retry_wait work items and queued/downloading download jobs that have **no** active commercial reservation.
- **`queued_jobs`** & **`running_jobs`**: Monitored as informational work-item status metrics for worker scheduling, but **never** added to reservation counts.
- **Terminal States**: Succeeded (`finalized`), failed (`released`), cancelled (`released`), and expired (`expired`) operations immediately release their slot and consume 0 outstanding capacity.

### 2.2 Supporting SQL Implementation
The authoritative metric calculation in `api/services/entitlement_service.py` is implemented as:

```sql
-- 1. Reservation-backed slots
SELECT count(id) FROM gid.commercial_reservations
WHERE tenant_id = :tenant_id AND status = 'reserved';

SELECT count(id) FROM gid.commercial_reservations
WHERE tenant_id = :tenant_id AND status = 'active';

-- 2. Legacy unreserved fallback (deduplicated by resource_id and idempotency_key)
SELECT count(w.id) FROM gid.media_work_items w
WHERE w.tenant_id = :tenant_id
  AND w.status IN ('queued', 'running', 'retry_wait')
  AND NOT EXISTS (
      SELECT 1 FROM gid.commercial_reservations r
      WHERE r.tenant_id = :tenant_id
        AND r.status IN ('reserved', 'active')
        AND (r.resource_id = w.id OR r.idempotency_key = w.idempotency_key)
  );

SELECT count(d.id) FROM gid.download_jobs d
WHERE d.tenant_id = :tenant_id
  AND d.status IN ('pending', 'queued', 'downloading')
  AND NOT EXISTS (
      SELECT 1 FROM gid.commercial_reservations r
      WHERE r.tenant_id = :tenant_id
        AND r.status IN ('reserved', 'active')
        AND r.resource_id = d.id
  );
```

---

## 3. Reservation State Machine & Lifecycle Transitions

```
              ┌────────────────────────────────────────────────────────┐
              │           acquire_admission_and_reservation            │
              └──────────────────────────┬─────────────────────────────┘
                                         │
                                         ▼
                                  [ RESERVED ]
                                         │
                   ┌─────────────────────┴─────────────────────┐
                   │ Worker Claims Job                         │ User Aborts / Cancels
                   ▼                                           ▼
              [ ACTIVE ]                                 [ RELEASED ]
                   │                                           ▲
         ┌─────────┴─────────┐                                 │
         │ Job Succeeded     │ Permanent Failure               │
         ▼                   └─────────────────────────────────┘
    [ FINALIZED ]
```

### State Definitions:
1. **`reserved`**: Slot secured at request admission. Resource ID may be assigned or pending. Lease defaults to 30 minutes.
2. **`active`**: Worker has claimed the job and begun execution. Lease is actively maintained by worker heartbeats.
3. **`finalized`**: Operation completed successfully. Finalized timestamp recorded. Capacity is released; usage event is committed.
4. **`released`**: Operation failed, cancelled, or aborted. Capacity is released immediately with zero recorded chargeable usage.
5. **`expired`**: Reservation exceeded lease expiration without worker activity or reconciliation evidence. Reclaimed by background reconciler.

---

## 4. Idempotency Identity, Fingerprinting, and Isolation

### 4.1 Constraint and Fingerprint Policy
The table `gid.commercial_reservations` enforces `UNIQUE (tenant_id, idempotency_key)`. To ensure unrelated requests cannot accidentally reuse or corrupt existing reservations:

1. **Request Fingerprint**: When a request payload is provided, a deterministic SHA-256 hash is computed:
   $$\text{Fingerprint} = \text{SHA256}(\text{operation\_type} \parallel \text{sorted\_json\_payload})$$
   Stored in `metadata_json.request_fingerprint`.
2. **Payload Conflict (409)**: Resubmission with the same key but differing request parameters raises `HTTP 409 Conflict` with `reason_code: "IDEMPOTENCY_CONFLICT"`.
3. **Operation Type Conflict (409)**: Reusing a key across differing operations (e.g., download vs merge) raises `HTTP 409 Conflict`.
4. **Principal Isolation (409)**: If User B in Tenant A attempts to supply an idempotency key owned by User A, the request is rejected with `HTTP 409 Conflict` without disclosing User A's private resource identifiers.
5. **Terminated Reservation Protection**: Reusing an idempotency key associated with a `released` or `expired` reservation raises `HTTP 409 Conflict`, requiring a new key rather than allowing corrupted restarts.
6. **Finalized Reservation Safe Reuse**: A finalized reservation safely returns the existing result without generating duplicate chargeable usage.

---

## 5. Transactional Atomicity & Boundary Architecture

### 5.1 Atomicity Guarantee
A commercial reservation is **never** committed without its corresponding persistent operation.

1. **Admission Phase**: `acquire_admission_and_reservation()` acquires the tenant `FOR UPDATE` lock, evaluates capacity against limits, adds `CommercialReservationDB` to the session, and flushes without committing.
2. **Operation Persistence Phase**: The caller creates and adds `MediaWorkItemDB` or `DownloadJobDB` to the same transaction session.
3. **Coordinated Commit**: A single `await session.commit()` persists both the reservation and the work item atomically.
4. **Rollback Behavior**: If queue persistence or database insertion fails, `await session.rollback()` unrolls the entire transaction, ensuring zero orphaned reservations exist.

---

## 6. Long-Running Job Protection & Lease Recovery

### 6.1 Heartbeat Lease Extension
For operations requiring extensive compute (e.g., 4K video transcoding, multi-segment merging):
- The media worker calls `heartbeat_and_check_cancellation()` every few seconds.
- Alongside updating `MediaWorkItemDB.last_heartbeat_at` and `lease_expires_at`, it updates `CommercialReservationDB.lease_expires_at = now + 15 minutes`.
- An active worker will **never** lose its reservation solely due to elapsed initial admission TTL.

### 6.2 Safe Stale Reservation Reconciliation
The administrative reconciler (`reconcile_stale_reservations`):
1. Queries reservations where `status IN ('reserved', 'active')` and `lease_expires_at < now`.
2. Cross-checks the authoritative `MediaWorkItemDB` and `DownloadJobDB` records:
   - If work item is `succeeded`: finalizes reservation.
   - If work item is `failed` or `cancelled`: releases reservation.
   - If work item is `running` with recent heartbeat ($< 5\text{ min}$): **extends lease** by 15 minutes without expiring.
   - If work item is in `retry_wait`: extends lease to cover the scheduled retry window.
   - If work item is `queued` within normal pickup window: extends lease.
   - Only truly orphaned or abandoned reservations are transitioned to `expired`.

---

## 7. Compound Quota Coverage & Metric Classification

An operation can touch multiple resources simultaneously. The platform categorizes quota metrics into strictly enforced admission gates versus deferred/advisory post-execution metrics:

| Metric Category | Metric Key | Enforcement Type | Rationale |
|---|---|---|---|
| **Concurrency** | `max_concurrent_jobs` | **Strict Admission Gate** | Known upfront (1 job slot). Serialized via tenant lock. |
| **Download Count** | `monthly_download_count` | **Strict Admission Gate** | Known upfront (1 download requested). Tracked against monthly limit. |
| **Clip Count** | `monthly_clip_count` | **Strict Admission Gate** | Known upfront (1 clip requested). Tracked against monthly limit. |
| **Merge Count** | `monthly_merge_count` | **Strict Admission Gate** | Known upfront (1 merge requested). Tracked against monthly limit. |
| **API Access** | `api_access` | **Strict Admission Gate** | Boolean subscription gate. Enforced for API-key requests. |
| **Download Bytes** | `monthly_download_bytes` | **Deferred / Shadow Metering** | Indeterminate upfront. Measured from actual downloaded file stat upon completion. |
| **Transcode Seconds** | `monthly_clip_seconds` | **Deferred / Shadow Metering** | Estimated duration varies based on actual streams/keyframes. Measured post-transcode. |
| **Storage Bytes** | `storage_bytes` | **Deferred / Advisory Metering** | Physical disk storage measured post-completion. Over-quota accounts retain media without deletion. |
| **Transcription Minutes**| `monthly_transcription_minutes` | **Deferred / Shadow Metering** | Measured upon speech-to-text generation. |

### 7.1 Customer Media Preservation on Storage Overage
In accordance with MediaForge data safety guidelines:
- Exceeding billable storage limits **never** triggers automated deletion of customer videos or artifacts.
- Storage overage prevents new operations from being scheduled in enforce mode while preserving existing stored assets.

---

## 8. Standardized Error Contracts

All commercial policy violations return a standardized, machine-readable JSON structure conforming to the following envelope:

```json
{
  "success": false,
  "error_code": "COMMERCIAL_POLICY_VIOLATION",
  "reason_code": "CONCURRENCY_EXCEEDED",
  "message": "Maximum concurrent background jobs reached (2/2)",
  "metric": "max_concurrent_jobs",
  "limit_value": 2.0,
  "current_value": 2.0,
  "reset_at": null,
  "correlation_id": "corr-a1b2c3d4e5f6"
}
```

### Status Code Mappings:
- **`400 Bad Request`**: Monthly operation count quota exhausted (`DOWNLOAD_COUNT_EXCEEDED`, `CLIP_COUNT_EXCEEDED`, `MERGE_COUNT_EXCEEDED`). Includes ISO `reset_at` representing the start of the next UTC month.
- **`403 Forbidden`**: Subscription tier or trial restrictions (`FEATURE_NOT_INCLUDED`, `TRIAL_EXPIRED`, `SUBSCRIPTION_CANCELLED`, `PLAN_RECONCILIATION_REQUIRED`).
- **`409 Conflict`**: Idempotency conflicts (`IDEMPOTENCY_CONFLICT` for payload mismatch, operation mismatch, cross-principal key reuse, or terminated reservation restart).
- **`429 Too Many Requests`**: Concurrency limit reached (`CONCURRENCY_EXCEEDED`).
- **`503 Service Unavailable`**: Database or commercial admission service temporarily unavailable (`COMMERCIAL_SERVICE_UNAVAILABLE`). Fails closed in enforce mode without corrupting quotas.

---

## 9. Rollout Governance & Rollback Procedures

```
                    ┌────────────────────────┐
                    │    mode = disabled     │ (Default: Zero blocking across all tenants)
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │     mode = shadow      │ (Audit logging of potential violations)
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │     mode = enforce     │ (Strict blocking ONLY for tenant_allowlist)
                    └────────────────────────┘
```

1. **Default Production State**: `mode = "disabled"`, `tenant_allowlist = []`.
2. **Allowlist Isolation**: Even in `mode = "enforce"`, only tenants in `tenant_allowlist` are enforced. Existing customer tenants continue in advisory mode.
3. **Legacy Grandfathering Protection**: Tenants with `status = "legacy_active"` are exempted from hard blocking by code assertion, even if added to the allowlist.
4. **Immediate Rollback**: Setting `mode = "disabled"` immediately ceases all enforcement blocking in zero downtime without requiring application restarts.
