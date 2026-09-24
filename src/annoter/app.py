"""Application entry point: builds the QApplication and the MainWindow."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from annoter.services.fonts import register_ui_fonts
from annoter.views.main_window import MainWindow

SMOKE_FLAG = "--smoke-test"


def _icon_path() -> Path:
    """Resolve `resources/icons/app.ico` in dev and PyInstaller-frozen
    builds (same `_MEIPASS` pattern as `services.theme._themes_dir`);
    build.py bundles the whole `resources/icons/` directory."""
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return Path(base) / "resources" / "icons" / "app.ico"
    # src/annoter/app.py -> repo_root/resources/icons
    return Path(__file__).resolve().parents[2] / "resources" / "icons" / "app.ico"


def close_splash() -> None:
    """Close the packaged build's splash screen, if there is one.

    `pyi_splash` only exists inside a PyInstaller build made with a
    splash (see build.py); the bootloader shows it before Python starts,
    so it covers the unpacking of a one-file .exe. Anywhere else this is
    a no-op.
    """
    try:
        import pyi_splash  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 -- ImportError outside a build;
        return  # a build with no display to show the splash on raises too
    try:
        pyi_splash.close()
    except Exception:  # noqa: BLE001 -- never block start-up on it
        pass


def main(argv: list[str] | None = None) -> int:
    """Start Annoter. Returns the Qt exit code."""
    QCoreApplication.setOrganizationName("Annoter")
    QCoreApplication.setApplicationName("Annoter")

    args = list(argv) if argv is not None else list(sys.argv)
    # `--smoke-test` (used by `build.py --smoke` on a fresh build): start
    # normally, then quit as soon as the window is on screen.
    smoke = SMOKE_FLAG in args
    args = [a for a in args if a != SMOKE_FLAG]
    app = QApplication.instance() or QApplication(args)
    icon_path = _icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    # Before the first widget: the QSS template names these families.
    register_ui_fonts()

    win = MainWindow()
    win.show()
    close_splash()

    if len(args) > 1:
        win.open_path(args[1])

    if smoke:
        QTimer.singleShot(0, app.quit)
    return app.exec()
