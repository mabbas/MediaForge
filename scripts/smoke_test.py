"""GrabItDown Smoke Test — manual verification script."""

#!/usr/bin/env python3

from __future__ import annotations

import sys
import time


def main() -> int:
    """Main entry point for the smoke test."""
    print("=" * 60)
    print("  GrabItDown Smoke Test")
    print("=" * 60)
    print()

    errors: list[tuple[str, str]] = []

    # Test 1: Config
    print("[1/6] Loading configuration...")
    try:
        from src.config import get_settings

        settings = get_settings()
        assert settings.app.name == "GrabItDown"
        print(f"  ✓ App: {settings.app.name} v{settings.app.version}")
        print(f"  ✓ Max concurrent: {settings.download.max_concurrent_downloads}")
        print(f"  ✓ Default quality: {settings.video.default_quality}")
    except Exception as exc:  # pragma: no cover - manual script
        print(f"  ✗ FAILED: {exc}")
        errors.append(("Config", str(exc)))

    # Test 2: Feature Flags
    print("\n[2/6] Loading feature flags...")
    try:
        from src.features.feature_flags import load_feature_flags
        from src.features.feature_gate import FeatureGate

        flags = load_feature_flags()
        gate = FeatureGate(flags)
        assert gate.is_enabled("video_download")
        print(f"  ✓ Mode: {flags.mode}")
        print(f"  ✓ Tiers: {list(flags.tiers.keys())}")
        print(f"  ✓ Video download: enabled")
        print(f"  ✓ Max quality: {gate.get_limit('video_download', 'max_quality')}")
    except Exception as exc:  # pragma: no cover - manual script
        print(f"  ✗ FAILED: {exc}")
        errors.append(("Feature Flags", str(exc)))

    # Test 3: Models
    print("\n[3/6] Testing data models...")
    try:
        from src.models import (
            DownloadRequest,
            MediaType,
            Quality,
            TranscriptSegment,
        )

        req = DownloadRequest(url="https://youtube.com/watch?v=test")
        assert req.media_type == MediaType.VIDEO
        assert req.quality == Quality.Q_1080P
        seg = TranscriptSegment(start_seconds=83.456, end_seconds=87.0, text="Hello")
        assert seg.start_timestamp == "00:01:23.456"
        print("  ✓ DownloadRequest: defaults OK")
        print("  ✓ TranscriptSegment: timestamp formatting OK")
        print("  ✓ All models importable")
    except Exception as exc:  # pragma: no cover - manual script
        print(f"  ✗ FAILED: {exc}")
        errors.append(("Models", str(exc)))

    # Test 4: Provider Registry
    print("\n[4/6] Setting up provider registry...")
    try:
        from src.core.provider_factory import create_provider_registry

        registry = create_provider_registry()
        providers = registry.list_providers()
        print(f"  ✓ Providers registered: {len(providers)}")
        for p in providers:
            print(f"    - {p['name']} ({p['type']})")
    except Exception as exc:  # pragma: no cover - manual script
        print(f"  ✗ FAILED: {exc}")
        errors.append(("Registry", str(exc)))
        registry = None  # type: ignore[assignment]

    # Test 5: Provider Detection
    print("\n[5/6] Testing provider detection...")
    try:
        if registry is None:  # type: ignore[truthy-function]
            raise RuntimeError("Registry not initialized")
        test_urls = {
            "https://www.youtube.com/watch?v=test": "YouTube",
            "https://youtu.be/test": "YouTube",
            "https://random-site.com/video": "Generic",
        }
        for url, expected in test_urls.items():
            detected = registry.detect_provider(url)
            assert detected.name == expected, f"Expected {expected}, got {detected.name}"
            print(f"  ✓ {url[:45]}... → {detected.name}")
    except Exception as exc:  # pragma: no cover - manual script
        print(f"  ✗ FAILED: {exc}")
        errors.append(("Detection", str(exc)))

    # Test 6: Real YouTube API call
    print("\n[6/6] Extracting real YouTube video info...")
    try:
        from src.providers.youtube.provider import YouTubeProvider
        from src.exceptions import ProviderError

        yt = YouTubeProvider()

        start = time.time()
        info = yt.extract_info("https://www.youtube.com/watch?v=3hakaoeakiI")
        elapsed = time.time() - start

        assert info.title
        assert info.media_id == "3hakaoeakiI"
        assert len(info.formats) > 0

        print(f"  ✓ Title: {info.title}")
        print(f"  ✓ ID: {info.media_id}")
        print(f"  ✓ Duration: {info.duration_human}")
        print(f"  ✓ Channel: {info.channel_name}")
        print(f"  ✓ Formats: {len(info.formats)}")
        print(f"  ✓ Thumbnails: {len(info.thumbnails)}")

        video_fmts = info.get_video_formats()
        audio_fmts = info.get_audio_formats()
        print(f"  ✓ Video formats: {len(video_fmts)}")
        print(f"  ✓ Audio formats: {len(audio_fmts)}")

        subs = list(info.subtitles_available.keys())[:5]
        print(f"  ✓ Subtitle langs: {subs}")
        print(f"  ✓ Extracted in {elapsed:.1f}s")
    except ProviderError as exc:  # pragma: no cover - manual script
        msg = str(exc)
        if "Video unavailable" in msg or "Private video" in msg:
            print(f"  ! WARNING: YouTube test video not accessible: {msg}")
            print("  ! Skipping YouTube API smoke step; core logic is still verified.")
        else:
            print(f"  ✗ FAILED: {exc}")
            errors.append(("YouTube API", str(exc)))
    except Exception as exc:  # pragma: no cover - manual script
        print(f"  ✗ FAILED: {exc}")
        errors.append(("YouTube API", str(exc)))

    print()
    print("=" * 60)
    if not errors:
        print("  ✓ ALL SMOKE TESTS PASSED")
        print("  GrabItDown core engine is working!")
    else:
        print(f"  ✗ {len(errors)} TEST(S) FAILED:")
        for name, err in errors:
            print(f"    - {name}: {err}")
    print("=" * 60)

    return 0 if not errors else 1


if __name__ == "__main__":  # pragma: no cover - manual script
    sys.exit(main())

