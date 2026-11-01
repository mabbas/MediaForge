# MediaForge — Gate 3A Final Release Preparation & Staging Publication Architecture

**Document Version:** 1.1.0
**Target Release Candidate:** `v0.4.0-staging.1`
**Git Baseline Commit:** `09b2dc36391a0985283246b91e90519a9791886a`
**Working Branch:** `main`
**Remote Origin:** `https://github.com/mabbas/MediaForge.git` (`origin/main = f43ae562408e4e217920d78c47a5019099af376b`)
**Status:** Preflight Staging Qualification — Awaiting Gate 3B Push Authorization

---

## 1. Executive Summary & Release Baseline

MediaForge has reached Gate 3A release readiness following the formal acceptance of Gate 2 (`09b2dc36391a0985283246b91e90519a9791886a`). All core backend pipelines, database concurrency protections, subscription schedulers, Next.js Super Admin interfaces, and isolated Docker staging topologies are fully implemented and verified locally.

This architectural runbook details the critical corrections, hardening measures, and publication safeguards enacted to qualify the release candidate for remote private staging publication (Gate 3B). In strict compliance with release governance:
- **Zero Git pushes** have occurred.
- **Zero Git tags** have been created.
- **Zero remote CI/CD pipelines** have been triggered.
- All code and configuration adjustments remain **uncommitted local modifications** for operator review.
- Operational safety settings remain locked: `commercial:enforcement` is `disabled` and `commercial:scheduler_config` is `allowlist_only` with an empty allowlist.

---

## 2. GitHub Publication Side-Effect Audit

A thorough audit of potential remote side effects upon publishing commit `09b2dc36391a` to `https://github.com/mabbas/MediaForge.git` was performed:

| Target Surface | Inspection Method | Finding | Publication Safety Assessment |
|---|---|---|---|
| **Repository CI Workflows** | Local tree inspection (`.github/workflows`) | No `.github` directory exists in the codebase. Zero local workflow files. | **PASS** — No actions triggered from repo tree. |
| **Client-Side Git Hooks** | Local `.git/hooks` inspection | Only inactive `.sample` scripts present. Zero executable push hooks. | **PASS** — Local push execution is pure Git. |
| **Remote Branch Tracking** | `git ls-remote origin` | `origin/main` is `f43ae562408e4e217920d78c47a5019099af376b`. Local `main` is exactly 1 commit ahead. | **PASS** — Clean, direct fast-forward push. |
| **Remote Webhooks** | Remote GitHub API | Workstation lacks authenticated GitHub CLI (`gh`) and remote tokens. | **UNVERIFIED AT REPOSITORY SETTINGS LEVEL** |
| **Installed GitHub Apps** | Remote GitHub API | Remote organization/app settings not locally queryable. | **UNVERIFIED AT REPOSITORY SETTINGS LEVEL** |
| **External CI/CD Triggers** | Remote GitHub API | Remote webhooks listening for push events to `main` cannot be ruled out locally. | **UNVERIFIED AT REPOSITORY SETTINGS LEVEL** |
| **Branch Rulesets & Protection** | Remote GitHub API | Status checks and push permissions must be confirmed by the repository owner. | **UNVERIFIED AT REPOSITORY SETTINGS LEVEL** |

> [!IMPORTANT]
> Because remote repository settings cannot be verified without active GitHub credentials, the release team classifies remote publication side effects as **UNVERIFIED AT REPOSITORY SETTINGS LEVEL**. Prior to executing `git push origin main`, the operator must manually inspect GitHub repository settings (Webhooks, GitHub Apps, Environments) to guarantee that pushing `main` does not trigger unintended external deployment hooks.

---

## 3. Secure Super Admin Bootstrap & Rotation Architecture

### 3.1 Security Hazard of Command-Line Credentials
Supplying administrator passwords via `--password "<secret>"` presents severe vulnerabilities in Linux hosting environments:
1. **Process Table Inspection:** Command-line arguments are globally visible via `/proc/<PID>/cmdline` or `ps aux` to unprivileged users on the host.
2. **Shell History Persistence:** Plaintext passwords are logged to `~/.bash_history` or shell audit logs.
3. **Container Logging:** Compose orchestrator logs capture command arguments when containers are invoked via `docker compose run`.

### 3.2 Implemented Bootstrap Hardening (`api/services/manage_user.py`)
`api/services/manage_user.py` has been updated with a dedicated, secure `bootstrap-admin` command and secured `set-password` command:
- **Legacy `--password` Arguments Strictly Rejected:** Passing `--password` immediately aborts execution with a security error explaining the process inspection risk.
- **Secure Standard Input (`--password-stdin`):** Accepts passwords piped directly via standard input without shell echoing or process argument exposure.
- **Secret File Input (`--password-file`):** Reads credentials directly from operator-controlled secret files, closing descriptors immediately after ingestion.
- **File Permissions Validation:** On POSIX systems, `--password-file` verifies file modes and rejects files accessible to group or others (enforcing `0600` or `0400`).
- **Interactive TTY Fallback (`getpass`):** Prompts for password and confirmation interactively with terminal echo disabled.
- **Argon2id Hashing:** Enforces minimum 8 and maximum 128 character complexity, hashing with Argon2id parameters.
- **Tenant Auto-Resolution:** Automatically initializes the default tenant (`00000000-0000-0000-0000-000000000001`) if invoked on a fresh staging database prior to standard seeding.
- **Initial Bootstrap Non-Existence Requirement:** Initial bootstrap is permitted **only** when the administrator account does not yet exist.
- **Repeated Bootstrap Fails Closed by Default:** If an administrator account exists, re-running `bootstrap-admin` fails closed with an explicit error to prevent unauthorized credential replacement.
- **Explicit Credential Rotation (`--rotate-existing`):** Credential rotation requires passing `--rotate-existing` alongside a mandatory operator audit reason (`--reason "<justification>"`).
- **Session Revocation on Rotation:** When credentials are rotated, all previous active sessions and rotating refresh tokens for that user are immediately invalidated (`is_revoked = True`, `revocation_reason = "credential_rotation: <reason>"` in `gid.auth_sessions`).
- **Immutable Commercial Audit Trail:** Every bootstrap and rotation event records an entry in `gid.commercial_audit_logs` (`action = 'SUPER_ADMIN_BOOTSTRAPPED'` or `'SUPER_ADMIN_CREDENTIALS_ROTATED'`) with actor role `system_admin` and details metadata.
- **Least-Privilege Execution:** Executes exclusively under the runtime `gid_app` connection role; does not require database superuser or DDL authority.

```bash
# Secure Operator Bootstrap Example (via stdin):
echo "StagingLeadAdminPass2026!#" | docker compose -f deploy/docker-compose.staging.yml exec -T api \
  python -m api.services.manage_user bootstrap-admin \
    --email admin@staging.mediaforge.local \
    --display-name "Staging Lead Administrator" \
    --password-stdin

# Secure Credential Rotation Example (via stdin):
echo "RotatedAdminPass2026!#" | docker compose -f deploy/docker-compose.staging.yml exec -T api \
  python -m api.services.manage_user bootstrap-admin \
    --email admin@staging.mediaforge.local \
    --rotate-existing \
    --reason "Quarterly scheduled administrator credential rotation" \
    --password-stdin
```

---

## 4. Alembic Migration Identity & Privilege Separation

### 4.1 Strict PostgreSQL Role Isolation
MediaForge enforces cryptographic separation between migration execution and application runtime:
- **Migration Role (`gid_user`):** Schema owner (`AUTHORIZATION "gid"`). Possesses `USAGE, CREATE ON SCHEMA gid`. Authorized to execute Alembic DDL (`CREATE`, `ALTER`, `DROP`, `TRUNCATE`).
- **Runtime Role (`gid_app`):** Least-privilege application role. Explicitly revoked of `CREATE ON SCHEMA gid`. Possesses only `SELECT`, `INSERT`, `UPDATE`, `DELETE` and sequence permissions on `gid` objects. **Zero DDL privileges**.

### 4.2 Empirical Privilege Verification
Empirical testing on PostgreSQL 16 proved:
1. `gid_app` executing `CREATE TABLE gid.hack_test (id serial);` is strictly denied:
   ```
   ProgrammingError: permission denied for schema gid
   [SQL: CREATE TABLE gid.hack_test (id serial primary key);]
   ```
2. `gid_user` executing DDL successfully creates and drops migration schema objects.
3. Fresh migration from base to head revision `c3d4e5f6a7b8` on an isolated test database succeeded cleanly across all 12 Alembic revisions, generating all 23 schema tables.
4. FastAPI application startup executes read-only validation (`validate_database_revision`) ensuring head `c3d4e5f6a7b8` is present, failing closed if outdated without attempting auto-DDL.

---

## 5. Authoritative Commercial Plan Catalog

Auditing `gid.commercial_plans` confirms the actual 6 core commercial plans established by migrations `9c4d3e2f1a5b` and `a1f2b3c4d5e6`:

| Plan ID | Plan Code | Name | Version | Status | Public | Tier | Price | Metadata & Commercial Notes |
|---|---|---|---|---|---|---|---|---|
| `plan-free-v1` | `free` | MediaForge Free | 1 | `active` | Yes | `free` | $0.00 | Default public free tier (`tier_level: 1`). |
| `plan-trial-v1` | `enterprise_trial` | MediaForge Pro Trial | 1 | `active` | No | `trial` | $0.00 | 14-day default trial duration (`trial_duration_days: 14`). Private. |
| `plan-creator-monthly-v1` | `creator_monthly` | MediaForge Creator | 1 | `retired` | No | `paid` | $19.00 | Unapproved legacy price ($19.00). Formally retired in STEP 04-A1-Fix-A. |
| `plan-creator-monthly-v2` | `creator_monthly` | MediaForge Creator | 2 | `draft` | No | `paid` | `NULL` | Draft tier awaiting executive commercial pricing approval. |
| `plan-pro-monthly-v1` | `pro_monthly` | MediaForge Pro | 1 | `retired` | No | `paid` | $49.00 | Unapproved legacy price ($49.00). Formally retired in STEP 04-A1-Fix-A. |
| `plan-pro-monthly-v2` | `pro_monthly` | MediaForge Pro | 2 | `draft` | No | `paid` | `NULL` | Draft tier awaiting executive commercial pricing approval. |

> [!NOTE]
> No hypothetical plans (e.g. arbitrary "starter" or "enterprise" subscriptions) or fabricated payment transactions exist in the committed schema.

---

## 6. Web and Backend Secret Boundaries & Configuration Matrix

### 6.1 JWT Signing vs BFF Proxy Architecture
1. **Symmetric Signing Key (`HS256`):** `GID_JWT_SECRET_KEY` is a symmetric HMAC-SHA256 secret.
2. **Authority & Separation:** FastAPI is the exclusive issuer and cryptographically authoritative validator of JWT access tokens.
3. **BFF Access Delegation:** Next.js BFF acts as a confidential reverse proxy:
   - Stores the signed access token in an HTTP-only `Secure` cookie (`mf_admin_access_token`).
   - Forwards the access token in `Authorization: Bearer <token>` to FastAPI.
   - Delegates token validation to FastAPI's `/auth/me` endpoint.
   - Next.js BFF does **not** sign or forge JWTs.
4. **CSRF Boundary:** Next.js BFF utilizes its own dedicated secret (`CSRF_SECRET_KEY` / `ADMIN_CSRF_SECRET`) to compute HMAC-SHA256 signatures for session-bound anti-CSRF tokens.
5. **Production Fail-Closed Enforcement:** `web/src/lib/csrf.ts` enforces that in `NODE_ENV === "production"`, if `CSRF_SECRET_KEY` is absent, it throws a critical security configuration error rather than falling back to development defaults.
6. **No Client Leakage:** No secrets are prefixed with `NEXT_PUBLIC_*`. Only `NEXT_PUBLIC_API_URL` is exposed to browser bundles.
7. **No Layer Leaks:** Root `.dockerignore` excludes `.env`, `deploy/.env*`, and backup dumps from all container build contexts.

### 6.2 Staging Configuration Contract Matrix

| Variable Name | Consuming Service | Purpose | Req/Opt | Classification | Provisioning Method | Validation Method |
|---|---|---|---|---|---|---|
| `COMPOSE_PROJECT_NAME` | Docker Compose | Namespaces containers, volumes, and networks | Optional | Operational | `.env.staging` | Checked during `docker compose config` |
| `STAGING_DOMAIN` | Caddy | Virtual host domain for TLS routing | Required | Public / DNS | `.env.staging` | Caddyfile validation |
| `STAGING_HTTPS_PORT` | Caddy / Compose | Host port for Caddy direct HTTPS (e.g. 8443 or 443) | Optional | Network | `.env.staging` | Port binding verification |
| `STAGING_HTTP_PORT` | Caddy / Compose | Host port for Caddy direct HTTP redirect (e.g. 8088 or 80) | Optional | Network | `.env.staging` | Port binding verification |
| `STAGING_UPSTREAM_HTTP_PORT`| Caddy / Tailscale | Host loopback HTTP port consumed by Tailscale Serve (8089) | Required | Network (Private) | `.env.staging` | Bound exclusively to `127.0.0.1:8089` |
| `STAGING_ALLOWED_CIDRS` | Caddy | IP filtering (Tailnet `100.64.0.0/10`, RFC 1918, `127.0.0.1`) | Required | Network Security | `.env.staging` | Caddy `client_ip` directive |
| `STAGING_ALLOWED_ORIGINS` | Web BFF / FastAPI | CORS and CSRF origin verification | Required | App Security | `.env.staging` | Checked against `Origin` / `Referer` headers |
| `POSTGRES_DB` | PostgreSQL / API / Worker | Primary database name (`grabitdown_staging`) | Required | Database Config | `.env.staging` | PostgreSQL entrypoint init |
| `POSTGRES_USER` | PostgreSQL | Database superuser account (`postgres`) | Required | Database Admin | `.env.staging` | PostgreSQL entrypoint init |
| `POSTGRES_PASSWORD` | PostgreSQL | Database superuser password | Required | Secret (High) | `.env.staging` / Vault | Password authentication |
| `GID_DB_USER` | PostgreSQL / Migrator | Schema owner and migration role (`gid_user`) | Required | DB Owner / DDL | `.env.staging` | `01-init-database.sh` |
| `GID_DB_PASSWORD` | PostgreSQL / Migrator | Migration role password | Required | Secret (High) | `.env.staging` / Vault | Password authentication |
| `GID_DB_SCHEMA` | PostgreSQL / API | Schema namespace (`gid`) | Optional | Database Config | `.env.staging` | Verified in SQLAlchemy metadata |
| `GID_APP_USER` | PostgreSQL / API / Worker | Least-privilege runtime DML role (`gid_app`) | Required | App Runtime | `.env.staging` | `02-provision-roles.sh` |
| `GID_APP_PASSWORD` | PostgreSQL / API / Worker | Runtime role password | Required | Secret (High) | `.env.staging` / Vault | Password authentication |
| `GID_API_DATABASE_URL` | API / Worker | SQLAlchemy connection URL for runtime DML | Required | Secret Connection | Docker Compose interpolation | Engine connection pool pre-ping |
| `GID_JWT_SECRET_KEY` | API / Web BFF | HMAC-SHA256 signing key for JWT tokens | Required | Secret (Critical) | `.env.staging` / Vault | Min 32-byte entropy check |
| `CSRF_SECRET_KEY` | Web BFF | HMAC-SHA256 signing key for anti-CSRF tokens | Required | Secret (High) | `.env.staging` / Vault | Min 32-byte entropy check |
| `ADMIN_CSRF_SECRET` | Web BFF | Alias for `CSRF_SECRET_KEY` in Next.js BFF | Required | App Security | Injected via Compose | Token signature verification |
| `JWT_SECRET_KEY` | Web BFF | Alias for `GID_JWT_SECRET_KEY` in Next.js BFF | Required | App Security | Injected via Compose | Token signature verification |
| `API_INTERNAL_URL` | Web BFF | Upstream internal FastAPI URL (`http://api:8000/api/v1`) | Required | Internal Routing | Docker Compose | Health check curl validation |
| `REDIS_HOST` | API / Worker | Redis service hostname (`redis`) | Required | Internal Routing | Docker Compose | Redis ping in worker startup |
| `REDIS_PORT` | API / Worker | Redis service port (`6379`) | Required | Internal Routing | Docker Compose | Redis ping in worker startup |

> [!NOTE]
> `GID_COMMERCIAL_ENFORCEMENT_MODE` and `GID_SCHEDULER_ROLLOUT_MODE` are not container environment variables. They are authoritative database-level settings persisted in `gid.app_settings` (initialized to `mode='disabled'` and `rollout_mode='allowlist_only'` via Alembic revisions `b2c3d4e5f6a7` and `c3d4e5f6a7b8`).

---

## 7. Immutable Container Image Promotion & Rollback

### 7.1 Image Build and Promotion Policy
To guarantee reproducibility and zero drift between local verification and staging execution:
1. **Source Pinning:** Build images strictly from the committed Git SHA:
   ```bash
   GIT_SHA=$(git rev-parse --short HEAD)
   docker build -t ghcr.io/mabbas/mediaforge-api:v0.4.0-staging.1-${GIT_SHA} -f Dockerfile .
   docker build -t ghcr.io/mabbas/mediaforge-web:v0.4.0-staging.1-${GIT_SHA} -f web/Dockerfile ./web
   ```
2. **Vulnerability Scanning & SBOM:** Prior to any deployment, scan images using Trivy or Grype (`trivy image --severity HIGH,CRITICAL <image>`). Retain Software Bill of Materials (SBOM) using `syft`.
3. **Registry Digest Capture:** Upon push to the container registry, record the immutable `@sha256:...` digest. Pin staging Compose definitions to the exact digest, preventing mutable tag hijacking.

### 7.2 Distinguishing Application vs Database Rollback
- **Application Rollback:** Reverting an application defect without schema changes requires only updating the container image digest in `deploy/docker-compose.staging.yml` to the previous known-good digest and running `docker compose up -d api web worker`.
- **Database Rollback:** Database migrations must **never** be rolled back automatically. Any Alembic downgrade requires an explicit operational review:
  - Additive migrations (new tables, indexes) can safely remain in place during an application rollback.
  - Destructive or breaking migrations require either:
    1. Forward-migrating with a corrective patch migration; or
    2. Restoring the verified pre-migration logical database backup.

---

## 8. Coordinated Safe-Backup & Disaster Recovery Architecture

### 8.1 Coordinated Quiescence & Snapshot Process
Stopping the background worker alone does not guarantee a globally consistent snapshot if the API continues to admit uploads or create metadata rows. A safe, coordinated backup follows a multi-phase quiescence process:

```
[Phase 1: Admission Pause] ──▶ Pause new job creation & chunked upload intake
[Phase 2: Worker Drain]    ──▶ Wait for in-flight ffmpeg/leases to reach 0
[Phase 3: DB Snapshot]     ──▶ pg_dump -F c custom archive at T_snap
[Phase 4: Media Snapshot]  ──▶ Helper container mounts mediaforge_staging_media_data:ro
[Phase 5: Manifest Gen]    ──▶ Record checksums (SHA256), DB head, table/file counts
[Phase 6: Resumption]      ──▶ Re-enable upload admission and restart worker polling
```

```bash
# 1. Quiesce background media processing
docker compose -f deploy/docker-compose.staging.yml stop worker

# 2. Backup named media volume to encrypted host archive (No host path assumptions)
docker run --rm \
  -v mediaforge_staging_media_data:/volume_data:ro \
  -v /opt/mediaforge/backups:/backup_target \
  alpine:latest tar -czf /backup_target/media_staging_$(date +%Y%m%d_%H%M%S).tar.gz -C /volume_data .

# 3. Resume background worker
docker compose -f deploy/docker-compose.staging.yml start worker
```

### 8.2 Logical PostgreSQL Database Backup
```bash
# Generate custom-format binary dump inside the running database container
docker compose -f deploy/docker-compose.staging.yml exec -T postgres \
  pg_dump -U postgres -d grabitdown_staging -F c -b -v > /opt/mediaforge/backups/db_staging_$(date +%Y%m%d_%H%M%S).dump

# Encrypt backup archives with AES-256 before offsite replication
gpg --symmetric --cipher-algo AES256 /opt/mediaforge/backups/db_staging_*.dump
```

### 8.3 Disaster Recovery Verification & Claim Boundaries
- **Implemented and Tested Restoration:** Logical database restore into clean disposable targets verified 23/23 tables, foreign keys, and revision `c3d4e5f6a7b8`. Synthetic H.264 video streams backed up from named volumes and restored to isolated targets matched 100% of SHA-256 checksums (`test_staging_backup_restore.py`).
- **Proposed Hosted Infrastructure (Not Yet Implemented):** Automated systemd/cron timers for scheduled backups and S3/B2 offsite cloud replication.
- **GDPR / Right to Be Forgotten Claim Boundary:** Automated GDPR tombstone reconciliation is **not** currently implemented in application code. In the event of a database restore, re-purging accounts deleted between snapshot creation and restore is an operator-executed manual audit replay procedure.
- **RPO & RTO Realism:** Sub-second benchmarks on tiny test fixtures do not represent production performance. Target Recovery Point Objective (RPO) is bounded by scheduled cron interval (e.g. 1 hour). Target Recovery Time Objective (RTO) is bounded by archive transfer and disk decompression speeds.
- **Interrupted Backup Recovery:** If a backup routine fails before manifest generation, temporary `.part` archives are cleanly discarded without leaving dangling locks or corrupting active volumes.

---

## 9. Private Ingress & Tailscale Security Review

### 9.1 Network Topology
```
Authorized Tailnet Operator (Identity Verified)
       │
       ▼ (Tailscale WireGuard Mesh)
Tailscale Node on VPS Host
       │
       ▼ tailscale serve (Terminates Tailnet TLS)
127.0.0.1:8089 (Internal Loopback Only)
       │
       ▼ Caddy Reverse Proxy (mediaforge-staging-caddy)
       ├── /api/*   ──────▶ FastAPI (mediaforge-staging-api:8000)
       └── /*       ──────▶ Next.js (mediaforge-staging-web:3000)
```

### 9.2 Ingress Safety Constraints
1. **Loopback Port Isolation:** PostgreSQL (`55433`), Redis (`26380`), and Caddy HTTP upstream (`8089`) bind exclusively to `127.0.0.1`. Under no circumstances are database or internal service ports bound to `0.0.0.0`.
2. **Tailscale Funnel Strictly Prohibited:** `tailscale funnel` is disabled. The application is invisible to the public internet and accessible only to authenticated tailnet identities.
3. **Host Firewall (UFW):** Host firewall defaults to `deny incoming`. Only Tailscale interface traffic (`ufw allow in on tailscale0`) and rate-limited SSH (`ufw limit 22/tcp`) are permitted.

---

## 10. Publication Gate Separation & Staging Procedure

To prevent premature container publishing or unverified VPS mutations, the deployment pipeline is partitioned into four sequential gates:

```
[Gate 3B: Git Source Publication] ──▶ git push origin main ONLY (No GHCR publish)
               │
               ▼
[Gate 4: Infrastructure Provisioning] ──▶ VPS provisioning, UFW rules, Tailscale install
               │
               ▼
[Gate 5: Container Registry & Deploy] ──▶ Build & scan images, push to GHCR, deploy Compose
               │
               ▼
[Gate 6: Remote Environment Acceptance] ──▶ Tailnet verification, Super Admin acceptance
```

### Gate 3B: Git Source Publication ONLY
- **Scope:** Authorizes pushing the local committed `main` branch and creating release tag `v0.4.0-staging.1`.
- **Precondition:** Operator manual review of GitHub Repository Settings (Webhooks, Installed GitHub Apps, Branch Rulesets) to verify no automatic deployment triggers fire on push.
- **Prohibition:** Strictly **no** container image publication to GHCR during Gate 3B.

### Gate 4: Private VPS Provisioning
- **Scope:** Host OS installation (Ubuntu 24.04 LTS), system updates, Tailscale installation, and UFW firewall configuration.

### Gate 5: Container Image Approval & Deployment
- **Scope:** Building images from the exact committed SHA, vulnerability scanning (Trivy), pushing to GHCR, capturing immutable digests (`@sha256:...`), and pulling onto the VPS.

### Gate 6: Remote Environment Certification
- **Scope:** Running database migrations (`gid_user`), Super Admin bootstrap via `--password-stdin`, configuring Tailscale Serve, and running browser acceptance tests over the private Tailnet.
