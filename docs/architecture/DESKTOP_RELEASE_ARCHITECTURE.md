# MediaForge Desktop Release Architecture and Distribution Strategy

**Document Status:** Authoritative Architectural Specification
**Milestone:** STEP 03-D — Windows Desktop Release Architecture, Electron Security Upgrade and Product Readiness
**Target Platform:** Windows 11 / Windows 10 (x64)
**Desktop Runtime:** Electron 44.10.0 (Chromium 130+, Node.js 24.22.1)
**Backend Infrastructure:** FastAPI, PostgreSQL 16, Redis 7, Docker, FFmpeg / FFprobe

---

## 1. Existing System Architecture

MediaForge is currently implemented as a modular monolith:
1. **Desktop Client (`desktop/`):**
   - Built on React 18, TypeScript 5.6, Tailwind CSS, and Vite.
   - Electron main and preload processes with strict process isolation (`contextIsolation: true`, `sandbox: true`, `webSecurity: true`, `nodeIntegration: false`).
   - OS-encrypted vault (`safeStorage.encryptString`) storing short-lived access tokens and rotating refresh tokens in memory and `%APPDATA%/MediaForge/session.vault`.
   - Custom privileged streaming protocol `mediaforge-media://playback/<artifact_id>` providing RFC 7233 HTTP 206 Partial Content byte-range streaming and seeking.
   - Local disk media cache partitioned by tenant GUID and user GUID (`%APPDATA%/MediaForge/media_cache/<tenant_id>/<user_id>/<artifact_id>/`).
2. **Backend API (`api/`):**
   - FastAPI application running in Docker container `mediaforge-api` on port `58000`.
   - Multi-tenant data segregation with schema `gid` on PostgreSQL `grabitdown`.
   - JWT authentication with rotating refresh token families in Redis and PostgreSQL.
   - Comprehensive rate limiting, anti-enumeration security, and commercial usage ledgers.
3. **Background Media Workers (`api/workers/`):**
   - Dedicated service container `mediaforge-worker` executing media jobs asynchronously.
   - Concurrency control via PostgreSQL transactional `SELECT ... FOR UPDATE SKIP LOCKED`.
   - Fenced worker leases, exponential retry scheduling, and running process cancellation.
   - Media storage mapped to shared Docker volume `mediaforge_api_downloads`.

---

## 2. Comparative Deployment Architecture Study

To prepare for commercial distribution, three distinct deployment models have been analyzed:

### Model A: Hosted Backend with Lightweight Desktop Client (Cloud SaaS)
- **Architecture:** The desktop application is a slim Electron executable (~85 MB) communicating with a cloud-hosted FastAPI, PostgreSQL, Redis, and Worker cluster over TLS (HTTPS/WSS).
- **Client Footprint:** Minimal disk consumption (only cached media); zero database or Python dependencies on the user's PC.
- **Offline Processing:** No offline clipping or merging. Previously cached media remains 100% playable offline via `mediaforge-media://`.
- **Bandwidth & Compute:** Media downloads and video transcoding occur entirely on cloud infrastructure. Media streaming to the client consumes cloud egress bandwidth.
- **Monetization & Control:** Authoritative server-side licensing, quota enforcement, and tier management. Impossible for clients to tamper with usage meters or token limits.
- **Maintenance & Updates:** yt-dlp extractors and FFmpeg codecs are maintained centrally in the cloud without requiring desktop installer updates.

### Model B: Bundled Local Python & FFmpeg Processing Runtime (Thick Monolith)
- **Architecture:** The desktop installer bundles Electron, Python, FastAPI, SQLite/PostgreSQL, Redis, and FFmpeg/FFprobe binaries into a self-contained local installation.
- **Client Footprint:** Very large installer (>1.5 GB); high RAM and CPU consumption during video transcoding.
- **Offline Processing:** Fully autonomous local downloads, clipping, merging, and playback without internet connectivity.
- **Bandwidth & Compute:** Zero server bandwidth or cloud compute costs.
- **Database Challenges:** SQLite lacks row-level `FOR UPDATE SKIP LOCKED` locking, requiring a custom file-lock scheduler. Local PostgreSQL requires running an embedded daemon or Windows Service, which is prone to corruption on battery death or hard shutdown.
- **Support & Security Burden:** Antivirus false-positives on bundled Python/FFmpeg executables; port collisions on localhost; path length limits (`MAX_PATH`) on Windows; complex LGPL license compliance requirements.
- **Maintenance Fragility:** Frequent YouTube extraction changes break local yt-dlp, forcing emergency desktop app releases to prevent churn.

### Model C: Hybrid Local Processing with Hosted Control Plane (Recommended Long-Term)
- **Architecture:** Cloud-hosted control plane manages identity, licensing, and media metadata; a bundled or dynamically downloaded local FFmpeg sidecar handles local clip extraction and concatenation on the customer's hardware.
- **Client Footprint:** Moderate (~250 MB).
- **Offline Processing:** Trimming and merging of previously downloaded local media can occur offline; new downloads and transcripts require cloud connectivity.
- **Bandwidth & Compute:** Offloads CPU-intensive video encoding from cloud servers to client GPUs/CPUs, dramatically lowering SaaS infrastructure costs while retaining control-plane monetization.
- **Complexity:** Requires a dual-dispatch job scheduler that decides whether an operation runs locally or in the cloud.

---

## 3. Decision Matrix

| Dimension | Model A: Hosted SaaS | Model B: Bundled Monolith | Model C: Hybrid (Target) |
| :--- | :--- | :--- | :--- |
| **Installer Size** | **~85 MB (Optimal)** | >1.5 GB (Heavy) | ~250 MB (Moderate) |
| **Installation Complexity** | **1-Click, No Admin (Optimal)** | High (Service setup, AV alerts) | Moderate |
| **System Prerequisites** | None (Self-contained) | High (C++ runtimes, storage) | Moderate |
| **Offline Playback** | **Supported (Cached media)** | Supported | Supported |
| **Offline Processing** | Not Supported | Supported | Supported (Local clips) |
| **Cloud Transcoding Costs** | High ($0.02 - $0.05/hr) | **$0.00 (Zero)** | **$0.00 (Zero)** |
| **yt-dlp Maintenance** | **Instant (Server hotfix)** | Painful (App update required) | **Instant (Cloud downloads)** |
| **Commercial Quota Security** | **Air-tight (Server enforced)**| Weak (Client can reverse-engineer)| **Air-tight (Cloud control)** |
| **Windows Support Tickets** | **Low (Standard web app)** | Extremely High (Ports, AV, DB) | Moderate |
| **Engineering Time to MVP** | **Immediate (Uses current API)**| 6–8 Weeks (Engine rewrite) | 4–6 Weeks (Sidecar worker) |

---

## 4. Recommended Commercial MVP Runtime Architecture

### The MVP Strategy: Model A (Hosted Backend)
For the initial commercial release, **Model A** is selected:
1. **Speed to Market:** MediaForge already has a battle-tested FastAPI backend with PostgreSQL SKIP LOCKED worker orchestration, JWT session security, and commercial usage ledgers.
2. **Support Risk Reduction:** Packaging PostgreSQL, Redis, and Celery/Worker processes into a Windows installer creates catastrophic support liability across diverse consumer hardware.
3. **yt-dlp Resiliency:** Because YouTube and social media sites modify streaming formats weekly, hosting the download engine centrally ensures downloads never break for paying desktop users.
4. **Clean Upgrade Path to Hybrid:** Step 03-D implements clean boundary abstractions in Electron, allowing local FFmpeg clipping to be introduced in Phase 2 without disrupting the user experience or authentication model.

---

## 5. Electron Dependency Security Upgrade (v44.10.0)

### Version Baseline
- **Previous Version:** Electron 33.4.11 (Out of vendor security support).
- **Upgraded Version:** **Electron 44.10.0** (Actively supported stable major release; Electron maintains the latest 3 major versions [43, 44, 45]; Node.js 24.22.1, Chromium 130+). Note: Unlike Node.js, the Electron project does not designate formal "LTS" branches.

### Compatibility & API Verifications
1. **Context Isolation & Sandboxing:** Unchanged and strictly enforced (`contextIsolation: true`, `sandbox: true`, `webSecurity: true`, `nodeIntegration: false`).
2. **SafeStorage DPAPI Integration:** Native Windows Data Protection API (`safeStorage.encryptString`, `safeStorage.decryptString`) functions seamlessly without requiring external C++ native modules.
3. **Custom Protocol API:** `protocol.handle('mediaforge-media', async (request) => new Response(...))` adheres strictly to modern web standards (`ReadableStream`, `Response`), replacing legacy buffer protocol callbacks.
4. **RFC 7233 Streaming:** Standard Range requests (`bytes=0-1023`, `bytes=-500`, and `416` range out-of-bounds) verified working with zero deprecation warnings.

---

## 6. Security and Deployment Boundaries

### 1. Process Separation
```
+-------------------------------------------------------------+
|                     ELECTRON DESKTOP                        |
|                                                             |
|  +---------------------+        +------------------------+  |
|  |    Renderer SPA     |        |      Main Process      |  |
|  |  (React 18 / Vite)  |  IPC   |      (Node / OS)       |  |
|  |                     | -----> |                        |  |
|  | - No Refresh Token  |        | - Vaulted Refresh Token|  |
|  | - No fs / child_proc| <----- | - safeStorage (DPAPI)  |  |
|  | - Strict CSP        |        | - HTTP 206 Streaming   |  |
|  +---------------------+        +------------------------+  |
+---------------------------------------------|---------------+
                                              |
                          HTTPS / WSS (Strict Origin Validation)
                                              |
                                              v
+-------------------------------------------------------------+
|                   HOSTED BACKEND CLUSTER                    |
|                                                             |
|  +------------------+  SKIP LOCKED   +-------------------+  |
|  |   FastAPI API    | -------------> |  Worker Service   |  |
|  |   (Auth / Jobs)  |                | (FFmpeg / yt-dlp) |  |
|  +------------------+                +-------------------+  |
|          |                                     |            |
|          v                                     v            |
|  +------------------+                +-------------------+  |
|  | PostgreSQL (gid) |                | Persistent Volume |  |
|  | & Redis Sessions |                |  (Artifact Store) |  |
|  +------------------+                +-------------------+  |
+-------------------------------------------------------------+
```

### 2. Desktop Security Policy
- **Loopback vs. Remote Enforcements:** Unencrypted HTTP is permitted exclusively on loopback hostnames (`127.0.0.1`, `localhost`, `::1`) for development testing. Remote APIs strictly mandate TLS (`https://`).
- **Dynamic Content Security Policy:** The `connect-src` CSP directive dynamically derives its allowed origin from the validated backend URL, preventing cross-origin data exfiltration.
- **Origin Switch Session Purge:** If the backend URL or origin is altered in Settings, all active sessions and local token vaults are instantly wiped to prevent token leakage across environments.
- **Intra-Tenant Cache Isolation:** Local cached media files are stored strictly under `%APPDATA%/MediaForge/media_cache/<tenant_id>/<user_id>/<artifact_id>/`. Ordinary members cannot access media belonging to other members.
- **OS-Level Boundary Notice:** Desktop application-level user isolation does not protect against another user or malware operating under the same Windows user profile. Complete data-at-rest confidentiality against local profile compromise requires Windows BitLocker or per-user DPAPI-derived AES-256-GCM file encryption.

---

## 7. Product Workflow Verification Results

The automated continuous acceptance suite ([desktop/tests/electron_step03d_continuous_workflow.js](file:///d:/Dev/MediaForge/desktop/tests/electron_step03d_continuous_workflow.js)) executed all 17 continuous phases against the live backend with zero mock data:

| Step | Action | Expected Outcome | Result |
| :---: | :--- | :--- | :---: |
| **1** | Sign in via real GUI | Controlled React form input, token vaulted, dashboard mounts | **PASS** |
| **2** | Submit video download | Dispatches `POST /downloads`, receives non-empty job ID | **PASS** |
| **3** | Observe completion | Polling confirms progress reaches 100%, terminal `completed` | **PASS** |
| **4** | Locate in library | Download indexed in media library | **PASS** |
| **5** | Cache and play video | Downloads to local cache, plays via `mediaforge-media://` (HTTP 206) | **PASS** |
| **6** | Select clipping source | Source path bound in clip view | **PASS** |
| **7** | Extract two clips | Worker processes clip 1 and clip 2 | **PASS** |
| **8** | Wait for success | Both jobs reach terminal `succeeded`, producing valid artifacts | **PASS** |
| **9** | Merge clips | Submits merge job with clip artifact IDs | **PASS** |
| **10** | Preview merged result | Caches merged video, streams HTTP 206 byte-ranges | **PASS** |
| **11** | Extract captions | Live YouTube subtitle extraction returns valid SRT | **PASS** |
| **12** | Restart desktop | Window destroyed, session reloaded from safeStorage vault | **PASS** |
| **13** | Recover session & jobs | Dashboard restores user profile and lists previous job history | **PASS** |
| **14** | Disconnect backend | Switches to unreachable endpoint, diagnostics detect offline | **PASS** |
| **15** | Play cached media offline | HTTP 206 byte-range playback functions without network | **PASS** |
| **16** | Reconnect backend | Reconnects to live API, diagnostics confirm online status | **PASS** |
| **17** | Logout & access revoke | Session vault purged, playback returns HTTP 401 Unauthorized | **PASS** |

**Workflow Acceptance Outcome:** `17/17 STEPS PASSED (0 FAILURES)`.

---

## 8. Release Acceptance Checklist

| Component / Requirement | Status | Verification & Risk Assessment |
| :--- | :---: | :--- |
| **Supported Electron Version** | **READY** | Electron 44.10.0 (one of 3 actively maintained stable major versions, Node 24.22.1). |
| **Security Architecture** | **READY** | contextIsolation, sandboxing, safeStorage vaulting, IPC frame validation. |
| **Windows Lifecycle** | **READY** | Clean startup, persistence loading, graceful exit, zero zombie processes. |
| **Installer & Uninstaller** | **NOT STARTED** | electron-builder / NSIS packaging scheduled for Step 03-E. |
| **Code Signing** | **NOT STARTED** | Authenticode certificate planning (OV/Standard or Azure Trusted Signing; EV is not mandatory, and SmartScreen reputation requires build telemetry regardless of cert tier). |
| **Auto-Update Engine** | **NOT STARTED** | electron-updater integration scheduled for release packaging phase. |
| **Authentication & Revocation**| **READY** | Rotating refresh tokens, single-flight mutex, instant reuse detection. |
| **API TLS Enforcement** | **READY** | Loopback HTTP allowed only for dev; HTTPS mandatory for remote origins. |
| **Media Permissions** | **READY** | All arbitrary web permissions blocked via permissionRequestHandler. |
| **Local Cache Isolation** | **READY** | Scoped by tenant GUID and user GUID, anti-enumeration 404s enforced. |
| **Worker Crash Recovery** | **READY** | PostgreSQL SKIP LOCKED lease recovery and stale worker fencing. |
| **Job Retries & Backoff** | **READY** | Exponential retry backoff with persistent attempt limits. |
| **Download Engine** | **READY** | Asynchronous yt-dlp worker execution with stream endpoints. |
| **Transcript Capabilities** | **PARTIAL** | YouTube SRT extraction operational; local neural Whisper AI deferred. |
| **Logging & Diagnostics** | **READY** | Correlated request tracking (`mf-desk-...`), zero credential leakage. |
| **Data Privacy** | **READY** | Sanitized test accounts, no raw passwords or JWTs in logs. |
| **License Compliance** | **PARTIAL** | Permissive desktop dependencies; FFmpeg binary redistribution requires GPL/LGPL review (libx264 triggers GPL obligations if bundled). |
| **Database & Migrations** | **READY** | Alembic current at `8b3c2d1e0f4a (head)` on schema `gid`. |
| **Operational Monitoring** | **PARTIAL** | Docker healthchecks active; production APM scraping scheduled. |
| **Documentation** | **READY** | Complete architectural and security guides established. |

---

## 9. Product Terminology Guidelines

To maintain legal and marketing precision, external product claims must strictly adhere to the following definitions:
- **Offline Playback:** Refers exclusively to playing media files that have already been fully cached to the local device. The desktop app does NOT currently perform offline video clipping or transcoding.
- **Offline Local Clipping:** Refers to future client-side trimming of local files; current clipping is performed by backend workers.
- **Internet-Based Downloads:** Refers to fetching media from remote streaming providers via the backend yt-dlp service.
- **Hosted Media Processing:** Refers to transcoding, clipping, merging, and thumbnail generation executed on cloud worker nodes.
- **Hybrid Processing:** Refers to the planned Phase 2 capability where lightweight clip trims are processed on local hardware while heavy downloads and transcripts use the cloud.
- **Commercial Usage Metering vs. Billing:** Existing usage tracking and quota ledgers record compute seconds and download volume; they provide *advisory metering* but do not yet enforce hard billing lockouts, payment gateways, or subscription cancellations.
- **Deployment Topology:** A hardened single-node Ubuntu VPS running Docker Compose with Caddy (automatic TLS) is a robust, cost-effective initial production deployment model. Full Kubernetes orchestration is an enterprise scaling option, not a launch prerequisite.
- **Code-Signing & Publisher Identity:** EV Authenticode certificates are not mandatory for distribution; standard OV certificates and Microsoft Store packaging are valid options. Furthermore, modern SmartScreen builds reputation algorithmically; EV certificates no longer grant instant bypass without established download volume. Azure Trusted Signing requires verifying the publisher's geographic eligibility and organization vetting.

---

## 10. Remaining Implementation Phases (Roadmap to Launch)

1. **Phase 1 (STEP 03-E) — Production Packaging & Code Signing:**
   - Configure `electron-builder` for Windows NSIS x64 packaging.
   - Establish Authenticode code-signing strategy (Standard OV or Azure Trusted Signing subject to eligibility).
   - Configure differential auto-updates via `electron-updater` and GitHub Releases.
2. **Phase 2 — Hybrid Offline Processing Sidecar:**
   - Evaluate FFmpeg.wasm or a bundled lightweight FFmpeg sidecar binary for instant local cuts of cached videos without cloud network transit.
3. **Phase 3 — Whisper Neural Transcription:**
   - Package quantized Whisper AI models (e.g. `whisper.cpp` / ONNX runtime) for offline speech-to-text without relying on provider subtitle tracks.
