"""End-to-end MainWindow wiring tests under offscreen Qt platform."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import fitz
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication, QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.commands import AddAnnotationCommand  # noqa: E402
from annoter.controllers.tools import Tool  # noqa: E402
from annoter.views.items import EllipseItem, RectangleItem  # noqa: E402
from annoter.views.items.base import AnnotationItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    QCoreApplication.setOrganizationName("AnnoterTest")
    QCoreApplication.setApplicationName("AnnoterTest")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=842, height=595)
    doc.save(str(path))
    doc.close()
    return path


def test_main_window_opens_pdf(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        assert win._doc is not None
        assert win._doc.page_count == 3
        assert win._page_index == 0
        assert win._scene.page_item() is not None
    finally:
        win._on_close()
        win.close()


def test_page_navigation(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        assert win._page_index == 0
        win._goto_next_page()
        assert win._page_index == 1
        win._goto_next_page()
        assert win._page_index == 2
        win._goto_next_page()  # clamps at last page
        assert win._page_index == 2
        win._goto_prev_page()
        assert win._page_index == 1
        win._goto_last_page()
        assert win._page_index == 2
    finally:
        win._on_close()
        win.close()


def test_zoom_actions(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        win._view.zoom_to_actual()
        assert win._view.zoom() == pytest.approx(1.0)
        before = win._view.zoom()
        win._view.zoom_in()
        assert win._view.zoom() > before
        win._view.zoom_out()
        assert win._view.zoom() == pytest.approx(before)
    finally:
        win._on_close()
        win.close()


def test_rotation_changes_pixmap_orientation(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        item = win._scene.page_item()
        assert item is not None
        landscape = item.boundingRect()
        win._rotate(90)
        portrait = win._scene.page_item().boundingRect()
        # 90 deg rotation swaps width and height.
        assert portrait.width() == pytest.approx(landscape.height())
        assert portrait.height() == pytest.approx(landscape.width())
    finally:
        win._on_close()
        win.close()


def test_close_clears_state(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        assert win._doc is not None
        win._on_close()
        assert win._doc is None
        assert win._scene.page_item() is None
    finally:
        win.close()


# ----------------------------------------------------------------------
# M2 wiring
# ----------------------------------------------------------------------


def _push_rect(win: MainWindow, rect: QRectF) -> RectangleItem:
    item = RectangleItem(rect)
    page = win._scene.page_item()
    assert page is not None
    stack = win._undo_group.activeStack()
    assert stack is not None
    stack.push(AddAnnotationCommand(win._scene, page, item))
    return item


def test_tool_palette_and_annotation_list_present(qapp) -> None:
    win = MainWindow()
    try:
        assert win._tool_palette is not None
        assert win._annotation_list is not None
    finally:
        win.close()


def test_tool_controller_changes_propagate(qapp) -> None:
    win = MainWindow()
    try:
        win._tool_controller.set_tool(Tool.RECTANGLE)
        assert win._tool_controller.tool() is Tool.RECTANGLE
        win._tool_controller.set_color(QColor("#abcdef"))
        assert win._tool_controller.color().name() == "#abcdef"
    finally:
        win.close()


def test_add_then_undo_then_redo(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        item = _push_rect(win, QRectF(10, 10, 30, 30))
        assert item.scene() is win._scene
        win._undo_group.activeStack().undo()
        assert item.scene() is None
        win._undo_group.activeStack().redo()
        assert item.scene() is win._scene
    finally:
        win._on_close()
        win.close()


def test_delete_selection_action(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        item = _push_rect(win, QRectF(0, 0, 10, 10))
        item.setSelected(True)
        win._delete_selected()
        assert item.scene() is None
        # And it round-trips via the page's undo stack.
        win._undo_group.activeStack().undo()
        assert item.scene() is win._scene
    finally:
        win._on_close()
        win.close()


def test_select_all_selects_only_annotations(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        a = _push_rect(win, QRectF(0, 0, 10, 10))
        b = _push_rect(win, QRectF(20, 20, 10, 10))
        win._select_all()
        selected = {
            it for it in win._scene.selectedItems()
            if isinstance(it, AnnotationItem)
        }
        assert selected == {a, b}
    finally:
        win._on_close()
        win.close()


def test_page_switch_preserves_annotations(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        item = _push_rect(win, QRectF(5, 5, 50, 50))
        assert item.scene() is win._scene

        win._goto_next_page()
        # Item is detached from the scene while we are off-page.
        assert item.scene() is None
        # And the new page has its own (empty) bucket and stack.
        assert win._page_index == 1

        win._show_page(0)
        # Item is reattached.
        assert item.scene() is win._scene
        assert item.parentItem() is win._scene.page_item()
    finally:
        win._on_close()
        win.close()


def test_per_page_undo_stacks(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        stack0 = win._undo_group.activeStack()
        _push_rect(win, QRectF(0, 0, 10, 10))
        assert stack0.count() == 1

        win._goto_next_page()
        stack1 = win._undo_group.activeStack()
        assert stack1 is not stack0
        assert stack1.count() == 0

        win._show_page(0)
        assert win._undo_group.activeStack() is stack0
        assert stack0.count() == 1
    finally:
        win._on_close()
        win.close()


def test_text_empty_rollback_does_not_push(qapp, sample_pdf: Path) -> None:
    """Empty text after edit should be discarded without polluting undo."""
    from annoter.views.items import TextAnnotationItem

    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        scene = win._scene
        page = scene.page_item()
        assert page is not None
        stack = win._undo_group.activeStack()
        assert stack is not None
        before = stack.count()

        # Manually drive what the dispatcher does on TEXT-tool click.
        item = TextAnnotationItem(QPointF(20, 20))
        item.setParentItem(page)
        item.editingFinished.connect(
            lambda txt, it=item: scene._on_text_edit_finished(it, txt)
        )
        item.editingFinished.emit("")  # empty -> rollback

        assert item.scene() is None
        assert stack.count() == before
    finally:
        win._on_close()
        win.close()
