# Commercial Usage Metering, Ledger Security, and Measurement Architecture

**Document Version:** 1.0.0 (STEP 02-E2B-Fix-A)
**Database Schema:** `gid`
**Authoritative Tables:** `gid.usage_events`, `gid.tenant_quotas`

---

## 1. Overview & Trust Boundaries

MediaForge meters commercial consumption across four primary operations:
1. Media Downloads
2. Video Clip Extractions
3. Multi-Clip Video Merging
4. Subtitle Extractions and AI Transcriptions

### Fail-Closed Public Ingestion Boundary
- **Customer Restriction:** Customer JWT principals (tenant owners, admins, members) and commercial API keys **cannot** submit or inject arbitrary usage events.
- **Scope Elimination:** The `usage:write` scope has been permanently removed from public `VALID_API_SCOPES`. API keys may only hold `usage:read` to query tenant-scoped metrics.
- **Worker Authentication:** `POST /api/v1/usage/events` strictly fails closed (`HTTP 403 Forbidden`) for any principal lacking `system_admin` status. Ingestion is reserved for verified internal processing workers.
- **Immutable Ledger:** No public API route permits updating or deleting rows in `gid.usage_events`.

---

## 2. In-Transaction Atomicity & Usage Reconciliation

### Transactional Atomicity
To eliminate the risk of operations completing without corresponding usage being recorded:
- **Clips & Merges:** Handled inside `complete_artifact_in_fresh_session`. The transition of `MediaArtifactDB.status` to `ready` and the insertion of `UsageEventDB` occur in the **exact same database transaction** before commit.
- **Downloads:** Handled inside `EngineDatabaseSync`. When `progress.status == DownloadStatus.COMPLETED`, the download record transition and the `UsageEventDB` insertion execute in the same transaction.
- **File Size Verification:** Downloaded bytes are cross-verified against the physical file on disk via `os.path.getsize` before recording.

### Reconciliation Engine (`api/services/reconcile_usage.py`)
For disaster recovery, system crashes, or auditing historical state:
- **Audit Mode (`dry_run=True`):** Scans all `ready` artifacts and `completed` download jobs, comparing them against the ledger using deterministic idempotency keys (`clip:{id}`, `merge:{id}`, `download:{id}`). Returns an audit report without mutating data.
- **Repair Mode (`dry_run=False`):** Derives exact quantities (`bytes`, `duration_seconds`) strictly from persisted, authoritative `MediaArtifactDB` and `DownloadJobDB` records, inserting missing events idempotently. Requires explicit authorization and can be scoped by tenant.

---

## 3. Measurement Dimensions & Monotonic Processing Time

MediaForge rigorously separates media duration from compute processing time:

| Dimension | Field | Unit | Meaning |
| :--- | :--- | :--- | :--- |
| **Output Media Duration** | `duration_seconds` | Float seconds | Length of the playable media file as measured by `ffprobe`. |
| **Server Compute Time** | `metadata_json.processing_time_ms` | Integer ms | Wall-clock elapsed server execution time measured with monotonic `time.perf_counter()`. |
| **Storage / Bandwidth** | `bytes` | BigInt bytes | Physical file size on disk or network payload transferred. |
| **Operation Count** | `quantity` | Float count | Operational execution count (e.g. 1.0 for completed operation). |

---

## 4. Clip Extraction Duration & Seeking Mechanics

FFmpeg operates in two distinct cutting modes:

### Fast Mode (`mode="fast"`, Stream Copy `-c copy`)
- **Mechanics:** Copies compressed packets directly from the container without re-encoding.
- **Seeking Limitation:** Stream copy **cannot cut on arbitrary B-frames or P-frames**. It must seek to the nearest preceding Keyframe (IDR frame). If a 5-second video only has a single keyframe at `0.0s`, cutting `00:00:02` to `00:00:04` will snap to `0.0s` to preserve decode reference frames, producing ~4.1 seconds of output instead of 2.0s.
- **Use Case:** High-speed, zero-CPU trimming where approximate keyframe boundaries are acceptable.

### Precise Mode (`mode="precise"`, Re-encode)
- **Mechanics:** Decodes the video stream from the preceding keyframe, discards pre-roll frames, and re-encodes exactly from `start_time` to `end_time`.
- **Accuracy:** Frame-accurate to the millisecond (`2.000000s` requested produces `2.000000s` output).
- **Default:** Precise mode is the default in MediaForge API (`ClipExtractRequest.mode = "precise"`).

---

## 5. Subtitle Retrieval vs. AI Speech-to-Text Transcription

- **Subtitle Extraction (`subtitle_retrieval`):** Non-AI retrieval of existing closed captions/subtitles (e.g. YouTube VTT/SRT). Metered with `event_type="subtitle_retrieval"`, unit `count`, and does **not** consume `whisper_minutes`.
- **AI Transcription (`transcription`):** Audio transcription using Whisper AI models. Metered with `event_type="transcription"`, unit `minutes`, and updates billable `whisper_minutes`.

---

## 6. Database-Enforced Idempotency & Conflict Rejection

- Every usage event requires an `idempotency_key`, constrained by unique index `(tenant_id, idempotency_key)`.
- **Identical Replay:** Returns existing event with `is_new = False` without double-counting.
- **Conflicting Replay:** If a request reuses an idempotency key with conflicting parameters (`bytes`, `duration_seconds`, `event_type`), the service raises `IdempotencyConflictError` (`HTTP 409 Conflict`), rejecting inconsistent accounting.

---

## 7. Advisory Quotas

- Stored in `gid.tenant_quotas`.
- Evaluated via `GET /api/v1/usage/quotas`.
- **Default Policy:** Unconfigured quotas return `limit = -1` (UNLIMITED) with `is_exceeded = False`.
- **Non-blocking:** In accordance with STEP 02-E2B, quotas are strictly advisory and do not hard-block customer media operations.
