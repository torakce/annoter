"""Welcome screen / home page (Discussion #1, item 12)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QCoreApplication, QRectF, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.welcome_screen import WelcomeScreen  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path):
    """QSettings needs org/app names to persist anything (app.main sets
    them in production; tests must too) -- and isolation keeps the
    developer's real recent-files list out of assertions."""
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    QCoreApplication.setOrganizationName("AnnoterTest")
    QCoreApplication.setApplicationName(f"AnnoterTest_{tmp_path.name}")
    yield


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


def test_welcome_shown_without_document(qapp) -> None:
    win = MainWindow()
    try:
        assert win._central.currentWidget() is win._welcome
    finally:
        win.close()


def test_open_switches_to_view_and_close_back(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        assert win._central.currentWidget() is win._view
        win._on_close()
        assert win._central.currentWidget() is win._welcome
    finally:
        win.close()


def test_recent_list_populates_and_thumbnails_render(
    qapp, sample_pdf: Path
) -> None:
    ws = WelcomeScreen()
    ws.set_recent([str(sample_pdf)])
    assert ws._list.count() == 1
    while ws._pending:
        ws._render_next()
    assert not ws._list.item(0).icon().isNull()


def test_missing_file_gets_placeholder_not_crash(qapp, tmp_path: Path) -> None:
    ws = WelcomeScreen()
    ws.set_recent([str(tmp_path / "gone.pdf")])
    while ws._pending:
        ws._render_next()  # must not raise
    assert ws._list.count() == 1


def test_click_recent_opens_document(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win._recent.add(sample_pdf)
        win._welcome.set_recent(win._recent.list())
        item = win._welcome._list.item(0)
        win._welcome._on_item_open(item)
        assert win._doc is not None
        assert win._doc.path.name == sample_pdf.name
    finally:
        win.close()


def test_remove_recent_signal_updates_list(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win._recent.add(sample_pdf)
        assert str(sample_pdf.resolve()) in win._recent.list()
        win._welcome.removePathRequested.emit(str(sample_pdf.resolve()))
        assert str(sample_pdf.resolve()) not in win._recent.list()
    finally:
        win.close()


def test_blank_document_opens_untitled_a4(qapp) -> None:
    win = MainWindow()
    try:
        recent_before = win._recent.list()
        win._new_blank_document()
        assert win._doc is not None
        assert win._is_untitled is True
        assert win._doc.page_count == 1
        w, h = win._doc.page_size_pt(0)
        assert (round(w), round(h)) == (595, 842)
        # The scratch file never enters the recent list.
        assert win._recent.list() == recent_before
        assert win._central.currentWidget() is win._view
    finally:
        win._on_close()
        win.close()


def test_blank_document_is_annotatable_and_saveable(
    qapp, tmp_path: Path
) -> None:
    win = MainWindow()
    try:
        win._new_blank_document()
        win._scene.push_add(RectangleItem(QRectF(10, 10, 80, 40)))
        assert win._has_unsaved_changes() is True

        target = tmp_path / "was_blank.pdf"
        assert win._save_to(target) is True
        win._reopen_after_save(target)
        assert win._is_untitled is False
        assert win._doc.path.name == "was_blank.pdf"

        doc = fitz.open(str(target))
        try:
            assert len(list(doc[0].annots() or [])) == 1
        finally:
            doc.close()
    finally:
        win._on_close()
        win.close()
