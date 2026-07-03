"""Unsaved-changes prompt on close / open-over (Discussion #1, item 11)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


def _make_dirty(win: MainWindow) -> None:
    item = RectangleItem(QRectF(10, 10, 50, 50))
    win._scene.push_add(item)


def test_clean_document_is_not_dirty(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        assert win._has_unsaved_changes() is False
        assert win.isWindowModified() is False
    finally:
        win.close()


def test_pushing_a_command_marks_dirty_and_title(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        _make_dirty(win)
        assert win._has_unsaved_changes() is True
        assert win.isWindowModified() is True
        assert "[*]" in win.windowTitle()
        # Undoing back to the clean point clears the flag.
        win._undo_group.activeStack().undo()
        assert win._has_unsaved_changes() is False
        assert win.isWindowModified() is False
    finally:
        win.close()


def test_close_event_cancel_keeps_window_open(
    qapp, sample_pdf: Path, monkeypatch
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        _make_dirty(win)
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *a, **k: QMessageBox.Cancel),
        )
        ev = QCloseEvent()
        win.closeEvent(ev)
        assert not ev.isAccepted()
        assert win._doc is not None  # document untouched
    finally:
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *a, **k: QMessageBox.Discard),
        )
        win.close()


def test_close_event_discard_closes(qapp, sample_pdf: Path, monkeypatch) -> None:
    win = MainWindow()
    win.open_path(sample_pdf)
    _make_dirty(win)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: QMessageBox.Discard),
    )
    ev = QCloseEvent()
    win.closeEvent(ev)
    assert ev.isAccepted()
    assert win._doc is None


def test_close_event_save_writes_file_then_closes(
    qapp, sample_pdf: Path, monkeypatch
) -> None:
    win = MainWindow()
    win.open_path(sample_pdf)
    _make_dirty(win)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: QMessageBox.Save),
    )
    ev = QCloseEvent()
    win.closeEvent(ev)
    assert ev.isAccepted()

    doc = fitz.open(str(sample_pdf))
    try:
        annots = list(doc[0].annots() or [])
        assert len(annots) == 1  # the rectangle was saved
    finally:
        doc.close()


def test_open_over_dirty_document_prompts(
    qapp, sample_pdf: Path, tmp_path: Path, monkeypatch
) -> None:
    other = tmp_path / "other.pdf"
    doc = fitz.open()
    doc.new_page(width=300, height=300)
    doc.save(str(other))
    doc.close()

    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        _make_dirty(win)

        # Cancel: the original document stays.
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *a, **k: QMessageBox.Cancel),
        )
        win.open_path(other)
        assert win._doc is not None
        assert win._doc.path == sample_pdf.resolve() or (
            win._doc.path.name == sample_pdf.name
        )

        # Discard: the new document replaces it.
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *a, **k: QMessageBox.Discard),
        )
        win.open_path(other)
        assert win._doc is not None
        assert win._doc.path.name == other.name
        assert win._has_unsaved_changes() is False
    finally:
        win.close()


def test_file_close_action_prompts_and_cancel_keeps_doc(
    qapp, sample_pdf: Path, monkeypatch
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        _make_dirty(win)
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *a, **k: QMessageBox.Cancel),
        )
        win._on_close_requested()
        assert win._doc is not None
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *a, **k: QMessageBox.Discard),
        )
        win._on_close_requested()
        assert win._doc is None
    finally:
        win.close()


def test_save_marks_stacks_clean(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        _make_dirty(win)
        assert win._has_unsaved_changes() is True
        assert win._save_to(sample_pdf) is True
        # _save_to closed the raw doc; the stacks are clean now, so a
        # subsequent close/open never prompts.
        assert win._has_unsaved_changes() is False
        win._reopen_after_save(sample_pdf)
        assert win._doc is not None
        assert win._has_unsaved_changes() is False
    finally:
        win.close()
