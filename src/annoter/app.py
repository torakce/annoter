"""Application entry point: builds the QApplication and the MainWindow."""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from annoter.views.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    """Start Annoter. Returns the Qt exit code."""
    QCoreApplication.setOrganizationName("Annoter")
    QCoreApplication.setApplicationName("Annoter")

    args = list(argv) if argv is not None else list(sys.argv)
    app = QApplication.instance() or QApplication(args)

    win = MainWindow()
    win.show()

    if len(args) > 1:
        win.open_path(args[1])

    return app.exec()
