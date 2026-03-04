"""Tests for feature flags and feature gate."""

import pytest

from src.exceptions import ConfigurationError
from src.features.feature_flags import FeatureFlagsConfig, load_feature_flags
from src.features.feature_gate import FeatureGate


def test_load_feature_flags() -> None:
    """Verify feature_flags.yaml loads successfully."""
    flags = load_feature_flags()
    assert flags.mode == "personal"
    assert flags.personal_mode_tier == "platinum"
    assert "basic" in flags.tiers
    assert "pro" in flags.tiers
    assert "platinum" in flags.tiers


def test_personal_mode_uses_platinum() -> None:
    """In personal mode, all features use platinum tier."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    assert gate.is_enabled("video_download") is True
    assert gate.is_enabled("multi_connection") is True
    assert gate.is_enabled("transcript_whisper") is True


def test_basic_tier_limits() -> None:
    """Verify basic tier has restrictive limits."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    features = gate.get_tier_features("basic")
    assert features.video_download.max_quality == "720p"
    assert features.video_download.daily_limit == 5
    assert features.concurrent_downloads.max_value == 1
    assert features.playlist_download.enabled is False


def test_pro_tier_limits() -> None:
    """Verify pro tier has mid-range limits."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    features = gate.get_tier_features("pro")
    assert features.video_download.max_quality == "1080p"
    assert features.concurrent_downloads.max_value == 3
    assert features.playlist_download.enabled is True
    assert features.multi_connection.enabled is False


def test_platinum_tier_limits() -> None:
    """Verify platinum tier has highest limits."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    features = gate.get_tier_features("platinum")
    assert features.video_download.max_quality == "2160p"
    assert features.video_download.daily_limit == -1
    assert features.concurrent_downloads.max_value == 5
    assert features.multi_connection.max_connections == 8


def test_feature_gate_is_enabled_personal_mode() -> None:
    """is_enabled returns True for all features in personal mode."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    assert gate.is_enabled("playlist_download") is True
    assert gate.is_enabled("batch_download") is True
    assert gate.is_enabled("api_access") is True


def test_feature_gate_is_enabled_basic_restricted() -> None:
    """Basic tier should have some features disabled."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    assert gate.is_enabled("playlist_download", tier="basic") is False
    assert gate.is_enabled("batch_download", tier="basic") is False
    assert gate.is_enabled("multi_connection", tier="basic") is False


def test_feature_gate_get_limit() -> None:
    """get_limit returns correct tier-specific values."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    assert gate.get_limit("video_download", "max_quality") == "2160p"
    assert gate.get_limit("video_download", "max_quality", tier="basic") == "720p"


def test_feature_gate_check_access_allowed() -> None:
    """check_access returns allowed for enabled features."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    access = gate.check_access("video_download")
    assert access.allowed is True
    assert access.reason is None


def test_feature_gate_check_access_denied() -> None:
    """check_access returns denied with reason for disabled features."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    access = gate.check_access("playlist_download", tier="basic")
    assert access.allowed is False
    assert "Pro" in access.reason or "higher" in access.reason


def test_feature_gate_unknown_feature() -> None:
    """Unknown feature name raises ConfigurationError."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    with pytest.raises(ConfigurationError):
        gate.is_enabled("nonexistent_feature")


def test_feature_gate_unknown_tier() -> None:
    """Unknown tier name raises ConfigurationError."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    with pytest.raises(ConfigurationError):
        gate.get_tier_features("enterprise")


def test_feature_gate_list_all_features() -> None:
    """list_all_features returns complete feature map."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    features = gate.list_all_features()
    assert "video_download" in features
    assert "audio_download" in features
    assert isinstance(features["video_download"], bool)

