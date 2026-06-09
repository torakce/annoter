"""Smoke tests for the theme service."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.services.theme import Theme, apply, load_qss  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_load_qss_returns_non_empty_for_both_themes() -> None:
    assert load_qss(Theme.LIGHT).strip() != ""
    assert load_qss(Theme.DARK).strip() != ""


def test_apply_sets_application_stylesheet(qapp) -> None:
    apply(Theme.DARK, qapp)
    assert qapp.styleSheet().strip() != ""
    apply(Theme.LIGHT, qapp)
    # Reset to a known state for other tests.
    qapp.setStyleSheet("")
