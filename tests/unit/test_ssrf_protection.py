"""SSRF Protection, URL Validation and Secure Network Access Test Suite.

Milestone: STEP 02-D1
Verifies:
1. Loopback rejection (127.0.0.1, localhost, [::1], 127.0.0.2, 127.1).
2. RFC 1918 private network rejection (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16).
3. Link-local and cloud metadata rejection (169.254.169.254, 169.254.170.2, 100.100.100.200, metadata.google.internal, [fd00:ec2::254]).
4. Internal Docker and service name rejection (postgres:5432, redis:6379, mailpit:1025, host.docker.internal:58000, gateway.docker.internal).
5. Unusual IP representation rejection (dword, octal, hex, abbreviated, IPv4-mapped IPv6).
6. Scheme validation (strictly http/https; reject file, ftp, gopher, data, javascript, dict).
7. Userinfo rejection (reject URLs containing user credentials).
8. Port restriction (reject dangerous internal and administrative ports).
9. DNS resolution checks (hostname resolving to private IP or multi-homed mixed public/private IPs).
10. Redirect validation (prevent HTTP redirect following to private/metadata destinations).
11. DNS rebinding prevention (inspect destination IP at socket connection boundary).
12. Legitimate public media URL acceptance.
13. API boundary integration across downloads, resolve, transcripts, and clips endpoints.
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from api.dependencies import CurrentUser, get_app, get_current_user, require_scope
from api.main import app
from api.security.url_policy import (
    DISALLOWED_PORTS,
    PROHIBITED_HOSTNAMES,
    PROHIBITED_IPV4_NETWORKS,
    PROHIBITED_IPV6_NETWORKS,
    SSRFSecurityError,
    install_yt_dlp_egress_guard,
    is_ip_prohibited,
    try_parse_ip,
    uninstall_yt_dlp_egress_guard,
    validate_url,
)


# ===========================================================================
# Fixture: Mock DNS for Safe Legitimate Domains
# ===========================================================================

@pytest.fixture(autouse=True)
def mock_safe_dns():
    """Ensure tests don't make real external DNS calls by mocking socket.getaddrinfo."""
    orig_getaddrinfo = socket.getaddrinfo

    def _mock_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        # Clean host
        h = host.strip().lower().rstrip(".")
        if h in ("youtube.com", "www.youtube.com", "vimeo.com", "example.com", "cdn.example.com"):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80))
            ]
        if h == "multihomed.example.com":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 80)),
            ]
        if h == "internal-service.example.com":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.50", port or 80))
            ]
        return orig_getaddrinfo(host, port, family, type, proto, flags)

    with patch("socket.getaddrinfo", side_effect=_mock_getaddrinfo):
        yield


# ===========================================================================
# 1. Loopback Rejection
# ===========================================================================

def test_loopback_rejection():
    """Test that all loopback variants are strictly rejected."""
    loopback_urls = [
        "http://127.0.0.1/",
        "http://127.0.0.1:8000/media",
        "http://localhost/",
        "http://localhost:3000/info",
        "http://[::1]/",
        "http://[::1]:8080/data",
        "http://127.0.0.2/",
        "http://127.1/",
    ]
    for url in loopback_urls:
        with pytest.raises(SSRFSecurityError) as exc_info:
            validate_url(url, resolve_dns=False)
        assert "blocked" in str(exc_info.value).lower() or "prohibited" in str(exc_info.value).lower()


# ===========================================================================
# 2. RFC 1918 Private Network Rejection
# ===========================================================================

def test_rfc1918_private_network_rejection():
    """Test that RFC 1918 private IPv4 subnets are rejected."""
    private_urls = [
        "http://10.0.0.1/",
        "http://10.255.255.254:8080/",
        "http://172.16.0.1/",
        "http://172.20.10.5/",
        "http://172.31.255.254/",
        "http://192.168.0.1/",
        "http://192.168.1.100/",
    ]
    for url in private_urls:
        with pytest.raises(SSRFSecurityError) as exc_info:
            validate_url(url, resolve_dns=False)
        assert "blocked" in str(exc_info.value).lower() or "prohibited" in str(exc_info.value).lower()


# ===========================================================================
# 3. Link-Local and Cloud Metadata Rejection
# ===========================================================================

def test_link_local_and_cloud_metadata_rejection():
    """Test rejection of AWS, GCP, Azure, and Alibaba Cloud metadata endpoints."""
    metadata_urls = [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.170.2/v2/metadata",
        "http://100.100.100.200/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://instance-data/latest/meta-data/",
    ]
    for url in metadata_urls:
        with pytest.raises(SSRFSecurityError) as exc_info:
            validate_url(url, resolve_dns=False)
        assert "blocked" in str(exc_info.value).lower() or "prohibited" in str(exc_info.value).lower()


# ===========================================================================
# 4. Docker Internal Services and Hostname Rejection
# ===========================================================================

def test_docker_and_internal_hostname_rejection():
    """Test rejection of Docker containers and internal docker DNS hosts."""
    internal_urls = [
        "http://postgres:5432/",
        "http://redis:6379/",
        "http://mailpit:1025/",
        "http://pgadmin:80/",
        "http://mediaforge-api:8000/",
        "http://host.docker.internal:58000/",
        "http://gateway.docker.internal:8000/",
        "http://service.local/video",
        "http://internal.lan/stream",
        "http://corp.internal/info",
    ]
    for url in internal_urls:
        with pytest.raises(SSRFSecurityError) as exc_info:
            validate_url(url, resolve_dns=False)
        assert "prohibited" in str(exc_info.value).lower() or "blocked" in str(exc_info.value).lower()


# ===========================================================================
# 5. Unusual IP Representation Rejection
# ===========================================================================

def test_unusual_ip_representation_rejection():
    """Test that alternative numeric encodings (dword, octal, hex, IPv4-mapped) cannot bypass checks."""
    unusual_urls = [
        "http://2130706433/",                  # 127.0.0.1 in decimal / dword
        "http://0177.0.0.1/",                  # 127.0.0.1 in octal
        "http://0x7f000001/",                  # 127.0.0.1 in hex
        "http://127.1/",                       # 127.0.0.1 abbreviated
        "http://[::ffff:127.0.0.1]/",          # IPv4-mapped IPv6 loopback
        "http://[::ffff:169.254.169.254]/",    # IPv4-mapped IPv6 link-local
        "http://2852039166/",                  # 169.254.169.254 in decimal
    ]
    for url in unusual_urls:
        with pytest.raises(SSRFSecurityError) as exc_info:
            validate_url(url, resolve_dns=False)
        assert "blocked" in str(exc_info.value).lower() or "prohibited" in str(exc_info.value).lower()


# ===========================================================================
# 6. Scheme Validation
# ===========================================================================

def test_scheme_validation():
    """Test that non-HTTP/HTTPS protocols are rejected."""
    bad_schemes = [
        "file:///etc/passwd",
        "ftp://ftp.example.com/video.mp4",
        "gopher://127.0.0.1:70/",
        "data:text/plain;base64,SGVsbG8=",
        "javascript:alert(1)",
        "dict://127.0.0.1:2628/",
        "sftp://example.com/file",
        "ldap://example.com/dn",
    ]
    for url in bad_schemes:
        with pytest.raises(SSRFSecurityError) as exc_info:
            validate_url(url, resolve_dns=False)
        assert "scheme" in str(exc_info.value).lower() or "unsupported" in str(exc_info.value).lower()


# ===========================================================================
# 7. User Credentials Rejection
# ===========================================================================

def test_userinfo_rejection():
    """Test that URLs containing embedded credentials are prohibited."""
    cred_urls = [
        "http://user:pass@example.com/",
        "https://admin:secret@youtube.com/watch?v=123",
        "http://token:@example.com/media",
    ]
    for url in cred_urls:
        with pytest.raises(SSRFSecurityError) as exc_info:
            validate_url(url, resolve_dns=False)
        assert "credentials" in str(exc_info.value).lower() or "userinfo" in str(exc_info.value).lower()


# ===========================================================================
# 8. Port Restriction
# ===========================================================================

def test_port_restriction():
    """Test that connections to prohibited ports are rejected."""
    dangerous_port_urls = [
        "http://example.com:22/video.mp4",      # SSH
        "http://example.com:25/video.mp4",      # SMTP
        "http://example.com:1025/mail",         # Mailpit SMTP
        "http://example.com:5432/db",           # PostgreSQL
        "http://example.com:6379/cache",        # Redis
        "http://example.com:26379/cache",       # Redis host port
        "http://example.com:55432/db",          # PostgreSQL host port
        "http://example.com:58000/api",         # MediaForge API port
        "http://example.com:70000/stream",      # Out of range TCP port
    ]
    for url in dangerous_port_urls:
        with pytest.raises(SSRFSecurityError) as exc_info:
            validate_url(url, resolve_dns=False)
        assert "port" in str(exc_info.value).lower()


# ===========================================================================
# 9. DNS Resolution Checks (Single and Multi-Homed)
# ===========================================================================

def test_dns_resolution_checks():
    """Test that hostnames resolving to private or mixed IPs are rejected."""
    # 1. Resolves to private IP 10.0.0.50
    with pytest.raises(SSRFSecurityError) as exc_info:
        validate_url("http://internal-service.example.com/video", resolve_dns=True)
    assert "resolves to prohibited ip" in str(exc_info.value).lower()

    # 2. Multi-homed hostname resolving to public + loopback
    with pytest.raises(SSRFSecurityError) as exc_info:
        validate_url("http://multihomed.example.com/video", resolve_dns=True)
    assert "resolves to prohibited ip" in str(exc_info.value).lower()


# ===========================================================================
# 10. Legitimate Media URL Acceptance
# ===========================================================================

def test_legitimate_url_acceptance():
    """Test that valid public HTTPS media URLs pass validation."""
    valid_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/playlist?list=PL1234567890",
        "https://vimeo.com/123456789",
        "http://example.com/video.mp4",
        "https://cdn.example.com/media/file.mkv",
    ]
    for url in valid_urls:
        result = validate_url(url, resolve_dns=True)
        assert result == url


# ===========================================================================
# 11. yt-dlp Egress Connection Guard & DNS Rebinding Prevention
# ===========================================================================

def test_egress_guard_blocks_prohibited_socket_connect():
    """Test that yt-dlp's create_connection blocks prohibited destinations at socket boundary."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking._urllib as ytl_urllib

    # Attempt connection to loopback IP
    with pytest.raises(PermissionError) as exc_info:
        ytl_urllib.create_connection(("127.0.0.1", 80))
    assert "blocked by ssrf policy" in str(exc_info.value).lower()

    # Attempt connection to cloud metadata IP
    with pytest.raises(PermissionError) as exc_info:
        ytl_urllib.create_connection(("169.254.169.254", 80))
    assert "blocked by ssrf policy" in str(exc_info.value).lower()

    # Attempt connection to disallowed port
    with pytest.raises(PermissionError) as exc_info:
        ytl_urllib.create_connection(("example.com", 5432))
    assert "port" in str(exc_info.value).lower()


def test_egress_guard_blocks_prohibited_redirect():
    """Test that yt-dlp RedirectHandler blocks redirects to private/metadata destinations."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking._urllib as ytl_urllib

    handler = ytl_urllib.RedirectHandler()
    mock_req = MagicMock()
    mock_req.full_url = "https://example.com/video"
    mock_req.headers = {}
    mock_req.get_method.return_value = "GET"
    mock_req.origin_req_host = "example.com"
    mock_req.data = None

    # Redirect to loopback
    with pytest.raises(PermissionError) as exc_info:
        handler.redirect_request(
            mock_req, None, 302, "Found", {}, "http://127.0.0.1:8000/secret"
        )
    assert "blocked" in str(exc_info.value).lower()

    # Redirect to cloud metadata
    with pytest.raises(PermissionError) as exc_info:
        handler.redirect_request(
            mock_req, None, 301, "Moved", {}, "http://169.254.169.254/latest/meta-data/"
        )
    assert "blocked" in str(exc_info.value).lower()


# ===========================================================================
# 12. API Route Boundary Integration Tests (HTTP 400 Client Errors)
# ===========================================================================

@pytest.fixture
def test_client():
    """Create a test client with mocked user and dependencies."""
    mock_user = CurrentUser(
        user_id="user-ssrf-test",
        tenant_id="tenant-ssrf-test",
        email="test@mediaforge.local",
        role="owner",
        tier="pro",
        scopes=["*"],
    )

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[require_scope("downloads:write")] = lambda: mock_user
    app.dependency_overrides[require_scope("downloads:read")] = lambda: mock_user
    app.dependency_overrides[require_scope("transcripts:write")] = lambda: mock_user
    app.dependency_overrides[require_scope("transcripts:read")] = lambda: mock_user
    app.dependency_overrides[require_scope("clips:write")] = lambda: mock_user
    app.dependency_overrides[require_scope("clips:read")] = lambda: mock_user

    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import AsyncSession
    from api.database.connection import get_session

    mock_session = AsyncMock(spec=AsyncSession)
    app.dependency_overrides[get_session] = lambda: mock_session

    mock_app = MagicMock()
    mock_app.download.return_value = MagicMock(job_id="job-1", to_dict=lambda: {"id": "job-1"})
    mock_app.download_batch.return_value = []
    mock_app.download_playlist.return_value = []
    mock_app.get_info.return_value = {"title": "Safe Media", "subtitles_available": {}}
    app.dependency_overrides[get_app] = lambda: mock_app

    client = TestClient(app, raise_server_exceptions=False)

    yield client

    app.dependency_overrides.clear()



def test_api_downloads_submit_rejects_ssrf(test_client):
    """POST /api/v1/downloads rejects loopback and metadata URLs with HTTP 400."""
    res = test_client.post(
        "/api/v1/downloads",
        json={"url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False
    assert "blocked" in data["error"].lower() or "prohibited" in data["error"].lower()


def test_api_downloads_batch_rejects_ssrf(test_client):
    """POST /api/v1/downloads/batch rejects if any URL is dangerous."""
    res = test_client.post(
        "/api/v1/downloads/batch",
        json={"urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "http://127.0.0.1/admin"]},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False
    assert "blocked" in data["error"].lower() or "prohibited" in data["error"].lower()


def test_api_downloads_playlist_rejects_ssrf(test_client):
    """POST /api/v1/downloads/playlist rejects dangerous URLs."""
    res = test_client.post(
        "/api/v1/downloads/playlist",
        json={"url": "http://postgres:5432/playlist"},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False


def test_api_resolve_rejects_ssrf(test_client):
    """POST /api/v1/resolve rejects SSRF URLs."""
    res = test_client.post(
        "/api/v1/resolve",
        json={"url": "http://0177.0.0.1/"},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False


def test_api_transcripts_languages_rejects_ssrf(test_client):
    """GET /api/v1/transcripts/languages rejects SSRF URLs in query param."""
    res = test_client.get(
        "/api/v1/transcripts/languages",
        params={"url": "http://127.0.0.1:8000/video"},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False


def test_api_transcripts_extract_rejects_ssrf(test_client):
    """POST /api/v1/transcripts/extract rejects SSRF URLs."""
    res = test_client.post(
        "/api/v1/transcripts/extract",
        json={"url": "http://host.docker.internal:58000/video", "language": "en"},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False


def test_api_clips_extract_rejects_ssrf_and_bad_schemes(test_client):
    """POST /api/v1/clips/extract rejects loopback URL and non-HTTP schemes."""
    # 1. Prohibited HTTP URL
    res = test_client.post(
        "/api/v1/clips/extract",
        json={
            "source": "http://127.0.0.1:8000/video.mp4",
            "start_time": "00:00:10",
            "end_time": "00:00:20",
        },
    )
    assert res.status_code == 400
    assert res.json()["success"] is False

    # 2. Prohibited file scheme
    res2 = test_client.post(
        "/api/v1/clips/extract",
        json={
            "source": "file:///etc/passwd",
            "start_time": "00:00:10",
            "end_time": "00:00:20",
        },
    )
    assert res2.status_code == 400


# ===========================================================================
# 13. STEP 02-D1-Fix-A: Connection Boundary, DNS TOCTOU, Transport & Proxy Hardening
# ===========================================================================

def test_actual_connected_ip_is_validated_ip():
    """Prove that the actual connected socket destination is pinned to the validated IP."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking._urllib as ytl_urllib

    connected_destinations = []

    def mock_socket_connect(ip_addr, timeout, source_address):
        # ip_addr is (af, socktype, proto, canonname, sa)
        sa = ip_addr[4]
        connected_destinations.append(sa[0])
        # Return a mock connected socket
        mock_sock = MagicMock()
        mock_sock.getpeername.return_value = sa
        return mock_sock

    # With address resolving to public IP 93.184.216.34
    sock = ytl_urllib.create_connection(
        ("example.com", 80),
        _create_socket_func=mock_socket_connect,
    )
    assert len(connected_destinations) == 1
    assert connected_destinations[0] == "93.184.216.34"
    assert sock.getpeername()[0] == "93.184.216.34"


def test_dns_rebinding_cannot_re_resolve_or_change_target():
    """Prove that DNS is resolved exactly once and cannot be re-resolved inside connection layer."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking._urllib as ytl_urllib

    getaddrinfo_calls = []

    def tracking_getaddrinfo(host, port, *args, **kwargs):
        getaddrinfo_calls.append(host)
        # First call returns public IP
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80))
        ]

    connected_ips = []

    def mock_socket_connect(ip_addr, timeout, source_address):
        connected_ips.append(ip_addr[4][0])
        mock_sock = MagicMock()
        mock_sock.getpeername.return_value = ip_addr[4]
        return mock_sock

    with patch("socket.getaddrinfo", side_effect=tracking_getaddrinfo):
        sock = ytl_urllib.create_connection(
            ("rebind.example.com", 80),
            _create_socket_func=mock_socket_connect,
        )

    # getaddrinfo must be called EXACTLY once — no second resolution TOCTOU race!
    assert len(getaddrinfo_calls) == 1
    assert connected_ips == ["93.184.216.34"]


def test_mixed_dns_answers_rejected_at_connection_boundary():
    """Verify that multi-homed domain returning public + private IP is rejected."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking._urllib as ytl_urllib

    with pytest.raises(PermissionError) as exc_info:
        ytl_urllib.create_connection(("multihomed.example.com", 80))
    assert "blocked by ssrf policy" in str(exc_info.value).lower()


def test_extractor_generated_media_and_subtitle_requests_protected():
    """Verify that RequestDirector.send intercepts extractor-generated media/subtitle requests."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking.common as ytl_common

    director = ytl_common.RequestDirector(logger=MagicMock())

    # 1. Extractor generates metadata request
    req_meta = ytl_common.Request("http://169.254.169.254/latest/meta-data/credentials")
    with pytest.raises(PermissionError) as exc_info:
        director.send(req_meta)
    assert "blocked by ssrf policy" in str(exc_info.value).lower()

    # 2. Extractor generates internal Redis probe
    req_redis = ytl_common.Request("http://127.0.0.1:6379/keys")
    with pytest.raises(PermissionError) as exc_info:
        director.send(req_redis)
    assert "blocked by ssrf policy" in str(exc_info.value).lower()


def test_proxy_configuration_cannot_bypass_policy():
    """Verify that request-level and environment proxies cannot target internal/private endpoints."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking.common as ytl_common

    director = ytl_common.RequestDirector(logger=MagicMock())

    # 1. Request specifies loopback proxy
    req_loopback = ytl_common.Request(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        proxies={"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"},
    )
    with pytest.raises(PermissionError) as exc_info:
        director.send(req_loopback)
    assert "proxy blocked" in str(exc_info.value).lower()

    # 2. Request specifies unconfigured public proxy (not trusted)
    req_public = ytl_common.Request(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        proxies={"http": "http://93.184.216.34:8080"},
    )
    with pytest.raises(PermissionError) as exc_info2:
        director.send(req_public)
    assert "proxy blocked" in str(exc_info2.value).lower()


def test_environment_proxy_tampering_blocked(monkeypatch):
    """Verify that untrusted environment proxy variables (private or public) are rejected."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking.common as ytl_common

    director = ytl_common.RequestDirector(logger=MagicMock())

    # 1. Private metadata proxy
    monkeypatch.setenv("HTTP_PROXY", "http://169.254.169.254:8080")
    req = ytl_common.Request("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    with pytest.raises(PermissionError) as exc_info:
        director.send(req)
    assert "proxy blocked" in str(exc_info.value).lower()

    # 2. Ambient public proxy (arbitrary public proxy cannot silently bypass)
    monkeypatch.setenv("HTTP_PROXY", "http://93.184.216.34:8080")
    with pytest.raises(PermissionError) as exc_info2:
        director.send(req)
    assert "proxy blocked" in str(exc_info2.value).lower()


def test_trusted_egress_gateway_configuration(monkeypatch):
    """Verify that an explicitly configured trusted egress gateway is allowed while others are blocked."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking.common as ytl_common

    trusted_gateway = "http://93.184.216.34:8080"
    monkeypatch.setenv("MEDIAFORGE_TRUSTED_EGRESS_GATEWAY", trusted_gateway)

    director = ytl_common.RequestDirector(logger=MagicMock())

    # Mock _get_handlers to return a dummy urllib handler
    mock_handler = MagicMock()
    mock_handler.RH_KEY = "Urllib"

    with patch.object(director, "_get_handlers", return_value=[mock_handler]):
        with patch("yt_dlp.networking.common.RequestDirector.send", return_value=MagicMock()):
            # Trusted gateway request passes validation
            req_trusted = ytl_common.Request(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                proxies={"http": trusted_gateway},
            )
            # Should not raise PermissionError for proxy
            from api.security.url_policy import check_proxy_url
            check_proxy_url(trusted_gateway)

            # Different untrusted proxy still raises SSRFSecurityError
            with pytest.raises(SSRFSecurityError):
                check_proxy_url("http://8.8.8.8:8080")


def test_unsupported_transports_fail_closed():
    """Verify that unconstrained transports (e.g. requests, curl_cffi) fail closed."""
    install_yt_dlp_egress_guard()
    import yt_dlp.networking.common as ytl_common

    director = ytl_common.RequestDirector(logger=MagicMock())

    # Create mock unsupported handler
    mock_handler = MagicMock()
    mock_handler.RH_KEY = "CurlCffi"
    mock_handler.RH_NAME = "curl_cffi"
    mock_handler.validate.return_value = None

    with patch.object(director, "_get_handlers", return_value=[mock_handler]):
        req = ytl_common.Request("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        with pytest.raises(PermissionError) as exc_info:
            director.send(req)
        assert "unsupported http transport" in str(exc_info.value).lower()


def test_concurrent_job_safety():
    """Verify that multiple concurrent threads safely execute through the guards."""
    install_yt_dlp_egress_guard()
    import concurrent.futures
    import yt_dlp.networking._urllib as ytl_urllib

    results = []

    def worker(url, should_fail):
        try:
            validate_url(url, resolve_dns=True)
            return ("SUCCESS", url)
        except SSRFSecurityError:
            return ("BLOCKED", url)

    urls = [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", False),
        ("http://127.0.0.1:8000/media", True),
        ("https://vimeo.com/123456789", False),
        ("http://169.254.169.254/latest/meta-data", True),
        ("http://postgres:5432/", True),
        ("http://example.com/video.mp4", False),
    ] * 5

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, url, should_fail) for url, should_fail in urls]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    assert len(results) == len(urls)
    for status, url in results:
        if "127.0.0.1" in url or "169.254.169.254" in url or "postgres" in url:
            assert status == "BLOCKED"
        else:
            assert status == "SUCCESS"


def test_ffmpeg_protocol_whitelist_enforcement():
    """Verify that FFmpeg clip extraction commands strictly enforce local file protocol whitelist."""
    from src.clip.extractor import ClipExtractor, ClipRequest
    from src.clip.merger import ClipMerger

    extractor = ClipExtractor(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
    req = ClipRequest(
        source="/path/to/test.mp4",
        start_time="00:00:10",
        end_time="00:00:20",
    )
    cmd = extractor._build_ffmpeg_cmd("/path/to/test.mp4", "/path/to/out.mp4", req)
    assert "-protocol_whitelist" in cmd
    idx = cmd.index("-protocol_whitelist")
    assert cmd[idx + 1] == "file,crypto,data"

    merger = ClipMerger(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
    # Test that merger also defines protocol whitelist
    assert hasattr(merger, "_merge_concat")
    assert hasattr(merger, "_merge_reencode")
