"""GrabItDown usage tracker — tracks feature usage against tier limits."""

from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Dict

from src.exceptions import LimitExceededError
from src.features.feature_gate import FeatureGate

logger = logging.getLogger(__name__)


class UsageTracker:
    """Tracks per-user, per-day usage for limit enforcement.

    In-memory for now. Will be backed by database in the API layer.
    """

    def __init__(self, feature_gate: FeatureGate) -> None:
        self._gate = feature_gate
        # {user_id: {date_str: {counter_name: count}}}
        self._usage: Dict[str, Dict[str, Dict[str, int]]] = {}
        self._lock = threading.Lock()

    def increment(
        self,
        user_id: str,
        counter: str,
        amount: int = 1,
        tier: str | None = None,
    ) -> int:
        """Increment a usage counter and enforce limits."""
        today = date.today().isoformat()

        with self._lock:
            if user_id not in self._usage:
                self._usage[user_id] = {}
            if today not in self._usage[user_id]:
                self._usage[user_id][today] = {}

            current = self._usage[user_id][today].get(counter, 0)

            limit = self._get_limit(counter, tier)
            if limit != -1 and (current + amount) > limit:
                raise LimitExceededError(
                    feature_name=counter,
                    current_usage=current,
                    max_allowed=limit,
                )

            new_total = current + amount
            self._usage[user_id][today][counter] = new_total
            return new_total

    def get_usage(
        self,
        user_id: str,
        counter: str,
        day: str | None = None,
    ) -> int:
        """Get current usage count for a counter."""
        day = day or date.today().isoformat()
        with self._lock:
            return (
                self._usage.get(user_id, {})
                .get(day, {})
                .get(counter, 0)
            )

    def get_remaining(
        self,
        user_id: str,
        counter: str,
        tier: str | None = None,
        day: str | None = None,
    ) -> int:
        """Get remaining quota for a counter.

        Returns -1 for unlimited features.
        """
        limit = self._get_limit(counter, tier)
        if limit == -1:
            return -1
        used = self.get_usage(user_id, counter, day=day)
        remaining = max(limit - used, 0)
        return remaining

    def reset(self, user_id: str) -> None:
        """Reset all usage counters for a user."""
        with self._lock:
            if user_id in self._usage:
                del self._usage[user_id]

    def get_all_usage(
        self,
        user_id: str,
        day: str | None = None,
    ) -> Dict[str, int]:
        """Get all usage counters for a user for a given day."""
        day = day or date.today().isoformat()
        with self._lock:
            return dict(self._usage.get(user_id, {}).get(day, {}))

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _get_limit(self, counter: str, tier: str | None) -> int:
        """Resolve limit for a given logical counter name.

        Counter-to-feature mappings:
        - 'daily_downloads' → video_download.daily_limit
        Other counters default to unlimited (-1).
        """
        try:
            if counter == "daily_downloads":
                value = self._gate.get_limit(
                    "video_download",
                    "daily_limit",
                    tier=tier,
                )
                return int(value)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to resolve limit for %s: %s", counter, exc)
            return -1

        return -1

