# MediaForge Desktop Architecture — STEP 03-D

## 1. Executive Summary

MediaForge Desktop is an enterprise-grade Electron desktop client designed to interact securely and seamlessly with the MediaForge FastAPI backend (`http://127.0.0.1:58000/api/v1`). It provides real-time download management, unified media library reconciliation, asynchronous background media job submission (clip extraction and multi-clip video merging), transcript and caption retrieval, durable progress tracking, and per-account isolated offline playback without compromising desktop host security or exposing raw operating system handles to web renderers.

Recent milestones achieved:
- **Electron Security Upgrade (STEP 03-D)**: Upgraded runtime from Electron 33 to **Electron 44.10.0** (Node.js 24.22.1 / Chromium 130+), restoring full vendor security maintenance.
- **Continuous 17-Step GUI Workflow (STEP 03-D)**: Verified complete 17-phase continuous user journey across real GUI inputs, live download, clipping, merging, caption retrieval, app restart, session persistence, and offline cached playback.
- **Backend Configuration & Origin Isolation (STEP 03-D)**: Strict loopback-only HTTP policy, mandatory remote HTTPS enforcement, and dynamic CSP origin derivation.
- **Refresh Token Isolation (STEP 03-C2-Fix-A)**: Refresh tokens remain strictly confined to the Electron main process OS-encrypted vault; React renderers and preload scripts never receive or store decrypted refresh tokens.
- **Main-Process Refresh Mutex (STEP 03-C2-Fix-A)**: Single-flight mutex in the Electron main process eliminates rotating refresh token race conditions.
- **Per-Account Cache Partitioning (STEP 03-C2-Fix-A)**: Media cache directories are partitioned by `<tenant_id>/<user_id>/<artifact_id>/` enforcing both cross-tenant and intra-tenant member isolation.

---

## 2. Desktop Folder Structure

The desktop project resides cleanly inside `desktop/` without colliding with existing Python desktop files (`app.py`, `bridge.py`, `dashboard.py`):

```
desktop/
├── electron/                         # Main and preload processes
│   ├── main.ts                       # Electron main process (window, protocol, safeStorage, media cache, IPC)
│   └── preload.ts                    # Strict contextBridge script (no Node.js leaks to renderer)
├── src/                              # React 18 / TypeScript renderer SPA
│   ├── api/                          # Typed API client targeting FastAPI endpoints
│   │   └── client.ts                 # BaseURL config, auth injection, auto-refresh mutex, timeout
│   ├── components/                   # Reusable UI components
│   │   ├── Layout.tsx                # App sidebar, navigation tabs, account header, connection indicator
│   │   ├── StatusBadge.tsx           # Semantic status pills (queued, running, succeeded, failed)
│   │   └── VideoPlayerModal.tsx      # Video modal streaming via mediaforge-media:// custom protocol
│   ├── context/                      # State providers
│   │   ├── AuthContext.tsx           # Authentication state, login/logout, session restoration
│   │   └── ApiStatusContext.tsx      # Backend health monitoring (periodic 10s ping)
│   ├── types/                        # Shared TypeScript interfaces (Downloads, Clips, Merges, Transcripts, Jobs)
│   │   └── index.ts
│   ├── views/                        # Core application views (8 primary workspaces)
│   │   ├── DashboardView.tsx         # Workspace overview, metrics, quick workflow shortcuts
│   │   ├── DownloadsView.tsx         # Download management (URL submission, queue states, priority controls)
│   │   ├── MediaLibraryView.tsx      # Unified library reconciling downloads and artifacts, search, filters
│   │   ├── ClipWorkflowView.tsx      # First-class clip extraction, boundary validation, mode toggle
│   │   ├── MergeWorkflowView.tsx     # Multi-clip sequencing (2-20 items), duration estimation, mode toggle
│   │   ├── TranscriptView.tsx        # Subtitle/caption retrieval (SRT, VTT, TXT, JSON), language detection
│   │   ├── JobsView.tsx              # Live PostgreSQL background job status, progress bars, cancellation
│   │   └── SettingsView.tsx          # Backend URL config, health test, media cache purge
│   ├── App.tsx                       # Root routing and view orchestrator with cross-view state hand-offs
│   ├── main.tsx                      # React DOM mounting
│   └── index.css                     # Tailwind CSS styling and dark scrollbars
├── dist/                             # Compiled distribution
│   ├── electron/                     # Compiled main.js and preload.js
│   └── renderer/                     # Compiled Vite production SPA bundle
├── tests/                            # Verification and test suites
│   ├── electron_gui_test.js          # Electron binary startup and DOM invariant test
│   ├── electron_full_gui_acceptance.js # Real interactive Electron GUI acceptance (login, 8 tabs, playback, logout)
│   ├── electron_step03d_continuous_workflow.js # Complete 17-step continuous GUI acceptance workflow
│   └── e2e_c2fix_acceptance.mjs      # Complete 43-assertion real end-to-end vertical slice test
├── index.html                        # Application entry HTML with strict Content-Security-Policy
├── package.json                      # Locked dependencies (Electron 44.10.0, React 18, Vite 5, Tailwind 3)
├── tsconfig.json                     # TypeScript configuration for React renderer
├── tsconfig.electron.json            # TypeScript configuration for Electron main process
├── vite.config.ts                    # Vite build configuration with alias resolution
├── tailwind.config.js                # Tailwind CSS palette and extensions
└── postcss.config.js                 # PostCSS setup
```

---

## 3. Process Responsibilities & Isolation

MediaForge strictly segregates duties between the privileged Electron Main process, the Preload barrier, and the unprivileged React Renderer:

```
+-----------------------------------------------------------------------------------------+
|                                 REACT RENDERER PROCESS                                  |
|  - UI State & Routing (Dashboard, Downloads, Media Library, Clip, Merge, Transcripts)   |
|  - Typed API Client (Fetch calls to http://127.0.0.1:58000/api/v1)                      |
|  - Strict CSP: script-src 'self'; connect-src 'self' http://127.0.0.1:58000 ...         |
|  - HTML5 <video> tag consuming mediaforge-media://playback/<id>                         |
|  - ZERO Node.js access: no 'require', no 'process.env', no 'fs', no 'child_process'    |
|  - ZERO Refresh Token Access: React never handles, stores, or sees refresh tokens       |
+-----------------------------------------------------------------------------------------+
                                           │
                                           ▼ (contextBridge: window.mediaforge)
+-----------------------------------------------------------------------------------------+
|                                  PRELOAD SCRIPT BARRIER                                 |
|  - Exposes narrow, typed bridge methods: auth, media, shell, app                        |
|  - Preload exposes NO getRefreshToken capability                                        |
|  - Sanitizes and validates arguments before invoking IPC                                |
|  - NEVER exposes raw ipcRenderer.send or ipcRenderer.on to window                        |
+-----------------------------------------------------------------------------------------+
                                           │
                                           ▼ (IPC: invoke / handle with sender validation)
+-----------------------------------------------------------------------------------------+
|                                  ELECTRON MAIN PROCESS                                  |
|  - Window Lifecycle & OS Navigation Control (will-navigate block, popup denial)         |
|  - Handles auth:login directly: vaults refresh token in safeStorage; returns ONLY access|
|  - Owns single-flight refresh lock: concurrent 401s share one in-flight refresh promise |
|  - Per-Account Cache Engine: %APPDATA%/MediaForge/media_cache/<tenant_id>/<user_id>/    |
|  - SafeStorage fallback: in-memory session only if OS encryption unavailable (fails     |
|    closed against unencrypted disk persistence)                                         |
|  - Custom Streaming Protocol Handler (mediaforge-media:// supporting HTTP 206 Range)    |
|  - Strict senderFrame validation on all IPC handler callbacks                           |
+-----------------------------------------------------------------------------------------+
                                           │
                                           ▼ (Network HTTP / Range / Bearer JWT)
+-----------------------------------------------------------------------------------------+
|                                FASTAPI BACKEND & WORKER                                 |
|  - Port 58000: /api/v1/health, /auth, /artifacts, /downloads, /jobs, /transcripts        |
|  - Durable PostgreSQL media_work_items with worker SKIP LOCKED leasing                  |
+-----------------------------------------------------------------------------------------+
```

---

## 4. Main-Process Session & Refresh Ownership

### A. Refresh Token Confinement
The application enforces strict separation between authentication material:
1. **Short-Lived Access Token**: Maintained in renderer memory for standard HTTP API requests.
2. **Rotating Refresh Token**: Resides exclusively in the Electron main process memory and is persisted to disk only through Electron's OS-backed `safeStorage.encryptString()` API (`session.vault`).
3. **Renderer Invariant**: Preload and React components cannot retrieve, inspect, or pass refresh tokens. `auth.getSession()` returns `{ accessToken, user }`. `auth.login()` returns `{ success, accessToken, user }`. No `getRefreshToken` method exists.

### B. Main-Process Refresh Mutex
When multiple components encounter HTTP 401 simultaneously:
1. The renderer invokes `window.mediaforge.auth.refreshToken()`.
2. The Electron main process checks an in-flight promise variable:
   ```typescript
   let refreshMutexPromise: Promise<{ accessToken: string; expiresIn: number } | null> | null = null;
   ```
3. If a refresh is already in-flight, all subsequent callers await the exact same promise.
4. The main process dispatches `POST /api/v1/auth/refresh` using the vaulted refresh token, updates `session.vault` with the new rotating token, and returns the new access token to all callers.
5. This eliminates race conditions where parallel 401s could replay an already-consumed rotating refresh token (which would trigger backend token-reuse detection and revoke the session).

### C. OS safeStorage Fallback Policy
- When `safeStorage.isEncryptionAvailable()` is true: session credentials are encrypted with OS DPAPI (Windows) or Keychain (macOS) before writing to `session.vault`.
- When `safeStorage.isEncryptionAvailable()` is false: the application **fails closed against plaintext disk storage**. Session state is retained in-memory only for the duration of the application lifetime, and any unencrypted vault file is unlinked. Plaintext refresh tokens are never written to disk.

---

## 5. IPC Authentication Contract

| Domain | Channel / Method | Input Parameters | Return Value | Security Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| **Auth** | `auth.login(credentials)` | `{ email, password, tenant_slug? }` | `Promise<{ success, accessToken?, user?, error? }>` | Main process handles login; vaults refresh token; returns ONLY access token and user profile |
| **Auth** | `auth.getSession()` | None | `Promise<{ accessToken, user }>` | Restores session without exposing refresh token |
| **Auth** | `auth.refreshToken()` | None | `Promise<{ accessToken, expiresIn }>` | Main process executes single-flight refresh with vaulted token |
| **Auth** | `auth.logout()` | None | `Promise<boolean>` | Calls backend logout, purges `activeSession`, and unlinks `session.vault` |
| **Auth** | `auth.clearSession()` | None | `Promise<boolean>` | Clears local session vault without network call |
| **Media** | `media.download(params)` | `{ artifactId, downloadUrl, filename, expectedSha256? }` | `Promise<{ success, mediaUrl, filename }>` | Streams file to per-account cache; verifies SHA-256 |
| **Media** | `media.checkCache(id)` | `artifactId: string` | `Promise<{ isCached, mediaUrl?, sizeBytes? }>` | Verifies if local cached file exists for caller's account |
| **Media** | `media.clearCache()` | None | `Promise<boolean>` | Purges local media cache directory |
| **Media** | `media.onDownloadProgress(cb)` | `callback(data)` | `unsubscribe()` | Emits `{ artifactId, percent, bytesReceived, totalBytes }` |
| **Shell** | `shell.openExternal(url)` | `url: string` | `Promise<boolean>` | Validates and launches external URL in OS browser |

---

## 6. Credential-Handling Restrictions

- **Zero Hardcoded Credentials**: Source files, test fixtures, and documentation are strictly forbidden from including real or static credentials.
- **Dynamic Disposable Fixtures**: All automated tests generate random UUID-based test accounts per execution run and revoke them upon completion.
- **Exclusion of Secrets**: All `.env` files, `.vault` files, and local logs are Git-ignored.

---

## 7. Per-Account Cache Isolation & Security Boundary

### Storage Layout
Local cached media files are stored strictly under:
```
%APPDATA%/MediaForge/media_cache/<tenant_id>/<user_id>/<artifact_id>/<safe_filename>
```

### Access Control Rules
1. **Tenant Separation**: `tenant_id` is sanitized to alphanumeric and underscore characters. Files belonging to Tenant A cannot be referenced or accessed by Tenant B (returns HTTP 404).
2. **Intra-Tenant Member Isolation**: Ordinary `member` users can only access cached files within their own `<user_id>` directory. Attempting to play an artifact belonging to another user in the same tenant returns **HTTP 404 Not Found** (anti-enumeration).
3. **Tenant Owner / Admin Scoping**: Users with `owner` or `admin` roles can access artifacts cached by other users within their own tenant directory.
4. **Path Traversal & Symlink Defense**:
   - Filenames are sanitized via `path.basename`.
   - Directory traversal checks confirm the resolved path starts with the user's directory root.
   - Symlinks are detected via `fs.lstatSync(filePath).isSymbolicLink()` and rejected with **HTTP 403 Forbidden**.
5. **Offline Playback & Logout Policy**:
   - If an authenticated session is active, cached media plays offline even if the FastAPI backend is stopped or unreachable.
   - Upon logout (`window.mediaforge.auth.logout()`), `activeSession` is set to null. Any subsequent request to `mediaforge-media://playback/<id>` is immediately rejected with **HTTP 401 Unauthorized**.
   - Cached media files are preserved on disk for the authorized account but cannot be played or enumerated until that account re-authenticates.

---

## 8. Download Management Architecture & Queue States

The desktop client provides a full download management workspace (`DownloadsView.tsx`):

1. **Submission Contract**:
   - Dispatches `POST /api/v1/downloads` with payload `{ url, mode, quality, priority, start }`.
   - Receives **HTTP 202 Accepted** with:
     ```json
     {
       "success": true,
       "message": "Download queued",
       "job": {
         "job_id": "3d884ccf-e158-4cbe-b57c-00a338b7ac78",
         "status": "deferred",
         "url": "https://www.youtube.com/watch?v=...",
         "progress_percent": 0.0,
         "priority": "normal"
       }
     }
     ```
   - Client normalizes both `res.job.job_id` and `res.job.id`.

2. **Queue States**:
   - `queued` / `deferred`: Awaiting processing.
   - `downloading`: Actively downloading and converting.
   - `paused`: Temporarily suspended via `POST /downloads/{id}/pause`.
   - `completed`: Successfully finalized with real downloaded file available at `GET /downloads/{id}/file`.
   - `failed`: Terminal failure with structured error message.

3. **Queue Reordering**:
   - "Move Up" and "Move Down" buttons dispatch `POST /downloads/{id}/move-up` and `POST /downloads/{id}/move-down`.

---

## 9. Actual Validated Backend API Contracts

### A. Clip Extraction Workflow
- Submission: `POST /api/v1/jobs/clips` with `{ source, start_time, end_time, mode, output_format }`.
- Response: HTTP 202 Accepted with durable `job_id`.
- Polling: `GET /api/v1/jobs/{job_id}` returns status `queued` -> `running` -> `succeeded`.
- Worker Execution: Asynchronous worker running inside Docker executes FFmpeg, outputs to staging, verifies with FFprobe and SHA-256, and atomically promotes artifact.
- Artifact Verification: Output duration matches requested slice (e.g., 3.0s), file size is valid, and SHA-256 is recorded.

### B. Multi-Clip Merge Workflow
- Submission: `POST /api/v1/jobs/merges` with `{ clips: [clip1_id, clip2_id], output_format, mode }`.
- Rules: Enforces min 2 clips and max 20 clips.
- Worker Execution: Worker generates safe concat manifest with newline and quote escaping, executes concat demuxer, and produces merged artifact.
- Output: Duration equals the combined clip durations (~6.0s for two 3.0s clips).

### C. Transcript & Subtitle Workflow
- Extraction: `POST /api/v1/transcripts/extract` with `{ url, language, format }`.
- Response: HTTP 200 with extracted subtitle content in SRT, VTT, TXT, or JSON format.
- Commercial Usage Metering: Categorized as `subtitle_retrieval` in the usage ledger.
- **Architectural Status**: Provider subtitle extraction via yt-dlp is active. Server-side Whisper AI speech-to-text transcription remains **deferred** on the backend and is clearly documented in the UI.

---

## 10. Verified vs Unverified Offline Playback

| Capability | Status | Verification Method |
| :--- | :---: | :--- |
| **Local Cache Download with SHA-256** | **VERIFIED** | Automated: bytes streamed to disk, SHA-256 verified before atomic promotion |
| **RFC 7233 Normal Range (`bytes=0-1023`)** | **VERIFIED** | Automated: returns HTTP 206 with `Content-Range: bytes 0-1023/81614` |
| **Open-Ended Range (`bytes=1000-`)** | **VERIFIED** | Automated: returns HTTP 206 from byte 1000 to EOF |
| **Suffix Range (`bytes=-500`)** | **VERIFIED** | Automated: returns HTTP 206 with final 500 bytes |
| **Out-of-Bounds Range (`bytes=999999999-`)** | **VERIFIED** | Automated: returns HTTP 416 Range Not Satisfiable |
| **Playback Disconnected from Backend** | **VERIFIED** | Automated: `mediaforge-media://` protocol serves cached media independently of API |
| **Post-Logout Access Revocation** | **VERIFIED** | Automated: playback rejected with HTTP 401 after `auth.logout()` |
| **Windows Hardware GPU Seeking Smoothness** | **UNVERIFIED (Manual)** | Manual QA: interactive scrub smoothness on discrete Windows GPU hardware |

---

## 11. Distribution Architecture Decision Matrix

| Criteria | Strategy A: Bundled Python Backend Sidecar | Strategy B: Hosted API Client |
| :--- | :--- | :--- |
| **Description** | Electron bundles and spawns a local Python FastAPI runtime, embedded PostgreSQL/SQLite, and FFmpeg binary directly on the user's workstation. | Electron acts as a pure presentation layer connecting to a hosted multi-tenant SaaS backend (or controlled Docker cluster) via HTTPS. |
| **Offline Capability** | Full offline capability. Video extraction, downloads, and merging run locally on consumer hardware without an internet connection. | Offline playback is limited to previously cached media. Job processing requires network connectivity to backend workers. |
| **Licensing Implications** | FFmpeg GPL/LGPL compliance requires careful distribution isolation. Python runtime licensing must be shipped with the installer. | Minimal licensing exposure; FFmpeg runs in backend container infrastructure. Desktop application only ships standard MIT/BSD dependencies. |
| **System Requirements** | Heavy client footprint (500MB+ installer, high RAM/CPU during video re-encoding). Requires local hardware acceleration config. | Ultra-lightweight desktop client (~80MB installer, minimal RAM/CPU footprint). Heavy FFmpeg workloads run on cloud infrastructure. |
| **Multi-Tenancy & Usage**| Challenging to enforce commercial usage quotas or commercial licensing without intrusive local DRM. | Authoritative PostgreSQL usage ledger, metering, and tenant quotas are securely enforced at the API boundary. |
| **Recommendation** | **Adopt Strategy B (Hosted API / Controlled Cluster) for Enterprise SaaS**, while reserving Strategy A as an optional "MediaForge Local Edition" for air-gapped single-user deployments. |

---

## 12. Manual Windows QA Checklist

Follow this checklist to manually verify the desktop application on Windows 11:

- [ ] **1. Clean Launch**:
  - Run: `cmd.exe /c "desktop\node_modules\electron\dist\electron.exe desktop"`
  - Verify window appears centered, dark theme applied, and backend health status displays "Connected".
- [ ] **2. Authentication**:
  - Sign in with authorized test credentials (e.g., `<test_user_email>`, `<test_user_password>`, `<tenant_slug>`).
  - Verify seamless redirect to Dashboard with user email and tenant displayed in top bar.
- [ ] **3. Submit Download**:
  - Open Downloads tab. Submit `https://www.youtube.com/watch?v=jNQXAC9IVRw`.
  - Verify job appears in queue as `queued` or `downloading`, reaches `completed`, and displays "Me at the zoo".
- [ ] **4. Media Library**:
  - Open Media Library tab. Verify completed downloads and artifacts are listed.
  - Test search filter by filename.
  - Select 2 video items using checkboxes and click "Merge Selected" — verify redirection to Merge workspace.
- [ ] **5. Create Clip**:
  - Open Create Clip tab. Select a media source, enter start `00:00:01` and end `00:00:04`.
  - Select `precise` mode. Click "Extract Clip".
  - Verify toast notification and automatic redirection to Jobs tab.
- [ ] **6. Clip Merging**:
  - Open Merge Clips tab. Reorder items with up/down arrows.
  - Verify dynamic duration estimate updates.
  - Click "Start Merge Job". Verify redirection to Jobs tab.
- [ ] **7. Subtitle / Transcript**:
  - Open Transcripts tab. Submit a video URL with English subtitles.
  - Verify extracted captions render in viewer and copy to clipboard functions.
  - Verify Whisper AI disclaimer is clearly presented.
- [ ] **8. Job Cancellation & Tracking**:
  - In Jobs tab, observe progress bars and attempt counts.
  - Click "Cancel" on a queued or running job; verify status updates to `cancelled`.
- [ ] **9. Video Playback & Seeking**:
  - Click "Play" on a cached artifact.
  - Verify `VideoPlayerModal` opens and scrubbing along the seekbar works instantly without buffering delays.
- [ ] **10. Logout & Session Invalidation**:
  - Click "Sign Out". Verify session vault is purged and app returns to Login screen.
  - Confirm video player fails to load media after logout.
