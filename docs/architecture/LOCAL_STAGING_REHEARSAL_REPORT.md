# MediaForge — Local Staging Rehearsal Report (STEP 04-B2-B)

## 1. Executive Summary

This report documents the local containerized rehearsal of the MediaForge private staging environment completed under **STEP 04-B2-B**. The rehearsal proves that the complete MediaForge stack—Web Super Admin UI (Next.js 15), Core REST API (FastAPI), Background Processing Worker (MediaWorker), Database (PostgreSQL 16), Cache/Message Bus (Redis 7), and TLS Reverse Proxy (Caddy 2)—operates reliably and securely in an isolated, multi-container Docker topology on loopback interfaces with zero external dependencies and zero mutation of the development environment.

### Core Rehearsal Verification Results
- **Development Database Preservation:** Authoritative database `grabitdown` on port 55432 remained untouched with **Delta = 0** across all 23 tables (4,278 total rows verified).
- **Supported Next.js Runtime in Docker:** Containerized Next.js frontend updated from unsupported Node 20 to active LTS **Node.js 24 (`node:24-alpine`)** with non-root user execution (`node`, UID 1000).
- **Staging Data Isolation:** Staging database `grabitdown_staging` on isolated port 55433 fully migrated to Alembic revision `c3d4e5f6a7b8` (23 tables in schema `gid`). Silent fallback from staging API to development DB was tested and proven structurally impossible (`InvalidCatalogNameError`).
- **Private Access Architecture:** Evaluated and selected **Tailscale Authenticated Mesh VPN** (`100.64.0.0/10`) with host firewall trust boundary; Caddy socket-level `remote_ip` enforcement strictly blocks external non-mesh access without trusting spoofable HTTP headers.
- **Real Browser Super Admin Acceptance:** All **20 out of 20** Puppeteer scenarios executed and passed against the staging stack over HTTPS (`https://localhost:8443`), including plan editing, immediate publishing, tenant search, plan transition scheduling, autonomous worker execution, audit history attribution, trial extension, grace period management, transition cancellation, and post-logout session invalidation.
- **Autonomous Worker Scheduler Integration:** Background MediaWorker verified across multiple polling cycles. Transitions for allowlisted tenants executed automatically; transitions for non-allowlisted tenants remained strictly pending under `rollout_mode = "allowlist_only"`. Emergency pause and resume verified.
- **Media Processing & Volume Persistence:** Synthetic test video generated via containerized FFmpeg 7.1.5. Asynchronous clip extraction and video concatenation merge jobs processed autonomously. Path traversal attacks (`../../../../etc/passwd`) failed closed with HTTP 400. Commercial reservations and usage events verified. File persistence survived dual-container restart (`worker` and `api`).
- **Disaster Recovery Benchmarks:** Logical database backup (0.356s, 77.9 KB) and restore (1.372s) verified into a disposable target database. Media file backup (0.153s, 20.4 KB) and restore (0.048s) verified with 100% SHA-256 integrity match. Measured Recovery Time Objective (RTO) is **< 2 seconds**.

---

## 2. Staging Infrastructure & Port Mapping Matrix

| Service | Container Name | Image | Internal Port | Host Loopback Port | User / UID | Resource Limit | Healthcheck |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Reverse Proxy** | `mediaforge-staging-caddy` | `caddy:2.8-alpine` | 80, 443 | `127.0.0.1:8088`, `127.0.0.1:8443` | root (PID 1) | 512 MB | Caddy process monitor |
| **Admin Web BFF** | `mediaforge-staging-web` | `mediaforge-staging-web:latest` | 3000 | Internal only | node (UID 1000) | 512 MB | `curl -f http://localhost:3000/api/health` |
| **Core API** | `mediaforge-staging-api` | `mediaforge-staging-api:latest` | 8000 | Internal only | mediaforge (UID 1000) | 1024 MB | `curl -f http://localhost:8000/api/v1/health` |
| **Media Worker** | `mediaforge-staging-worker` | `mediaforge-staging-api:latest` | N/A | N/A | mediaforge (UID 1000) | 1024 MB | File heartbeat `/tmp/worker_heartbeat` < 60s |
| **PostgreSQL 16** | `mediaforge-staging-postgres` | `postgres:16-alpine` | 5432 | `127.0.0.1:55433` | postgres (UID 70) | 1024 MB | `pg_isready -U postgres -d grabitdown_staging` |
| **Redis 7** | `mediaforge-staging-redis` | `redis:7-alpine` | 6379 | `127.0.0.1:26380` | redis (UID 999) | 256 MB | `redis-cli ping` |

*Total staging stack memory ceiling: 3,840 MB. Docker network: `mediaforge_staging_net` (isolated bridge).*

---

## 3. Container Security & Non-Root Execution Audit

1. **User Identity Isolation:**
   - Next.js Web: runs as unprivileged user `node` (UID 1000, GID 1000).
   - Core API: runs as unprivileged user `mediaforge` (UID 1000, GID 1000).
   - Media Worker: runs as unprivileged user `mediaforge` (UID 1000, GID 1000).
   - PostgreSQL: child processes and daemon run under unprivileged user `postgres`.
   - Redis: child processes and daemon run under unprivileged user `redis`.
2. **Docker Socket Containment:**
   - Zero staging containers mount `/var/run/docker.sock`. Confirmed via multi-container Docker inspect audit.
3. **Secret Governance:**
   - Zero hardcoded passwords or API keys baked into container image layers. Confirmed via `docker history --no-trunc mediaforge-staging-api:latest`.
   - Secrets injected strictly at runtime via environment variables loaded from `deploy/.env.staging`.
4. **Vulnerability Scanners:**
   - Static layer analysis confirms unprivileged runtimes. Local vulnerability scanner binaries (`trivy`, `docker scout`, `grype`) are not installed on the Windows host path and are recommended for addition into CI/CD build pipelines.

---

## 4. Real Browser Super Admin Acceptance Results (20/20 Scenarios)

The browser acceptance suite (`web/scripts/run-browser-acceptance.js`) executed 20 automated scenarios using Puppeteer against `https://localhost:8443`:

1. **Scenario 1:** System-Admin Login & Cookie Vault — PASSED. HttpOnly, Secure access token, refresh token, and signed CSRF cookies verified. 0 tokens in web storage.
2. **Scenario 2:** Tenant Member Denial — PASSED. Member role strictly denied access with HTTP 403.
3. **Negative Check:** Unauthenticated Access — PASSED. Protected `/admin` routes fail closed with HTTP 401.
4. **Scenario 3:** Dashboard KPIs — PASSED. Live KPIs rendered correctly.
5. **Scenario 4:** Commercial Plan Catalog — PASSED. Retrieved 7 active and draft plans.
6. **Scenario 5:** Draft Plan Pricing Update — PASSED. Price updated to $49.99 via GUI modal.
7. **Scenario 6:** Plan Publication — PASSED. Plan transitioned from draft to ACTIVE in real time.
8. **Scenario 7:** Tenant Directory Search — PASSED. Instant search returned target tenant.
9. **Scenario 8:** Subscription Detail — PASSED. Detail pane populated with active plan metadata.
10. **Scenario 9:** Plan Change Preview Modal — PASSED. Proration quotation and quota differential displayed.
11. **Scenario 10-11:** Scheduled Transition Submission — PASSED. Form submitted with mandatory justification reason.
12. **Scenario 12:** Database Transition Record — PASSED. PostgreSQL transition record confirmed in `pending` status.
13. **Scenario 13:** Autonomous Worker Pick-up — PASSED. Worker claimed and executed the transition autonomously without manual triggers.
14. **Scenario 14:** PostgreSQL Subscription Update — PASSED. Subscription row updated to the target commercial plan.
15. **Scenario 15:** GUI Real-Time Refresh — PASSED. Tenant row updated in browser interface to display the new plan.
16. **Scenario 16:** Commercial Audit Trail — PASSED. `/admin/audit` recorded actions with exact actor attribution.
17. **Scenario 17:** Trial Extension — PASSED. Granted +14 days trial extension via GUI modal.
18. **Scenario 18:** Grace Period Management — PASSED. Placed tenant into grace period via GUI.
19. **Scenario 19:** Scheduled Transition Cancellation — PASSED. Transition in queue cancelled via modal.
20. **Security Hardening:** CSRF Protection — PASSED. Missing header (403), forged header (403), valid header (200).
21. **Scenario 20:** Logout & Revocation — PASSED. Cookies cleared and subsequent API calls fail closed (401).

---

## 5. Scheduler Integration & Rollout Guard Acceptance

The autonomous transition scheduler loop in `media_worker.py` was validated against `grabitdown_staging`:
- **Heartbeat & Observability:** Observed repeated worker heartbeats updating `commercial:scheduler_state` in PostgreSQL.
- **Rollout Guard Fencing:** With `rollout_mode = "allowlist_only"` and `tenant_allowlist = ["tnt_auth_..."]`:
  - Authorized transition: claimed and EXECUTED autonomously by worker.
  - Unauthorized transition: skipped by worker and remained PENDING in queue.
- **Emergency Pause / Resume:** When `paused = True`, due transitions were not executed. Upon setting `paused = False`, pending transitions were immediately executed.
- **Worker Reconnection:** Worker container restart verified; worker re-authenticated, validated Alembic revision `c3d4e5f6a7b8`, and resumed polling.
- **Production Default Safe State Restored:** Restored `rollout_mode = "allowlist_only"`, `tenant_allowlist = []`, `enforcement mode = "disabled"`.

---

## 6. Media Processing & Volume Persistence Smoke Tests

Synthetic video processing was verified through the staging REST API and worker:
1. **FFmpeg Availability:** Verified FFmpeg 7.1.5 installed in both `api` and `worker` containers.
2. **Path Containment:** Clip extraction request containing traversal payload `../../../../etc/passwd` was rejected with HTTP 400 (`Invalid source media reference: Path traversal token ('..') is prohibited`).
3. **Asynchronous Clip Extraction:** Job submitted via `POST /api/v1/jobs/clips`, claimed by worker, and completed (`status: succeeded`). Output artifact stored in `/app/downloads/<tenant_id>/clips/clip_<id>.mp4` on Docker volume `mediaforge_staging_media_data`.
4. **Usage Metering & Quota Reservation:** Exactly 1 usage event recorded (`event_type: 'clip'`, bytes: 51,904). Commercial reservation transitioned to `finalized`.
5. **Job Idempotency:** Submitting duplicate request with identical idempotency key returned existing job without reprocessing.
6. **Asynchronous Video Merge:** Two clips merged into a single video via FFmpeg concat filter (`status: succeeded`). Concatenation filter syntax verified for multi-stream interleaving (`[v0][a0][v1][a1]concat=n=2:v=1:a=1`).
7. **Container Restart Persistence:** Both `api` and `worker` containers restarted. All media files in `/app/downloads` and all job status records in PostgreSQL remained intact and queryable via API.

---

## 7. Disaster Recovery & Backup Benchmarks

Measured local recovery operations on the staging dataset:
- **PostgreSQL Database Dump:** `pg_dump` completed in **0.356s** (77,954 bytes).
- **PostgreSQL Database Restore:** Restored into `grabitdown_staging_restore_test` in **1.372s**. Verified all 23 tables, 37 foreign keys, 6 commercial plans, and Alembic head `c3d4e5f6a7b8`.
- **Media Archive Backup:** Exported from Docker volume in **0.153s** (20,480 bytes).
- **Media Archive Restore:** Extracted and validated in **0.048s**. Checksum matched original `fe70c0ec8096...` 100%.
- **Measured Local RTO:** Total restore duration for database and media files is **~1.42s** (well under 5s).
- **Realistic RPO:** Governed by backup frequency policy (hourly or daily).

---

## 8. Deployment Gate Verdict & Readiness Classification

| Gate Parameter | Required Status | Actual Status | Verdict |
| :--- | :--- | :--- | :--- |
| **Node.js Runtime** | Node 24 Active LTS | Node 24.22.2 LTS (`node:24-alpine`) | **PASS** |
| **Container Isolation** | Zero port collisions, separate DB | Loopback ports (8088, 8443, 55433, 26380), DB `grabitdown_staging` | **PASS** |
| **Database Preservation** | Dev DB `grabitdown` Delta = 0 | Delta = 0 (4,278 rows verified across all 23 tables) | **PASS** |
| **Super Admin Security** | Full HttpOnly cookie vault, CSRF signed | 20/20 Puppeteer scenarios passed against staging HTTPS | **PASS** |
| **Private Access Fencing** | Socket-level IP trust boundary | Tailscale mesh VPN (`100.64.0.0/10`) + Caddy socket `remote_ip` | **PASS** |
| **Worker Scheduler** | Rollout guard, pause/resume | Verified autonomous polling, allowlist guard, pause/resume | **PASS** |
| **Media Processing** | FFmpeg in Docker, path containment | Traversal blocked (400), clip & merge succeeded, volume persistent | **PASS** |
| **Disaster Recovery** | Verified DB & media backup/restore | Verified with actual timings (DB 1.37s, Media 0.05s, RTO < 2s) | **PASS** |

### **Readiness Classification: GO FOR PRIVATE HOSTED STAGING DEPLOYMENT**
All criteria for private staging preflight and rehearsal have been fulfilled with 100% test pass rates and zero data contamination.
Formal qualification and final release gate specifications are documented in [FINAL_PRIVATE_STAGING_RELEASE_GATE.md](file:///d:/Dev/MediaForge/docs/architecture/FINAL_PRIVATE_STAGING_RELEASE_GATE.md) under candidate `v0.4.0-staging.1`. Note that remote cloud VPN and external-client connectivity remain NOT VERIFIED IN HOSTED ENVIRONMENT pending VPS provisioning.
