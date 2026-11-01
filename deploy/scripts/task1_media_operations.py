"""Task 1 Media Generation and Verification Helper.

Executed inside the mediaforge-api container:
1. Creates a small 5-second synthetic MP4 video using FFmpeg in tenant downloads folder.
2. Extracts Clip 1 (00:00:00 - 00:00:02) via POST /api/v1/clips/extract.
3. Extracts Clip 2 (00:00:02 - 00:00:04) via POST /api/v1/clips/extract.
4. Merges Clip 1 and Clip 2 via POST /api/v1/clips/merge.
5. Verifies all 3 artifacts exist in database with status='ready'.
6. Computes and prints relative path, size, and SHA-256 for all 3 media files.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import urllib.error

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from api.config import get_api_settings
from api.database.models import MediaArtifactDB
from api.security.jwt import create_access_token

TENANT_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
API_BASE = "http://127.0.0.1:8000/api/v1"


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_source_video(downloads_dir: Path) -> Path:
    downloads_dir.mkdir(parents=True, exist_ok=True)
    source_file = downloads_dir / "synthetic_backup_test_source.mp4"
    if source_file.exists():
        source_file.unlink()

    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-f", "lavfi", "-i", "testsrc=duration=5:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(source_file),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert source_file.exists(), "Source synthetic video creation failed"
    print(f"[1] Synthetic source video created: {source_file} ({source_file.stat().st_size} bytes)")
    return source_file


def call_api(endpoint: str, payload: dict, token: str) -> dict:
    url = f"{API_BASE}{endpoint}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        raise RuntimeError(f"API call to {endpoint} failed ({e.code}): {err_msg}") from e


def verify_artifacts_via_api(artifact_ids: list[str], token: str) -> list[dict]:
    results = []
    for aid in artifact_ids:
        url = f"{API_BASE}/artifacts/{aid}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ready", f"Artifact {aid} status is {data['status']}, expected 'ready'"
            results.append({
                "id": data["id"],
                "type": data["artifact_type"],
                "status": data["status"],
                "storage_path": data["storage_path"],
                "file_size": data["file_size_bytes"],
                "parent_id": data.get("parent_artifact_id"),
                "input_sources": data.get("input_sources"),
            })
    return results


def main():
    token, _ = create_access_token(
        user_id=USER_ID,
        tenant_id=TENANT_ID,
        extra_claims={"role": "owner", "tier": "platinum"},
    )

    storage_root = Path("/app/downloads")
    tenant_dir = storage_root / TENANT_ID
    downloads_dir = tenant_dir / "downloads"

    # Step 1: Generate synthetic source MP4
    source_file = generate_source_video(downloads_dir)

    # Step 2: Extract Clip 1 (00:00:00 - 00:00:02)
    clip1_res = call_api("/clips/extract", {
        "source": "synthetic_backup_test_source.mp4",
        "start_time": "00:00:00",
        "end_time": "00:00:02",
        "output_format": "mp4",
        "mode": "precise",
    }, token)
    clip1_id = clip1_res["clip_id"]
    clip1_path_rel = clip1_res["output_path"]
    clip1_file = tenant_dir / clip1_path_rel
    assert clip1_file.exists(), f"Clip 1 file does not exist: {clip1_file}"
    print(f"[2] Clip 1 extracted: id={clip1_id}, path={clip1_path_rel}, size={clip1_file.stat().st_size}")

    # Step 3: Extract Clip 2 (00:00:02 - 00:00:04)
    clip2_res = call_api("/clips/extract", {
        "source": "synthetic_backup_test_source.mp4",
        "start_time": "00:00:02",
        "end_time": "00:00:04",
        "output_format": "mp4",
        "mode": "precise",
    }, token)
    clip2_id = clip2_res["clip_id"]
    clip2_path_rel = clip2_res["output_path"]
    clip2_file = tenant_dir / clip2_path_rel
    assert clip2_file.exists(), f"Clip 2 file does not exist: {clip2_file}"
    print(f"[3] Clip 2 extracted: id={clip2_id}, path={clip2_path_rel}, size={clip2_file.stat().st_size}")

    # Step 4: Merge Clip 1 and Clip 2
    merge_res = call_api("/clips/merge", {
        "clips": [clip1_file.name, clip2_file.name],
        "output_format": "mp4",
    }, token)
    merge_id = merge_res["merge_id"]
    merge_path_rel = merge_res["output_path"]
    merge_file = tenant_dir / merge_path_rel
    assert merge_file.exists(), f"Merge file does not exist: {merge_file}"
    print(f"[4] Merged video created: id={merge_id}, path={merge_path_rel}, size={merge_file.stat().st_size}")

    # Step 5: Verify all 3 in DB
    artifact_ids = [clip1_id, clip2_id, merge_id]
    db_records = verify_artifacts_via_api(artifact_ids, token)
    print("[5] All 3 database artifact records confirmed ready:")
    for rec in db_records:
        print(f"    - ID: {rec['id']} ({rec['type']}) -> status={rec['status']}, path={rec['storage_path']}")

    # Step 6: Compute byte sizes and SHA-256 for all 3 artifacts + source
    manifest = {
        "source": {
            "relative_path": f"{TENANT_ID}/downloads/{source_file.name}",
            "byte_size": source_file.stat().st_size,
            "sha256": compute_sha256(source_file),
        },
        "clip1": {
            "id": clip1_id,
            "relative_path": f"{TENANT_ID}/{clip1_path_rel}",
            "byte_size": clip1_file.stat().st_size,
            "sha256": compute_sha256(clip1_file),
        },
        "clip2": {
            "id": clip2_id,
            "relative_path": f"{TENANT_ID}/{clip2_path_rel}",
            "byte_size": clip2_file.stat().st_size,
            "sha256": compute_sha256(clip2_file),
        },
        "merge": {
            "id": merge_id,
            "relative_path": f"{TENANT_ID}/{merge_path_rel}",
            "byte_size": merge_file.stat().st_size,
            "sha256": compute_sha256(merge_file),
        },
    }

    manifest_path = Path("/app/data/task1_manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("\n[6] Media Manifest Recorded:")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
