# MediaForge Architecture — Media Input Security, SSRF Protection & Filesystem Isolation

**Document Version:** 2.1.0
**Milestone:** STEP 02-D2-Fix-A
**Status:** Approved & Enforced
**Classification:** Core Security Architecture

---

## 1. Executive Summary & Philosophy

MediaForge is a multi-tenant media processing and downloading platform. By its nature, the service accepts externally supplied URLs from clients to resolve media metadata, stream audio/video content, extract transcripts, and generate clips.

Accepting arbitrary user-provided URLs introduces severe Server-Side Request Forgery (SSRF) and network pivoting risks. Without comprehensive defense-in-depth, an attacker could abuse MediaForge's egress network capabilities to:
- Probe internal infrastructure and container services (PostgreSQL, Redis, Mailpit, pgAdmin).
- Query cloud provider instance metadata services (IMDS) to steal IAM credentials or instance tokens.
- Pivot to private corporate subnets (RFC 1918 ranges).
- Exploit DNS rebinding or malicious HTTP redirects to bypass pre-flight URL filters.
- Exploit DNS Time-of-Check to Time-of-Use (TOCTOU) windows if addresses are re-resolved at connection time.
- Inject malicious protocols via subprocess pipelines (FFmpeg).

### Deny-by-Default Security Principles

MediaForge enforces a strict **multi-layered, deny-by-default security architecture**:
1. **Pre-flight Ingress Validation:** Every URL submitted to any API route is strictly validated and resolved before jobs are created, persisted, or passed to background workers.
2. **Direct IP Pinning at Socket Boundary:** Outbound network connections resolve DNS exactly once, validate every returned IP against prohibited ranges, and connect directly to the validated IP via `_socket_connect`. The connection function never re-resolves the hostname, completely closing DNS TOCTOU rebinding windows while preserving TLS SNI and certificate verification.
3. **Transport Layer Enforcement & RequestDirector Interception:** In yt-dlp, all outbound HTTP requests (extractor queries, intermediate metadata lookups, media chunks, and subtitle files) are intercepted via `RequestDirector.send`. Unconstrained transports (e.g. `requests`, `curl_cffi`) are removed and fail closed; only constrained transports (`Urllib`, `Websockets`) are permitted.
4. **Proxy & Environment Hardening:** Request-level proxies (`request.proxies`) and environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) are validated against the SSRF policy to prevent proxy-based pivoting.
5. **Redirect Chain Inspection:** Every intermediate HTTP redirect (301, 302, 303, 307, 308) is inspected against the full safety policy before following.
6. **Local-Only FFmpeg Subprocess Boundary:** FFmpeg operations are restricted strictly to validated local files with explicit protocol whitelisting (`-protocol_whitelist file,crypto,data,concat,pipe`), preventing FFmpeg from initiating outbound network connections.

---

## 2. Threat Model & Prohibited Destinations

### 2.1 Prohibited IP Address Ranges

MediaForge identifies and classifies destination IP addresses using Python's standard `ipaddress` library combined with `socket.inet_aton` address normalization. Outbound connections to the following subnets are strictly prohibited:

| Category | IPv4 / IPv6 Subnet | RFC Standard | Threat Vector |
| :--- | :--- | :--- | :--- |
| **Current Network** | `0.0.0.0/8`, `::/128` | RFC 1122, RFC 4291 | Source-only address, internal host loopback |
| **Loopback** | `127.0.0.0/8`, `::1/128` | RFC 1122, RFC 4291 | Localhost services, host-bound daemon interception |
| **Private Networks** | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7` | RFC 1918, RFC 4193 | Intranet resources, Docker bridge networks, internal VPCs |
| **Link-Local** | `169.254.0.0/16`, `fe80::/10` | RFC 3927, RFC 4291 | Cloud metadata endpoints, link-local container bridges |
| **Carrier-Grade NAT** | `100.64.0.0/10` | RFC 6598 | Shared provider space; includes Alibaba Cloud IMDS (`100.100.100.200`) |
| **Documentation / Test**| `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, `2001:db8::/32` | RFC 5737, RFC 3849 | Non-routable documentation subnets |
| **Multicast / Reserved**| `224.0.0.0/4`, `240.0.0.0/4`, `ff00::/8` | RFC 5771, RFC 1112 | Class E reserved, broadcast, multicast groups |
| **IPv4-Mapped IPv6** | `::ffff:0:0/96` | RFC 4291 | Evasion attempts wrapping prohibited IPv4 inside IPv6 literals |

### 2.2 Cloud Metadata Protection

Cloud instances (AWS, GCP, Azure, Alibaba, OpenStack) expose Instance Metadata Services (IMDS) containing sensitive identity credentials:
- **AWS / OpenStack / Azure:** `169.254.169.254` (IPv4) and `fd00:ec2::254` (IPv6 ULA)
- **AWS ECS Containers:** `169.254.170.2`
- **Google Cloud Platform:** `metadata.google.internal` (and `metadata.google.internal.`)
- **Alibaba Cloud:** `100.100.100.200` (within CGNAT `100.64.0.0/10`)
- **Legacy Instance Data:** `instance-data`

All such addresses and hostnames are explicitly blocked regardless of URI scheme or encoding.

### 2.3 Internal Docker Services

The MediaForge local environment consists of 5 interconnected Docker services:
- **PostgreSQL:** Port `5432` (host mapped `55432`)
- **Redis:** Port `6379` (host mapped `26379`)
- **Mailpit:** Ports `1025`, `8025` (host mapped `51025`, `58025`)
- **pgAdmin:** Port `80` (host mapped `58080`)
- **MediaForge API:** Port `8000` (host mapped `58000`)

The policy prohibits direct access to these services via:
- Container service hostnames (`postgres`, `redis`, `mailpit`, `pgadmin`, `mediaforge-api`, `api`).
- Docker host gateway DNS names (`host.docker.internal`, `gateway.docker.internal`).
- Container IP addresses on the `mediaforge-net` bridge network (`172.16.0.0/12`).
- Disallowed service ports across any host (e.g. ports `5432`, `6379`, `1025`, `22`, `25`, etc.).

---

## 3. Alternative Address Encodings & Normalization

Attackers frequently employ unusual IP address encodings to bypass naive regex or string matching filters. MediaForge normalizes and unpacks all representations prior to classification:

1. **Decimal / Dword:** `http://2130706433/` is normalized to `127.0.0.1` via `socket.inet_aton`.
2. **Octal Notation:** `http://0177.0.0.1/` is normalized to `127.0.0.1`.
3. **Hexadecimal Notation:** `http://0x7f000001/` is normalized to `127.0.0.1`.
4. **Abbreviated Quads:** `http://127.1/` is normalized to `127.0.0.1`.
5. **IPv4-Mapped IPv6:** `http://[::ffff:127.0.0.1]/` is unpacked via `ipaddress.IPv6Address.ipv4_mapped` to `127.0.0.1`.
6. **URL Percent-Encoding:** Percent-encoded hostnames such as `http://%31%32%37%2e%30%2e%30%2e%31/` are unquoted via `urllib.parse.unquote` before host evaluation.

---

## 4. Architecture & Implementation

### 4.1 Centralized URL Safety Policy (`api/security/url_policy.py`)

The module `api/security/url_policy.py` provides the canonical validation primitives:
- `validate_url(url: str, resolve_dns: bool = True) -> str`: High-level entry point validating schemes (`http`, `https` only), disallowing userinfo credentials, verifying valid ports, checking prohibited hostnames, normalizing IP literals, and performing DNS resolution.
- `try_parse_ip(host_str: str) -> IPv4Address | IPv6Address | None`: Universal IP parser handling standard, alternative (octal/hex/dword), and IPv4-mapped addresses.
- `is_ip_prohibited(ip) -> bool`: Checks address membership against RFC prohibited subnets and explicit metadata IPs.
- `check_proxy_url(proxy_url: str) -> None`: Validates proxy endpoints against SSRF rules to prevent egress tunnel bypasses.
- `install_yt_dlp_egress_guard()`: Hooks into `yt_dlp` networking components (`_helper.create_connection`, `_urllib`, `_websockets`, and `RequestDirector.send`).

### 4.2 Single-Resolution DNS & Direct Socket IP Pinning

Prior implementations resolved DNS during pre-flight validation and then passed the hostname back to the underlying `create_connection`, allowing a second `getaddrinfo` call to resolve. If an attacker configured a DNS server with a low TTL (or 0 TTL) that alternates between a benign IP and a private IP, a TOCTOU bypass could occur.

MediaForge implements **Single-Resolution Direct IP Pinning**:
1. When `guarded_create_connection(address, timeout=..., source_address=...)` is invoked with `address = (host, port)`:
   - If `host` is an IP literal, it is checked directly with `is_ip_prohibited`.
   - If `host` is a domain name, `socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)` resolves the host **once**.
2. **Multi-Homed DNS Rejection:** Every returned address record is validated. If any IP address belongs to a prohibited subnet, the connection is immediately aborted with `PermissionError`.
3. **Direct IP Connection:** The socket is created and connected directly to the resolved address tuple using `_socket_connect(ip_addr, timeout, source_address)` where `ip_addr[4]` contains the already-validated `(ip_str, port)`.
4. **Zero TOCTOU:** No subsequent resolution occurs. The socket connects only to the IP validated in that call.
5. **TLS SNI & Verification Preserved:** When TLS negotiation occurs (e.g. in `HTTPSConnection.connect()`), standard Python/urllib TLS logic invokes `ctx.wrap_socket(sock, server_hostname=self.host)`. Because `self.host` retains the original SNI hostname, TLS certificate verification, SAN matching, and SNI headers function with 100% fidelity.

### 4.3 Outbound Transport Interception (`RequestDirector.send`)

yt-dlp supports multiple pluggable request handlers (`Urllib`, `Websockets`, `Requests`, `CurlCFFI`). To guarantee complete coverage:
1. **Transport Whitelisting:** `ALLOWED_REQUEST_HANDLERS` permits only `{"Urllib", "Websockets"}`.
2. **Fails Closed on Unconstrained Transports:** Any unconstrained handlers (`requests`, `curl_cffi`) are stripped from `YoutubeDL._REQUEST_HANDLERS`. If an extractor or caller attempts to dispatch through an unconstrained handler, `guarded_request_director_send` raises `PermissionError`.
3. **Omni-Directional URL Inspection:** `guarded_request_director_send` intercepts `RequestDirector.send(request)`. All outbound requests—including extractor queries, playlist lookups, video stream segment URLs, and subtitle endpoints—are re-validated via `validate_url(request.url, resolve_dns=True)`.
4. **Proxy Guarding:** Request-specific proxies (`request.proxies`) and ambient environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) are inspected with `check_proxy_url`. Proxies pointing to loopback, RFC 1918, or cloud metadata endpoints are rejected immediately.

### 4.4 HTTP Redirect Protection

Attackers may host an ostensibly benign public web server that issues HTTP 301, 302, 303, 307, or 308 redirects pointing to `http://169.254.169.254/latest/meta-data/` or internal containers.

The `guarded_redirect_request` hook wraps `yt_dlp.networking._urllib.RedirectHandler.redirect_request`. Before constructing the subsequent request object, the target `newurl` is subjected to the full `validate_url(newurl, resolve_dns=True)` policy. Any redirect to a prohibited destination immediately terminates the request chain.

### 4.5 Subprocess Protocol Whitelisting (FFmpeg)

FFmpeg's built-in network demuxers (`http://`, `tcp://`, `rtmp://`, `crypto://`, `concat://`) present severe risks of protocol injection or arbitrary file disclosure. MediaForge isolates FFmpeg through two independent mechanisms:
1. **Local-Only Input Files:** Video and audio inputs are always downloaded and verified on disk by yt-dlp prior to FFmpeg invocation.
2. **Hardened Protocol Whitelisting:** In `src/clip/extractor.py` and `src/clip/merger.py`, all FFmpeg command invocations explicitly specify:
   ```bash
   -nostdin -protocol_whitelist file,crypto,data,concat,pipe
   ```
   If FFmpeg encounters an attempt to open remote URLs (`http://`, `https://`, `tcp://`), the operation fails immediately at the binary boundary (`Protocol not on whitelist`).

---

## 5. API Entry Points & Error Handling

All URL-accepting API endpoints invoke `validate_url` immediately upon request arrival:

| Endpoint | Parameter | Action on SSRF Rejection |
| :--- | :--- | :--- |
| `POST /api/v1/downloads` | `body.url` | HTTP 400 Bad Request (`SSRF_SECURITY_ERROR`) |
| `POST /api/v1/downloads/batch` | `body.urls` (all) | HTTP 400 Bad Request (`SSRF_SECURITY_ERROR`) |
| `POST /api/v1/downloads/playlist` | `body.url` | HTTP 400 Bad Request (`SSRF_SECURITY_ERROR`) |
| `POST /api/v1/resolve` | `body.url` | HTTP 400 Bad Request (`SSRF_SECURITY_ERROR`) |
| `GET /api/v1/transcripts/languages`| `url` query param | HTTP 400 Bad Request (`SSRF_SECURITY_ERROR`) |
| `POST /api/v1/transcripts/extract` | `body.url` | HTTP 400 Bad Request (`SSRF_SECURITY_ERROR`) |
| `POST /api/v1/clips/extract` | `body.source` (URL) | HTTP 400 Bad Request (`SSRF_SECURITY_ERROR`) |

Rejected URLs fail fast at the boundary and **never** create database records, enqueue background jobs, or trigger outbound network traffic.

---

## 6. Verification & Test Matrix

The test suite in `tests/unit/test_ssrf_protection.py` validates 28 comprehensive security scenarios using fast, fully-mocked fixtures without external network dependencies:
- Loopback rejection (127.0.0.1, localhost, [::1], 127.0.0.2, 127.1).
- RFC 1918 private subnets (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16).
- Cloud metadata endpoints (AWS, GCP, Azure, Alibaba, IPv6 IMDS).
- Docker container hostnames and gateways (`postgres`, `redis`, `host.docker.internal`).
- Alternative encodings (dword, octal, hex, abbreviated, IPv4-mapped IPv6).
- Prohibited schemes (`file://`, `ftp://`, `gopher://`, `data:`, `javascript:`, `dict://`).
- Embedded credentials rejection (`http://user:pass@host/`).
- Port restrictions (SSH 22, SMTP 25, Mailpit 1025, PostgreSQL 5432, Redis 6379, host ports).
- Multi-homed DNS answers (reject if any IP is prohibited).
- Direct socket IP pinning (`test_actual_connected_ip_is_validated_ip`).
- DNS TOCTOU rebinding resistance: single resolution verified (`test_dns_rebinding_cannot_re_resolve_or_change_target`).
- Mixed DNS answer rejection at connection boundary (`test_mixed_dns_answers_rejected_at_connection_boundary`).
- `RequestDirector.send` protection for extractor media and subtitle requests (`test_extractor_generated_media_and_subtitle_requests_protected`).
- Request-level proxy bypass defense (`test_proxy_configuration_cannot_bypass_policy`).
- Environment proxy tampering defense (`test_environment_proxy_tampering_blocked`).
- Unconstrained transport fail-closed policy (`test_unsupported_transports_fail_closed`).
- Multi-threaded concurrent job safety (`test_concurrent_job_safety`).
- Subprocess FFmpeg protocol whitelist enforcement (`test_ffmpeg_protocol_whitelist_enforcement`).
- yt-dlp HTTP redirect intercept (`guarded_redirect_request`).
- Full API route boundary rejection across all 7 endpoints.
- Legitimate public HTTPS media URLs (YouTube, Vimeo, web CDN) pass smoothly.

---

## 7. Outbound Egress Proxy Security Architecture

Media processing components (especially yt-dlp) may encounter client-controlled or environment-level proxy configurations. In an untrusted multi-tenant context, allowing arbitrary proxies or ambient proxy inheritance introduces severe risk of bypassing DNS and IP socket guards.

### 7.1 Rejection of Arbitrary Public and Ambient Proxies
1. **Public Proxies Never Trusted:** MediaForge strictly forbids API requests from specifying arbitrary HTTP, HTTPS, or SOCKS proxies (`proxy` or `proxies` options). Public proxies do not guarantee destination IP enforcement and can be abused to establish blind tunnels to internal network addresses.
2. **Ambient Environment Neutralization:** Ambient environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `http_proxy`, `https_proxy`, `all_proxy`) are prohibited from silently bypassing the SSRF guard. If set to an untrusted proxy, requests fail closed.
3. **Explicit Trusted Gateway:** Only an explicitly configured enterprise egress proxy specified in `MEDIAFORGE_TRUSTED_EGRESS_GATEWAY` is permitted. When configured, yt-dlp dispatches requests through this single verified gateway while preserving full TLS certificate verification (`check_certificate = True`).

---

## 8. Tenant Filesystem Isolation Architecture

MediaForge operates in a multi-tenant environment where media downloads, clips, merged video files, and temporary workspaces must remain strictly partitioned between tenants.

### 8.1 Directory Hierarchy & Storage Layout
All media files are segregated into a deterministic, tenant-isolated filesystem hierarchy rooted at `get_media_storage_root()`:
```
{MEDIA_ROOT}/
└── {tenant_id}/
    ├── downloads/    # Completed and queued video downloads
    ├── clips/        # Extracted media clips
    ├── merged/       # Merged video artifacts
    └── temp/         # Isolated scratch workspaces (auto-purged)
```

### 8.2 Path Security Primitives (`api/security/media_storage.py`)
1. **Tenant ID Sanitization:** `validate_tenant_identifier(tenant_id)` ensures identifiers strictly match `^[a-zA-Z0-9_\-]+$` with a maximum length of 64 characters, blocking directory traversal tokens (`..`) and separators (`/`, `\`).
2. **Filename Normalization & Forbidden Tokens:** `validate_safe_filename(name)` rejects:
   - Directory traversal (`..`, `.`).
   - Path separators (`/`, `\`).
   - Control characters and null bytes (`\0`, `\r`, `\n`).
   - Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`).
   - NTFS Alternate Data Streams (`filename:stream`).
3. **Traversal Token Detection:** `check_untrusted_path_traversal(path_str)` rejects client-supplied strings containing absolute path prefixes (`/`, `\`, drive letters `C:`, UNC `\\`) and `..` segments before any filesystem syscall is performed.
4. **Symlink Escape Defense:** `resolve_and_authorize_clip_source()` verifies `os.path.realpath()` and `Path.resolve()` against the caller's authorized tenant directory. If a file is a symlink pointing outside the tenant boundary, access is denied immediately with HTTP 404/403.
5. **No Server Path Exposure:** API endpoints never expose absolute host server paths (e.g. `/app/downloads/...` or `D:\Dev\...`). Output paths in API responses are sanitized relative references (e.g. `clips/video_clip_abc123.mp4` or `merged/merged_xyz789_2clips.mp4`).

---

## 9. Secure Offline Clipping and Media Merging

MediaForge enables authenticated API clients to perform offline clipping and video concatenation on existing downloaded media without initiating outbound network requests.

### 9.1 Source Media Authorization & Anti-Enumeration
- When an API client submits a clip request with a local file identifier (e.g. `"video_a.mp4"` or `"downloads/video_a.mp4"`), `resolve_and_authorize_clip_source` inspects only the caller's tenant directory `{media_root}/{tenant_id}/`.
- If an attacker attempts to reference another tenant's file (e.g. `secret_b.mp4`), the resolver returns **HTTP 404 Not Found**, preventing cross-tenant existence enumeration.
- Direct absolute paths (e.g. `/etc/passwd`) are rejected with **HTTP 400 Bad Request**.

### 9.2 Timestamp and Boundary Validation
- `ClipExtractor.validate_timestamps(start, end, source_duration)` executes prior to spawning subprocesses.
- Rejects negative start times (`start < 0`).
- Rejects non-chronological bounds (`end <= start`).
- Rejects clips shorter than 0.5 seconds or longer than 7,200 seconds (2 hours).
- Enforces source media duration bounds when probing local files (`end <= source_duration`).

### 9.3 Collision-Resistant Output Naming
Outputs are generated using `generate_safe_output_path()` which combines:
- Sanitized source stem or prefix.
- Normalized start and end timestamps.
- A cryptographically random UUID token (`uuid.uuid4().hex[:12]`).
- Explicit file extension whitelist (`.mp4`, `.m4a`, `.webm`, `.mp3`).

---

## 10. FFmpeg / FFprobe Subprocess Hardening

Media processing involves invoking `ffmpeg` and `ffprobe` as OS subprocesses. MediaForge enforces rigid sandboxing at the execution boundary:

1. **Structured Argument Arrays:** Commands are executed strictly via `list[str]` arguments passed directly to `subprocess.run()`. `shell=True` is forbidden across the entire codebase, eliminating shell interpolation vulnerabilities.
2. **Local-Only Protocol Whitelists:**
   - Probing & Clipping: `-nostdin -protocol_whitelist file,crypto,data`
   - Concatenation / Merging: `-nostdin -protocol_whitelist file,crypto,data,concat`
   Attempts by FFmpeg to access remote protocols (`http://`, `https://`, `tcp://`, `rtmp://`) or dangerous local demuxers (`pipe:`, `subfile:`) fail immediately.
3. **Concat Manifest Injection Neutralization:**
   - FFmpeg's `concat` demuxer reads manifest text files with `file 'path'` syntax.
   - Attackers could inject arbitrary demuxer directives if filenames contained unescaped single quotes or newlines (`\n`, `\r`).
   - `build_safe_concat_manifest()` escapes single quotes (`'` -> `'\''`), verifies all inputs reside strictly inside the caller's tenant folder, and rejects filenames containing newlines or null bytes (`\0`).
4. **Execution Timeouts:**
   - Dynamic timeout scaling: `timeout = max(300, min(1800, int(clip_duration * 1.5)))`.
   - Subprocesses exceeding the timeout are terminated (`subprocess.TimeoutExpired`), and partial output artifacts are immediately unlinked.
5. **Isolated Temporary Workspaces:**
   - `tenant_temp_workspace(tenant_id)` context manager provides unique temporary directories under `{media_root}/{tenant_id}/temp/`.
   - Upon completion or failure, temporary directories are recursively unlinked (`shutil.rmtree`), preventing temporary disk leaks.

---

## 11. Resource Quotas and Concurrency Limiting

Media operations are compute- and I/O-intensive. MediaForge protects system availability through explicit resource quotas:

| Guard / Limit | Threshold | Configuration Key | Enforcement Point |
| :--- | :--- | :--- | :--- |
| **Max Media Input Size** | 2048 MB (2 GB) | `MEDIAFORGE_MAX_INPUT_SIZE_MB` | `resolve_and_authorize_clip_source` |
| **Max Clip Duration** | 7200 s (2 hours) | `MEDIAFORGE_MAX_CLIP_DURATION` | `ClipExtractor.validate_timestamps` |
| **Min Clip Duration** | 0.5 s | Hardcoded | `ClipExtractor.validate_timestamps` |
| **Max Concat Inputs** | 20 clips | `MAX_MERGE_INPUTS` | `POST /api/v1/clips/merge` |
| **Min Free Disk Space** | 500 MB | `MEDIAFORGE_MIN_DISK_SPACE_MB` | Pre-flight check before clip/merge |
| **FFmpeg Concurrency** | 3 concurrent jobs | `MEDIAFORGE_MAX_FFMPEG_CONCURRENCY`| `asyncio.Semaphore` in clips routers |
| **Subprocess Timeout** | 1800 s (30 min max)| `MEDIAFORGE_FFMPEG_TIMEOUT` | `subprocess.run(timeout=...)` |

When disk space falls below 500 MB, the API returns **HTTP 507 Insufficient Storage**. When concurrent FFmpeg execution slots are saturated, callers wait on an async semaphore with a 10-second timeout, failing gracefully with **HTTP 503 Service Unavailable**.

---

## 12. Artifact Authorization & Persistent Schema Proposal

### 12.1 Current Artifact Authorization
All artifact serving endpoints (`/api/v1/downloads/{id}/file`, `/api/v1/clips/{id}/download`, `/api/v1/clips/merge/{id}/download`) enforce:
1. **Authenticated Caller Scoping:** Requiring valid JWT Bearer or API Key credentials with appropriate scopes (`downloads:read`, `clips:read`).
2. **Tenant Scoping:** Files are searched and served strictly from `{media_root}/{user.tenant_id}/`.
3. **Anti-Enumeration 404:** Non-existent or cross-tenant file IDs return HTTP 404 with identical generic error messages.
4. **Traversal & Regular File Checks:** Returned files must resolve inside the authorized directory and must be regular files (`is_file()`).

### 12.2 Recommended Future Persistent Schema (`ClipDB` and `MergeDB`)
To support commercial artifact auditing, retention policies, and per-tenant usage accounting in future milestones without schema modifications in this phase, the following PostgreSQL schema extension is recommended:

```sql
-- Proposed schema for future migration (milestone STEP 02-E)
CREATE TABLE gid.clips (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES gid.tenants(id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES gid.users(id) ON DELETE CASCADE,
    source_download_id VARCHAR(64) REFERENCES gid.download_jobs(id) ON DELETE SET NULL,
    source_file_path VARCHAR(512) NOT NULL,
    output_relative_path VARCHAR(512) NOT NULL,
    start_time VARCHAR(32) NOT NULL,
    end_time VARCHAR(32) NOT NULL,
    duration_seconds DOUBLE PRECISION NOT NULL,
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    file_hash_sha256 VARCHAR(64),
    status VARCHAR(32) NOT NULL DEFAULT 'completed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_clips_tenant_created ON gid.clips(tenant_id, created_at DESC);
CREATE INDEX idx_clips_user_created ON gid.clips(user_id, created_at DESC);

CREATE TABLE gid.merges (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES gid.tenants(id) ON DELETE CASCADE,
    user_id VARCHAR(64) NOT NULL REFERENCES gid.users(id) ON DELETE CASCADE,
    output_relative_path VARCHAR(512) NOT NULL,
    clip_count INTEGER NOT NULL,
    clips_used JSONB NOT NULL,
    total_duration_seconds DOUBLE PRECISION NOT NULL,
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    file_hash_sha256 VARCHAR(64),
    status VARCHAR(32) NOT NULL DEFAULT 'completed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_merges_tenant_created ON gid.merges(tenant_id, created_at DESC);
```

---

## 13. Operational Verification & Real Subprocess Safety (STEP 02-D2-Fix-A)

Milestone STEP 02-D2-Fix-A rigorously verified all filesystem isolation, subprocess sandboxing, and offline clipping operations against real FFmpeg and FFprobe binaries executing inside the live Docker API container (`mediaforge-api:dev`).

### 13.1 Unit-Tested Safeguards vs. Real Docker Operational Verifications
To avoid false security confidence based solely on unit test mocks, MediaForge enforces a strict two-tier verification model:
1. **Unit Test Matrix (97 passing tests):** Evaluates fast boundary checks, mock subprocess invocations, regex validation, HTTP exception codes, and dependency override behaviors without external processes.
2. **Real Docker Integration Verification (`deploy/scripts/verify_real_media_processing.py`):**
   - Synthesizes real MP4 media inside the container using FFmpeg 7.1.5 (video: `testsrc`, audio: `sine=1000Hz`, libx264/aac).
   - Probes format, stream properties, and durations using real FFprobe.
   - Executes real offline clipping and merging through authenticated FastAPI HTTP calls (`http://127.0.0.1:8000`).
   - Verifies on-disk artifacts, playability, relative output paths, source immutability, and cross-tenant anti-enumeration (HTTP 404).

### 13.2 Subprocess CLI Audit & Options Hardening
During operational verification, specific FFmpeg/FFprobe CLI behaviors were audited and hardened:
1. **FFprobe `-nostdin` Incompatibility Fix:** FFmpeg supports `-nostdin` to prevent reading interactive terminal commands, but `ffprobe` does not recognize this option (`Failed to set value '-protocol_whitelist' for option 'nostdin'`). `-nostdin` was removed from all FFprobe invocations (`_get_duration`, `_is_output_playable`, `_get_stream_info`), restoring real duration probing.
2. **Concat Demuxer `-safe 0` Necessity:** In FFmpeg's concat demuxer (`-f concat`), the default `-safe 1` rejects any absolute path or path with directory separators as an "Unsafe file name". Because MediaForge resolves and confirms absolute canonical paths strictly inside `{media_root}/{tenant_id}/`, `-safe 0` is strictly necessary. Concat manifest injection is defended by escaping single quotes (`'` -> `'\''`), verifying absolute tenant containment, and rejecting newlines (`\n`, `\r`) and null bytes (`\0`).
3. **Protocol Whitelisting:**
   - Clipping & Probing: Restrict to `-protocol_whitelist file` (or `file,crypto,data` for streams).
   - Concat Demuxer: Restrict to `-protocol_whitelist file,concat` (eliminating `crypto` and `data` protocols).

### 13.3 Filesystem Race Conditions (TOCTOU) & Ownership Analysis
1. **Symlink Replacement Defenses:**
   - Pre-execution: `generate_safe_output_path` unlinks any pre-existing symlink at the destination path before FFmpeg execution and generates 16-hex cryptographically random UUID tokens ($2^{64}$ entropy).
   - Post-execution: `download_clip`, `download_merged`, and `download_file` explicitly check `not resolved.is_symlink()`, completely preventing `FileResponse` from streaming symlink targets.
2. **Hardlink Cross-Tenant Attacks:**
   - Container runs as unprivileged user `mediaforge` (uid 1000). Modern Linux kernel protections (`fs.protected_hardlinks=1`) prevent creating hardlinks to files not owned by the caller.
   - Verification of `st_nlink > 1` provides detection against anomalous hardlinked media files.
3. **Intra-Tenant Member Ownership Resolution (STEP 02-E1):**
   - The intra-tenant access gap has been resolved in STEP 02-E1 through the `gid.media_artifacts` table.
   - Every clip and merge now possesses an authoritative, database-persisted record linking `user_id`, `tenant_id`, `api_key_id`, output checksums, file size, and lifecycle state.
   - Intra-tenant member isolation is strictly enforced: non-admin members can only query, stream, or delete artifacts where `artifact.user_id == caller.user_id`. Attempting to access another member's artifact within the same tenant returns HTTP 404 (anti-enumeration).
   - Tenant administrators and owners maintain oversight across all tenant artifacts.

---

## 14. Persistent Media Artifacts & Lifecycle Architecture (STEP 02-E1)

### 14.1 Database Model & Schema (`gid.media_artifacts`)

The authoritative PostgreSQL schema `gid.media_artifacts` was introduced in Alembic migration revision `3f6397f5179d`:

```sql
CREATE TABLE gid.media_artifacts (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(36) NOT NULL REFERENCES gid.tenants(id) ON DELETE CASCADE,
    user_id VARCHAR(36) REFERENCES gid.users(id) ON DELETE SET NULL,
    api_key_id VARCHAR(36) REFERENCES gid.api_keys(id) ON DELETE SET NULL,
    artifact_type VARCHAR(20) NOT NULL,            -- 'clip' or 'merge'
    source_download_id VARCHAR(36) REFERENCES gid.download_jobs(id) ON DELETE SET NULL,
    parent_artifact_id VARCHAR(36) REFERENCES gid.media_artifacts(id) ON DELETE SET NULL,
    storage_path VARCHAR(512) NOT NULL,            -- Relative to tenant storage root
    filename VARCHAR(255) NOT NULL,
    format VARCHAR(20) NOT NULL DEFAULT 'mp4',
    duration_seconds FLOAT DEFAULT 0.0,
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    sha256_checksum VARCHAR(64),
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- 'pending', 'processing', 'ready', 'failed', 'deleted'
    error_message TEXT,
    input_sources JSON,
    metadata_json JSON,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITHOUT TIME ZONE
);
```

#### Performance Indexes:
1. `ix_media_artifacts_tenant_created` on `(tenant_id, created_at)`: Optimizes paginated artifact listings.
2. `ix_media_artifacts_tenant_user` on `(tenant_id, user_id)`: Enforces intra-tenant member scoping.
3. `ix_media_artifacts_tenant_type_status` on `(tenant_id, artifact_type, status)`: Accelerates filtering by type and ready/deleted state.
4. `ix_media_artifacts_storage_path` (UNIQUE) on `(tenant_id, storage_path)`: Prevents tenant path collision.

### 14.2 Decoupled Database Transactions & Asynchronous Execution

To prevent database connection starvation during long FFmpeg encoding passes:
1. **Pre-execution Commit:** When `/api/v1/clips/extract` or `/api/v1/clips/merge` is called, a pending record (`status="processing"`) is committed in the active request session.
2. **Decoupled Worker Execution:** FFmpeg execution runs in a threadpool executor bounded by the `asyncio.Semaphore` (`get_ffmpeg_semaphore()`). No database transaction is held open during transcoding.
3. **Decoupled Completion Commit:** Upon FFmpeg completion, the output file size, duration, and SHA-256 checksum are calculated and committed in a brand-new, decoupled database session via `complete_artifact_in_fresh_session()`. If transcoding fails, errors are sanitized and committed via `fail_artifact_in_fresh_session()`.

### 14.3 Dedicated Artifact Endpoints & RBAC

Mounted under `/api/v1/artifacts`:
- `GET /api/v1/artifacts`: List persistent artifacts with pagination (`limit`, `offset`) and filtering (`artifact_type`, `status`). Scoped to tenant; members see only own artifacts.
- `GET /api/v1/artifacts/{id}`: Detailed metadata inspection.
- `GET /api/v1/artifacts/{id}/download`: File streaming. Fails closed with HTTP 400 if unready, and HTTP 404 if deleted or cross-tenant.
- `DELETE /api/v1/artifacts/{id}`: Soft-deletes database record (`status="deleted"`) and immediately unlinks physical file from tenant disk storage.

### 14.4 Backward Compatibility & Legacy Fallback
For backward compatibility with media generated prior to STEP 02-E1:
- By default, unindexed legacy media path fallback is **disabled** (`MEDIAFORGE_ENABLE_LEGACY_MEDIA_FALLBACK=0`), failing closed with HTTP 404 for all callers while strictly preserving physical disk files.
- When explicitly enabled (`MEDIAFORGE_ENABLE_LEGACY_MEDIA_FALLBACK=1`), only tenant admins or owners can access legacy files on disk; regular members are rejected with HTTP 404.

---

## 15. Docker Storage Architecture, Volume Consistency, and Reconciliation (STEP 02-E1-Fix-B)

### 15.1 Storage Mount Layout & Volume Isolation
MediaForge API container (`mediaforge-api:dev`) operates with two decoupled persistent Docker volumes:
1. **`mediaforge_api_downloads` -> `/app/downloads`:**
   - Canonical root configured via environment variable `GID_DOWNLOAD__OUTPUT_DIRECTORY="/app/downloads"`.
   - Host function `get_media_storage_root()` returns `/app/downloads`.
   - Isolated tenant workspace created dynamically at `/app/downloads/{tenant_id}/` with subdirectories `clips/`, `merged/`, `downloads/`, and `temp/`.
   - All FFmpeg clipping, merging, and downloading operations execute within this volume mount.
   - Authoritative paths stored in `gid.media_artifacts.storage_path` are strictly relative to `{tenant_id}/` (e.g. `clips/synthetic_test_clip_00_xyz.mp4`), guaranteeing persistent resolution regardless of host or container directory prefixes.
2. **`mediaforge_api_data` -> `/app/data`:**
   - Isolated volume for runtime state, application logs, and transient daemon data.

### 15.2 Container Lifecycle Persistence & Verification
Verification with `deploy/scripts/verify_storage_persistence.py` confirmed 100% data persistence:
- **Container Restart:** Media files and `ready` database artifacts remained fully intact and accessible after `docker compose restart api`.
- **Container Destruction and Recreation:** After executing `docker compose up -d --force-recreate api` (destroying container instance and creating a new container from the image without removing named volumes):
  - Both clip and merged media files remained available at `/app/downloads/{tenant_id}/`.
  - HTTP `GET /api/v1/artifacts/{id}/download` returned byte-for-byte identical content matching recorded SHA-256 checksums and file sizes.
  - Multi-tenant and intra-tenant isolation remained strictly enforced (Member 2 received 404, Owner received 200, Cross-tenant caller received 404).

### 15.3 Reconciliation Scanner Architecture & Anomaly Classification
The dry-run reconciliation scanner (`api/services/reconcile_artifacts.py`) was enhanced to audit discrepancies with zero destructive writes:
- Validates canonical media storage root existence, directory type, and read permissions.
- Distinguishes absent storage files from inaccessible paths (permission errors or filesystem faults).
- Recursively audits all tenant directories and files.
- Separates orphaned temporary work files (`orphaned_temp_count`) from unindexed legacy media (`orphaned_unindexed_count`).

#### Root Cause Analysis of Historical Anomalies
1. **Zero-File Scan (0 files scanned on disk):**
   - **Root Cause:** Historical integration tests ran an aggressive teardown step that swept and unlinked all files from disk before the operator executed the standalone CLI reconciliation utility.
2. **Missing Ready Files (7 merge artifacts, 2 test fixtures):**
   - **Root Cause:** In past integration runs, Task 2.2 created merged video records in `gid.media_artifacts` with `status='ready'`. The test script teardown unlinked the physical files on disk via Python's `Path.unlink()` without invoking `DELETE /api/v1/artifacts/{id}` via the API.
   - **Resolution:** `verify_real_media_processing.py` Task 6.2 now deletes the merge artifact via the API, synchronizing database status (`deleted`) with disk file deletion.
3. **Stale Processing Artifact (`f76f7f6cc0c0`):**
   - **Root Cause:** Created during initial STEP 02-E1 development on 2026-10-18 at 17:47 UTC when an FFmpeg transcode was interrupted before completion. The record remained stuck in `status='processing'` beyond the 30-minute threshold.
4. **Historical Database Downgrade Assessment:**
   - Dropping `gid.media_artifacts` during development dropped ephemeral test records created in that 10-minute development window.
   - Zero production data loss occurred, as no production users, download jobs, or customer media existed in the system.

### 15.4 Proposed Remediation Plan for Historical Test Artifacts
In accordance with production safety policies (no automatic mutation of live database records):
- **Plan for 7 Missing Ready Merge Artifacts**: Issue administrative soft-delete or archive update (`UPDATE gid.media_artifacts SET status = 'deleted', error_message = 'Storage file missing on disk (pre-Fix-B test cruft)' WHERE status = 'ready' AND id IN (...)`).
- **Plan for 1 Stale Processing Artifact `f76f7f6cc0c0`**: Update status to `failed` (`UPDATE gid.media_artifacts SET status = 'failed', error_message = 'Processing timed out or interrupted' WHERE id = 'f76f7f6cc0c0'`).
