# MediaForge — Gate 2 Local Checkpoint Certification & Pre-Commit Approval Package (STEP 04-B2-C, Fix-A, Fix-B & Fix-C)

## 1. Executive Summary & Gate 2 Purpose

This document constitutes the authoritative, final pre-commit certification package for MediaForge release candidate **`v0.4.0-staging.1`** at Git HEAD `f43ae562408e4e217920d78c47a5019099af376b` on branch `main`.

Following the comprehensive qualification in **STEP 04-B2-C**, the TLS and access-control refinements in **STEP 04-B2-C-Fix-A**, the data reconciliation and network hardening in **STEP 04-B2-C-Fix-B**, and the final Tailscale-to-Caddy ingress verification in **STEP 04-B2-C-Fix-C**, this package certifies:
1. Conclusive resolution of cross-report database row count discrepancies (exact count 4,278 vs statistical estimate; Delta = 0).
2. Complete audit and verification of host-port bindings (strictly 127.0.0.1 across all 12 containers).
3. Dedicated loopback HTTP upstream listener on port `8089` eliminating Caddy automatic HTTPS 308 redirect loops when reverse-proxied by Tailscale Serve.
4. Validated Tailscale access control specification (HuJSON) with grant semantics, Funnel audit, and delineation of local vs remote policy checks.
5. Exact Tailscale Serve CLI command syntax (`tailscale serve --bg --https=8443 http://127.0.0.1:8089`) with browser-trusted public Let's Encrypt TLS.
6. Minimal, scoped, idempotent Docker firewall architecture using a dedicated custom sub-chain (`MEDIAFORGE-INGRESS`) with safe rollback runbooks.
7. Honest, uncompromised TLS acceptance boundary classification (PARTIAL for local headless testing).
8. Exhaustive pre-commit file manifest (48 modified tracked files, source-controlled additions, zero secrets, zero git commits).

---

## 2. Cross-Report Database Count Reconciliation

### The Discrepancy
- **STEP 04-B2-C Report:** Stated 856 tenants, 61 commercial plans, 870 commercial audit logs.
- **STEP 04-B2-C-Fix-A Report:** Stated 855 tenants, 60 commercial plans, 867 commercial audit logs.

### Empirical Investigation Findings
A deep audit of query commands and historical database dumps conclusively proved:

1. **Statistical Estimate vs Transactional Count:**
   - In STEP 04-B2-C (transcript step 16571), the report table was populated from:
     ```sql
     SELECT schemaname, relname, n_live_tup FROM pg_stat_user_tables WHERE schemaname = 'gid';
     ```
     This query returns `n_live_tup`, which is PostgreSQL's asynchronous statistics collector estimate, NOT an exact count.
   - In STEP 04-B2-C-Fix-A, `scripts/verify_dev_db_pristine.py` queried exact transactional row counts using:
     ```sql
     SELECT count(*) FROM gid."{table}";
     ```
2. **Pre-Qualification Backup Verification:**
   Inspection of `deploy/backups/grabitdown_pre_step04b2c.sql` (taken Oct 31, 2026 at 15:31:00, prior to STEP 04-B2-C qualification) proved the exact row counts were:
   - `gid.tenants`: **855**
   - `gid.commercial_plans`: **60**
   - `gid.commercial_audit_logs`: **867**
3. **Primary Key Match:**
   A 100% primary key comparison between `grabitdown_pre_step04b2c.sql` and the live `grabitdown` database confirmed:
   - Missing from live DB: **0 rows**
   - Extra in live DB: **0 rows**
   - Exact PK match: **100.0%**
4. **Data Mutation Audit:**
   The latest `created_at` timestamp across all tenants and audit logs in `grabitdown` is `2026-10-30 06:22:05`. No records were added, deleted, or altered on Oct 31, 2026.
5. **Grandfathered Accounts & Reference Integrity:**
   - All **22 grandfathered `legacy_active` accounts** are 100% intact.
   - Orphaned subscriptions: **0**
   - Orphaned plans: **0**
   - Orphaned entitlements: **0**
   - Orphaned audit logs: **0**

### Authoritative Reconciliation Table

| Table Name | Exact Live Count (`count(*)`) | Stats Estimate (`n_live_tup`) | Discrepancy Cause | Integrity Verdict |
| :--- | :---: | :---: | :--- | :--- |
| `gid.tenants` | **855** | 856 | Stats collector tuple drift (+1) | **VERIFIED & IMMUTABLE** |
| `gid.commercial_plans` | **60** | 61 | Stats collector tuple drift (+1) | **VERIFIED & IMMUTABLE** |
| `gid.commercial_audit_logs` | **867** | 870 | Stats collector tuple drift (+3) | **VERIFIED & IMMUTABLE** |
| `gid.tenant_subscriptions` | **782** | 782 | Exact match | **VERIFIED & IMMUTABLE** |
| `gid.commercial_plan_entitlements` | **78** | 78 | Exact match | **VERIFIED & IMMUTABLE** |
| `gid.users` | **517** | 517 | Exact match | **VERIFIED & IMMUTABLE** |

**Conclusion:** The authoritative development database `grabitdown` remained 100% pristine throughout all milestones (**Delta = 0**).

---

## 3. Host-Port Exposure Audit & Verification

### The Finding
The STEP 04-B2-C report text mentioned `0.0.0.0` bindings due to legacy documentation snippets. A comprehensive socket and configuration audit revealed:

1. **Docker Compose Declarations:** Both `deploy/docker-compose.yml` (dev) and `deploy/docker-compose.staging.yml` (staging) explicitly configure `127.0.0.1:` on all exposed ports.
2. **Container HostConfig:** `docker inspect` across all running containers confirmed `.HostConfig.PortBindings` specifies `HostIp: "127.0.0.1"` for every published port.
3. **Windows Host Sockets:** `Get-NetTCPConnection` confirmed that **100% of listening sockets** are bound strictly to `127.0.0.1`.

### Host Exposure Matrix

| Service | Container Name | Internal Port | Host Port | Bound IP | Public Exposure Risk |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Dev PostgreSQL** | `postgres` | 5432 | 55432 | `127.0.0.1` | **ZERO (Loopback Only)** |
| **Dev Redis** | `redis` | 6379 | 26379 | `127.0.0.1` | **ZERO (Loopback Only)** |
| **Dev FastAPI Core** | `mediaforge-api` | 8000 | 58000 | `127.0.0.1` | **ZERO (Loopback Only)** |
| **Dev pgAdmin** | `pgadmin` | 80 | 58080 | `127.0.0.1` | **ZERO (Loopback Only)** |
| **Dev Mailpit SMTP** | `mailpit` | 1025 | 51025 | `127.0.0.1` | **ZERO (Loopback Only)** |
| **Dev Mailpit Web** | `mailpit` | 8025 | 58025 | `127.0.0.1` | **ZERO (Loopback Only)** |
| **Staging PostgreSQL**| `mediaforge-staging-postgres` | 5432 | 55433 | `127.0.0.1` | **ZERO (Loopback Only)** |
| **Staging Redis** | `mediaforge-staging-redis` | 6379 | 26380 | `127.0.0.1` | **ZERO (Loopback Only)** |
| **Staddy Staging HTTP (Redirect)** | `mediaforge-staging-caddy` | 80 | 8088 | `127.0.0.1` | **ZERO (Loopback Only — 308 to 8443)** |
| **Staging Caddy HTTP (Upstream)** | `mediaforge-staging-caddy` | 8089 | 8089 | `127.0.0.1` | **ZERO (Loopback Only — Tailscale Upstream)** |
| **Staging Caddy HTTPS**| `mediaforge-staging-caddy` | 443 | 8443 | `127.0.0.1` | **ZERO (Loopback Only — Internal PKI)** |

**Action Taken:** No container restart or disruption was necessary; the live runtime topology is already loopback-hardened.

---

## 4. Authoritative Tailscale Access Control & Funnel Audit

### Policy Specification (`deploy/staging/tailscale-staging-acl.hujson`)
The Tailscale ACL specification enforces:
- **Default Deny Posture:** Zero `reject` actions (invalid in Tailscale ACL syntax); all rules use `"action": "accept"`.
- **Identity Groups:** `group:staging-admins` and `group:staging-auditors` model human operators.
- **Tag Ownership:** `tag:vps-staging` and `tag:ci-cd` are strictly owned by `group:staging-admins`.
- **Protocol & Port Separation:**
  - Administrative Web: Port `8443` (HTTPS) accessible to `group:staging-admins`.
  - CI/CD Deployment: Port `22` (SSH) accessible to `tag:ci-cd`.
  - Databases & Internal Ports: Ports 5432, 6379, 8000, 3000 have no allow rules and are strictly denied.
- **Dedicated Tailscale SSH:** Managed in the `"ssh"` block with `"action": "check"`.

### Funnel Permission & Inheritance Audit
- **Funnel Threat Model:** Tailscale Funnel exposes nodes to the public Internet, defeating private staging boundaries.
- **Node Attribute Audit:** In `nodeAttrs`, `tag:vps-staging` has `attr: []`.
- **Inheritance Caveat:** If an enterprise tailnet contains a wildcard grant such as `{"target": ["*"], "attr": ["funnel"]}`, `attr: []` does not revoke inherited permissions.
- **Operational Requirement:**
  1. The staging VPS deployment runbook strictly prohibits running `tailscale funnel <port> on`.
  2. The staging service is exposed exclusively via `tailscale serve` (private to tailnet).
  3. Tailnet administrators must verify that `funnel` is not targeted at `*` or `tag:vps-staging`.

### Policy Engine Status & Delineation
- **Local Simulator Tests (PASSED 15/15):** Verified via `deploy/scripts/validate_tailscale_policy.py`. Validates JSON/HuJSON parsing, tag ownership structure, absence of invalid `reject` actions, default-deny behavior across 12 ports, and non-admin/CI/CD segregation.
- **Authoritative Remote Tailnet Validation (PENDING HOSTED PROVISIONING):**
  - Requires active connection to a corporate Tailscale coordination server.
  - Requires binding actual identity provider (IdP) directory users to `group:staging-admins`.
  - Requires attaching and authenticating the Linux VPS node under `tag:vps-staging`.
  - Requires tailnet-wide audit confirming no ancestor rule grants wildcard Funnel (`* -> funnel`).
  - Marked honestly as **REMOTE VALIDATION PENDING**.

---

## 5. Final Tailscale Serve & Caddy Ingress Architecture

```mermaid
flowchart TD
    subgraph "Authorized Tailnet Boundary"
        Operator["Authorized Admin Browser<br/>(Tailscale Client)"]
    end

    subgraph "Linux VPS Host (tag:vps-staging)"
        TS_Serve["Tailscale Daemon / Serve<br/>(MagicDNS: staging-node.tailnet.ts.net)<br/>Auto Let's Encrypt Public TLS<br/>Interface: tailscale0, Port 8443"]

        subgraph "Host Loopback Network (127.0.0.1)"
            LB_Port["127.0.0.1:8089 (Dedicated Loopback HTTP Upstream Listener)"]
        end

        subgraph "Docker Compose Staging Stack"
            Caddy["Caddy 2 Reverse Proxy<br/>(Container: mediaforge-staging-caddy)<br/>Preserves X-Forwarded-* Headers<br/>Configured with trusted_proxies"]
            Web["Next.js 15.5.28 Admin Portal (Port 3000)<br/>BFF Route Proxy & Cookie Vault"]
            API["FastAPI Commercial Core (Port 8000)<br/>System Admin RBAC & Entitlements"]
        end
    end

    Operator -->|HTTPS (Browser-Trusted Let's Encrypt)| TS_Serve
    TS_Serve -->|Internal Proxy (HTTP)| LB_Port
    LB_Port --> Caddy
    Caddy -->|/api/admin/* (BFF)| Web
    Caddy -->|/api/v1/* (Direct API)| API
    Web -->|Internal Service Proxy| API
```

### Architecture Decision: Option A (Tailscale Serve to Loopback HTTP)

#### Discovery and Resolution of Port 8088 Redirect Loop:
1. **The Risk Identified:** Caddy's default configuration on HTTP port 80/8088 performs an automatic `HTTP 308 Permanent Redirect` with `Location: https://127.0.0.1/` to enforce TLS. If Tailscale Serve terminated HTTPS at `https://node.tailnet.ts.net:8443` and forwarded requests to `http://127.0.0.1:8088`, client browsers would receive a 308 redirect pointing back to `https://127.0.0.1/`, causing an immediate redirect loop or connection refusal.
2. **Dedicated Upstream Listener (Port 8089):**
   - Added published port `127.0.0.1:8089:8089` in `deploy/docker-compose.staging.yml`.
   - Added dedicated `http://:8089` block in `deploy/staging/Caddyfile`:
     - Explicit `http://` scheme prevents automatic HTTPS redirects.
     - Restricts `remote_ip` to loopback (`127.0.0.1/32`, `::1`), RFC 1918, and Tailscale CGNAT (`100.64.0.0/10`).
     - Configures `trusted_proxies private_ranges 127.0.0.1/32` so incoming `X-Forwarded-*` headers are honored.
     - Routes `/api/v1/*` directly to `api:8000` and `/*` fallback to `web:3000`.
   - **Verification:** Empirically verified across 19 automated assertions in `scripts/test_upstream_http_ingress.js` (zero 301/308 redirects, HTTP 200 on login and API health, static asset delivery, CSRF origin rejection, WebSocket upgrade).

#### Tailscale Serve CLI Commands:
- **Enable Ingress Proxy:**
  ```bash
  tailscale serve --bg --https=8443 http://127.0.0.1:8089
  ```
- **Inspect Status:**
  ```bash
  tailscale serve status
  ```
- **Teardown / Reset:**
  ```bash
  tailscale serve reset
  ```

---

## 6. Scoped Docker Firewall Architecture & Rollback Strategy

### The Architectural Context
Docker manages its own iptables chains (`PREROUTING`, `DOCKER`, `DOCKER-USER`). However:
1. **Loopback Binding Eliminates External Forwarding:** Because all published ports in `deploy/docker-compose.staging.yml` are bound explicitly to `127.0.0.1` (e.g. `127.0.0.1:8089:8089`), Docker's DNAT rule in the `nat` table matches **only** packets with destination `-d 127.0.0.1/32`. Packets arriving from external network interfaces (`eth0`) never match this DNAT rule. Therefore, they are not routed across the Docker bridge and do not enter `DOCKER-USER`; instead, they hit the host `INPUT` chain where UFW's default drop policy drops them.
2. **Dynamic Docker Bridge Interface Names:** Docker dynamically names bridge networks using the first 12 characters of the network ID (e.g. `br-0672e7d8d135`), rather than static strings like `br-staging`. Hardcoding `br-staging` in firewall rules causes silent rule failure.
3. **Flushing `DOCKER-USER` is Unsafe:** Running `iptables -F DOCKER-USER` is destructive, as it wipes out firewall rules established by Docker or other co-located container workloads.

### Isolated Custom Sub-Chain Strategy (`MEDIAFORGE-INGRESS`)
To provide defense-in-depth without risking host services or other containers, MediaForge uses a dedicated, isolated sub-chain:

```bash
#!/usr/bin/env bash
# MediaForge Isolated Staging Ingress Firewall Policy
set -euo pipefail

# 1. Resolve dynamic Docker bridge interface for staging_net
STAGING_NET_ID=$(docker network inspect mediaforge_staging_net --format '{{.Id}}' | cut -c1-12)
BRIDGE_IF="br-${STAGING_NET_ID}"

# 2. Create dedicated custom sub-chain (idempotent)
iptables -N MEDIAFORGE-INGRESS 2>/dev/null || iptables -F MEDIAFORGE-INGRESS

# 3. Populate MEDIAFORGE-INGRESS rules
# Allow established/related traffic
iptables -A MEDIAFORGE-INGRESS -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
# Allow outbound egress to WAN
iptables -A MEDIAFORGE-INGRESS -o eth0 -j ACCEPT
# Allow forwarded traffic from Tailscale interface
iptables -A MEDIAFORGE-INGRESS -i tailscale0 -o "$BRIDGE_IF" -j ACCEPT
# Allow intra-bridge communication
iptables -A MEDIAFORGE-INGRESS -i "$BRIDGE_IF" -o "$BRIDGE_IF" -j ACCEPT
# Drop direct forwarding from public WAN to staging bridge
iptables -A MEDIAFORGE-INGRESS -i eth0 -o "$BRIDGE_IF" -j DROP
# Return to parent chain for any other traffic
iptables -A MEDIAFORGE-INGRESS -j RETURN

# 4. Link into DOCKER-USER idempotently at top
iptables -C DOCKER-USER -j MEDIAFORGE-INGRESS 2>/dev/null || iptables -I DOCKER-USER 1 -j MEDIAFORGE-INGRESS
```

### Isolated Rollback Runbook (Zero Impact on Other Services)
```bash
# Surgical removal of MediaForge sub-chain without flushing DOCKER-USER:
iptables -D DOCKER-USER -j MEDIAFORGE-INGRESS 2>/dev/null || true
iptables -F MEDIAFORGE-INGRESS 2>/dev/null || true
iptables -X MEDIAFORGE-INGRESS 2>/dev/null || true
```

---

## 7. TLS Verification Limitations & Operational Realities

| Environment | TLS Mechanism | Certificate Issuer | Browser Trust Status | Classification |
| :--- | :--- | :--- | :--- | :--- |
| **Local Automated Test** | Caddy Internal PKI | `Caddy Local Authority` (Private Root) | Fails closed (`ERR_CERT_AUTHORITY_INVALID`) unless bypass flags or OS store modified | **PARTIAL** |
| **Local Operator Browser** | Caddy Internal PKI | `Caddy Local Authority` (Private Root) | Trusted after running `certutil -user -addstore Root caddy_root.crt` | **FULL (MANUAL RUNBOOK)** |
| **Remote Private Staging** | Tailscale Serve | Let's Encrypt (Public CA) via MagicDNS | Natively trusted by all browsers with zero configuration | **FULL (ARCHITECTURAL)** |

**Key Decision:** The local automated headless test classification remains **PARTIAL**. No further private CA injection tools are required because the Tailscale Serve architecture natively solves browser trust for remote staging.

---

## 8. Complete Pre-Commit File Manifest (Gate 2 Checkpoint)

### Summary
- **Tracked Modified Files:** 48
- **Untracked Additions (Source Control Candidates):** 211 files (including migrations, services, schemas, admin portal, tests, validation scripts, and documentation)
- **Excluded Sensitive / Build Files:** 100% ignored via `.gitignore`
- **Clean Source Snapshot:** `deploy/snapshots/mediaforge_source_step04b2c_20261031.zip` (465 files, 1.30 MB, SHA-256: `6d62e6d43a0654b5b1baaf95c8e1ff56e05e3be4777ea8bc4d60109f4c909654`)

### Category Breakdown of Additions Proposed for Source Control:
1. **Commercial Backend Services:** `api/services/enforcement_service.py`, `entitlement_service.py`, `subscription_lifecycle_service.py`, `job_service.py`, `usage_service.py`, `manage_user.py`, `session_service.py`, `retention_service.py`.
2. **Database Migrations:** 12 versioned migrations up to Alembic head `c3d4e5f6a7b8`.
3. **Super Admin Web Portal:** Next.js pages under `web/src/app/admin/*` and BFF proxy routes under `web/src/app/api/admin/*`, `web/src/app/api/auth/*`.
4. **Desktop Electron App:** `desktop/src/*`, `desktop/electron/*`, `desktop/tests/*`.
5. **Deployment & Security:** `deploy/docker-compose.staging.yml`, `deploy/staging/Caddyfile`, `deploy/staging/tailscale-staging-acl.hujson`, `deploy/scripts/*`.
6. **Architecture Documents:** 21 specifications under `docs/architecture/*`.
7. **Testing & Ingress Validation:** `scripts/test_upstream_http_ingress.js` (19 assertions), `scripts/scan_untracked_secrets.py`, `scripts/verify_dev_db_pristine.py`.

### Complete Exclusion Manifest:
- `.env`, `deploy/.env`, `deploy/.env.staging` (Contains live/staging secrets — verified ignored by `.gitignore`)
- `deploy/backups/*.sql`, `*.dump` (Database snapshots — verified ignored by `.gitignore`)
- `deploy/snapshots/*.zip` (Build snapshot archives — verified ignored by `.gitignore`)
- `node_modules/`, `web/.next/`, `desktop/release/`, `dist/` (Compiled artifacts)
- `__pycache__/`, `.pytest_cache/` (Python bytecode and caches)
- Staging media uploads in volume `staging_media_data`

---

## 9. Proposed Git Commit Message (Gate 2 Checkpoint)

*(Proposed only — strictly zero commits or pushes executed)*

```
feat(commercial): qualify private staging candidate v0.4.0-staging.1 with verified upstream ingress and isolated firewall policy

- Configure dedicated loopback HTTP listener on port 8089 in Caddy and docker-compose.staging.yml to eliminate 308 redirect loops under Tailscale Serve
- Verify upstream loopback ingress across 19 automated assertions (health, zero redirects, Next.js admin login, BFF Secure/HttpOnly cookies, CSRF rejection, WebSockets)
- Define modern Tailscale Serve background proxying runbook (tailscale serve --bg --https=8443 http://127.0.0.1:8089)
- Architect isolated custom firewall sub-chain (MEDIAFORGE-INGRESS) linked into DOCKER-USER with dynamic bridge resolution and surgical zero-impact rollback
- Enforce strict fail-closed TLS certificate rejection and classify headless browser acceptance as PARTIAL pending operator root CA installation
- Correct Tailscale staging ACL policy in deploy/staging/tailscale-staging-acl.hujson using valid HuJSON syntax, groups, tag owners, SSH check block, and automated simulator assertions
- Qualify disaster recovery benchmarks distinguishing 9.67s local HTTPS RTO from production scaled targets, and backup snapshot RPO from continuous streaming WAL
- Verify clean source snapshot mediaforge_source_step04b2c_20261031.zip (465 files, 1.30 MB, SHA-256 6d62e6d4...) with zero binaries, dumps, or secrets
- Confirm zero mutations on authoritative development database grabitdown (Delta = 0 across 23 tables, 4,278 rows)
- Maintain safe operational baseline: commercial:enforcement = disabled and commercial:scheduler_config = allowlist_only with empty allowlists
```

---

## 10. Operational Status & Final Decision Package

| Gate Check | Classification | Evidence & Operational Scope |
| :--- | :--- | :--- |
| **OVERALL GATE STATUS** | **PASS WITH WARNINGS** | All criteria pass; warning applies to local headless TLS trust boundary |
| **LOCAL STAGING STACK** | **READY** | All 12 containers healthy (6 staging, 6 dev); loopback-only host binding (127.0.0.1) |
| **INGRESS PROXY CORRECTNESS** | **VERIFIED** | Port 8089 upstream verified (19/19 passed); zero 301/308 redirects; BFF CSRF and cookies intact |
| **DATABASE INTEGRITY** | **VERIFIED** | Delta = 0 confirmed (23 tables, 4,278 rows); stats estimate discrepancy reconciled; 22 legacy accounts intact |
| **PRIVATE INGRESS DESIGN** | **READY** | Option A (Tailscale Serve -> loopback 8089) finalized; MEDIAFORGE-INGRESS sub-chain specified |
| **TLS STRATEGY** | **READY** | Remote browser trust guaranteed via Let's Encrypt / MagicDNS; local partial status documented |
| **SOURCE CHECKPOINT** | **READY FOR GATE 2** | 48 modified files audited; 211 untracked candidates; 0 secrets; clean exclusions; proposed commit prepared |
| **REMOTE VPS DEPLOYMENT** | **NOT AUTHORIZED** | Awaiting user sign-off; remote network and client access **NOT VERIFIED IN HOSTED ENVIRONMENT** |
| **PUBLIC COMMERCIAL RELEASE**| **NOT AUTHORIZED** | Payment credentials unconfigured; hard quota enforcement held in disabled mode |
