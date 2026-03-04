"""Crash handler — logs unhandled exceptions before exit."""

from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def install_crash_handler(log_dir: str) -> None:
    crash_dir = Path(log_dir)
    crash_dir.mkdir(parents=True, exist_ok=True)
    original_hook = sys.excepthook

    def crash_hook(exc_type, exc_value, exc_tb):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        crash_file = crash_dir / f"crash_{timestamp}.log"
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        report = (
            "GrabItDown Crash Report\n"
            + "=" * 40 + "\n"
            f"Time: {datetime.now().isoformat()}\n"
            f"Python: {sys.version}\n\nTraceback:\n{tb_text}\n"
        )
        try:
            crash_file.write_text(report)
            logger.critical("Crash report saved: %s", crash_file)
        except Exception:
            pass
        logger.critical("UNHANDLED EXCEPTION: %s", exc_value, exc_info=(exc_type, exc_value, exc_tb))
        original_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = crash_hook
    logger.info("Crash handler installed")


def get_crash_reports(log_dir: str) -> list[dict]:
    crash_dir = Path(log_dir)
    reports = []
    for f in sorted(crash_dir.glob("crash_*.log"), reverse=True)[:10]:
        try:
            reports.append({
                "file": str(f),
                "name": f.name,
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
        except Exception:
            pass
    return reports
