# MediaForge — Private Staging Deployment Plan & Operational Runbooks

## 1. Executive Summary & Staging Mission

MediaForge is an enterprise-grade multi-tenant media processing and subscription platform. Following the successful completion of **STEP 04-B2-A**, the local rehearsal of **STEP 04-B2-B**, the final qualification release gate of **STEP 04-B2-C**, the security refinements of **STEP 04-B2-C-Fix-A**, and the data reconciliation and network hardening of **STEP 04-B2-C-Fix-B**, this document establishes the deployment architecture, network policies, security controls, disaster recovery procedures, and formal release gates for deploying MediaForge into a private, hosted staging environment.

For the definitive qualification report and proposed release candidate manifest (`v0.4.0-staging.1`), refer to [FINAL_PRIVATE_STAGING_RELEASE_GATE.md](file:///D:/Dev/MediaForge/docs/architecture/FINAL_PRIVATE_STAGING_RELEASE_GATE.md). For the formal Gate 2 pre-commit certification package, see [GATE2_LOCAL_CHECKPOINT_APPROVAL.md](file:///D:/Dev/MediaForge/docs/architecture/GATE2_LOCAL_CHECKPOINT_APPROVAL.md). Note that remote cloud VPN and external-client connectivity remain **NOT VERIFIED IN HOSTED ENVIRONMENT** pending VPS provisioning.

### Staging Mission Principles
1. **Private Access Exclusivity:** Zero public exposure. Access is strictly confined to trusted internal CIDR ranges (RFC 1918 and localhost) enforced at the reverse-proxy layer.
2. **Zero External Billing Risk:** Live payment merchant integrations remain completely unconfigured. All billing events and administrative renewals operate through verified transactional ledger and audit trails.
3. **Database Baseline Immutability:** Authoritative databases remain guarded against test pollution. All acceptance testing is executed on isolated test databases (`grabitdown_test`).
4. **Enforcement Fail-Open:** Quota enforcement begins in `mode = "disabled"` and transition scheduler begins in `rollout_mode = "allowlist_only"` with empty allowlists, ensuring zero premature customer disruptions.

---

## 2. Staging Infrastructure & Component Topology

```mermaid
flowchart TD
    subgraph "External Boundary (Untrusted)"
        PublicInternet["Public Internet / Untrusted Traffic"]
    end

    subgraph "Private Network Boundary (CIDR Filtered)"
        Operator["Authorized Operator / Admin<br/>(10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.1)"]
    end

    subgraph "Staging Gateway Tier"
        Caddy["Caddy 2 Reverse Proxy<br/>(TLS Internal, CIDR ACL, Ports 80/443)<br/>Container: mediaforge-staging-caddy"]
    end

    subgraph "Application Tier (Docker Bridge: staging_net)"
        Web["Next.js 15.5.28 Admin Portal & BFF<br/>(Port 3000, Non-Root Node)<br/>Container: mediaforge-staging-web"]
        API["FastAPI Backend Core<br/>(Port 8000, Non-Root Mediaforge)<br/>Container: mediaforge-staging-api"]
        Worker["Autonomous Background Worker<br/>(Transition Scheduler & Media Jobs)<br/>Container: mediaforge-staging-worker"]
    end

    subgraph "Persistence Tier"
        Postgres[("PostgreSQL 16 (Alpine)<br/>Schema: gid, Alembic: c3d4e5f6a7b8<br/>Container: mediaforge-staging-postgres")]
        Redis[("Redis 7 (Alpine)<br/>AOF Enabled, maxmemory 256MB<br/>Container: mediaforge-staging-redis")]
    end

    PublicInternet -->|Attempt Connection| Caddy
    Operator -->|HTTPS / Private IP| Caddy

    Caddy -->|Blocked: 403 Forbidden| PublicInternet
    Caddy -->|Path: /* (Admin GUI)| Web
    Caddy -->|Path: /api/v1/* (Direct API)| API

    Web -->|Proxy Admin Requests with JWT| API
    API -->|Read/Write DML (gid_app)| Postgres
    API -->|Pub/Sub & Queues| Redis
    Worker -->|FOR UPDATE SKIP LOCKED| Postgres
    Worker -->|Job Processing| Redis
```

### Staging Services Specifications

| Service | Image / Base | Internal Port | External Port | User / Security Context |
| :--- | :--- | :--- | :--- | :--- |
| **caddy** | `caddy:2.8-alpine` | 8089, 80, 443 | 8089 (Upstream), 8088 (Redirect), 8443 (HTTPS) (all bound to 127.0.0.1) | Non-root, TLS internal |
| **web** | `mediaforge-staging-web` (Node 24 Alpine) | 3000 | None (routed via Caddy) | `node` (UID 1000), Next.js 15.5.28 |
| **api** | `mediaforge-staging-api` (Python 3.11 Slim) | 8000 | None (routed via Caddy) | `mediaforge` (UID 1000), Uvicorn ASGI |
| **worker** | `mediaforge-staging-api` (Python 3.11 Slim) | N/A | None | `mediaforge` (UID 1000), MediaWorker |
| **postgres** | `postgres:16-alpine` | 5432 | 55433 (bound to 127.0.0.1) | `postgres` (UID 70), Schema `gid` |
| **redis** | `redis:7-alpine` | 6379 | 26380 (bound to 127.0.0.1) | `redis` (UID 999), maxmemory 256MB |

---

## 3. Private Access Policy & Network Whitelisting

Staging must never be accessible from the public Internet. Private access enforcement is implemented at multiple independent layers:

### Layer 1: Reverse Proxy IP Filtering (Caddyfile)
The Caddy staging reverse proxy enforces a strict socket-level IP filter block (without trusting spoofable client headers):
```caddy
@blocked not remote_ip {$ALLOWED_CIDRS:127.0.0.1/32 ::1 100.64.0.0/10 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}
respond @blocked "Access Denied: Private Staging Access Restricted to Internal Networks." 403
```
Any request originating from an IP outside the authorized Tailscale mesh (`100.64.0.0/10`), RFC 1918 private address space, or loopback receives an immediate HTTP 403 Forbidden without reaching the application layer.

### Layer 2: Host Firewall, Tailscale Mesh VPN & Docker Ingress Security
- **Tailscale Mesh Architecture (Option A):** The host runs the official Tailscale daemon on the VPS. Traffic directed to `https://<staging-node>.tailnet.ts.net:8443` is proxied via `tailscale serve` to the local loopback port `127.0.0.1:8089`:
  ```bash
  tailscale serve --bg --https=8443 http://127.0.0.1:8089
  ```
  This eliminates Caddy's automatic HTTP 308 redirect loop (which occurs if proxying to port 8088), while Tailscale Serve terminates public browser-trusted Let's Encrypt certificates.
- **Strict Tailscale HuJSON Policy:** Defined in `deploy/staging/tailscale-staging-acl.hujson`, the policy models human operator groups (`group:staging-admins`), defines tag ownership (`tagOwners`), enforces default-deny with **zero `reject` actions**, separates Tailscale SSH into a dedicated `ssh` block with interactive check, and explicitly disables Tailscale Funnel. All 15 assertions verified via `deploy/scripts/validate_tailscale_policy.py`.
- **Docker-UFW Bypass Mitigation:** Docker by default injects `PREROUTING` iptables rules that route packets before UFW's `INPUT` chain, which can inadvertently expose published container ports on `eth0`. To prevent this bypass:
  1. All container port bindings in `deploy/docker-compose.staging.yml` are strictly bound to `127.0.0.1` (`127.0.0.1:8089:8089`, `127.0.0.1:8088:80`, `127.0.0.1:8443:443`, `127.0.0.1:55433:5432`, `127.0.0.1:26380:6379`). Because destination IP is `-d 127.0.0.1/32`, external packets arriving on `eth0` never match Docker's DNAT rule and are dropped by UFW's default deny `INPUT` policy.
  2. Linux host firewall configures an isolated custom sub-chain (`MEDIAFORGE-INGRESS`) linked into `DOCKER-USER` with dynamic bridge resolution (`br-${STAGING_NET_ID}`):
     ```bash
     STAGING_NET_ID=$(docker network inspect mediaforge_staging_net --format '{{.Id}}' | cut -c1-12)
     BRIDGE_IF="br-${STAGING_NET_ID}"
     iptables -N MEDIAFORGE-INGRESS 2>/dev/null || iptables -F MEDIAFORGE-INGRESS
     iptables -A MEDIAFORGE-INGRESS -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
     iptables -A MEDIAFORGE-INGRESS -o eth0 -j ACCEPT
     iptables -A MEDIAFORGE-INGRESS -i tailscale0 -o "$BRIDGE_IF" -j ACCEPT
     iptables -A MEDIAFORGE-INGRESS -i "$BRIDGE_IF" -o "$BRIDGE_IF" -j ACCEPT
     iptables -A MEDIAFORGE-INGRESS -i eth0 -o "$BRIDGE_IF" -j DROP
     iptables -A MEDIAFORGE-INGRESS -j RETURN
     iptables -C DOCKER-USER -j MEDIAFORGE-INGRESS 2>/dev/null || iptables -I DOCKER-USER 1 -j MEDIAFORGE-INGRESS
     ```
     Rollback is non-destructive to other Docker services: `iptables -D DOCKER-USER -j MEDIAFORGE-INGRESS; iptables -F MEDIAFORGE-INGRESS; iptables -X MEDIAFORGE-INGRESS`.
- **Port Separation:** Direct SSH/RDP and database ports are prohibited from the public Internet. SSH is strictly confined to Tailscale SSH (`check` action) or authenticated private keys over `tailscale0`.

### Layer 3: Origin & Host Header Verification
The Next.js BFF strictly inspects incoming `Host`, `Origin`, and `Referer` headers using constant-time string comparison (`crypto.timingSafeEqual`) against `ALLOWED_ORIGINS` (`https://staging.mediaforge.local`, `http://localhost:3000`).

---

## 4. Security Architecture & Threat Model

### 4.1 Next.js 15.5.28 Hardening
- **CVE-2025-29927 Immunity:** Next.js was upgraded to 15.5.28, fully resolving authorization-bypass risks present in prior versions.
- **CVE-2024-34351 Immunity:** Built-in image optimization server is disabled (`images: { unoptimized: true }`), preventing SSRF vulnerabilities via crafted `image/vnd.microsoft.icon` or remote SVG requests.
- **Header Obfuscation:** `poweredByHeader: false` suppresses the `X-Powered-By: Next.js` fingerprint header.
- **Vulnerability Audit:** `npm audit --json` verified 0 production vulnerabilities.

### 4.2 Session-Bound HMAC-SHA256 CSRF Protection
The Next.js BFF issues cryptographic CSRF tokens with the following invariants:
- **Format:** `${nonce}:${userId}:${sessionId}:${timestamp}:${hmac}`
- **Signing Algorithm:** HMAC-SHA256 keyed with a 32-byte secret (`CSRF_SECRET_KEY`).
- **Binding:** The token is cryptographically bound to both the authenticated `userId` and the backend `sessionId` (`sid`).
- **Expiration:** Hard maximum lifetime of 24 hours (`86,400,000 ms`).
- **Timing-Safe Verification:** Token verification uses `crypto.timingSafeEqual` to eliminate timing side-channel attacks.
- **Negative Testing Acceptance:** Rejection of missing CSRF (403), forged CSRF (403), and mismatched session CSRF (403) verified in automated headless browser acceptance suites.

### 4.3 BFF Route Lockdown
The catch-all admin route proxy (`web/src/app/api/admin/[...path]/route.ts`) enforces:
- **Cache Invalidation:** `export const dynamic = "force-dynamic"`, `revalidate = 0`. Headers set: `Cache-Control: private, no-store, no-cache, max-age=0, must-revalidate` and `Surrogate-Control: no-store`.
- **Payload Bomb Protection:** Request body sizes exceeding 1MB (1,048,576 bytes) are rejected with HTTP 413 Payload Too Large.
- **Path Traversal Defense:** Paths containing `..`, `//`, or control characters are rejected with HTTP 400 Bad Request.
- **Hop-by-Hop Header Stripping:** Headers including `transfer-encoding`, `connection`, `keep-alive`, and `upgrade` are stripped before proxying to FastAPI.

---

## 5. Secrets Management & KMS/Vault Transition Plan

### Phase 1: Private Staging (Current Implementation)
Staging secrets are managed via an uncommitted, mode-600 environment file `deploy/.env.staging` derived from `deploy/.env.staging.example`:
- `GID_JWT_SECRET_KEY`: 32-byte cryptographically random hex string (`openssl rand -hex 32`).
- `CSRF_SECRET_KEY`: 32-byte cryptographically random hex string.
- Database passwords: Independent 32-character high-entropy strings for `postgres`, `gid_user` (DDL), and `gid_app` (least-privilege DML).

### Phase 2: Production Transition Roadmap (HashiCorp Vault / AWS Secrets Manager)
Prior to production launch:
1. **Secret Storage Migration:** Secrets will be stored in HashiCorp Vault or AWS Secrets Manager with KMS envelope encryption.
2. **Dynamic Database Credentials:** PostgreSQL credentials for `gid_app` will transition to Vault-generated ephemeral credentials with 1-hour TTLs.
3. **Automated Secret Rotation:** `GID_JWT_SECRET_KEY` and `CSRF_SECRET_KEY` will support dual-key verification during rotation windows.
4. **Zero-Secret Containers:** Containers will retrieve secrets via environment injection at runtime using IAM roles / Kubernetes service account tokens, eliminating file-based secret storage.

---

## 6. Zero-Downtime Migration & Database Management Runbook

### Migration Lineage Status
- **Current Head:** `c3d4e5f6a7b8`
- **Managed Schema:** `gid`
- **Total Tables:** 23
- **Foreign Key Constraints:** 37
- **Application Role Privilege:** `gid_app` possesses DML only (`SELECT, INSERT, UPDATE, DELETE`); DDL requires `gid_user`.

### Pre-Deployment Backup Protocol
Before any staging migration is executed:
```bash
# 1. Generate timestamped logical backup with MD5 checksum
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
docker exec postgres pg_dump -U postgres -d grabitdown > deploy/backups/grabitdown_pre_deploy_${TIMESTAMP}.sql
sha256sum deploy/backups/grabitdown_pre_deploy_${TIMESTAMP}.sql > deploy/backups/grabitdown_pre_deploy_${TIMESTAMP}.sql.sha256

# 2. Verify backup readability
head -n 20 deploy/backups/grabitdown_pre_deploy_${TIMESTAMP}.sql
```

### Migration Execution Protocol
```bash
# 3. Apply forward-only migration using schema owner role
export GID_MIGRATION_DATABASE_URL="postgresql+asyncpg://gid_user:${GID_DB_PASSWORD}@postgres:5432/grabitdown"
py -m alembic upgrade head

# 4. Verify post-migration Alembic revision
docker exec postgres psql -U postgres -d grabitdown -c "SELECT version_num FROM gid.alembic_version;"
```

---

## 7. Backup, Disaster Recovery & Automated Restore Verification Runbook

### Recovery Objectives
- **Local Application Orchestration RTO Benchmark:** 9.67 seconds (empirical test restore on 2.35 MB dump, 23 tables, 4,278 rows to live HTTPS 200).
- **Production Scaled RTO:** < 30 minutes (accounting for gigabyte-scale data volumes and index rebuilds).
- **Snapshot-Level RPO:** 0 data loss relative to backup snapshot timestamp.
- **Production Continuous RPO Target:** < 15 minutes (via automated WAL archiving / streaming replication in Phase 2).

### Automated Restore Verification Procedure
As rehearsed in Task 12 (`deploy/scripts/rehearse_staging_dr.py`):
1. Create temporary disposable target: `CREATE DATABASE grabitdown_staging_restore_test;`
2. Restore latest logical dump: `docker exec -i postgres psql -U postgres -d grabitdown_staging_restore_test < backup.sql`
3. Execute validation suite:
   - Check Alembic revision: `SELECT version_num FROM gid.alembic_version;` (Must equal `c3d4e5f6a7b8`).
   - Validate base table count: `SELECT count(*) FROM information_schema.tables WHERE table_schema = 'gid';` (Must equal 23).
   - Validate foreign key count: `SELECT count(*) FROM information_schema.table_constraints WHERE constraint_schema = 'gid' AND constraint_type = 'FOREIGN KEY';` (Must equal 37).
4. Drop disposable database: `DROP DATABASE grabitdown_staging_restore_test;`

---

## 8. Rollback Procedure

In the event of an operational failure during deployment:

### Application Rollback
Because application containers are stateless:
```bash
# Revert to previous Docker image tags
docker compose -f deploy/docker-compose.staging.yml up -d --no-deps web api worker
```

### Database Rollback
1. **Forward-Fix Priority:** In schema-driven platforms, forward-fix migrations are strongly preferred over downgrades.
2. **Alembic Downgrade (If Safe):**
   ```bash
   py -m alembic downgrade -1
   ```
3. **Full Logical Restoration (Disaster Scenario):**
   ```bash
   # Terminate active connections
   docker exec postgres psql -U postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'grabitdown' AND pid <> pg_backend_pid();"
   # Restore clean pre-deploy backup
   docker exec -i postgres psql -U postgres -d grabitdown < deploy/backups/grabitdown_pre_deploy_${TIMESTAMP}.sql
   ```

---

## 9. Release Gates & Production Certification Checklist

Before MediaForge is promoted from private staging to production, the following mandatory release gates must be formally signed off:

- [x] **Gate 1: Next.js Security Vulnerability Remediation**
  - Upgraded to Next.js 15.5.28 (patched for CVE-2025-29927).
  - Unoptimized images enabled (`images: { unoptimized: true }`).
  - 0 vulnerabilities reported by `npm audit`.
- [x] **Gate 2: Session-Bound CSRF & BFF Defense-in-Depth**
  - Cryptographic binding to authenticated `userId` and `sid`.
  - Constant-time signature verification (`crypto.timingSafeEqual`).
  - Strict Origin / Referer validation.
  - Proxy route caching disabled (`Cache-Control: private, no-store`).
- [x] **Gate 3: Authoritative Database Baseline Preservation**
  - Delta = 0 across all verification runs (`plans=60, tenants=855, subs=782, admins=40, transitions=196, audits=867`).
  - Isolated test database (`grabitdown_test`) used for all automated suites.
- [x] **Gate 4: Headless Browser Acceptance Verification**
  - 20/20 end-to-end browser scenarios passed with 100% success rate on headless Chromium.
  - HttpOnly cookie vault verified (0 web storage tokens).
  - Ordinary tenant admin denied access with 403 error banner.
- [x] **Gate 5: Autonomous Scheduler Reliability & Concurrency**
  - 6/6 scheduler acceptance tests passed (`test_step04b1_fixa_scheduler_acceptance.py`).
  - Verified concurrent execution safety using PostgreSQL row-level locks (`FOR UPDATE SKIP LOCKED`).
  - Worker restart resumption and fail-closed emergency pause verified.
- [x] **Gate 6: Commercial Lifecycle Invariants**
  - 12/12 lifecycle invariant tests passed (`test_step04b2a_lifecycle_invariants.py`).
  - Verified trial accumulation, published plan immutability, legacy tenant grandfathering, and reservation continuity during plan transitions.
- [x] **Gate 7: Migration Lineage & DR Rehearsal**
  - Complete lineage executed from scratch to head on disposable DB (`c3d4e5f6a7b8`).
  - Dump, restore, and 100% schema match verified across 23 tables and 37 foreign keys.
- [ ] **Gate 8: Commercial Hard-Quota Rollout Signoff (HOLD)**
  - Staging status: `mode = "disabled"`, `tenant_allowlist = []`.
  - Requires executive signoff before enabling `mode = "allowlist_only"` or `mode = "all_tenants"`.
- [ ] **Gate 9: Live Payment Merchant Account Onboarding (HOLD)**
  - Zero live credentials configured.
  - Requires signed merchant services agreements and PCI-DSS compliance verification before activating payment webhooks.
