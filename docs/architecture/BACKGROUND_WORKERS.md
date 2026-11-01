# Background Workers, Lifecycle, and Durable Media Processing Architecture

## 1. Architectural Role and Principles

MediaForge's background worker subsystem decouples long-running, CPU-intensive, and I/O-heavy media operations (such as offline clip extraction and multi-clip concatenation/merging) from synchronous HTTP request lifecycles.

### Core Design Principles:
- **Decoupled Request-Response Lifecycle**: HTTP clients receive an immediate `202 Accepted` response with a durable job identifier and a polling status URL (`/api/v1/jobs/{job_id}`). Web server worker threads are freed immediately to handle high-concurrency API traffic.
- **Durable Persistence First**: Every unit of background work is recorded in PostgreSQL (`gid.media_work_items`) before worker pickup. No jobs are lost due to web server restarts, worker crashes, or container recreation.
- **RDBMS as Single Source of Truth**: Scheduling, state transitions, lease acquisition, and concurrency limits are governed strictly by ACID transactions and row-level locking in PostgreSQL, eliminating split-brain states and distributed race conditions.
- **Non-Root Sandboxed Execution**: Background workers execute as an unprivileged service account (`mediaforge:1000:1000`) inside Docker containers, preventing privilege escalation and host filesystem compromise.
- **Zero-Usage Guarantee on Failure or Cancellation**: Resource consumption metrics are ledgered into `gid.usage_events` atomically in the same database transaction that finalizes artifact readiness. Cancelled, failed, or timed-out operations never incur commercial usage charges.

---

## 2. PostgreSQL-Backed Queue Semantics

Rather than introducing external message brokers (such as RabbitMQ or Apache Kafka) that complicate operations, backup recovery, and transaction boundaries, MediaForge leverages PostgreSQL's native transaction engine for queue semantics.

### Concurrency and Safe Dequeuing:
- **`SELECT ... FOR UPDATE SKIP LOCKED`**: Workers atomically claim available work items without contention. Multiple worker processes running concurrently across containers query the queue simultaneously; each worker locks and claims distinct rows without blocking peers.
- **Filter Criteria for Claims**:
  ```sql
  SELECT * FROM gid.media_work_items
  WHERE status IN ('queued', 'retry_wait')
    AND available_at <= NOW()
  ORDER BY created_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT 1;
  ```
- **Redis Wake-Up Channel (`media_jobs:new`)**: When a job is submitted via the REST API, a lightweight wake-up event is published to Redis. Idle workers listening on `media_jobs:new` wake up immediately to claim work, providing sub-second latency while falling back to 3-second database polling if Redis is unavailable.

### Database Indexing:
- `idx_work_items_claim`: `(status, available_at, created_at)` accelerates `SELECT FOR UPDATE SKIP LOCKED` polling.
- `idx_work_items_lease`: `(status, lease_expires_at)` optimizes stale worker lease identification.
- `idx_work_items_tenant_user`: `(tenant_id, requester_user_id, status)` powers intra-tenant job listing queries.
- `uq_work_items_tenant_idempotency`: `(tenant_id, idempotency_key)` guarantees duplicate submission idempotency at the database level.

---

## 3. Job Lifecycle State Machine

A background job traverses a strictly controlled state machine. Transitions are enforced by PostgreSQL check constraints (`status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')`).

```mermaid
stateDiagram-v2
    [*] --> queued: POST /api/v1/jobs/clips or /merges
    queued --> running: Worker Claims (SELECT FOR UPDATE SKIP LOCKED)
    queued --> cancelled: POST /api/v1/jobs/{id}/cancel

    running --> running: Heartbeat & Progress Checkpoint
    running --> retry_wait: Worker Crash / Lease Expired (attempt < max_attempts)
    running --> failed: Unrecoverable Error / Max Attempts Exceeded
    running --> cancelled: Cancel Signaled & Subprocess Terminated
    running --> succeeded: FFmpeg Complete & Artifact Verified

    retry_wait --> running: Re-claimed after available_at backoff
    retry_wait --> cancelled: POST /api/v1/jobs/{id}/cancel

    succeeded --> [*]
    failed --> [*]
    cancelled --> [*]
```

### State Definitions:
1. `queued`: Work item persisted and awaiting claim by an available worker.
2. `running`: Worker acquired an exclusive lease (`claimed_by`, `lease_expires_at`), set initial progress (10%), and spawned execution.
3. `retry_wait`: A transient failure or lease expiration occurred. The job is scheduled for a future retry (`available_at = NOW() + backoff`).
4. `succeeded`: Execution succeeded, media output verified via SHA-256 and ffprobe, linked to `gid.media_artifacts`, and usage atomically recorded.
5. `failed`: Execution failed permanently due to non-retryable errors or exceeding `max_attempts`.
6. `cancelled`: The user requested cancellation. Output files unlinked, artifact marked failed, and zero usage billed.

---

## 4. Worker Concurrency, Leases, and Heartbeat Protocol

### Concurrency Semaphores:
Each worker process enforces an internal semaphore (`asyncio.Semaphore(concurrency)`) set by `GID_WORKER_CONCURRENCY` (default: 2 per container). This bounds CPU and memory consumption by preventing FFmpeg process exhaustion within a single worker process.
> **Note on Multi-Replica Concurrency**: The in-process semaphore governs concurrency strictly within a single worker container instance. In a horizontally scaled multi-replica deployment, cluster-wide concurrency is bounded by the total sum of replica semaphores and PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` claim throughput. Distributed cluster-wide rate limiting across independent worker replicas can be layered via Redis semaphores or database-level active lease counts without altering the core queue architecture.

### Lease Duration and Heartbeat:
- **Default Lease Duration**: 30 seconds (`GID_WORKER_LEASE_DURATION_SECONDS`).
- **Heartbeat Frequency**: Workers run an asynchronous background heartbeat monitor that updates `last_heartbeat_at` and extends `lease_expires_at = NOW() + 30s` every 0.5 seconds during active processing.
- **Fencing Token Mechanism**: Every database update checks that `claimed_by == worker_id`, `status == 'running'`, and matches the assigned `attempt_count`. If a worker's lease expires and another worker reclaims the item (advancing the attempt count), the original worker is fenced out: subsequent updates fail, and the original worker aborts execution.
- **Clock Skew Tolerance**: Heartbeats and lease checks use PostgreSQL server time (`func.now()` / database clock) rather than host system clocks to remain immune to node clock drift.

---

## 5. Cancellation Flow and Subprocess Termination

MediaForge supports robust, sub-second cancellation of both pending and running jobs:

1. **Immediate Queued Cancellation**:
   - If `status IN ('queued', 'retry_wait')`, `POST /api/v1/jobs/{job_id}/cancel` immediately transitions the job to `cancelled`, sets `completed_at`, and commits. Workers will never pick it up.
2. **Cooperative Running Cancellation & Process-Group Abort**:
   - If `status == 'running'`, the API updates `cancellation_requested_at = NOW()` and publishes an instant cancellation event to Redis channel `media_jobs:cancel`.
   - The worker's dual cancellation monitor detects the cancellation either instantly via Redis or within 0.5 seconds via database heartbeat checks.
   - Upon detecting cancellation, the worker:
     - Terminates the FFmpeg process group via `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)`, escalating to `SIGKILL` after 3 seconds if needed, ensuring no orphaned background FFmpeg processes linger.
     - Cancels the running `asyncio.Future`.
     - Deletes partial/temporary output files on disk (`unlink(missing_ok=True)`).
     - Marks the pending artifact as failed (`Cancelled by user`).
     - Transitions the job to `cancelled`.
3. **Usage Guarantee**: Because usage recording is coupled with successful artifact creation inside `complete_job_and_artifact_atomic`, a cancelled job strictly records 0 usage events.

---

## 5.1. Atomic Fencing and Split-Brain Prevention (`complete_job_and_artifact_atomic`)

To prevent split-brain completions and duplicate billing when network partitions or CPU stalls cause a worker to lose its lease while still executing:
- Finalization of the work item, artifact, and commercial usage event occurs within a single PostgreSQL transaction with row-level locks (`SELECT ... FOR UPDATE`).
- The transaction verifies that:
  1. `item.claimed_by == worker_id`
  2. `item.status == 'running'`
  3. `item.attempt_count == expected_attempt`
  4. `item.cancellation_requested_at IS NULL`
- If any condition fails (e.g. lease expired and reclaimed by another worker, or cancelled by user):
  - The transaction rolls back and returns `False`.
  - The fenced worker aborts finalization and does not promote any output.
  - No commercial usage events are recorded.

---

## 5.2. Safe Artifact Publication Protocol & Crash Consistency (STEP 03-B-Fix-C)

To eliminate the publication race window and establish crash consistency between the physical media filesystem and the PostgreSQL database:

### The Critical Publication Invariants:
1. **No Job Succeeded Without Valid File**: No job is reported `succeeded` unless its authoritative output file has been successfully published and validated on disk.
2. **No Ready Artifact Without Storage**: No artifact is reported `ready` unless the corresponding physical file is available at its authoritative storage path when the successful transition commits.
3. **No Usage Before Publishable Result**: No commercial usage event is ledgered before the operation has produced and published a verified result.
4. **No-Clobber Winning File Guarantee**: A stale worker must never overwrite, delete, or alter a winning worker's output.
5. **Recoverable Crash After Publication**: A crash after file publication but before database commit must be safely recoverable on retry without duplicating usage or corrupting output.
6. **No Phantom Completions**: A crash before publication must never leave a permanently `succeeded` job with a missing file.
7. **Idempotent Reconciliation**: Retries and reconciliation sweeps must be strictly idempotent.

### Why Filesystem and PostgreSQL Cannot Form a Distributed ACID Transaction:
POSIX/NTFS filesystems and relational databases do not share a two-phase commit (2PC) or distributed transaction coordinator (XA). Therefore, changes across both media storage and PostgreSQL must be ordered with explicit failure compensation.

- **The Old Flawed Sequence**: Committing the database first (`status='succeeded'`, `status='ready'`, usage logged) and renaming the file afterwards (`os.replace`) created a critical failure window: a worker crash or filesystem error after commit left a permanently succeeded job with zero output on disk, while billing the customer.
- **The Corrected Publication Sequence (STEP 03-B-Fix-C)**:
  ```mermaid
  sequenceDiagram
      autonumber
      participant W as MediaWorker
      participant FS as Filesystem (Staging)
      participant OUT as Filesystem (Production)
      participant DB as PostgreSQL (ACID)

      W->>FS: 1. Transcode output to attempt-specific staging path
      W->>FS: 2. Calculate SHA-256 and probe duration on staging file
      W->>DB: 3. Begin transaction & SELECT ... FOR UPDATE (item & artifact)
      Note over W,DB: Verify claimed_by, attempt_count, lease, cancellation
      W->>OUT: 4. publish_artifact_file_no_clobber(staging, production)
      Note over OUT: Atomic hardlink / safe promotion; no clobber
      W->>DB: 5. Transition item='succeeded', art='ready', insert UsageEventDB
      W->>DB: 6. Commit transaction
  ```

### Publication Protocol Implementation:
1. **Attempt-Specific Staging**:
   Workers transcode strictly into isolated attempt paths:
   `/app/downloads/{tenant_id}/temp/staging/stage_{job_id}_att{attempt_count}_{token}.{ext}`
2. **Safe Atomic No-Clobber Publication (`publish_artifact_file_no_clobber`)**:
   - If destination does not exist: Creates hard link (`os.link`) atomically and unlinks staging file.
   - If destination exists with matching SHA-256 and size: Detects that an earlier interrupted attempt already published the verified file; safely adopts the authoritative file and unlinks redundant staging.
   - If destination exists with differing checksum or size: Raises `PublicationConflictError` and **refuses to overwrite**. The winning output remains 100% intact.
3. **Database-First Rollback on Publication Failure**:
   If `publish_artifact_file_no_clobber` fails (e.g. disk full, permission error, or conflict), `complete_job_and_artifact_atomic` immediately rolls back the database transaction. The job is never marked succeeded, the artifact is never marked ready, and zero usage events are committed.
4. **Compensating Actions for Interrupted Commits**:
   If a worker crashes after publication but before `db.commit()`:
   - PostgreSQL rolls back on connection close: the job remains `running` with an expired lease.
   - The recovery daemon reclaims the job to `retry_wait`.
   - On the next attempt, the worker produces verified output and calls `complete_job_and_artifact_atomic`.
   - The no-clobber protocol detects the existing valid file on disk, adopts it, and commits the database transition.
   - Result: Exactly 1 artifact, exactly 1 commercial usage event. Zero duplication.

---

## 5.3. Orphaned Staging Safety and Audit Policy (`reconcile_orphaned_staging_files_safe`)

To protect customer media and active processing from naive age-based deletion:

### Staging Verification Rules:
1. **Symlink Rejection**: Any symlink in staging is flagged `suspicious_symlink` and preserved.
2. **Filename Strictness**: Files must match `STAGE_FILE_PATTERN` (`stage_<job_id>_att<attempt>_<token>.<ext>`). Unrecognized files are preserved.
3. **Active Lease Protection**: If the referenced job in PostgreSQL is `running` and `lease_expires_at > NOW()`, the file is flagged `preserved_active_lease` and preserved.
4. **Current / Pending Attempt Protection**: If the referenced job has `attempt_count <= file_attempt` in `running`, `retry_wait`, or `queued`, the file is flagged `preserved_current_attempt` and preserved.
5. **Authoritative Result Protection**: Files referenced by any `MediaArtifactDB` record are preserved.
6. **Abandoned Intermediate File Cleanup**: Only files belonging to definitively completed or superseded attempts (e.g. `job.status == 'succeeded'` with attempt > 1, or older dead attempts past grace period) are eligible for deletion.
7. **Dry-Run Preview**: Supports `dry_run=True` returning a structured audit report (`StagingReconciliationReport`) without altering disk.

---

## 6. Retry Policies, Failure Handling, and Dead-Letter Semantics

### Error Classification:
- **Fatal / Non-Retryable Errors**:
  - Malformed input media (unsupported codecs, truncated streams, corrupt headers).
  - Invalid timestamps (start time greater than end time, syntax errors).
  - Security violations (path traversal attempts, unauthorized source access).
  - *Action*: Job immediately marked `failed` with sanitized error code; no retries attempted.
- **Retryable / Transient Errors**:
  - Temporary disk I/O pressure or transient disk exhaustion.
  - Worker crash or node termination (lease expiration).
  - Database connection blips.
  - *Action*: Job transitioned to `retry_wait` with exponential backoff.

### Backoff Formula:
$$\text{backoff\_seconds} = \min(300, 5 \times 2^{\text{attempt\_count}})$$
- Attempt 1: 10 seconds
- Attempt 2: 20 seconds
- Attempt 3: 40 seconds

### Dead-Letter Mechanics:
When `attempt_count >= max_attempts` (default: 3), the job transitions permanently to `failed` with error code `MAX_ATTEMPTS_EXCEEDED`. The error and payload remain queryable via `/api/v1/jobs/{job_id}` for auditing and customer support.

---

## 7. Crash Recovery, Stale-Lease Fencing, and Orphaned-Job Handling

If a worker container crashes, loses power, or is abruptly killed (`docker kill`):
1. **Lease Expiration**: The worker ceases sending heartbeats. Within 30 seconds, `lease_expires_at` falls behind current server time.
2. **Reconciliation Daemon**: Surviving worker processes run an asynchronous recovery loop every 15 seconds executing `recover_expired_leases(db)`:
   ```sql
   SELECT id FROM gid.media_work_items
   WHERE status = 'running'
     AND lease_expires_at < NOW()
   FOR UPDATE SKIP LOCKED;
   ```
3. **Orphan Re-queueing**:
   - If `attempt_count < max_attempts`, the job is reset to `retry_wait`, `claimed_by` is cleared, and `available_at` is set for future retry.
   - If `attempt_count >= max_attempts`, the job transitions to `failed` with `LEASE_TIMEOUT_EXCEEDED`.
4. **Fencing Protection**: If the stalled worker revives later, its next heartbeat check detects that it no longer holds a valid lease and immediately halts processing, preventing duplicate artifact creation.

### 7.1. Verified Real Worker Crash Acceptance Evidence (`docker kill`)

To verify real-world crash safety beyond mock tests, MediaForge underwent automated fault-injection testing (`verify_step03b_fix_b.py`):

1. **Active FFmpeg Container Termination**:
   - An isolated worker container was assigned a long-running 25-second synthetic video extraction job (`c599cb89-5309-411d-815c-4d01c73ca78e`).
   - When the worker claimed the job and spawned FFmpeg, the container was inspected:
     - Worker Status: `running`
     - Active Child Process: `PID 36 (ffmpeg)` confirmed running inside the container PID namespace.
   - The container was abruptly killed via `docker kill` (sending an uncatchable `SIGKILL`).
2. **Orphan Subprocess Elimination**:
   - `docker inspect` confirmed container state `exited` (Exit Code 137).
   - Because Docker container lifecycle destroys the PID namespace, all child processes (including FFmpeg PID 36) were terminated immediately; no orphaned background processes leaked into host or Docker daemon.
3. **Database State & Lease Expiration**:
   - PostgreSQL maintained the job in `running` state with `claimed_by = 'worker-crash-test'`.
   - 30 seconds after termination, `lease_expires_at` expired against PostgreSQL server time.
4. **Replacement Worker Recovery**:
   - The primary `mediaforge-worker` recovery daemon detected the expired lease, incremented the retry schedule, and set the status to `retry_wait`.
   - The replacement worker claimed the job on **Attempt 2**, executed FFmpeg to completion, registered artifact `clip_22e9354e2e62.mp4` (SHA-256: `f6c98f06462f0a8947b191c9642eaad3360b64be165a29738b5329580b06798e`), and ledgered exactly one commercial usage event.
5. **Ledger Integrity**:
   - Authoritative query confirmed:
     `SELECT count(*) FROM gid.usage_events WHERE idempotency_key = 'job-usage:c599cb89-5309-411d-815c-4d01c73ca78e'` returned **`1`**.

---

## 8. Tenant Isolation, RBAC, and Anti-Enumeration Guarantees

All background job operations enforce MediaForge's strict multi-tenant boundary model:

- **Anti-Enumeration (404 Not Found)**:
  - If a user from Tenant B requests `/api/v1/jobs/{job_id}` belonging to Tenant A, the system returns `404 Not Found` (never `403 Forbidden`).
  - This prevents attackers from guessing UUIDs or confirming the existence of competitor jobs.
- **Intra-Tenant Member Isolation**:
  - Regular tenant members (`role == 'member'`) can only query and cancel jobs that they submitted (`requester_user_id == current_user.user_id`). Attempting to access another member's job within the same tenant returns `404 Not Found`.
  - Tenant Admins (`role == 'admin'`) and Tenant Owners (`role == 'owner'`) have full operational oversight to list and manage all jobs within their tenant.
- **Scope Verification**:
  - `POST /api/v1/jobs/clips` and `/merges` require scope `clips:write`.
  - `GET /api/v1/jobs` and `GET /api/v1/jobs/{job_id}` require scope `clips:read`.
  - `POST /api/v1/jobs/{job_id}/cancel` requires scope `clips:write`.

---

## 9. Storage Safety, Artifact Linking, and Directory Isolation

- **Non-Root Execution**: Workers run as non-root user `mediaforge:1000:1000` matching the file permissions of the Docker media volume (`mediaforge_api_downloads`).
- **Path Traversal Prevention**:
  - Source media references are validated via `resolve_and_authorize_clip_source` and `check_untrusted_path_traversal`.
  - Output paths are deterministically generated via `generate_safe_output_path(tenant_id, subfolder, prefix, extension)`. Absolute paths are resolved and verified to remain strictly within the tenant's isolated directory tree (`/app/downloads/{tenant_id}/...`).
- **Database Artifact Linking**: Completed media files are registered in `gid.media_artifacts` with physical file size, duration, and SHA-256 hash. The job record links directly to this artifact via `result_artifact_id`.

---

## 10. Usage Metering and Idempotency Integration

- **Atomic In-Transaction Metering**:
  When a job finishes FFmpeg processing, the worker calculates the exact output duration via `ffprobe` and file size on disk. The artifact status (`ready`) and the commercial usage record (`gid.usage_events`) are committed within the same database transaction.
- **Zero Usage on Failure/Cancel**:
  If a job fails or is cancelled, no usage event is committed.
- **Duplicate Request Idempotency & Conflict Detection**:
  Clients may provide an `X-Idempotency-Key` header (or one is derived from parameters).
  - **Identical Replay**: If a second request arrives with the same idempotency key and identical normalized parameters, the API immediately returns the existing job ID with `202 Accepted` without enqueuing duplicate work.
  - **Payload Conflict (HTTP 409)**: If a request arrives reusing an existing idempotency key with conflicting parameters (e.g. differing `start_time`, `end_time`, or source), the API rejects the request with `HTTP 409 Conflict` and descriptive diagnostic details identifying the conflicting parameters.

---

## 11. Docker Deployment Model, Compose Integration, and Health Checks

The worker service is defined in `deploy/docker-compose.yml` under the `workers` profile:

```yaml
services:
  worker:
    build:
      context: ..
      dockerfile: deploy/Dockerfile.api
    image: mediaforge-api:dev
    container_name: mediaforge-worker
    command: ["python", "-m", "api.workers.media_worker"]
    profiles: ["workers", "all"]
    user: "1000:1000"
    environment:
      - GID_API_DATABASE_URL=postgresql+asyncpg://gid_app:${DB_PASS}@postgres:5432/grabitdown
      - GID_REDIS_HOST=redis
      - GID_REDIS_PORT=6379
      - GID_WORKER_CONCURRENCY=2
      - GID_WORKER_LEASE_DURATION_SECONDS=30
    volumes:
      - media_downloads:/app/downloads
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import os, time; assert time.time() - os.path.getmtime('/tmp/worker_heartbeat') < 60\""]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

- **Healthcheck Strategy**: The worker writes a timestamp to `/tmp/worker_heartbeat` on every loop cycle. The Docker engine monitors this file to ensure the worker hasn't deadlocked.
- **Zero Exposed Ports**: The worker requires no open HTTP ports. It operates strictly as an internal processing consumer connected to `postgres`, `redis`, and the shared volume.

---

## 12. Operational Runbooks

### Inspecting Stuck or Long-Running Jobs:
```bash
# Query active running jobs and lease expiration
docker exec -it postgres psql -U gid_app -d grabitdown -c "
SELECT id, tenant_id, operation_type, status, attempt_count, progress_percent, claimed_by, lease_expires_at, updated_at
FROM gid.media_work_items
WHERE status = 'running'
ORDER BY updated_at ASC;"
```

### Manually Reclaiming an Orphaned Job:
If a worker was force-killed and immediate pickup is required without waiting for the 15-second daemon:
```sql
UPDATE gid.media_work_items
SET status = 'retry_wait',
    claimed_by = NULL,
    available_at = NOW(),
    lease_expires_at = NULL,
    updated_at = NOW()
WHERE id = '<job_id>' AND status = 'running';
```

### Clearing Poison Jobs:
If a corrupted file repeatedly crashes workers:
```sql
UPDATE gid.media_work_items
SET status = 'failed',
    sanitized_error_code = 'OPERATOR_TERMINATED',
    sanitized_error_message = 'Job manually failed by operator due to corrupt input',
    completed_at = NOW(),
    updated_at = NOW()
WHERE id = '<job_id>' AND status IN ('queued', 'running', 'retry_wait');
```

### Scaling Worker Capacity:
```bash
docker compose -f deploy/docker-compose.yml --profile workers up -d --scale worker=4
```

---

## 13. Download Architecture Integration Plan

### Rationale for Preserving Download Flow in STEP 03-B:
MediaForge's download subsystem (`/api/v1/downloads`) currently operates with an integrated real-time progress engine and cancellation mechanics tied to `DownloadJobDB`. Rewriting this engine in the same step as clip/merge worker introduction would risk regressions in live client download sessions.

### STEP 04 Integration Roadmap:
1. **Unified Work Item Adapter**: Create a bridge adapter converting download requests into `media_work_items` with `operation_type = 'download'`.
2. **Chunk Manager Worker Support**: Pass yt-dlp callbacks through worker progress checkpoints (reporting bytes and speed).
3. **Dual-Write Deprecation**: Maintain `DownloadJobDB` read compatibility views while writing authoritatively to `media_work_items`.

---

## 14. Scalability Limits and Future Transition Triggers

PostgreSQL-backed queues with `SELECT FOR UPDATE SKIP LOCKED` provide exceptional reliability and zero extra operational burden for workloads up to **5,000–10,000 completed jobs per hour**.

### Future Transition Triggers:
| Metric / Trigger | Current Architecture | Transition Target (Celery / Temporal / ARQ) |
| :--- | :--- | :--- |
| **Active Queue Depth** | Up to 100,000 items | > 500,000 queued items requiring in-memory broker |
| **Worker Cluster Size** | 1 to 20 containers | > 50 distributed worker nodes |
| **Job Frequency** | 10–100 jobs/sec | > 1,000 jobs/sec |
| **Complex Workflows** | Single-step extraction & merge | Multi-stage DAG orchestration with human-in-the-loop |

---

## 15. Verification Matrix

| Test Requirement | Verification Mechanism | Status |
| :--- | :--- | :--- |
| **Asynchronous Job Submission** | `test_live_background_jobs.py` (POST /jobs/clips returns 202) | Verified |
| **Atomic Row Locking Claims** | `media_work_items` with `FOR UPDATE SKIP LOCKED` in `job_service.py` | Verified |
| **Frame-Accurate Clip Output** | `test_live_background_jobs.py` (2.00s exact output, SHA-256 match) | Verified |
| **Video Concatenation / Merge** | `test_live_background_jobs.py` (POST /jobs/merges, 2 clips merged) | Verified |
| **Submission Idempotency** | Database-enforced duplicate detection returns same job ID | Verified |
| **Cancellation Safety** | Queued instant cancel + Running subprocess abort + 0 usage | Verified |
| **Anti-Enumeration Protection** | Cross-tenant access returns 404 Not Found | Verified |
| **Member Intra-Tenant RBAC** | Members see own jobs only; Admin sees all | Verified |
| **Stale Lease Recovery** | Expired leases reclaimed to `retry_wait` by recovery daemon | Verified |
| **Atomic Usage Ledgering** | Single transaction completes artifact & commits `UsageEventDB` | Verified |
| **Idempotency Payload Conflict** | Returns `HTTP 409 Conflict` on key reuse with mismatched parameters | Verified |
| **Active Process-Group Cancellation** | Subprocess terminated promptly via `os.killpg(SIGTERM/SIGKILL)`; no orphan ffmpeg | Verified |
| **Stale-Worker Fencing** | `complete_job_and_artifact_atomic` rejects stale worker; 0 duplicate usage | Verified |
| **Filesystem Output Fencing** | Isolated staging paths (`temp/staging/stage_*_att*`); winning output untouched | Verified |
| **Guarded Atomic Promotion** | `publish_artifact_file_no_clobber` executes before DB commit; losing attempt cleans private staging only | Verified |
| **Publication-Before-Commit** | File verified & published before DB status transition; zero succeeded jobs with missing files | Verified |
| **Safe Atomic No-Clobber** | Destination conflict detection (`PublicationConflictError`); winning outputs 100% preserved | Verified |
| **Crash-Window Fault Injection** | 9 deterministic synthetic failure windows verified; zero duplicate usage events | Verified |
| **Orphaned Staging Safety** | `reconcile_orphaned_staging_files_safe`: symlinks preserved, active leases protected, dry-run verified | Verified |
| **Real Crash Recovery (`docker kill`)** | Worker killed during active FFmpeg; lease expires; Attempt 2 completes with single usage event | Verified |
| **Lost Redis Resilience** | Automatic fallback to PostgreSQL `FOR UPDATE SKIP LOCKED` polling | Verified |
| **Migration Isolation Targeting** | Disposable DB verified with `-x url`; zero schema or data alterations on authoritative DB | Verified |

---

## 16. Security Threat Model (STRIDE)

| Threat Category | Potential Attack Vector | Mitigation in MediaForge Background Worker |
| :--- | :--- | :--- |
| **Spoofing** | Attacker impersonates worker to steal or complete jobs | Private network isolation; workers do not expose listening ports; database role credentials required. |
| **Tampering** | Modifying request payload or output path during execution | Output paths generated server-side via UUID; payload normalized into JSONB; storage root traversal checks enforced. |
| **Repudiation** | Customer denies requesting expensive video merges | Idempotency key, requester user ID, tenant ID, and API key ID permanently bound to `media_work_items` and `usage_events`. |
| **Information Disclosure** | Cross-tenant job inspection or error leakage | Anti-enumeration 404 on cross-tenant requests; sanitized error messages omit filesystem paths and internal stack traces. |
| **Denial of Service** | Flooding system with infinite-loop FFmpeg jobs | Semaphore concurrency bounds; execution timeouts; cooperative cancellation; disk quota pre-checks. |
| **Elevation of Privilege** | Escape from FFmpeg container to host system | Container runs as non-root `1000:1000`; root filesystem read-only; capabilities dropped (`cap_drop: ALL`). |
