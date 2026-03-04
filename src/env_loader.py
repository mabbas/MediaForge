"""Load .env from project root so GID_FFMPEG_LOCATION and other vars are available.

Use this from API, desktop, and providers so .env is found regardless of
current working directory. Call load_project_dotenv() as early as possible.
"""

from __future__ import annotations

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
