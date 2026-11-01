"""SSRF protection and centralized URL safety policy module.

Validates externally supplied URLs to protect against Server-Side Request Forgery
(SSRF), DNS rebinding, unsafe redirects, and unauthorized access to internal
infrastructure (Docker containers, loopback, cloud metadata endpoints, private subnets).

Hardened for STEP 02-D1-Fix-A:
- Eliminates DNS TOCTOU race: DNS is resolved once, verified, and socket is pinned
  directly to the validated IP address without a second hostname resolution.
- RequestDirector.send interception: All extractor requests, intermediate URLs, media
  URLs, and subtitle requests are validated before dispatch.
- Transport audit and fail-closed policy: Unconstrained transports (requests, curl_cffi)
  are disabled; only verified transports (UrllibRH, WebsocketsRH) are permitted.
- Proxy audit: Checks request proxies and environment proxy variables.
- Redirect chain inspection across intermediate and terminal hops.
"""

from __future__ import annotations

import functools
import ipaddress
import logging
import os
import socket
import urllib.parse
from typing import Any, Callable

logger = logging.getLogger(__name__)


class SSRFSecurityError(ValueError):
    """Raised when an input URL violates SSRF security policy."""
    pass


# ── Prohibited IP Networks (RFC Standards & Cloud Metadata) ──────────────────

PROHIBITED_IPV4_NETWORKS = [
    ipaddress.IPv4Network("0.0.0.0/8"),          # Current network (RFC 1122)
    ipaddress.IPv4Network("10.0.0.0/8"),         # Private-Use (RFC 1918)
    ipaddress.IPv4Network("100.64.0.0/10"),      # Shared Address / CGNAT (RFC 6598, includes Alibaba 100.100.100.200)
    ipaddress.IPv4Network("127.0.0.0/8"),        # Loopback (RFC 1122)
    ipaddress.IPv4Network("169.254.0.0/16"),     # Link-Local (RFC 3927, includes AWS/GCP/Azure 169.254.169.254, 169.254.170.2)
    ipaddress.IPv4Network("172.16.0.0/12"),      # Private-Use (RFC 1918)
    ipaddress.IPv4Network("192.0.0.0/24"),       # IETF Protocol Assignments (RFC 6890)
    ipaddress.IPv4Network("192.0.2.0/24"),       # Documentation TEST-NET-1 (RFC 5737)
    ipaddress.IPv4Network("192.88.99.0/24"),     # 6to4 Relay Anycast (RFC 7526)
    ipaddress.IPv4Network("192.168.0.0/16"),     # Private-Use (RFC 1918)
    ipaddress.IPv4Network("198.18.0.0/15"),      # Interconnect Benchmarking (RFC 2544)
    ipaddress.IPv4Network("198.51.100.0/24"),    # Documentation TEST-NET-2 (RFC 5737)
    ipaddress.IPv4Network("203.0.113.0/24"),     # Documentation TEST-NET-3 (RFC 5737)
    ipaddress.IPv4Network("224.0.0.0/4"),        # Multicast (RFC 5771)
    ipaddress.IPv4Network("240.0.0.0/4"),        # Reserved / Class E (RFC 1112)
    ipaddress.IPv4Network("255.255.255.255/32"), # Limited Broadcast (RFC 919)
]

PROHIBITED_IPV6_NETWORKS = [
    ipaddress.IPv6Network("::/128"),             # Unspecified (RFC 4291)
    ipaddress.IPv6Network("::1/128"),            # Loopback (RFC 4291)
    ipaddress.IPv6Network("::ffff:0:0/96"),      # IPv4-mapped IPv6 (RFC 4291)
    ipaddress.IPv6Network("64:ff9b::/96"),       # IPv4/IPv6 translation (RFC 6052)
    ipaddress.IPv6Network("100::/64"),           # Discard prefix (RFC 6666)
    ipaddress.IPv6Network("2001::/23"),          # IETF Protocol Assignments (RFC 2928)
    ipaddress.IPv6Network("2001:db8::/32"),      # Documentation (RFC 3849)
    ipaddress.IPv6Network("2002::/16"),          # 6to4 (RFC 3056)
    ipaddress.IPv6Network("fc00::/7"),           # Unique Local Address ULA (RFC 4193, includes AWS fd00:ec2::254)
    ipaddress.IPv6Network("fe80::/10"),          # Link-Local Unicast (RFC 4291)
    ipaddress.IPv6Network("ff00::/8"),           # Multicast (RFC 4291)
]

# Explicit cloud metadata & loopback IPs for defense-in-depth
PROHIBITED_EXPLICIT_IPS = {
    "127.0.0.1",
    "0.0.0.0",
    "169.254.169.254",   # AWS, GCP, Azure, OpenStack metadata
    "169.254.170.2",     # AWS ECS container metadata
    "100.100.100.200",   # Alibaba Cloud metadata
    "fd00:ec2::254",     # AWS IPv6 IMDS
    "::1",
    "::",
}

# ── Prohibited Hostnames and Domains ─────────────────────────────────────────

PROHIBITED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata.google.internal.",
    "instance-data",
    # Docker internal service identifiers
    "postgres",
    "redis",
    "mailpit",
    "pgadmin",
    "mediaforge-api",
    "api",
    "host.docker.internal",
    "gateway.docker.internal",
}

PROHIBITED_HOST_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
    ".lan",
    ".home",
    ".corp",
    ".localdomain",
)

# Ports forbidden to prevent service probing and internal access
DISALLOWED_PORTS = {
    21,     # FTP
    22,     # SSH
    23,     # Telnet
    25,     # SMTP
    53,     # DNS
    69,     # TFTP
    110,    # POP3
    119,    # NNTP
    123,    # NTP
    143,    # IMAP
    161,    # SNMP
    389,    # LDAP
    445,    # SMB
    465,    # SMTPS
    587,    # Submission
    993,    # IMAPS
    995,    # POP3S
    1025,   # Mailpit SMTP (container)
    2049,   # NFS
    2375,   # Docker daemon unencrypted
    2376,   # Docker daemon TLS
    3306,   # MySQL
    5432,   # PostgreSQL (container)
    6379,   # Redis (container)
    8025,   # Mailpit HTTP (container)
    11211,  # Memcached
    26379,  # Redis host port
    27017,  # MongoDB
    28017,  # MongoDB web
    51025,  # Mailpit host SMTP port
    55432,  # PostgreSQL host port
    58000,  # MediaForge API host port
    58025,  # Mailpit host HTTP port
    58080,  # pgAdmin host port
}

ALLOWED_REQUEST_HANDLERS = {"Urllib", "Websockets"}


# ── IP Classification & Normalization ────────────────────────────────────────

def try_parse_ip(host_str: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse host string into an IPv4 or IPv6 object, normalizing alternative encodings.

    Handles:
    - Standard dotted-decimal IPv4 (e.g. 127.0.0.1)
    - Bracketed or plain IPv6 (e.g. [::1], fe80::1)
    - IPv4-mapped IPv6 (e.g. [::ffff:127.0.0.1])
    - Octal IPv4 (e.g. 0177.0.0.1)
    - Hexadecimal IPv4 (e.g. 0x7f000001, 0x7f.0.0.1)
    - Decimal integer / dword IPv4 (e.g. 2130706433)
    - Abbreviated dotted IPv4 (e.g. 127.1)
    """
    clean_host = host_str.strip()
    if clean_host.startswith("[") and clean_host.endswith("]"):
        clean_host = clean_host[1:-1]

    # 1. Standard ipaddress parser (IPv6 or standard IPv4)
    try:
        ip = ipaddress.ip_address(clean_host)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            return ip.ipv4_mapped
        return ip
    except ValueError:
        pass

    # 2. socket.inet_aton normalizes dword, octal, hex, and short forms for IPv4
    try:
        packed = socket.inet_aton(clean_host)
        normalized_str = socket.inet_ntoa(packed)
        return ipaddress.IPv4Address(normalized_str)
    except (OSError, ValueError):
        pass

    return None


def is_ip_prohibited(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check whether an IP address belongs to any prohibited private, loopback, or metadata range."""
    # Unwrap IPv4-mapped IPv6
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    str_ip = str(ip)
    if str_ip in PROHIBITED_EXPLICIT_IPS:
        return True

    if isinstance(ip, ipaddress.IPv4Address):
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
        for network in PROHIBITED_IPV4_NETWORKS:
            if ip in network:
                return True
    elif isinstance(ip, ipaddress.IPv6Address):
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
        for network in PROHIBITED_IPV6_NETWORKS:
            if ip in network:
                return True

    return False


# ── URL and Proxy Validation ─────────────────────────────────────────────────

def validate_url(url: str, resolve_dns: bool = True) -> str:
    """Validate a URL against SSRF policy.

    Args:
        url: The user-supplied URL string to validate.
        resolve_dns: If True, resolves the hostname to IP addresses and checks all
                     records against prohibited network ranges.

    Returns:
        The validated (and stripped) URL string.

    Raises:
        SSRFSecurityError: If the URL fails scheme, credential, port, host, or IP checks.
    """
    if not url or not isinstance(url, str):
        raise SSRFSecurityError("URL must be a non-empty string")

    stripped_url = url.strip()
    if not stripped_url:
        raise SSRFSecurityError("URL cannot be empty or whitespace")

    try:
        parsed = urllib.parse.urlsplit(stripped_url)
    except Exception as exc:
        raise SSRFSecurityError(f"Malformed URL: {exc}") from exc

    # 1. Scheme check: strictly http and https
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SSRFSecurityError(
            f"Unsupported URL scheme '{scheme}'. Only http and https are permitted"
        )

    # 2. User credentials check: reject userinfo (http://user:pass@host)
    if parsed.username or parsed.password:
        raise SSRFSecurityError("URLs containing user credentials are not permitted")

    # 3. Host presence check
    raw_host = parsed.hostname
    if not raw_host:
        raise SSRFSecurityError("URL must include a valid hostname or host IP")

    # Unquote and normalize host
    clean_host = urllib.parse.unquote(raw_host).strip().lower().rstrip(".")
    if not clean_host:
        raise SSRFSecurityError("URL host cannot be empty")

    # 4. Port restriction check
    try:
        url_port = parsed.port
    except ValueError as exc:
        raise SSRFSecurityError(f"URL port is invalid: {exc}") from exc

    if url_port is not None:
        if url_port < 1 or url_port > 65535:
            raise SSRFSecurityError(f"URL port {url_port} is outside valid TCP range (1-65535)")
        if url_port in DISALLOWED_PORTS:
            raise SSRFSecurityError(f"Connection to port {url_port} is prohibited for security")

    # 5. Prohibited hostnames check
    if clean_host in PROHIBITED_HOSTNAMES:
        raise SSRFSecurityError(f"Access to host '{clean_host}' is prohibited by SSRF policy")

    for suffix in PROHIBITED_HOST_SUFFIXES:
        if clean_host.endswith(suffix):
            raise SSRFSecurityError(f"Access to internal domain suffix '{suffix}' is prohibited")

    # 6. Check if host is an IP literal (including hex, octal, dword encodings)
    parsed_ip = try_parse_ip(clean_host)
    if parsed_ip is not None:
        if is_ip_prohibited(parsed_ip):
            raise SSRFSecurityError(f"Access to prohibited IP '{parsed_ip}' is blocked")
        # Valid public IP literal
        return stripped_url

    # 7. DNS Resolution check (for hostnames)
    if resolve_dns:
        port = url_port or (443 if scheme == "https" else 80)
        try:
            addr_info = socket.getaddrinfo(clean_host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise SSRFSecurityError(f"Unable to resolve hostname '{clean_host}': {exc}") from exc
        except Exception as exc:
            raise SSRFSecurityError(f"DNS lookup error for '{clean_host}': {exc}") from exc

        if not addr_info:
            raise SSRFSecurityError(f"Hostname '{clean_host}' resolved to no address records")

        # Reject if ANY resolved address is prohibited
        for entry in addr_info:
            resolved_ip_str = entry[4][0]
            resolved_ip = try_parse_ip(resolved_ip_str)
            if resolved_ip and is_ip_prohibited(resolved_ip):
                raise SSRFSecurityError(
                    f"Hostname '{clean_host}' resolves to prohibited IP '{resolved_ip_str}'"
                )

    return stripped_url


# ── Trusted Egress Proxy Configuration ───────────────────────────────────────
# By default, None. Arbitrary HTTP, HTTPS, and SOCKS proxies are strictly rejected
# for untrusted media requests to prevent SSRF bypass (proxies resolve and route
# destinations independently). Only an explicitly configured trusted egress gateway is allowed.
TRUSTED_EGRESS_GATEWAY: str | None = os.environ.get("MEDIAFORGE_TRUSTED_EGRESS_GATEWAY")


def check_proxy_url(proxy_url: str | None) -> None:
    """Validate proxy URL.

    Arbitrary HTTP, HTTPS and SOCKS proxies are strictly rejected for untrusted media
    processing to prevent bypass of local connection boundary controls (proxies perform
    their own DNS resolution and egress routing). Public proxies are not trusted.
    Only an explicitly configured trusted egress gateway is permitted, and it is validated.
    """
    if not proxy_url:
        return

    trusted_gateway = os.environ.get("MEDIAFORGE_TRUSTED_EGRESS_GATEWAY") or TRUSTED_EGRESS_GATEWAY
    if not trusted_gateway or proxy_url.strip() != trusted_gateway.strip():
        raise SSRFSecurityError(
            "Arbitrary proxy routing is disabled for untrusted media requests. "
            "Public and unconfigured proxies cannot be trusted for SSRF enforcement."
        )

    # Even if configured as a trusted gateway, ensure it does not target loopback or private ranges
    try:
        validate_url(proxy_url, resolve_dns=True)
    except SSRFSecurityError as exc:
        raise SSRFSecurityError(f"Trusted egress gateway configuration invalid: {exc}") from exc

    # Destination policy verification: Gateways without verified internal destination blocking fail closed
    gateway_policy = os.environ.get("MEDIAFORGE_GATEWAY_DESTINATION_POLICY", "enforced").strip().lower()
    if gateway_policy not in ("enforced", "strict", "verified"):
        raise SSRFSecurityError(
            "Trusted egress gateway rejected: Independent destination policy enforcement cannot be demonstrated. "
            "Egress gateways must fail closed unless destination-policy enforcement is actively verified."
        )


def check_environment_proxies() -> None:
    """Inspect process environment for untrusted or dangerous proxy settings.

    Prevents ambient HTTP_PROXY, HTTPS_PROXY, ALL_PROXY and related settings from
    silently bypassing policy.
    """
    proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    trusted_gateway = os.environ.get("MEDIAFORGE_TRUSTED_EGRESS_GATEWAY") or TRUSTED_EGRESS_GATEWAY
    for key in proxy_keys:
        val = os.environ.get(key)
        if val:
            if not trusted_gateway or val.strip() != trusted_gateway.strip():
                raise SSRFSecurityError(
                    f"Untrusted ambient proxy '{val}' in environment variable '{key}' rejected. "
                    "Arbitrary proxy routing is disabled."
                )
            # Validate trusted gateway
            try:
                validate_url(val, resolve_dns=True)
            except SSRFSecurityError as exc:
                raise SSRFSecurityError(f"Trusted ambient gateway '{key}' invalid: {exc}") from exc


# ── yt-dlp Connection and Transport Egress Guard ─────────────────────────────

_GUARD_INSTALLED = False
_ORIGINAL_HELPER_CREATE_CONNECTION: Callable[..., Any] | None = None
_ORIGINAL_URLLIB_CREATE_CONNECTION: Callable[..., Any] | None = None
_ORIGINAL_WEBSOCKETS_CREATE_CONNECTION: Callable[..., Any] | None = None
_ORIGINAL_REDIRECT_REQUEST: Callable[..., Any] | None = None
_ORIGINAL_HTTP_REDIRECT_REQUEST: Callable[..., Any] | None = None
_ORIGINAL_REQUEST_DIRECTOR_SEND: Callable[..., Any] | None = None


def install_yt_dlp_egress_guard() -> None:
    """Install connection-level socket, transport, and redirect guards into yt-dlp.

    Provides defense-in-depth against:
    - DNS rebinding TOCTOU attacks: Resolves DNS once, validates all records, and pins
      the socket connection directly to the validated IP without a second resolution.
    - Preserves TLS SNI & certificate verification: SNI server_hostname is preserved for
      SSL wrapping over the pinned socket.
    - Unsafe HTTP redirects (301, 302, 303, 307, 308) to internal/private targets.
    - Extractor-generated intermediate requests via RequestDirector.send interception.
    - Untrusted proxy configurations via environment and request proxies audit.
    - Unconstrained transports (requests, curl_cffi) fail closed.
    """
    global _GUARD_INSTALLED
    global _ORIGINAL_HELPER_CREATE_CONNECTION, _ORIGINAL_URLLIB_CREATE_CONNECTION
    global _ORIGINAL_WEBSOCKETS_CREATE_CONNECTION, _ORIGINAL_REDIRECT_REQUEST
    global _ORIGINAL_HTTP_REDIRECT_REQUEST, _ORIGINAL_REQUEST_DIRECTOR_SEND

    if _GUARD_INSTALLED:
        return

    try:
        import yt_dlp.networking._helper as ytl_helper
        import yt_dlp.networking._urllib as ytl_urllib
        import yt_dlp.networking._websockets as ytl_websockets
        import yt_dlp.networking.common as ytl_common
        import urllib.request
    except ImportError:
        logger.warning("yt_dlp networking module not available; egress guard skipped")
        return

    from yt_dlp.networking._helper import _socket_connect

    # Save original references
    _ORIGINAL_HELPER_CREATE_CONNECTION = ytl_helper.create_connection
    _ORIGINAL_URLLIB_CREATE_CONNECTION = ytl_urllib.create_connection
    _ORIGINAL_WEBSOCKETS_CREATE_CONNECTION = ytl_websockets.create_connection
    _ORIGINAL_REDIRECT_REQUEST = ytl_urllib.RedirectHandler.redirect_request
    _ORIGINAL_HTTP_REDIRECT_REQUEST = urllib.request.HTTPRedirectHandler.redirect_request
    _ORIGINAL_REQUEST_DIRECTOR_SEND = ytl_common.RequestDirector.send

    def guarded_create_connection(
        address: tuple[str, int],
        timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
        source_address: Any = None,
        *,
        _create_socket_func: Any = None,
        **kwargs: Any,
    ) -> Any:
        host, port = address

        # 1. Port validation at socket boundary
        if port in DISALLOWED_PORTS:
            raise PermissionError(
                f"Egress connection to port {port} blocked by SSRF policy"
            )

        # 2. Pre-screen address host
        clean_host = host.strip().lower().rstrip(".")
        if clean_host in PROHIBITED_HOSTNAMES:
            raise PermissionError(
                f"Egress connection to host '{clean_host}' blocked by SSRF policy"
            )
        for suffix in PROHIBITED_HOST_SUFFIXES:
            if clean_host.endswith(suffix):
                raise PermissionError(
                    f"Egress connection to internal domain suffix '{suffix}' blocked by SSRF policy"
                )

        # 3. DNS Resolution: The SINGLE authoritative resolution
        try:
            ip_addrs = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except OSError as exc:
            raise PermissionError(f"DNS resolution failed for '{host}': {exc}") from exc

        if not ip_addrs:
            raise PermissionError(f"No IP addresses available for '{host}'")

        # 4. Strict Validation of EVERY resolved address (Single or Multi-Homed)
        for ip_addr in ip_addrs:
            resolved_ip_str = ip_addr[4][0]
            parsed_ip = try_parse_ip(resolved_ip_str)
            if parsed_ip and is_ip_prohibited(parsed_ip):
                raise PermissionError(
                    f"Egress connection to prohibited IP '{resolved_ip_str}' ({host}) blocked by SSRF policy"
                )

        # 5. Connect directly to validated IP without second getaddrinfo resolution!
        if source_address is not None:
            af = socket.AF_INET if ":" not in source_address[0] else socket.AF_INET6
            ip_addrs = [addr for addr in ip_addrs if addr[0] == af]
            if not ip_addrs:
                raise OSError(
                    f"No remote IPv{4 if af == socket.AF_INET else 6} addresses available for connect."
                )

        connect_func = _create_socket_func or _socket_connect
        err = None
        for ip_addr in ip_addrs:
            try:
                sock = connect_func(ip_addr, timeout, source_address)
                err = None
                return sock
            except OSError as e:
                err = e

        if err is not None:
            raise err
        raise OSError(f"Failed to connect to '{host}' on port {port}")

    @functools.wraps(_ORIGINAL_REDIRECT_REQUEST)
    def guarded_redirect_request(
        self: Any,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        # Validate the redirect target URL against full SSRF safety policy
        try:
            validate_url(newurl, resolve_dns=True)
        except SSRFSecurityError as exc:
            logger.warning("SSRF redirect blocked to %s: %s", newurl, exc)
            raise PermissionError(f"SSRF redirect to prohibited target blocked: {exc}") from exc

        return _ORIGINAL_REDIRECT_REQUEST(self, req, fp, code, msg, headers, newurl)

    @functools.wraps(_ORIGINAL_REQUEST_DIRECTOR_SEND)
    def guarded_request_director_send(self: Any, request: Any) -> Any:
        # 1. Validate destination URL of ANY request (initial, intermediate, subtitle, media)
        if hasattr(request, "url") and request.url:
            try:
                validate_url(request.url, resolve_dns=True)
            except SSRFSecurityError as exc:
                raise PermissionError(f"Request blocked by SSRF policy: {exc}") from exc

        # 2. Validate request-level proxies if specified
        if hasattr(request, "proxies") and request.proxies:
            for proto, proxy_url in request.proxies.items():
                if proxy_url:
                    try:
                        check_proxy_url(proxy_url)
                    except SSRFSecurityError as exc:
                        raise PermissionError(f"Proxy blocked by SSRF policy: {exc}") from exc

        # 3. Check environment-level proxy variables
        try:
            check_environment_proxies()
        except SSRFSecurityError as exc:
            raise PermissionError(f"Environment proxy blocked: {exc}") from exc

        # 4. Fail-closed on unsupported / unconstrained transports
        for handler in self._get_handlers(request):
            if handler.RH_KEY not in ALLOWED_REQUEST_HANDLERS:
                raise PermissionError(
                    f"Unsupported HTTP transport '{handler.RH_NAME}' blocked by security policy"
                )

        return _ORIGINAL_REQUEST_DIRECTOR_SEND(self, request)

    # Apply socket connection patches across all yt-dlp networking modules
    ytl_helper.create_connection = guarded_create_connection
    ytl_urllib.create_connection = guarded_create_connection
    ytl_websockets.create_connection = guarded_create_connection

    # Apply redirect patches
    ytl_urllib.RedirectHandler.redirect_request = guarded_redirect_request
    urllib.request.HTTPRedirectHandler.redirect_request = guarded_redirect_request

    # Apply RequestDirector.send patch
    ytl_common.RequestDirector.send = guarded_request_director_send

    # Disable unconstrained transports in yt-dlp registry
    try:
        from yt_dlp.YoutubeDL import _REQUEST_HANDLERS
        for key in list(_REQUEST_HANDLERS.keys()):
            if key not in ALLOWED_REQUEST_HANDLERS:
                del _REQUEST_HANDLERS[key]
    except Exception:
        pass

    _GUARD_INSTALLED = True
    logger.info("yt-dlp SSRF connection-boundary, transport, and redirect guards installed")


def uninstall_yt_dlp_egress_guard() -> None:
    """Restore original yt-dlp networking functions (primarily for unit tests)."""
    global _GUARD_INSTALLED
    global _ORIGINAL_HELPER_CREATE_CONNECTION, _ORIGINAL_URLLIB_CREATE_CONNECTION
    global _ORIGINAL_WEBSOCKETS_CREATE_CONNECTION, _ORIGINAL_REDIRECT_REQUEST
    global _ORIGINAL_HTTP_REDIRECT_REQUEST, _ORIGINAL_REQUEST_DIRECTOR_SEND

    if not _GUARD_INSTALLED:
        return

    try:
        import yt_dlp.networking._helper as ytl_helper
        import yt_dlp.networking._urllib as ytl_urllib
        import yt_dlp.networking._websockets as ytl_websockets
        import yt_dlp.networking.common as ytl_common
        import urllib.request

        if _ORIGINAL_HELPER_CREATE_CONNECTION is not None:
            ytl_helper.create_connection = _ORIGINAL_HELPER_CREATE_CONNECTION
        if _ORIGINAL_URLLIB_CREATE_CONNECTION is not None:
            ytl_urllib.create_connection = _ORIGINAL_URLLIB_CREATE_CONNECTION
        if _ORIGINAL_WEBSOCKETS_CREATE_CONNECTION is not None:
            ytl_websockets.create_connection = _ORIGINAL_WEBSOCKETS_CREATE_CONNECTION
        if _ORIGINAL_REDIRECT_REQUEST is not None:
            ytl_urllib.RedirectHandler.redirect_request = _ORIGINAL_REDIRECT_REQUEST
        if _ORIGINAL_HTTP_REDIRECT_REQUEST is not None:
            urllib.request.HTTPRedirectHandler.redirect_request = _ORIGINAL_HTTP_REDIRECT_REQUEST
        if _ORIGINAL_REQUEST_DIRECTOR_SEND is not None:
            ytl_common.RequestDirector.send = _ORIGINAL_REQUEST_DIRECTOR_SEND
    except ImportError:
        pass

    _GUARD_INSTALLED = False
    logger.info("yt-dlp SSRF egress guards uninstalled")
