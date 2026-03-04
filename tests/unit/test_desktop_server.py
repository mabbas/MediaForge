"""Tests for embedded server."""

from __future__ import annotations

from desktop.server import EmbeddedServer


def test_embedded_server_init() -> None:
    server = EmbeddedServer(host="127.0.0.1", port=9999)
    assert server.host == "127.0.0.1"
    assert server.port == 9999
    assert server.base_url == "http://127.0.0.1:9999"
    assert server.api_url == "http://127.0.0.1:9999/api/v1"
    assert server.is_running is False


def test_embedded_server_urls() -> None:
    server = EmbeddedServer(port=8765)
    assert "8765" in server.base_url
    assert "/api/v1" in server.api_url

