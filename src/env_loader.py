"""Load .env from project root so GID_FFMPEG_LOCATION and other vars are available.

Use this from API, desktop, and providers so .env is found regardless of
current working directory. Call load_project_dotenv() as early as possible.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _project_root() -> Path:
    """Return project root (directory containing pyproject.toml / config / src)."""
    start = Path(__file__).resolve().parent
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").exists():
            return parent
        if (parent / "config").is_dir() or (parent / ".env").exists():
            return parent
    return start.parent


def load_project_dotenv() -> bool:
    """Load .env from project root. Returns True if a file was loaded."""
    root = _project_root()
    env_file = root / ".env"
    if not env_file.is_file():
        return False
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
        return True
    except ImportError:
        return False


def get_ffmpeg_location() -> str | None:
    """Return directory containing ffmpeg/ffprobe, or None if not found.

    Single source of truth for both download (YouTube) and clip extraction.
    Reads GID_FFMPEG_LOCATION from env (call load_project_dotenv() first if using .env),
    then falls back to shutil.which("ffmpeg"). Normalizes Windows paths from .env.
    """
    load_project_dotenv()
    raw = os.environ.get("GID_FFMPEG_LOCATION", "").strip() or None
    if not raw:
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            return str(Path(ffmpeg_path).resolve().parent)
        return None
    raw_normalized = raw.replace("\\", "/")
    ffmpeg_dir = Path(raw_normalized).expanduser().resolve()
    if ffmpeg_dir.is_dir():
        return str(ffmpeg_dir)
    if os.path.isdir(raw):
        return os.path.normpath(raw)
    return None
