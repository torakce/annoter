"""Light / dark theme switching via QSS stylesheets.

The QSS files live in `resources/themes/`. We resolve the path relative
to the package so the bundled PyInstaller build (M5) can ship them too.
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

from PySide6.QtWidgets import QApplication


class Theme(Enum):
    LIGHT = "light"
    DARK = "dark"


def _themes_dir() -> Path:
    """Resolve `resources/themes/` in dev and PyInstaller-frozen builds."""
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return Path(base) / "resources" / "themes"
    # src/annoter/services/theme.py -> repo_root/resources/themes
    return Path(__file__).resolve().parents[3] / "resources" / "themes"


def load_qss(theme: Theme) -> str:
    path = _themes_dir() / f"{theme.value}.qss"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def apply(theme: Theme, app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None:
        return
    app.setStyleSheet(load_qss(theme))
