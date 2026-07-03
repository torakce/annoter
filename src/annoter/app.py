"""Application entry point: builds the QApplication and the MainWindow."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from annoter.views.main_window import MainWindow


def _icon_path() -> Path:
    """Resolve `resources/icons/app.ico` in dev and PyInstaller-frozen
    builds (same `_MEIPASS` pattern as `services.theme._themes_dir`);
    build.py bundles the whole `resources/icons/` directory."""
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return Path(base) / "resources" / "icons" / "app.ico"
    # src/annoter/app.py -> repo_root/resources/icons
    return Path(__file__).resolve().parents[2] / "resources" / "icons" / "app.ico"


def main(argv: list[str] | None = None) -> int:
    """Start Annoter. Returns the Qt exit code."""
    QCoreApplication.setOrganizationName("Annoter")
    QCoreApplication.setApplicationName("Annoter")

    args = list(argv) if argv is not None else list(sys.argv)
    app = QApplication.instance() or QApplication(args)
    icon_path = _icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    win = MainWindow()
    win.show()

    if len(args) > 1:
        win.open_path(args[1])

    return app.exec()
