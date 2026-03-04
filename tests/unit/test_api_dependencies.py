"""Tests for API dependencies."""

from __future__ import annotations

from api.dependencies import CurrentUser


def test_current_user_defaults():
    u = CurrentUser()
    assert u.user_id == "system"
    assert u.tenant_id == "default"
    assert u.tier == "platinum"
    assert u.role == "owner"


def test_current_user_custom():
    u = CurrentUser(
        user_id="alice",
        tenant_id="acme",
        email="alice@acme.com",
        role="admin",
        tier="pro",
    )
    assert u.user_id == "alice"
    assert u.tier == "pro"
