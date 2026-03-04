from src.features.usage_tracker import UsageTracker
from src.features.feature_flags import load_feature_flags
from src.features.feature_gate import FeatureGate
from src.exceptions import LimitExceededError


def main() -> None:
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    tracker = UsageTracker(gate)

    # Basic tier: should hit limit at 5
    for _ in range(5):
        tracker.increment("alice", "daily_downloads", tier="basic")

    print(f'Usage: {tracker.get_usage("alice", "daily_downloads")}')
    print(f'Remaining: {tracker.get_remaining("alice", "daily_downloads", tier="basic")}')

    try:
        tracker.increment("alice", "daily_downloads", tier="basic")
        print("ERROR: Should have raised")
    except LimitExceededError as e:
        print(f"Limit hit: {e}")

    # Platinum is unlimited
    tracker.increment("bob", "daily_downloads", 100, tier="platinum")
    print(f'Bob usage: {tracker.get_usage("bob", "daily_downloads")}')
    print(f'Bob remaining: {tracker.get_remaining("bob", "daily_downloads", tier="platinum")}')
    print("USAGE TRACKER OK")


if __name__ == "__main__":
    main()

