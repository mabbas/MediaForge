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


def _ffmpeg_exe_names() -> tuple[str, str]:
    suffix = ".exe" if os.name == "nt" else ""
    return f"ffmpeg{suffix}", f"ffprobe{suffix}"


def _dir_has_ffmpeg_binaries(directory: Path) -> bool:
    ffmpeg_name, ffprobe_name = _ffmpeg_exe_names()
    return (directory / ffmpeg_name).is_file() and (directory / ffprobe_name).is_file()


def resolve_ffmpeg_directory(raw_path: str | None = None) -> Path | None:
    """Resolve the directory that contains ffmpeg and ffprobe.

    Accepts common layouts:
    - ``C:/ffmpeg/bin`` (binaries directly in the folder)
    - ``C:/ffmpeg`` (binaries in a ``bin`` subfolder — typical Windows zip install)
  Falls back to PATH when *raw_path* is not set.
    """
    if raw_path and raw_path.strip():
        base = Path(raw_path).expanduser().resolve()
        if base.is_dir():
            if _dir_has_ffmpeg_binaries(base):
                return base
            bin_dir = base / "bin"
            if bin_dir.is_dir() and _dir_has_ffmpeg_binaries(bin_dir):
                return bin_dir
        return None

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return None
    parent = Path(ffmpeg_path).resolve().parent
    if _dir_has_ffmpeg_binaries(parent):
        return parent
    return None


def resolve_ffmpeg_binaries() -> tuple[str, str]:
    """Return absolute paths to ffmpeg and ffprobe executables.

    Raises FFmpegNotFoundError with an actionable message when neither
    GID_FFMPEG_LOCATION nor PATH provides a usable binary.
    """
    from src.exceptions import FFmpegNotFoundError

    load_project_dotenv()
    raw = os.environ.get("GID_FFMPEG_LOCATION", "").strip() or None
    ffdir = resolve_ffmpeg_directory(raw)
    if ffdir is None and raw:
        raise FFmpegNotFoundError(
            f"GID_FFMPEG_LOCATION is set to {raw!r} but ffmpeg/ffprobe were not found "
            f"there or in {raw!r}\\bin. On Windows, point at the folder that contains "
            "ffmpeg.exe (often C:/ffmpeg/bin, not C:/ffmpeg)."
        )

    if ffdir is None:
        ffdir = resolve_ffmpeg_directory(None)

    if ffdir is None:
        raise FFmpegNotFoundError(
            "ffmpeg not found. Install FFmpeg and either add it to PATH or set "
            "GID_FFMPEG_LOCATION to your FFmpeg folder (e.g. C:/ffmpeg or C:/ffmpeg/bin). "
            "See .env.example."
        )

    ffmpeg_name, ffprobe_name = _ffmpeg_exe_names()
    return str(ffdir / ffmpeg_name), str(ffdir / ffprobe_name)


def get_ffmpeg_location() -> str | None:
    """Return directory containing ffmpeg/ffprobe, or None if not found.

    Single source of truth for both download (YouTube) and clip extraction.
    Reads GID_FFMPEG_LOCATION from env (call load_project_dotenv() first if using .env),
    then falls back to shutil.which("ffmpeg"). Normalizes Windows paths from .env.
    """
    load_project_dotenv()
    raw = os.environ.get("GID_FFMPEG_LOCATION", "").strip() or None
    ffdir = resolve_ffmpeg_directory(raw)
    if ffdir is not None:
        return str(ffdir)
    if raw:
        # Env set but binaries missing — do not silently fall back to PATH.
        return None
    ffdir = resolve_ffmpeg_directory(None)
    return str(ffdir) if ffdir else None
