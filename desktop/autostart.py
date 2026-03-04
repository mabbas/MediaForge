"""Auto-start on login — platform-specific.

- Windows: Registry (HKCU\\...\\Run)
- macOS: LaunchAgent plist
- Linux: XDG autostart .desktop file
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

logger = logging.getLogger(__name__)


def enable_autostart() -> bool:
    """Enable GrabItDown to start on login."""
    system = platform.system()
    try:
        if system == "Windows":
            return _windows_autostart(True)
        elif system == "Darwin":
            return _macos_autostart(True)
        elif system == "Linux":
            return _linux_autostart(True)
        else:
            logger.warning(
                "Autostart not supported: %s", system
            )
            return False
    except Exception as e:
        logger.error("Autostart enable failed: %s", e)
        return False


def disable_autostart() -> bool:
    """Disable GrabItDown auto-start."""
    system = platform.system()
    try:
        if system == "Windows":
            return _windows_autostart(False)
        elif system == "Darwin":
            return _macos_autostart(False)
        elif system == "Linux":
            return _linux_autostart(False)
        return False
    except Exception as e:
        logger.error("Autostart disable failed: %s", e)
        return False


def is_autostart_enabled() -> bool:
    """Check if autostart is enabled."""
    system = platform.system()
    try:
        if system == "Windows":
            return _windows_check()
        elif system == "Darwin":
            return _macos_check()
        elif system == "Linux":
            return _linux_check()
    except Exception:
        pass
    return False


def _windows_autostart(enable: bool) -> bool:
    import sys

    import winreg

    key_path = (
        r"Software\Microsoft\Windows\CurrentVersion\Run"
    )
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            winreg.KEY_SET_VALUE,
        )
        if enable:
            exe = sys.executable
            cmd = f'"{exe}" -m desktop'
            winreg.SetValueEx(
                key, "GrabItDown", 0, winreg.REG_SZ, cmd
            )
        else:
            try:
                winreg.DeleteValue(key, "GrabItDown")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        logger.error("Windows autostart: %s", e)
        return False


def _windows_check() -> bool:
    import winreg

    key_path = (
        r"Software\Microsoft\Windows\CurrentVersion\Run"
    )
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path
        )
        winreg.QueryValueEx(key, "GrabItDown")
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _macos_autostart(enable: bool) -> bool:
    import sys

    plist_dir = (
        Path.home() / "Library" / "LaunchAgents"
    )
    plist_path = plist_dir / "com.grabitdown.app.plist"

    if enable:
        plist_dir.mkdir(parents=True, exist_ok=True)
        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.grabitdown.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>desktop</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>"""
        plist_path.write_text(content)
        return True
    else:
        if plist_path.exists():
            plist_path.unlink()
        return True


def _macos_check() -> bool:
    plist = (
        Path.home()
        / "Library"
        / "LaunchAgents"
        / "com.grabitdown.app.plist"
    )
    return plist.exists()


def _linux_autostart(enable: bool) -> bool:
    import sys

    autostart_dir = Path.home() / ".config" / "autostart"
    desktop_file = autostart_dir / "grabitdown.desktop"

    if enable:
        autostart_dir.mkdir(parents=True, exist_ok=True)
        content = f"""[Desktop Entry]
Type=Application
Name=GrabItDown
Comment=Production-grade media downloader
Exec={sys.executable} -m desktop
Icon=grabitdown
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""
        desktop_file.write_text(content)
        return True
    else:
        if desktop_file.exists():
            desktop_file.unlink()
        return True


def _linux_check() -> bool:
    desktop_file = (
        Path.home()
        / ".config"
        / "autostart"
        / "grabitdown.desktop"
    )
    return desktop_file.exists()
