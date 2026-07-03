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
