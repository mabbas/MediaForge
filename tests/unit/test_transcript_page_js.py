"""Ensure embedded transcript page JavaScript has valid syntax."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from desktop.dashboard import get_transcripts_html


def test_transcript_page_javascript_syntax() -> None:
    """Duplicate const / other syntax errors must not break the transcript UI."""
    node = shutil.which("node")
    if not node:
        return  # skip when Node.js is not installed

    html = get_transcripts_html("http://127.0.0.1:8765")
    start = html.index("<script>") + len("<script>")
    end = html.index("</script>", start)
    js = html[start:end]

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".js",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(js)
        js_path = Path(f.name)

    try:
        result = subprocess.run(
            [node, "--check", str(js_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
    finally:
        js_path.unlink(missing_ok=True)
