"""Restored Media Integrity and Playability Verification Helper.

Mounts disposable restored Docker volume and verifies:
1. Exact file byte size matches original source.
2. Exact SHA-256 cryptographic checksum matches original source.
3. FFprobe stream inspection verifies valid h264/aac streams and playable duration.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

MANIFEST = {
    "source": {
        "name": "Synthetic Source Video",
        "relative_path": "00000000-0000-0000-0000-000000000001/downloads/synthetic_backup_test_source.mp4",
        "expected_bytes": 117655,
        "expected_sha256": "eefd582b127513add1a884521506d175bfad934f0282a584614e93ec7edeb94a",
        "expected_min_duration": 4.8,
        "expected_max_duration": 5.2,
    },
    "clip1": {
        "name": "Extracted Clip 1 (0-2s)",
        "relative_path": "00000000-0000-0000-0000-000000000001/clips/synthetic_backup_test_source_cli_446c387fd6d5.mp4",
        "expected_bytes": 50275,
        "expected_sha256": "ea8bece1a01dd50bc39f16dc1502e9830efe16307af86948623d8de914a00235",
        "expected_min_duration": 1.8,
        "expected_max_duration": 2.2,
    },
    "clip2": {
        "name": "Extracted Clip 2 (2-4s)",
        "relative_path": "00000000-0000-0000-0000-000000000001/clips/synthetic_backup_test_source_cli_c9d5830bdb88.mp4",
        "expected_bytes": 56185,
        "expected_sha256": "1c858aab8f36f4855a1ecd775728896739e1847bfa9adcd28c37d00e12e48e91",
        "expected_min_duration": 1.8,
        "expected_max_duration": 2.2,
    },
    "merge": {
        "name": "Merged Composite Video",
        "relative_path": "00000000-0000-0000-0000-000000000001/merged/merged_2clips_a0d44d9b54d8.mp4",
        "expected_bytes": 105140,
        "expected_sha256": "20f3eb7e12c200e62609e34c6ce18c9ae17aad2dbada88b98931b0f48108ec01",
        "expected_min_duration": 3.8,
        "expected_max_duration": 4.2,
    },
}


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def run_ffprobe(path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def main():
    restored_root = Path("/restored")
    print("=== MediaForge Restored Media Verification ===")
    print(f"Restored Storage Root: {restored_root}\n")

    all_passed = True

    for key, spec in MANIFEST.items():
        file_path = restored_root / spec["relative_path"]
        print(f"Checking '{spec['name']}':")
        print(f"  Target File:     {file_path}")

        # 1. Existence Check
        if not file_path.exists():
            print("  [FAIL] Restored file does not exist!")
            all_passed = False
            continue

        # 2. Byte Size Comparison
        actual_size = file_path.stat().st_size
        size_match = actual_size == spec["expected_bytes"]
        status_size = "PASS" if size_match else "FAIL"
        print(f"  [{status_size}] Byte Size:     {actual_size} bytes (Expected: {spec['expected_bytes']})")
        if not size_match:
            all_passed = False

        # 3. SHA-256 Checksum Comparison
        actual_sha = compute_sha256(file_path)
        sha_match = actual_sha.lower() == spec["expected_sha256"].lower()
        status_sha = "PASS" if sha_match else "FAIL"
        print(f"  [{status_sha}] SHA-256:       {actual_sha}")
        if not sha_match:
            print(f"         Expected: {spec['expected_sha256']}")
            all_passed = False

        # 4. FFprobe Stream and Playability Inspection
        try:
            probe = run_ffprobe(file_path)
            fmt = probe.get("format", {})
            duration = float(fmt.get("duration", 0.0))
            streams = probe.get("streams", [])
            codecs = {s.get("codec_type"): s.get("codec_name") for s in streams}

            duration_ok = spec["expected_min_duration"] <= duration <= spec["expected_max_duration"]
            codecs_ok = codecs.get("video") == "h264" and codecs.get("audio") == "aac"
            probe_ok = duration_ok and codecs_ok
            status_probe = "PASS" if probe_ok else "FAIL"

            print(f"  [{status_probe}] FFprobe Streams: video={codecs.get('video')}, audio={codecs.get('audio')}")
            print(f"  [{status_probe}] Playable Duration: {duration:.2f}s (Expected window: {spec['expected_min_duration']}s - {spec['expected_max_duration']}s)")

            if not probe_ok:
                all_passed = False
        except Exception as exc:
            print(f"  [FAIL] FFprobe inspection error: {exc}")
            all_passed = False

        print()

    if all_passed:
        print("[OVERALL VERIFICATION: PASS] All restored files match source bytes, SHA-256, and are verified playable via FFprobe.")
        sys.exit(0)
    else:
        print("[OVERALL VERIFICATION: FAIL] One or more integrity checks failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
