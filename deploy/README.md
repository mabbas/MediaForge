# MediaForge / GrabItDown — Docker Development Infrastructure

This directory contains the self-contained, reproducible Docker Compose development infrastructure for the **MediaForge** application.

---

## 1. Fresh-Machine Prerequisites

Before starting the infrastructure on a new developer workstation:

1. **Docker Desktop for Windows:**
   - Docker Engine v24.0+ / Compose v2.20+ installed and running.
   - WSL2 backend enabled.
2. **PowerShell:**
   - Windows PowerShell 5.1 or PowerShell 7+.
   - Execution policy allowing local scripts: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.
3. **Network & Ports:**
   - The following local host ports must be free and not reserved by Windows Hyper-V:
     - `55432` (PostgreSQL 16)
     - `26379` (Redis 7)
     - `51025` (Mailpit SMTP)
     - `58025` (Mailpit Web UI)
     - `58080` (pgAdmin 4)

---

## 2. Environment Initialization

To generate your local environment configuration with cryptographically secure random passwords:

```powershell
# From repository root
cd deploy
.\scripts\Initialize-Dev.ps1
```

- This creates `deploy/.env` from `deploy/.env.example`.
- **Idempotency:** Existing credentials are preserved and will not be overwritten unless `-Force` is supplied.
- **Git Security:** `deploy/.env` is strictly ignored by Git and must never be committed.

---

## 3. Docker Compose Lifecycle

All commands should be executed from the `deploy` directory:

```powershell
cd D:\Dev\MediaForge\deploy

# Validate compose configuration and environment interpolation
docker compose config

# Start all services in the background
docker compose up -d

# Inspect status of services
docker compose ps -a

# View aggregated logs
docker compose logs -f

# View logs for a specific service
docker compose logs -f postgres

# Stop services (preserves all data volumes)
docker compose stop

# Shutdown services and remove container instances (preserves all data volumes)
docker compose down

# DO NOT run 'docker compose down -v' unless you explicitly intend to destroy all local database data!
```

---

## 4. Services, URLs, and Ports

All services bind exclusively to `127.0.0.1` to prevent unauthorized network exposure:

| Service | Container | Host Binding | Internal Port | Purpose |
|---|---|---|---|---|
| **FastAPI Backend** | `mediaforge-api` | `127.0.0.1:58000` | `8000` | Core REST/WebSocket API (`--profile api`) |
| **PostgreSQL 16** | `postgres` | `127.0.0.1:55432` | `5432` | Primary transactional relational database |
| **Redis 7** | `redis` | `127.0.0.1:26379` | `6379` | Cache, task queues, and download state tracking |
| **Mailpit SMTP** | `mailpit` | `127.0.0.1:51025` | `1025` | Local SMTP capture (no external mail forwarding) |
| **Mailpit Web UI**| `mailpit` | `127.0.0.1:58025` | `8025` | Browser interface for reviewing captured emails |
| **pgAdmin 4** | `pgadmin` | `127.0.0.1:58080` | `80` | Database management and SQL query console |

---

## 5. Database Connection & Role Specifications

### Administrative Access (Superuser)
- **Host:** `127.0.0.1` | **Port:** `55432` | **Database:** `grabitdown`
- **Username:** `postgres`
- **Password:** Defined in `deploy/.env` (`POSTGRES_PASSWORD`)

### Migration Role (`gid_user` — Schema Owner / DDL)
Used exclusively by CI/CD pipelines and local Alembic CLI for migrations.
- **Username:** `gid_user`
- **Permissions:** Schema owner of `gid`. Full DDL authority (`CREATE`, `ALTER`, `DROP`, `TRUNCATE`).
- **Alembic URL (Host):**
  `postgresql+asyncpg://gid_user:<password>@127.0.0.1:55432/grabitdown`

### Runtime Application Role (`gid_app` — Least-Privilege DML)
Used by FastAPI application containers and local backend workers.
- **Username:** `gid_app`
- **Permissions:** DML only: `SELECT`, `INSERT`, `UPDATE`, `DELETE` on all tables in `gid`. **Zero DDL privileges**.
- **Host Runtime URL:**
  `postgresql+asyncpg://gid_app:<password>@127.0.0.1:55432/grabitdown`
- **Docker Container Network URL:**
  `postgresql+asyncpg://gid_app:<password>@postgres:5432/grabitdown`

### pgAdmin 4 Access
- **URL:** [http://127.0.0.1:58080](http://127.0.0.1:58080)
- **Login Email:** `admin@mediaforge.com`
- **Login Password:** Defined in `deploy/.env` (`PGADMIN_DEFAULT_PASSWORD`)
- **Connecting to PostgreSQL from inside pgAdmin:**
  - Host name / address: `postgres`
  - Port: `5432`
  - Maintenance database: `grabitdown`
  - Username: `postgres`, `gid_user`, or `gid_app`

---

## 6. Windows Excluded Port Troubleshooting

Windows (via Hyper-V, WSL2, or WinNAT) frequently reserves dynamic port ranges. If a port conflict occurs during startup:

1. Check active exclusions:
   ```powershell
   netsh interface ipv4 show excludedportrange protocol=tcp
   ```
2. Verify active listeners:
   ```powershell
   Get-NetTCPConnection -LocalPort 55432, 26379, 58025, 51025, 58080 -ErrorAction SilentlyContinue
   ```
3. If an exclusion overlaps:
   - Change the conflicting port variable in `deploy/.env` (e.g., `POSTGRES_PORT=55433`).
   - Re-run `docker compose up -d`.

---

## 7. Database Backup & Safe Restoration

Automated PowerShell utilities are located in `deploy/scripts/`:

### Creating a Backup
```powershell
# Creates a compressed, validated .dump in deploy/backups/
.\scripts\Backup-Postgres.ps1

# Specify database or format
.\scripts\Backup-Postgres.ps1 -Database grabitdown -Format custom
```

### Restoring a Backup
By default, the restore tool restores into a sandbox database (`grabitdown_restore_test`) to prevent accidental destruction of production or active development data:

```powershell
# Safe sandbox restore
.\scripts\Restore-Postgres.ps1 -BackupFile .\backups\grabitdown_backup_20261018_120000.dump -TargetDatabase grabitdown_restore_test

# Authoritative database restore (requires explicit confirmation flag)
.\scripts\Restore-Postgres.ps1 -BackupFile .\backups\grabitdown_backup_20261018_120000.dump -TargetDatabase grabitdown -AllowAuthoritativeOverwrite -CleanFirst
```

---

## 8. Data Persistence Architecture

All service data is isolated in Docker-managed named volumes:
- `mediaforge_pgdata`: PostgreSQL data directory (`/var/lib/postgresql/data`)
- `mediaforge_redis_data`: Redis Append-Only File (`/data`)
- `mediaforge_mailpit_data`: Mailpit email store (`/data`)
- `mediaforge_pgadmin_data`: pgAdmin configuration and query sessions (`/var/lib/pgadmin`)

All volumes and containers attach exclusively to the `mediaforge_net` bridge network.

---

## 9. Safe Future Git Tracking

- **Git-ignored Artifacts:**
  - `deploy/.env`
  - `deploy/backups/*` (except `deploy/backups/.gitkeep`)
  - `*.dump`, `*.sql.gz`
- **Committed Infrastructure:**
  - `deploy/docker-compose.yml`
  - `deploy/.env.example`
  - `deploy/postgres/init/01-init-database.sh`
  - `deploy/scripts/*.ps1`
  - `deploy/backups/.gitkeep`
  - `deploy/README.md`

---

---

## 10. FastAPI Application Service (`--profile api`)

The FastAPI application service is containerized as `mediaforge-api` and runs under the optional Docker Compose profile `api`.

### 10.1 Service Architecture & Security Boundaries
- **Profile Isolation:** Default `docker compose up -d` only runs the 4 infrastructure services (`postgres`, `redis`, `mailpit`, `pgadmin`). The API is only launched when explicitly requesting `--profile api`.
- **Non-Root Execution:** Runs inside the container under unprivileged user `mediaforge` (UID 1000).
- **Least-Privilege Database Access:** Connects using `gid_app` on internal Docker network (`postgres:5432`), strictly denied DDL authority.
- **Loopback Host Binding:** Bound strictly to `127.0.0.1:58000` (never LAN/0.0.0.0).
- **Persistent Media Volumes:**
  - `mediaforge_api_data`: Application logs and internal database state (`/app/data`).
  - `mediaforge_api_downloads`: Media downloads and parts (`/app/downloads`).

### 10.2 Lifecycle Commands
All commands should be executed from `D:\Dev\MediaForge\deploy`:

```powershell
# Build the API image (from repo root build context)
docker compose --profile api build api

# Start the API container in background
docker compose --profile api up -d api

# Inspect container status and health
docker compose --profile api ps

# Stream API container logs
docker compose --profile api logs -f api

# Stop and remove the API container (infrastructure services keep running)
docker compose --profile api stop api
docker compose --profile api rm -f api
```

### 10.3 API Endpoints & Health Verification
- **Swagger UI:** [http://127.0.0.1:58000/docs](http://127.0.0.1:58000/docs)
- **OpenAPI Schema:** [http://127.0.0.1:58000/openapi.json](http://127.0.0.1:58000/openapi.json)
- **Liveness Endpoint:** [http://127.0.0.1:58000/api/v1/health](http://127.0.0.1:58000/api/v1/health)
- **Readiness Endpoint:** [http://127.0.0.1:58000/api/v1/health/ready](http://127.0.0.1:58000/api/v1/health/ready)
  - Checks live database query (`SELECT 1`), FFmpeg availability, yt-dlp version, and disk space.

### 10.4 Authentication & Security Notice
- The application currently operates in **personal mode** where localhost requests are automatically mapped to the default system owner tenant/user (`00000000-0000-0000-0000-000000000002`).
- **SECURITY BLOCKER FOR SHARED/PUBLIC DEPLOYMENT:** The container must NEVER be exposed beyond `127.0.0.1` or deployed to shared hosting until STEP 02 introduces multi-tenant JWT authentication and API key verification.
