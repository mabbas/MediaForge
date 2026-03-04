"""Native dialog wrappers — file/folder pickers, message boxes, etc.

Uses pywebview dialogs when window is available,
falls back to tkinter.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class NativeDialogs:
    """Cross-platform native dialogs."""

    def __init__(self, window=None):
        self._window = window

    def select_folder(
        self,
        title: str = "Select Download Folder",
    ) -> str | None:
        """Open native folder picker."""
        if self._window:
            try:
                import webview

                result = self._window.create_file_dialog(
                    webview.FOLDER_DIALOG,
                    directory="",
                    allow_multiple=False,
                )
                if result and len(result) > 0:
                    return result[0]
            except Exception as e:
                logger.warning(
                    "Webview dialog failed: %s", e
                )

        # Fallback to tkinter
        return self._tk_folder_dialog(title)

    def select_file(
        self,
        title: str = "Select File",
        file_types: list[tuple[str, str]] | None = None,
    ) -> str | None:
        """Open native file picker."""
        if self._window:
            try:
                import webview

                ft = (
                    tuple(
                        f"{desc} ({ext})"
                        for desc, ext in (file_types or [])
                    )
                    if file_types
                    else ()
                )
                result = self._window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    file_types=ft or (),
                    allow_multiple=False,
                )
                if result and len(result) > 0:
                    return result[0]
            except Exception as e:
                logger.warning(
                    "Webview dialog failed: %s", e
                )

        return self._tk_file_dialog(title, file_types)

    def save_file(
        self,
        title: str = "Save File",
        default_name: str = "",
        file_types: list[tuple[str, str]] | None = None,
    ) -> str | None:
        """Open native save dialog."""
        if self._window:
            try:
                import webview

                ft = (
                    tuple(
                        f"{desc} ({ext})"
                        for desc, ext in (file_types or [])
                    )
                    if file_types
                    else ()
                )
                result = self._window.create_file_dialog(
                    webview.SAVE_DIALOG,
                    save_filename=default_name,
                    file_types=ft or (),
                )
                if result:
                    if isinstance(result, list) and len(result) > 0:
                        return result[0]
                    return result if isinstance(result, str) else None
            except Exception as e:
                logger.warning(
                    "Webview dialog failed: %s", e
                )

        return self._tk_save_dialog(
            title, default_name, file_types
        )

    def message_box(
        self,
        title: str,
        message: str,
        msg_type: str = "info",
    ) -> bool:
        """Show native message box.

        msg_type: info, warning, error, question
        Returns True for OK/Yes, False for Cancel/No.
        """
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()

            if msg_type == "question":
                result = messagebox.askyesno(
                    title, message
                )
            elif msg_type == "warning":
                messagebox.showwarning(title, message)
                result = True
            elif msg_type == "error":
                messagebox.showerror(title, message)
                result = True
            else:
                messagebox.showinfo(title, message)
                result = True

            root.destroy()
            return result
        except Exception:
            logger.warning("Message box not available")
            return True

    def _tk_folder_dialog(self, title: str) -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            folder = filedialog.askdirectory(title=title)
            root.destroy()
            return folder if folder else None
        except Exception:
            return None

    def _tk_file_dialog(
        self,
        title: str,
        file_types: list[tuple[str, str]] | None,
    ) -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            ft = file_types or [("All files", "*.*")]
            result = filedialog.askopenfilename(
                title=title, filetypes=ft
            )
            root.destroy()
            return result if result else None
        except Exception:
            return None

    def _tk_save_dialog(
        self,
        title: str,
        default_name: str,
        file_types: list[tuple[str, str]] | None,
    ) -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            ft = file_types or [("All files", "*.*")]
            result = filedialog.asksaveasfilename(
                title=title,
                initialfile=default_name,
                filetypes=ft,
            )
            root.destroy()
            return result if result else None
        except Exception:
            return None
