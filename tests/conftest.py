"""Shared test fixtures.

Qt tests run under QT_QPA_PLATFORM=offscreen so they work in CI
without a display server.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(autouse=True)
def _auto_discard_unsaved_changes(monkeypatch):
    """Answer the unsaved-changes prompt with Discard by default.

    Most tests push undo commands and then call `win.close()` in a
    `finally:`; without this, the modal QMessageBox would block the
    suite under the offscreen platform. Tests that exercise the prompt
    itself re-patch `QMessageBox.warning` with their own answer.
    """
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:
        yield
        return
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: QMessageBox.Discard),
    )
    yield


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path_factory, monkeypatch):
    """Keep QSettings out of the developer's real preferences.

    MainWindow saves window state, theme and the color palette on close,
    and restores them on start. Without this, every test run would write
    into the user's registry (Windows) or ~/.config, and a test would
    restore whatever an earlier one left behind. Each test gets a fresh
    INI store; production code reaches it through `QSettings()`, which
    follows the default format and the application names set here. The
    start page's thumbnail cache is redirected the same way.
    """
    try:
        from PySide6.QtCore import QCoreApplication, QSettings
    except ImportError:
        yield
        return
    root = tmp_path_factory.mktemp("settings")
    monkeypatch.setenv("ANNOTER_CACHE_DIR", str(root / "cache"))
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(root))
    QCoreApplication.setOrganizationName("AnnoterTest")
    QCoreApplication.setApplicationName(f"AnnoterTest_{root.name}")
    yield
