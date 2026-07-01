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
from annoter.views.items import RectangleItem  # noqa: E402
from annoter.views.items.base import AnnotationItem  # noqa: E402
from annoter.views.items.note import StickyNoteItem  # noqa: E402
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


def test_high_dpi_rerender_hysteresis(qapp, sample_pdf: Path) -> None:
    """Zooming past the threshold supersamples the page pixmap without
    changing its logical (scene) geometry; dropping back below the exit
    threshold returns to base DPI. In between, the scale is sticky."""
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        page = win._scene.page_item()
        assert page is not None
        base_rect = QRectF(page.boundingRect())
        assert win._render_scale == 1.0

        def assert_logical_geometry_kept() -> None:
            # PyMuPDF rounds pixel dimensions per render, so the logical
            # size may differ by up to one device pixel. Annotations are
            # unaffected (their coordinates never change).
            r = win._scene.page_item().boundingRect()
            assert r.width() == pytest.approx(base_rect.width(), abs=1.0)
            assert r.height() == pytest.approx(base_rect.height(), abs=1.0)

        win._view._apply_zoom(2.5)  # above threshold (2.0)
        assert win._render_scale == 2.0
        pm = win._scene.page_item().pixmap()
        assert pm.devicePixelRatio() == pytest.approx(2.0)
        # Logical geometry unchanged -> child annotations stay put.
        assert_logical_geometry_kept()

        win._view._apply_zoom(1.7)  # between exit (1.5) and threshold
        assert win._render_scale == 2.0  # hysteresis: stays high

        win._view._apply_zoom(1.0)  # below exit
        assert win._render_scale == 1.0
        pm = win._scene.page_item().pixmap()
        assert pm.devicePixelRatio() == pytest.approx(1.0)
        assert_logical_geometry_kept()
    finally:
        win._on_close()
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


def _page_notes(win: MainWindow) -> list[StickyNoteItem]:
    page = win._scene.page_item()
    return [
        c for c in page.childItems() if isinstance(c, StickyNoteItem)
    ]


def test_sticky_note_placement_and_commit(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        win._tool_controller.set_tool(Tool.STICKY_NOTE)
        win._on_note_placement(QPointF(100, 100))
        assert win._note_editor is not None
        win._note_editor._edit.setPlainText("Inspect weld")
        win._commit_note_editor()
        assert win._note_editor is None
        notes = _page_notes(win)
        assert len(notes) == 1
        assert notes[0].text() == "Inspect weld"
        # Committing a placement returns to the Select tool.
        assert win._tool_controller.tool() is Tool.SELECT
    finally:
        win._on_close()
        win.close()


def test_sticky_note_empty_rolls_back(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        win._tool_controller.set_tool(Tool.STICKY_NOTE)
        win._on_note_placement(QPointF(120, 140))
        win._note_editor._edit.setPlainText("   ")
        win._commit_note_editor()
        assert _page_notes(win) == []
    finally:
        win._on_close()
        win.close()


def test_format_painter_copies_style_to_clicked_target(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        source = _push_rect(win, QRectF(0, 0, 10, 10))
        source.set_color(QColor("#123456"))
        source.set_stroke(5.0)
        target = _push_rect(win, QRectF(50, 50, 10, 10))

        source.setSelected(True)
        win.act_format_painter.setChecked(True)
        assert win._tool_controller.tool() is Tool.FORMAT_PAINTER
        source.setSelected(False)

        win._on_format_paint_requested(target)
        assert target.color().name() == "#123456"
        assert target.stroke() == pytest.approx(5.0)
        # Sticky: stays active for further clicks until toggled off.
        assert win._tool_controller.tool() is Tool.FORMAT_PAINTER

        stack = win._undo_group.activeStack()
        stack.undo()
        assert target.stroke() != pytest.approx(5.0)
    finally:
        win._on_close()
        win.close()


def test_format_painter_requires_single_selection(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        win.act_format_painter.setChecked(True)
        assert win.act_format_painter.isChecked() is False
        assert win._tool_controller.tool() is Tool.SELECT

        a = _push_rect(win, QRectF(0, 0, 10, 10))
        b = _push_rect(win, QRectF(20, 20, 10, 10))
        a.setSelected(True)
        b.setSelected(True)
        win.act_format_painter.setChecked(True)
        assert win.act_format_painter.isChecked() is False
    finally:
        win._on_close()
        win.close()


def test_switching_tool_cancels_format_painter(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        source = _push_rect(win, QRectF(0, 0, 10, 10))
        source.setSelected(True)
        win.act_format_painter.setChecked(True)
        assert win._tool_controller.tool() is Tool.FORMAT_PAINTER

        win._tool_controller.set_tool(Tool.RECTANGLE)
        assert win.act_format_painter.isChecked() is False
        assert win._format_paint_style is None
    finally:
        win._on_close()
        win.close()


def test_selection_toolbar_shows_and_hides_with_selection(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        assert win._selection_toolbar.isHidden()

        item = _push_rect(win, QRectF(10, 10, 30, 30))
        item.setSelected(True)
        assert not win._selection_toolbar.isHidden()

        item.setSelected(False)
        assert win._selection_toolbar.isHidden()
    finally:
        win._on_close()
        win.close()


def test_selection_toolbar_duplicate_and_delete(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        item = _push_rect(win, QRectF(10, 10, 30, 30))
        item.setSelected(True)

        win._selection_toolbar.duplicateClicked.emit()
        page = win._scene.page_item()
        annots = [c for c in page.childItems() if isinstance(c, RectangleItem)]
        assert len(annots) == 2

        for it in annots:
            it.setSelected(True)
        win._selection_toolbar.deleteClicked.emit()
        remaining = [
            c for c in page.childItems() if isinstance(c, RectangleItem)
        ]
        assert remaining == []
    finally:
        win._on_close()
        win.close()


def test_selection_toolbar_reflects_item_style(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        item = _push_rect(win, QRectF(10, 10, 30, 30))
        item.set_color(QColor("#00FF00"))
        item.set_stroke(7.0)
        item.setSelected(True)
        assert win._selection_toolbar._stroke_btn.text() == "7 px"
    finally:
        win._on_close()
        win.close()


def test_group_and_ungroup_actions(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        a = _push_rect(win, QRectF(0, 0, 10, 10))
        b = _push_rect(win, QRectF(50, 50, 10, 10))
        a.setSelected(True)
        b.setSelected(True)

        win.act_group.trigger()
        assert win._scene.group_of(a) == {a, b}

        win.act_ungroup.trigger()
        assert win._scene.group_of(a) is None
    finally:
        win._on_close()
        win.close()


def test_align_selection_via_action(qapp, sample_pdf: Path) -> None:
    from annoter.controllers.align import AlignMode

    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        a = _push_rect(win, QRectF(0, 0, 10, 10))
        b = _push_rect(win, QRectF(0, 0, 10, 10))
        b.setPos(50, 80)
        a.setSelected(True)
        b.setSelected(True)
        win._align_selection(AlignMode.LEFT)
        assert a.pos().x() == pytest.approx(b.pos().x())
        # Undoable as a single step.
        stack = win._undo_group.activeStack()
        stack.undo()
        assert b.pos().x() == pytest.approx(50)
    finally:
        win._on_close()
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
