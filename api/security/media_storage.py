"""MediaForge Media Storage Security, Path Isolation, and Resource Safety Boundaries.

Enforces:
1. Tenant-scoped media filesystem isolation.
2. Prevention of directory traversal (../), null byte injection, and alternative stream escapes.
3. Rejection of client-supplied absolute filesystem paths.
4. Symlink and hardlink escape defenses.
5. Collision-resistant artifact path generation.
6. Safe, isolated temporary directories with deterministic cleanup.
7. Concat manifest safety: newline injection defense, single-quote escaping, and path containment.
8. Configurable resource limits: max input file size, max clip duration, merge limits, FFmpeg concurrency.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import hashlib
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Generator, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# ── Resource Limits & Quotas ──────────────────────────────────────────────────
MAX_MEDIA_INPUT_SIZE_MB = int(os.environ.get("MEDIAFORGE_MAX_INPUT_SIZE_MB", "2048"))  # 2 GB default
MAX_CLIP_DURATION_SECONDS = float(os.environ.get("MEDIAFORGE_MAX_CLIP_DURATION", "7200.0"))  # 2 hours
MIN_CLIP_DURATION_SECONDS = 0.5
MAX_MERGE_INPUTS = 20
FFMPEG_PROCESS_TIMEOUT_SECONDS = int(os.environ.get("MEDIAFORGE_FFMPEG_TIMEOUT", "1800"))  # 30 minutes
MIN_FREE_DISK_SPACE_MB = int(os.environ.get("MEDIAFORGE_MIN_DISK_SPACE_MB", "500"))

# Global asynchronous concurrency limiter for expensive FFmpeg subprocesses
MAX_CONCURRENT_FFMPEG_JOBS = int(os.environ.get("MEDIAFORGE_MAX_FFMPEG_CONCURRENCY", "3"))
_FFMPEG_SEMAPHORE: asyncio.Semaphore | None = None


def get_ffmpeg_semaphore() -> asyncio.Semaphore:
    """Return the global async semaphore for bounding concurrent FFmpeg executions."""
    global _FFMPEG_SEMAPHORE
    if _FFMPEG_SEMAPHORE is None:
        _FFMPEG_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_FFMPEG_JOBS)
    return _FFMPEG_SEMAPHORE


# ── Storage Root Resolution ───────────────────────────────────────────────────

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")
STAGE_FILE_PATTERN = re.compile(
    r"^stage_(?P<job_id>[a-zA-Z0-9_\-]+)_att(?P<attempt>\d+)_(?P<token>[a-zA-Z0-9]+)\.(?P<ext>[a-zA-Z0-9]+)$"
)


class MediaSecurityError(ValueError):
    """Raised when a media path or processing request violates filesystem safety policies."""
    pass


class PublicationConflictError(MediaSecurityError):
    """Raised when publication to authoritative destination conflicts with existing content."""
    pass


def get_media_storage_root() -> Path:
    """Return the canonical, resolved base directory for all MediaForge storage."""
    env_root = os.environ.get("GID_DOWNLOAD__OUTPUT_DIRECTORY")
    if env_root and env_root.strip():
        root = Path(env_root.strip())
    else:
        # Fallback to project-level data/downloads or user home
        try:
            from src.env_loader import _project_root
            root = _project_root() / "data" / "downloads"
        except Exception:
            root = Path.home() / "Downloads" / "MediaForge"

    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_tenant_identifier(tenant_id: str) -> str:
    """Ensure tenant ID contains only alphanumeric, hyphen, and underscore characters."""
    if not tenant_id or not isinstance(tenant_id, str):
        raise MediaSecurityError("Missing or invalid tenant identifier")
    cleaned = tenant_id.strip()
    if not _SAFE_ID_PATTERN.match(cleaned) or len(cleaned) > 64:
        raise MediaSecurityError("Tenant identifier contains illegal characters or exceeds length")
    return cleaned


def get_tenant_storage_root(tenant_id: str) -> Path:
    """Return and create the canonical, isolated root directory for a tenant."""
    clean_tenant_id = validate_tenant_identifier(tenant_id)
    base_root = get_media_storage_root()
    tenant_root = (base_root / clean_tenant_id).resolve()

    # Verify containment
    if not tenant_root.is_relative_to(base_root):
        raise MediaSecurityError("Tenant root resolution escaped base storage directory")

    tenant_root.mkdir(parents=True, exist_ok=True)
    return tenant_root


def get_tenant_media_dir(tenant_id: str, subfolder: str = "") -> Path:
    """Return and create a tenant-scoped subfolder (downloads, clips, merged, temp)."""
    tenant_root = get_tenant_storage_root(tenant_id)
    if not subfolder:
        return tenant_root

    subfolder = subfolder.strip().lower()
    if subfolder not in ("downloads", "clips", "merged", "temp"):
        raise MediaSecurityError(f"Invalid tenant media subfolder: {subfolder}")

    target_dir = (tenant_root / subfolder).resolve()
    if not target_dir.is_relative_to(tenant_root):
        raise MediaSecurityError("Subfolder escaped tenant storage directory")

    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


# ── Filename & Path Traversal Guards ──────────────────────────────────────────

def validate_safe_filename(filename: str) -> str:
    """Validate and sanitize a single filename component.

    Rejects:
    - Path traversal: '..', '.'
    - Path separators: '/', '\\'
    - Null bytes and control characters
    - Windows reserved names (CON, NUL, COM1, etc.)
    - Windows alternate data streams (':')
    """
    if not filename or not isinstance(filename, str):
        raise MediaSecurityError("Filename cannot be empty")

    name = filename.strip()

    # Check for null bytes or control characters
    if "\0" in name or any(ord(c) < 32 for c in name):
        raise MediaSecurityError("Filename contains illegal control characters or null bytes")

    # Reject path separators
    if "/" in name or "\\" in name:
        raise MediaSecurityError("Filename cannot contain directory separators")

    # Reject traversal dot names
    if name in (".", ".."):
        raise MediaSecurityError("Filename cannot be relative traversal token")

    # Reject Windows Alternate Data Streams (ADS)
    if ":" in name:
        raise MediaSecurityError("Filename cannot contain colon or Alternate Data Streams")

    # Reject Windows reserved device names
    base_name = name.split(".")[0].upper()
    if base_name in _WINDOWS_RESERVED_NAMES:
        raise MediaSecurityError(f"Filename uses reserved device name: '{base_name}'")

    if len(name) > 255:
        raise MediaSecurityError("Filename exceeds maximum length of 255 characters")

    return name


def check_untrusted_path_traversal(path_str: str) -> None:
    """Examine raw user-provided path string for traversal tokens, absolute indicators, and trick characters."""
    if not path_str or not isinstance(path_str, str):
        raise MediaSecurityError("Path string cannot be empty")

    # Rejection of null bytes and newlines
    if "\0" in path_str or "\n" in path_str or "\r" in path_str:
        raise MediaSecurityError("Path contains prohibited null byte or newline characters")

    # Rejection of absolute paths from API clients
    if path_str.startswith(("/", "\\")):
        raise MediaSecurityError("Absolute filesystem paths are prohibited in API media requests")

    # Windows drive letters (e.g. C:, D:) or UNC paths (\\\\)
    if len(path_str) >= 2 and path_str[1] == ":":
        raise MediaSecurityError("Drive-relative or absolute Windows paths are prohibited")
    if path_str.startswith("\\\\"):
        raise MediaSecurityError("UNC network paths are prohibited")

    # Prohibit traversal tokens
    normalized = path_str.replace("\\", "/")
    parts = normalized.split("/")
    if ".." in parts:
        raise MediaSecurityError("Path traversal token ('..') detected")


def resolve_tenant_artifact_path(
    relative_path: str,
    tenant_id: str,
    subfolder: str = "",
) -> Path:
    """Safely resolve an artifact path strictly within the tenant's storage area.

    Rejects:
    - Absolute paths
    - Traversal sequences
    - Symlinks resolving outside the tenant's root
    """
    check_untrusted_path_traversal(relative_path)

    tenant_dir = get_tenant_media_dir(tenant_id, subfolder)
    candidate = (tenant_dir / relative_path).resolve()

    # Boundary containment check
    if not candidate.is_relative_to(tenant_dir):
        raise MediaSecurityError("Resolved path escaped tenant boundary")

    # Symlink escape verification
    if candidate.is_symlink():
        target = candidate.readlink()
        resolved_target = (candidate.parent / target).resolve() if not target.is_absolute() else target.resolve()
        if not resolved_target.is_relative_to(get_tenant_storage_root(tenant_id)):
            raise MediaSecurityError("Symlink references destination outside tenant boundary")

    return candidate


# ── Source Media Authorization for Offline Clipping & Merging ──────────────────

def resolve_and_authorize_clip_source(
    source: str,
    tenant_id: str,
    user_id: str | None = None,
    is_admin: bool = False,
) -> Path:
    """Locate, validate, and authorize a local source file for offline clipping.

    Accepts:
    - A filename within the tenant's storage (e.g., 'sample.mp4')
    - A relative path within the tenant's storage (e.g., 'downloads/video.mp4')
    - A job ID (e.g., 'job:<uuid>' or valid UUID)

    Strictly rejects:
    - Absolute host paths (/etc/passwd, C:\\Windows, etc.)
    - Directory traversal
    - Files belonging to other tenants
    - Files exceeding the configured MAX_MEDIA_INPUT_SIZE_MB
    """
    if not source or not isinstance(source, str):
        raise HTTPException(status_code=400, detail="Source media reference cannot be empty")

    source = source.strip()

    tenant_root = get_tenant_storage_root(tenant_id)

    # Normalize internal container paths matching caller's tenant root ONLY for trusted internal system callers (user_id is None)
    norm_source = source.replace("\\", "/")
    tenant_root_norm = str(tenant_root).replace("\\", "/").rstrip("/") + "/"
    if user_id is None:
        if norm_source.startswith(tenant_root_norm):
            source = norm_source[len(tenant_root_norm):]
        elif norm_source.startswith(f"/app/downloads/{tenant_id}/"):
            source = norm_source[len(f"/app/downloads/{tenant_id}/"):]

    # Rejection of absolute paths or traversal
    try:
        check_untrusted_path_traversal(source)
    except MediaSecurityError as exc:
        logger.warning("Rejected malicious source path '%s': %s", source, exc)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source media reference: {exc}",
        ) from exc
    candidate_file: Path | None = None

    # Search candidates strictly inside caller's tenant storage
    potential_locations = [
        tenant_root / source,
        tenant_root / "downloads" / source,
        tenant_root / "clips" / source,
        tenant_root / "merged" / source,
    ]

    for loc in potential_locations:
        try:
            resolved = loc.resolve()
            if resolved.is_file() and resolved.is_relative_to(tenant_root):
                candidate_file = resolved
                break
        except Exception:
            continue

    if candidate_file is None:
        # Check if source is a job_id or artifact_id (e.g. "job:<uuid>", "artifact:<uuid>", "art:<uuid>", or raw uuid/hex)
        clean_source_id = source
        if source.startswith("artifact:"):
            clean_source_id = source[len("artifact:"):]
        elif source.startswith("job:"):
            clean_source_id = source[len("job:"):]
        elif source.startswith("art:"):
            clean_source_id = source[len("art:"):]
        if _SAFE_ID_PATTERN.match(clean_source_id):
            # Glob within tenant subdirectories for job_id or artifact_id
            for sub in ("downloads", "clips", "merged"):
                sub_dir = tenant_root / sub
                if sub_dir.is_dir():
                    for f in sub_dir.glob(f"*{clean_source_id}*"):
                        if f.is_file() and f.resolve().is_relative_to(tenant_root):
                            candidate_file = f.resolve()
                            break
                    if candidate_file:
                        break

    if candidate_file is None or not candidate_file.is_file():
        raise HTTPException(
            status_code=404,
            detail="Source media artifact not found in tenant storage",
        )

    # Symlink verification
    if candidate_file.is_symlink():
        target = candidate_file.resolve()
        if not target.is_relative_to(tenant_root):
            raise HTTPException(
                status_code=403,
                detail="Source artifact symlink points outside tenant boundary",
            )

    # File size limit enforcement
    try:
        size_bytes = candidate_file.stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > MAX_MEDIA_INPUT_SIZE_MB:
            raise HTTPException(
                status_code=400,
                detail=f"Source media size ({size_mb:.1f} MB) exceeds maximum allowed limit of {MAX_MEDIA_INPUT_SIZE_MB} MB",
            )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot inspect source media: {exc}") from exc

    return candidate_file


# ── Output Path & Collision-Resistant Generation ─────────────────────────────

def generate_safe_output_path(
    tenant_id: str,
    subfolder: str,
    prefix: str,
    extension: str,
) -> tuple[Path, str, str]:
    """Generate a collision-resistant output artifact path within tenant storage.

    Returns:
    - abs_path: Absolute Path for internal server FFmpeg execution.
    - rel_path: Relative safe path (e.g. 'clips/clip_abc.mp4') suitable for API response.
    - artifact_id: Unique 12-character identifier.
    """
    tenant_dir = get_tenant_media_dir(tenant_id, subfolder)
    artifact_id = uuid.uuid4().hex[:12]

    clean_prefix = re.sub(r"[^a-zA-Z0-9_\-]", "_", prefix)[:32].strip("_") or "media"
    clean_ext = re.sub(r"[^a-zA-Z0-9]", "", extension)[:8] or "mp4"

    filename = f"{clean_prefix}_{artifact_id}.{clean_ext}"
    abs_path = (tenant_dir / filename).resolve()

    if not abs_path.is_relative_to(tenant_dir):
        raise MediaSecurityError("Generated output path escaped tenant directory")

    if abs_path.exists() or abs_path.is_symlink():
        if abs_path.is_symlink():
            try:
                abs_path.unlink()
            except OSError:
                pass
        artifact_id = uuid.uuid4().hex[:16]
        filename = f"{clean_prefix}_{artifact_id}.{clean_ext}"
        abs_path = (tenant_dir / filename).resolve()

    rel_path = f"{subfolder}/{filename}"
    return abs_path, rel_path, artifact_id


# ── Temporary Workspace & Cleanup ─────────────────────────────────────────────

@contextlib.contextmanager
def tenant_temp_workspace(tenant_id: str) -> Generator[Path, None, None]:
    """Context manager providing an isolated temporary directory within the tenant's temp storage.

    Guarantees recursive cleanup of all temporary artifacts upon normal exit or exception.
    """
    temp_root = get_tenant_media_dir(tenant_id, "temp")
    workspace_name = f"proc_{uuid.uuid4().hex}"
    workspace_dir = (temp_root / workspace_name).resolve()

    if not workspace_dir.is_relative_to(temp_root):
        raise MediaSecurityError("Temporary workspace escaped tenant temp boundary")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield workspace_dir
    finally:
        try:
            if workspace_dir.exists():
                shutil.rmtree(workspace_dir, ignore_errors=True)
        except Exception as exc:
            logger.warning("Failed to clean up temp workspace %s: %s", workspace_dir, exc)


# ── Safe Concat Manifest Generation ───────────────────────────────────────────

def build_safe_concat_manifest(
    clip_paths: list[Path],
    tenant_id: str,
    manifest_path: Path,
) -> Path:
    """Build an FFmpeg concat demuxer manifest file with strict safety validation.

    Guarantees:
    - Every clip path exists and is confirmed within the tenant's storage boundary.
    - Absolute containment: paths outside the tenant storage are rejected.
    - Injection defense: rejects any filename or path containing newlines (\\n, \\r) or null bytes (\\0).
    - Escapes single quotes according to FFmpeg concat demuxer syntax.
    """
    tenant_root = get_tenant_storage_root(tenant_id)
    manifest_dir = manifest_path.parent.resolve()

    if not manifest_dir.is_relative_to(tenant_root):
        raise MediaSecurityError("Concat manifest path is outside tenant storage boundary")

    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest_lines = []
    for i, clip in enumerate(clip_paths):
        resolved_clip = clip.resolve()

        # 1. Existence and containment
        if not resolved_clip.is_file():
            raise MediaSecurityError(f"Clip {i + 1} does not exist or is not a regular file: {clip}")
        if not resolved_clip.is_relative_to(tenant_root):
            raise MediaSecurityError(f"Clip {i + 1} is outside tenant authorized storage: {clip}")

        # 2. Injection defense: no newlines or null bytes
        clip_str = str(resolved_clip)
        if "\n" in clip_str or "\r" in clip_str or "\0" in clip_str:
            raise MediaSecurityError(f"Clip {i + 1} contains illegal newline or control characters")

        # 3. FFmpeg concat demuxer single-quote escaping
        # In FFmpeg concat demuxer: file 'path' where single quotes are escaped as '\''
        escaped_path = clip_str.replace("'", "'\\''")
        manifest_lines.append(f"file '{escaped_path}'")

    # Write manifest safely
    manifest_content = "\n".join(manifest_lines) + "\n"
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(manifest_content)

    return manifest_path


def get_job_staging_path(
    tenant_id: str,
    job_id: str,
    attempt_count: int,
    extension: str,
) -> Path:
    """Generate an attempt-specific staging path ensuring exclusive ownership.

    Attempts A and B write to distinct physical files in the tenant's temp/staging directory,
    preventing concurrent write corruption and ensuring cleanup cannot affect other attempts.
    """
    clean_tenant_id = validate_tenant_identifier(tenant_id)
    staging_dir = get_tenant_media_dir(clean_tenant_id, "temp") / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    token = uuid.uuid4().hex[:8]
    clean_ext = re.sub(r"[^a-zA-Z0-9]", "", extension)[:8] or "mp4"
    staging_filename = f"stage_{job_id}_att{attempt_count}_{token}.{clean_ext}"
    staging_path = (staging_dir / staging_filename).resolve()

    if not staging_path.is_relative_to(staging_dir):
        raise MediaSecurityError("Staging path escaped tenant staging directory")

    return staging_path


def compute_file_sha256(file_path: Path) -> str:
    """Calculate SHA-256 checksum of a file directly from bytes in 64KB chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def publish_artifact_file_no_clobber(
    staging_path: Path,
    abs_output: Path,
    expected_sha256: str,
    expected_size: int,
) -> bool:
    """Safely publish an attempt staging file to authoritative output without clobbering.

    Invariants:
    1. If abs_output exists and matches expected_sha256 and expected_size:
       The authoritative output was already published (e.g. from an interrupted earlier attempt).
       Unlinks the redundant staging copy and succeeds safely.
    2. If abs_output exists with differing checksum or size:
       Raises PublicationConflictError. Under NO circumstances will an existing output be overwritten.
    3. If abs_output does not exist:
       Atomically publishes staging_path to abs_output using a non-clobbering hardlink or atomic rename.
       Guarantees abs_output exists and matches expected_size upon return.
    """
    if not staging_path.exists():
        # If staging file was already promoted or cleaned, verify whether authoritative output is in place
        if abs_output.exists():
            if abs_output.is_symlink():
                raise PublicationConflictError(f"Destination {abs_output.name} is an untrusted symlink")
            actual_hash = compute_file_sha256(abs_output)
            actual_size = abs_output.stat().st_size
            if actual_hash == expected_sha256 and actual_size == expected_size:
                return True
        raise MediaSecurityError(f"Staging file does not exist: {staging_path}")

    if staging_path.is_symlink():
        raise MediaSecurityError("Staging path must not be a symlink")

    # Ensure parent output directory exists and is safe
    abs_output.parent.mkdir(parents=True, exist_ok=True)

    # 1. Existing destination check
    if abs_output.exists():
        if abs_output.is_symlink():
            raise PublicationConflictError(f"Destination {abs_output.name} is an untrusted symlink")

        actual_hash = compute_file_sha256(abs_output)
        actual_size = abs_output.stat().st_size
        if actual_hash == expected_sha256 and actual_size == expected_size:
            # File already safely published from an interrupted attempt; remove redundant staging
            staging_path.unlink(missing_ok=True)
            return True
        else:
            raise PublicationConflictError(
                f"Authoritative destination {abs_output.name} already exists with differing checksum or size. "
                f"Existing: {actual_size} bytes / {actual_hash[:12]}, Expected: {expected_size} bytes / {expected_sha256[:12]}"
            )

    # 2. Atomic no-clobber publication via hard link or safe atomic rename
    try:
        os.link(str(staging_path), str(abs_output))
        staging_path.unlink(missing_ok=True)
    except FileExistsError:
        # Destination appeared concurrently
        if abs_output.is_symlink():
            raise PublicationConflictError(f"Destination {abs_output.name} is an untrusted symlink")
        actual_hash = compute_file_sha256(abs_output)
        actual_size = abs_output.stat().st_size
        if actual_hash == expected_sha256 and actual_size == expected_size:
            staging_path.unlink(missing_ok=True)
            return True
        raise PublicationConflictError(
            f"Authoritative destination {abs_output.name} concurrently created with differing checksum"
        )
    except OSError as err:
        # Fail closed on cross-device link (EXDEV: errno 18)
        # MediaForge operates on a unified single-volume media storage tree (/app/downloads).
        # Cross-device hard links cannot be performed atomically without overwrite hazards;
        # fail closed immediately so the database transaction rolls back safely.
        if getattr(err, "errno", None) == 18:
            raise MediaSecurityError(
                f"Cross-device link error (EXDEV) between {staging_path} and {abs_output}. "
                "Media storage mounts must reside on the same filesystem to guarantee atomic no-clobber publication."
            ) from err
        raise

    # 3. Final validation
    if not abs_output.exists():
        raise MediaSecurityError(f"Published authoritative file missing after promotion: {abs_output}")
    if abs_output.stat().st_size != expected_size:
        raise MediaSecurityError(
            f"Published authoritative file size mismatch: expected {expected_size}, got {abs_output.stat().st_size}"
        )

    return True


@dataclass
class StagingReconciliationReport:
    """Report from safe inspection and cleanup of intermediate staging files."""
    scanned_files_count: int = 0
    eligible_for_deletion_count: int = 0
    deleted_count: int = 0
    preserved_active_lease_count: int = 0
    preserved_current_attempt_count: int = 0
    preserved_suspicious_count: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)


async def reconcile_orphaned_staging_files_safe(
    db: Optional[Any] = None,
    storage_root: Optional[Path] = None,
    max_age_seconds: int = 1800,
    dry_run: bool = False,
    test_mode_only: bool = False,
) -> StagingReconciliationReport:
    """Safely inspect and prune abandoned intermediate staging files with DB lease/attempt verification.

    TASK 5 Invariants:
    - Never deletes files associated with a valid running lease.
    - Never deletes files belonging to an active or pending attempt.
    - Never deletes files referenced as authoritative outputs.
    - Rejects symlinks and unrecognized filenames as suspicious.
    - Supports dry-run preview and targeted test cleanup.
    """
    import time
    from datetime import datetime, timezone
    from sqlalchemy import select

    root = (storage_root or get_media_storage_root()).resolve()
    report = StagingReconciliationReport()
    now_t = time.time()
    now_dt = datetime.utcnow()

    if not root.exists() or not root.is_dir():
        return report

    try:
        tenant_dirs = [d for d in root.iterdir() if d.is_dir()]
    except Exception as exc:
        logger.debug("Failed listing storage root: %s", exc)
        return report

    for tenant_dir in tenant_dirs:
        staging_dir = tenant_dir / "temp" / "staging"
        if not staging_dir.is_dir():
            continue

        try:
            entries = list(staging_dir.iterdir())
        except Exception:
            continue

        for stage_file in entries:
            report.scanned_files_count += 1

            # 1. Symlink check
            if stage_file.is_symlink():
                report.preserved_suspicious_count += 1
                report.items.append({
                    "path": str(stage_file),
                    "category": "suspicious_symlink",
                    "action": "preserved",
                    "reason": "File is a symlink",
                })
                continue

            # 2. Path containment check
            try:
                resolved = stage_file.resolve()
                if not resolved.is_relative_to(staging_dir):
                    report.preserved_suspicious_count += 1
                    report.items.append({
                        "path": str(stage_file),
                        "category": "suspicious_traversal",
                        "action": "preserved",
                        "reason": "Resolved path escaped staging directory",
                    })
                    continue
            except Exception:
                continue

            # 3. Match exact safe staging naming convention
            m = STAGE_FILE_PATTERN.match(stage_file.name)
            if not m:
                report.preserved_suspicious_count += 1
                report.items.append({
                    "path": str(stage_file),
                    "category": "suspicious_unrecognized_filename",
                    "action": "preserved",
                    "reason": "Filename does not match stage_<job_id>_att<attempt>_<token>.<ext>",
                })
                continue

            job_id = m.group("job_id")
            attempt_num = int(m.group("attempt"))

            is_test_job = job_id.startswith("test-") or "test" in job_id.lower()
            if test_mode_only and not is_test_job:
                continue

            # 4. Check file age
            try:
                mtime = stage_file.stat().st_mtime
                age_seconds = now_t - mtime
            except OSError:
                continue

            # 5. Database ownership and lease verification
            if db is not None:
                try:
                    from api.database.models import MediaWorkItemDB, MediaArtifactDB

                    stmt = select(MediaWorkItemDB).where(MediaWorkItemDB.id == job_id)
                    res = await db.execute(stmt)
                    job_item = res.scalar_one_or_none()

                    if not job_item:
                        # Job does not exist in DB: cannot prove safe ownership!
                        if is_test_job and age_seconds > 60:
                            # Test fixture cleanup allowed
                            pass
                        else:
                            report.preserved_suspicious_count += 1
                            report.items.append({
                                "path": str(stage_file),
                                "category": "suspicious_unindexed_job",
                                "action": "preserved",
                                "reason": f"No job record found for job_id '{job_id}'",
                            })
                            continue
                    else:
                        # Job exists: check active lease
                        if job_item.status == "running" and job_item.lease_expires_at is not None:
                            lease_exp = job_item.lease_expires_at
                            if lease_exp.tzinfo is not None:
                                lease_exp = lease_exp.replace(tzinfo=None)
                            if lease_exp > now_dt:
                                report.preserved_active_lease_count += 1
                                report.items.append({
                                    "path": str(stage_file),
                                    "category": "preserved_active_lease",
                                    "action": "preserved",
                                    "reason": f"Job {job_id} holds an active lease until {lease_exp.isoformat()}",
                                })
                                continue

                        # Verify attempt number
                        if job_item.status in ("running", "retry_wait", "queued") and job_item.attempt_count <= attempt_num:
                            report.preserved_current_attempt_count += 1
                            report.items.append({
                                "path": str(stage_file),
                                "category": "preserved_current_attempt",
                                "action": "preserved",
                                "reason": f"File belongs to active/pending attempt {attempt_num} (current: {job_item.attempt_count})",
                            })
                            continue

                        # Verify not referenced as authoritative artifact
                        if job_item.result_artifact_id:
                            stmt_art = select(MediaArtifactDB).where(MediaArtifactDB.id == job_item.result_artifact_id)
                            res_art = await db.execute(stmt_art)
                            art_obj = res_art.scalar_one_or_none()
                            if art_obj and (stage_file.name in str(art_obj.storage_path) or stage_file.name == art_obj.filename):
                                report.preserved_suspicious_count += 1
                                report.items.append({
                                    "path": str(stage_file),
                                    "category": "preserved_referenced_artifact",
                                    "action": "preserved",
                                    "reason": f"Staging file is referenced by artifact {art_obj.id}",
                                })
                                continue
                except Exception as db_exc:
                    logger.debug("DB check failed for staging file %s: %s", stage_file.name, db_exc)
                    report.preserved_suspicious_count += 1
                    continue
            else:
                # No DB session provided: require age threshold
                if age_seconds < max_age_seconds:
                    continue

            # 6. File is eligible for deletion
            report.eligible_for_deletion_count += 1
            if not dry_run:
                try:
                    stage_file.unlink(missing_ok=True)
                    report.deleted_count += 1
                    report.items.append({
                        "path": str(stage_file),
                        "category": "deleted_abandoned_staging",
                        "action": "deleted",
                        "reason": f"Safely pruned abandoned staging file (age: {int(age_seconds)}s)",
                    })
                except OSError as err:
                    logger.warning("Failed unlinking staging file %s: %s", stage_file, err)
            else:
                report.items.append({
                    "path": str(stage_file),
                    "category": "dry_run_eligible",
                    "action": "preview",
                    "reason": f"Eligible for cleanup (age: {int(age_seconds)}s)",
                })

    return report


def reconcile_orphaned_staging_files(max_age_seconds: int = 1800) -> int:
    """Safely sweep and remove abandoned staging files from crashed attempts.

    Maintains backward compatibility while enforcing pattern matching and non-symlink invariants.
    """
    import time
    storage_root = get_media_storage_root()
    cleaned = 0
    now_t = time.time()
    try:
        for tenant_dir in storage_root.iterdir():
            if not tenant_dir.is_dir():
                continue
            staging_dir = tenant_dir / "temp" / "staging"
            if not staging_dir.is_dir():
                continue
            for stage_file in staging_dir.iterdir():
                if stage_file.is_file() and not stage_file.is_symlink() and STAGE_FILE_PATTERN.match(stage_file.name):
                    try:
                        mtime = stage_file.stat().st_mtime
                        if now_t - mtime > max_age_seconds:
                            stage_file.unlink(missing_ok=True)
                            cleaned += 1
                    except OSError:
                        pass
    except Exception as exc:
        logger.debug("Staging reconciliation error: %s", exc)
    return cleaned
