"""Safe string for logging — avoids UnicodeEncodeError on Windows console (cp1252)."""

from __future__ import annotations


def safe_str(s: str | None, max_len: int = 200) -> str:
    """Return a string safe for logging (ASCII-only replacement for non-ASCII)."""
    if s is None:
        return ""
    try:
        out = s.encode("ascii", "replace").decode("ascii")
    except Exception:
        out = "?"
    if max_len > 0 and len(out) > max_len:
        out = out[:max_len] + "..."
    return out
