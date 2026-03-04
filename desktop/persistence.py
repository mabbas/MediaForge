# Desktop state and preferences persistence
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_window_state(data_dir: str):
    try:
        path = Path(data_dir) / "window_state.json"
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        logger.warning("Failed to load window state: %s", e)
    return None


def save_window_state(state: dict, data_dir: str):
    try:
        path = Path(data_dir) / "window_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2))
        return True
    except Exception as e:
        logger.warning("Failed to save window state: %s", e)
        return False


def load_desktop_prefs(data_dir: str):
    try:
        path = Path(data_dir) / "preferences.json"
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def save_desktop_prefs(prefs: dict, data_dir: str):
    try:
        path = Path(data_dir) / "preferences.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = load_desktop_prefs(data_dir) or {}
        existing.update(prefs)
        path.write_text(json.dumps(existing, indent=2))
        return True
    except Exception as e:
        logger.warning("Failed to save prefs: %s", e)
        return False
