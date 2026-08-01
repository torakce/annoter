"""Regression tests: typing inside an inline text edit must not be
hijacked by PdfView's pan-on-space or by app-wide QAction shortcuts
(Delete/Backspace/Ctrl+A/...) bound on the same window.

Space used to be swallowed unconditionally by the space-to-pan
handler, and Backspace/Delete/Ctrl+A used to win the Qt shortcut race
against the focused QGraphicsTextItem, deleting or reselecting
annotations instead of editing their text.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QAction, QKeySequence  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from annoter.views.items.text import TextAnnotationItem  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402
from annoter.views.pdf_view import PdfView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Harness:
    """A window hosting the view plus the same kind of app-wide
    shortcuts MainWindow registers, so the ShortcutOverride race is
    actually exercised (a bare PdfView has no competing shortcuts)."""

    def __init__(self, qapp) -> None:
        self.window = QWidget()
        layout = QVBoxLayout(self.window)
        self.scene = PdfScene()
        self.scene.set_page_pixmap(QPixmap(400, 400))
        self.view = PdfView()
        self.view.setScene(self.scene)
        layout.addWidget(self.view)

        self.delete_calls = 0
        self.select_all_calls = 0

        act_delete = QAction(self.window)
        act_delete.setShortcuts(
            [QKeySequence(Qt.Key_Delete), QKeySequence(Qt.Key_Backspace)]
        )
        act_delete.triggered.connect(self._on_delete)
        self.window.addAction(act_delete)

        act_select_all = QAction(self.window)
        act_select_all.setShortcut(QKeySequence.SelectAll)
        act_select_all.triggered.connect(self._on_select_all)
        self.window.addAction(act_select_all)

        self.item = TextAnnotationItem(QPointF(20, 20), "ab")
        self.scene.addItem(self.item)
        self.item.setParentItem(self.scene.page_item())
        self.item.setSelected(True)

        self.window.resize(400, 400)
        self.window.show()
        self.view.setFocus()
        qapp.processEvents()

    def _on_delete(self) -> None:
        self.delete_calls += 1
        self.scene.removeItem(self.item)

    def _on_select_all(self) -> None:
        self.select_all_calls += 1

    def close(self) -> None:
        self.window.hide()
        self.window.deleteLater()


@pytest.fixture()
def harness(qapp):
    h = _Harness(qapp)
    yield h
    h.close()


def test_space_is_typed_not_swallowed_for_pan(qapp, harness) -> None:
    harness.item.begin_edit()
    qapp.processEvents()
    QTest.keyClick(harness.view, Qt.Key_Space)
    qapp.processEvents()
    assert harness.item.text() == "ab "
    assert harness.view._panning is False


def test_backspace_deletes_a_character_not_the_annotation(
    qapp, harness
) -> None:
    harness.item.begin_edit()
    qapp.processEvents()
    QTest.keyClick(harness.view, Qt.Key_Backspace)
    qapp.processEvents()
    assert harness.item.text() == "a"
    assert harness.delete_calls == 0
    assert harness.item.scene() is harness.scene


def test_ctrl_a_selects_text_not_all_annotations(qapp, harness) -> None:
    harness.item.begin_edit()
    qapp.processEvents()
    QTest.keyClick(harness.view, Qt.Key_A, Qt.ControlModifier)
    qapp.processEvents()
    assert harness.select_all_calls == 0


def test_arrow_keys_move_cursor_not_the_annotation(qapp, harness) -> None:
    harness.item.begin_edit()
    qapp.processEvents()
    pos_before = QPointF(harness.item.pos())
    QTest.keyClick(harness.view, Qt.Key_Left)
    qapp.processEvents()
    assert harness.item.pos() == pos_before


def test_backspace_still_deletes_annotation_outside_edit_mode(
    qapp, harness
) -> None:
    # Sanity check: the ShortcutOverride guard must only kick in while
    # actually editing text, not steal Backspace/Delete permanently.
    QTest.keyClick(harness.view, Qt.Key_Backspace)
    qapp.processEvents()
    assert harness.delete_calls == 1


def test_can_insert_in_the_middle_of_existing_text(qapp, harness) -> None:
    # Type-to-edit ("select the item, just start typing") used to
    # re-enter start_typing() on *every* keystroke, which force-moved
    # the cursor to the end each time and made Left/Right arrows nudge
    # the annotation instead of the cursor -- so inserting a character
    # anywhere but the very end was impossible.
    harness.item.set_text("")
    harness.item.setSelected(True)
    QTest.keyClick(harness.view, Qt.Key_A)
    qapp.processEvents()
    QTest.keyClick(harness.view, Qt.Key_C)
    qapp.processEvents()
    QTest.keyClick(harness.view, Qt.Key_Home)
    qapp.processEvents()
    QTest.keyClick(harness.view, Qt.Key_Right)
    qapp.processEvents()
    QTest.keyClick(harness.view, Qt.Key_B)
    qapp.processEvents()
    assert harness.item.text() == "abc"
