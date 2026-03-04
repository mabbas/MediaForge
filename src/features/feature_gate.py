"""Feature gate utilities built on top of feature flags."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from src.exceptions import ConfigurationError
from src.features.feature_flags import FeatureFlagsConfig, TierFeatures


@dataclass
class FeatureAccess:
    """Result of a feature access check."""

    allowed: bool
    reason: str | None = None
    limit: Any = None


class FeatureGate:
    """Feature gate resolving tiered feature availability and limits."""

    def __init__(self, flags: FeatureFlagsConfig) -> None:
        """Initialize the gate with a FeatureFlagsConfig instance."""
        self._flags = flags

    def _resolve_tier(self, tier: str | None) -> str:
        """Resolve which tier to use based on mode and requested tier.

        If a tier is explicitly provided, it is always respected.
        When no tier is provided:
        - In personal mode, the personal_mode_tier is used.
        - In tiered mode, a ConfigurationError is raised.
        """
        if tier:
            return tier

        if self._flags.mode == "personal":
            return self._flags.personal_mode_tier
        if self._flags.mode == "tiered":
            raise ConfigurationError("Tier must be provided when feature flags mode is 'tiered'.")
        raise ConfigurationError(f"Unknown feature flags mode: {self._flags.mode}")

    def _get_tier_features_internal(self, tier_name: str) -> TierFeatures:
        """Return the TierFeatures for the given tier name or raise ConfigurationError."""
        try:
            tier_cfg = self._flags.tiers[tier_name]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown tier '{tier_name}'.") from exc
        return tier_cfg.features

    def _get_feature_model(self, feature_name: str, tier: str | None) -> Any:
        """Return the feature model for the given name and tier."""
        tier_name = self._resolve_tier(tier)
        features = self._get_tier_features_internal(tier_name)
        try:
            feature_model = getattr(features, feature_name)
        except AttributeError as exc:
            raise ConfigurationError(f"Unknown feature '{feature_name}'.") from exc
        return feature_model

    def is_enabled(self, feature_name: str, tier: str | None = None) -> bool:
        """Return whether a feature is enabled for a tier (or current mode)."""
        feature_model = self._get_feature_model(feature_name, tier)
        enabled = getattr(feature_model, "enabled", None)
        if enabled is None:
            raise ConfigurationError(f"Feature '{feature_name}' does not define an 'enabled' flag.")
        return bool(enabled)

    def get_limit(self, feature_name: str, limit_key: str, tier: str | None = None) -> Any:
        """Return a specific limit value from a feature configuration."""
        feature_model = self._get_feature_model(feature_name, tier)
        try:
            return getattr(feature_model, limit_key)
        except AttributeError as exc:
            raise ConfigurationError(
                f"Limit key '{limit_key}' is not defined for feature '{feature_name}'."
            ) from exc

    def get_tier_features(self, tier_name: str) -> TierFeatures:
        """Return the full feature configuration for the given tier."""
        return self._get_tier_features_internal(tier_name)

    def check_access(self, feature_name: str, tier: str | None = None) -> FeatureAccess:
        """Check if a feature is accessible for the given tier."""
        if self.is_enabled(feature_name, tier=tier):
            return FeatureAccess(allowed=True, reason=None, limit=None)

        # If explicitly tiered, mention Pro or higher as default guidance.
        reason = f"{feature_name} requires Pro or higher"
        return FeatureAccess(allowed=False, reason=reason, limit=None)

    def list_all_features(self, tier: str | None = None) -> Dict[str, bool]:
        """List all features and their enabled state for a given tier."""
        tier_name = self._resolve_tier(tier)
        features = self._get_tier_features_internal(tier_name)

        result: Dict[str, bool] = {}
        for name, value in features.__dict__.items():
            if hasattr(value, "enabled"):
                result[name] = bool(getattr(value, "enabled"))
        return result

