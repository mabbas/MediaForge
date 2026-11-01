# MediaForge Media Backup, Storage Durability & Retention Architecture

## 1. Executive Summary & Durability Philosophy

MediaForge (GrabItDown) provides enterprise-grade media download, clip extraction, transcode, and concatenation capabilities. Customer media assets and derivative works (clips, audio extracts, composite videos) represent critical customer data that must be safeguarded against accidental loss, unauthorized multi-tenant cross-contamination, and storage corruption.

To establish absolute durability and operational confidence, MediaForge implements the following core tenets:

1. **Default Retain-Forever Policy**: Customer media is retained indefinitely (`retention_days = -1`) unless a tenant explicitly configures an automated retention window. No physical files or database records are deleted automatically by default.
2. **Dual-System Coordinated Backup**: Authoritative media metadata residing in PostgreSQL (`gid.media_artifacts`, `gid.download_jobs`) and binary video/audio streams residing in the Docker storage volume (`mediaforge_api_downloads`) are backed up via decoupled, host-backed utilities that permit point-in-time cross-verification.
3. **Strict Read-Only Retention Previews**: Retention evaluation is implemented as a deterministic, tenant-aware dry run. The engine calculates eligible items and reclaimable storage without executing mutations or file system deletions.
4. **Referential & Historical Protection**: Artifacts referenced as inputs by other composite media, source media associated with download jobs, and known pre-Fix-B historical test anomalies are strictly isolated and preserved unmodified.
5. **Zero Silent Data Loss**: Physical media removal cannot be performed without explicit administrative invocation, authenticated multi-tenant RBAC checks, and prior cryptographic validation of backup archives.

---

## 2. Docker Volume Backup Architecture & Quiescent Procedure

### 2.1 Storage Volume Layout

The API service mounts the named Docker volume `mediaforge_api_downloads` to `/app/downloads`. The physical directory layout inside the container enforces tenant boundary isolation:

```
/app/downloads/
└── <tenant_id>/
    ├── clips/
    │   └── <artifact_id>.mp4
    ├── merged/
    │   └── <artifact_id>.mp4
    ├── audio/
    │   └── <artifact_id>.mp3
    └── <download_id>.mp4
```

### 2.2 Host-Backed Backup Mechanism (`Backup-MediaVolume.ps1`)

Docker volumes on Windows (Docker Desktop / WSL2) reside in isolated virtual disks (`vhdx`). To produce durable, host-backed archives without streaming binary data through Windows PowerShell stdout pipelines (which risk UTF-16 corruption), MediaForge executes a containerized GNU `tar` utility with direct volume mounts:

```powershell
deploy/scripts/Backup-MediaVolume.ps1 [-VolumeName <name>] [-BackupDir <dir>] [-AllowNonQuiescent]
```

**Architecture & Operational Safety Controls:**
1. **Container Tooling**: Uses `postgres:16-alpine` (pre-cached locally in the developer environment) to run GNU `tar`.
2. **Read-Only Source Mount**: The volume is mounted read-only (`:ro`) at `/source` to guarantee zero modification during archive generation.
3. **Quiescent Writer Audit**: Inspects running containers mounting the volume by reading `/proc/[0-9]*/cmdline`. If active media processing writers (`ffmpeg` or `yt-dlp`) are actively modifying files, the backup script halts immediately with an error unless `-AllowNonQuiescent` is explicitly supplied. This prevents tearing, incomplete video frames, and inconsistent snapshot states.
4. **Overwrite Prevention**: Checks destination host storage before archive generation. If a file with the target timestamp name already exists, the script aborts immediately.
5. **Companion Cryptographic Checksum**: Calculates the host-side SHA-256 hash and writes a companion `.sha256` sidecar file formatted as `<hash>  <filename>`.
6. **Failure Cleanup & Archive Integrity Validation**: Evaluates `tar -tzf` against the created archive immediately after export. If tar fails or yields a corrupt table of contents, partial archives and checksums are unlinked and the script exits with code 1.
7. **Git Exclusion**: Confirms `.gitignore` ignores all `.tar.gz` and `.sha256` files in `deploy/backups/`.

### 2.3 Consistency Risks of In-Flight Media Writes & Non-Quiescent Backups

When media files are modified while a backup is executing, severe consistency hazards arise:
1. **Torn Video Streams**: FFmpeg multiplexing writes interleaved audio and video chunks followed by container header metadata (`moov` atom in MP4). If `tar` captures the file mid-write, the resulting archive contains an unplayable video file with corrupted indexes or invalid payload lengths.
2. **Database-Storage Desynchronization**: If PostgreSQL commits a `MediaArtifactDB` row with status `ready` after `tar` has already read the volume directory, the backup represents a state where PostgreSQL claims a file exists, but the restored volume lacks the actual file. Conversely, if a file is partially written before the database transaction aborts, the restored volume contains an orphaned, unreferenced file.
3. **Quiescent Policy Enforcement**: MediaForge strictly refuses to claim a valid consistent snapshot if active media processing writers cannot be excluded. The backup utility polls `/proc` cmdlines across all containers attached to the target volume and aborts if writers are detected.

---

## 3. Real Media Backup & Restoration Evidence (STEP 02-E2A-Fix-A)

### 3.1 Real File-Backed Backup Evidence

Under STEP 02-E2A-Fix-A, a real 5-second synthetic source video was generated, two independent clips were extracted (0-2s and 2-4s), and the clips were merged into a composite video via the authenticated FastAPI endpoints. While the media files resided on `mediaforge_api_downloads`, `Backup-MediaVolume.ps1` produced a real file-backed archive:

| Attribute | Verification Metric |
| :--- | :--- |
| **Backup Archive Path** | `deploy/backups/mediaforge_api_downloads_backup_20261019_154608.tar.gz` |
| **Archive File Size** | **515,665 bytes (503.58 KB)** — Contains real video streams, not empty directories |
| **Total Archive Entries** | 17 filesystem items |
| **Archive SHA-256** | `9630A5E04CA3624B5716104CC06C8B815EAB58AB07BD95A4D09B62A1AEF3CB94` |
| **Companion Checksum File** | `mediaforge_api_downloads_backup_20261019_154608.tar.gz.sha256` |

### 3.2 Restored Media Integrity & FFprobe Playability Verification

The archive was restored into a newly created disposable Docker volume (`mediaforge_disposable_e2a_fix_a`) using `Restore-MediaVolume.ps1`. An automated inspection tool verified exact byte sizes, cryptographic SHA-256 matches, and executed `ffprobe` on every restored media stream:

| Media Asset | Relative Storage Path | Original Size | Restored Size | Restored SHA-256 | Streams | Duration | Result |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Synthetic Source** | `.../downloads/synthetic_backup_test_source.mp4` | 117,655 B | 117,655 B | `eefd582b127513add1a884521506d175bfad934f0282a584614e93ec7edeb94a` | h264 / aac | 5.00s | **PASS** |
| **Clip 1 (0-2s)** | `.../clips/...cli_446c387fd6d5.mp4` | 50,275 B | 50,275 B | `ea8bece1a01dd50bc39f16dc1502e9830efe16307af86948623d8de914a00235` | h264 / aac | 2.00s | **PASS** |
| **Clip 2 (2-4s)** | `.../clips/...cli_c9d5830bdb88.mp4` | 56,185 B | 56,185 B | `1c858aab8f36f4855a1ecd775728896739e1847bfa9adcd28c37d00e12e48e91` | h264 / aac | 2.00s | **PASS** |
| **Merged Video** | `.../merged/merged_2clips_a0d44d9b54d8.mp4` | 105,140 B | 105,140 B | `20f3eb7e12c200e62609e34c6ce18c9ae17aad2dbada88b98931b0f48108ec01` | h264 / aac | 4.02s | **PASS** |

All four files match 100% bit-for-bit with original media and are verified fully playable by FFprobe.

---

## 4. Disaster Recovery & Safe Restore Targeting

### 4.1 Restore Automation (`Restore-MediaVolume.ps1`)

MediaForge provides an independent restoration script equipped with strict safety guardrails:

```powershell
deploy/scripts/Restore-MediaVolume.ps1 -BackupFile <path> -TargetVolume <vol> [-ExpectedHash <sha>] [-Force] [-AllowProductionOverwrite]
```

**Guardrails & Controls:**
1. **Pre-Restore Checksum Verification**: Automatically inspects companion `.sha256` files or `-ExpectedHash`. If the cryptographic hash of the archive does not match, extraction is aborted before any volume operations occur.
2. **Production Volume Protection**: Refuses to target designated production volumes (`mediaforge_api_downloads`, `mediaforge_pgdata`, `mediaforge_redis_data`) unless `-AllowProductionOverwrite` is explicitly passed.
3. **Active Container In-Use Guard**: Detects running containers attached to the target volume (`docker ps -q --filter volume=<name>`) and blocks restoration if containers are active.
4. **Path Traversal & Escape Prevention**: Runs `tar -tzf` before extraction to verify no entries begin with `/` or contain `../` sequences, preventing directory traversal attacks.
5. **Non-Root Ownership Remediation**: Runs `chown -R 1000:1000 /target` after extraction so that restored files are fully accessible by the non-root `mediaforge:mediaforge` container process.

### 4.2 Coordinated Point-in-Time Database & Media Recovery Architecture

Because MediaForge employs a decoupled database-and-storage architecture, point-in-time disaster recovery must execute in coordinated lockstep:
1. **Quiescent Maintenance Window**: Initiate backup operations when active job submissions are paused and writer count is 0.
2. **PostgreSQL Custom-Format Dump**: Run `Backup-Database.ps1` to produce `grabitdown_backup_YYYYMMDD_HHMMSS.dump`. This captures the exact state of `gid.media_artifacts`, `gid.download_jobs`, and schema state at Alembic revision `3f6397f5179d`.
3. **Storage Volume Snapshot**: Immediately run `Backup-MediaVolume.ps1` to produce `mediaforge_api_downloads_backup_YYYYMMDD_HHMMSS.tar.gz`.
4. **Recovery Protocol**:
   - Restore PostgreSQL first using `pg_restore --clean --if-exists -d grabitdown`.
   - Restore media volume using `Restore-MediaVolume.ps1`.
   - Execute `python -m api.services.reconcile_artifacts` to verify zero missing files and zero orphaned artifacts.

---

## 5. Media Retention Engine & Safety Guarantees

### 5.1 Hierarchical Protection Rules

The retention preview engine (`api/services/retention_service.py`) evaluates media assets against hierarchical rules:

1. **Historical Anomaly Isolation**: The 10 known pre-Fix-B test anomalies (`HISTORICAL_TEST_ANOMALIES`) are classified under `historical_test_anomaly` and preserved unmodified.
2. **In-Progress Exclusion**: Any artifact with status `pending` or `processing` is classified as `in_progress_excluded` and protected from retention cleanup.
3. **Download Job Protection**: Media associated with active or historical `DownloadJobDB` records is classified as `download_job_protected` and retained permanently.
4. **Referenced Source Protection**: Media referenced in `parent_artifact_id` or `input_sources` of another artifact is classified as `referenced_source_protected` and retained permanently.
5. **Already Soft-Deleted**: Artifacts marked `status = 'deleted'` preserve their database records for audit integrity.
6. **Default Retain-Forever Baseline**: Customer media defaults to `retention_days = -1`. Zero files are eligible for cleanup.

### 5.2 Retention API Security (`GET /api/v1/artifacts/retention/preview`)

The retention preview endpoint enforces:
- **Authentication**: Mandatory JWT or API Key authentication.
- **RBAC**: Requires tenant administrator or owner privileges (`user.is_admin_or_owner`). Regular members receive HTTP 403.
- **Tenant Isolation**: Non-system administrators can only view their own tenant's preview. Specifying a foreign tenant returns HTTP 403.
- **Scope Verification**: API keys must possess the `clips:read` scope.
- **Operator Multi-Tenant Audit**: An all-tenant preview is available only to principals with `is_system_admin = True`.
- **Information Leakage Defense**: Internal server absolute paths (`/app/downloads`) are masked to `"downloads"` in API responses.

### 5.3 Narrowly Scoped Historical Test Exceptions vs. General Cleanup Policy

To maintain architectural integrity, historical test anomalies are strictly separated from general cleanup policy:
- General customer retention decisions are driven purely by database relationships, `created_at` timestamps, active transcode statuses, and configured tenant retention policies.
- Hardcoded IDs are restricted exclusively to `HISTORICAL_TEST_ANOMALIES` representing the 10 known legacy test fixtures created before durable volume persistence was established in STEP 02-E1-Fix-B.
- No customer-visible classification bypasses rely on ID matching; customer-tenant queries evaluate only real database foreign keys and job references.

---

## 6. Limitations of Same-Computer Backups & Future Recommendations

### 6.1 Limitations of Local Host-Backed Backups
- **Single Point of Hardware Failure**: Storing Docker volume backups in `deploy/backups/` on the same physical host protects against container destruction, Docker engine reset, or volume corruption, but does NOT protect against host disk failure, OS corruption, or site disasters.
- **Storage Amplification**: Retaining large video archives locally consumes host disk space.

### 6.2 Future Recommendations
1. **Offsite Object Storage Mirroring**: Establish automated, encrypted streaming of `.tar.gz` media archives to offsite S3-compatible cloud storage (AWS S3, Cloudflare R2, MinIO) with versioning and immutable object locks.
2. **Coordinated Point-in-Time Recovery**: Pair PostgreSQL custom-format database backups (`deploy/backups/grabitdown_backup_*.dump`) with media volume backups (`deploy/backups/mediaforge_api_downloads_backup_*.tar.gz`) taken during identical quiescent maintenance windows.
3. **Two-Phase Soft-Deletion Quarantine**: When automated deletion is introduced in future milestones, require a 7-day quarantine in `/app/downloads/.trash/<tenant>/` before irreversible physical disk unlinking.
