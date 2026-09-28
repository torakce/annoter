"""Right-click menu with icon rows and one-step stacking order (Lot J)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import fitz
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPoint, QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QUndoStack  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
)

from annoter.controllers.commands import ReorderCommand  # noqa: E402
from annoter.controllers.stacking import (  # noqa: E402
    LOWER,
    RAISE,
    TO_BACK,
    TO_FRONT,
    apply_stack,
    can_restack,
    page_stack,
    restacked,
)
from annoter.model.styles import END_STYLE_LABELS, EndStyle  # noqa: E402
from annoter.views.context_menu import (  # noqa: E402
    AnnotationContextMenu,
    IconCommand,
)
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.items.shapes import CloudItem, RectangleItem  # noqa: E402
from annoter.views.line_icons import line_icon  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=842, height=595)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def win(qapp, sample_pdf: Path):
    w = MainWindow()
    w.open_path(sample_pdf)
    yield w
    w._on_close()
    w.close()


def _page(n: int, rect=QRectF(0, 0, 50, 50)):
    scene = QGraphicsScene()
    page = QGraphicsPixmapItem()
    scene.addItem(page)
    items = []
    for i in range(n):
        it = RectangleItem(QRectF(rect))
        it.setParentItem(page)
        it.tag = i
        items.append(it)
    return scene, page, items


def _tags(page) -> list[int]:
    return [it.tag for it in page_stack(page)]


# ----------------------------------------------------------------------
# stacking logic
# ----------------------------------------------------------------------
def test_page_stack_is_bottom_first(qapp) -> None:
    _scene, page, items = _page(3)
    assert page_stack(page) == items
    assert page_stack(None) == []


def test_apply_stack_reorders_siblings(qapp) -> None:
    scene, page, items = _page(4)
    apply_stack([items[2], items[0], items[3], items[1]])
    assert _tags(page) == [2, 0, 3, 1]
    # Scene hit-testing agrees: topmost first.
    hits = [it.tag for it in scene.items(QPointF(10, 10)) if hasattr(it, "tag")]
    assert hits == [1, 3, 0, 2]


def test_raise_and_lower_one_overlapping_step(qapp) -> None:
    _scene, page, items = _page(5)
    order = page_stack(page)
    assert [i.tag for i in restacked(order, [items[0]], RAISE)] == [1, 0, 2, 3, 4]
    assert [i.tag for i in restacked(order, [items[4]], LOWER)] == [0, 1, 2, 4, 3]
    # Already at the top / bottom: nothing to do.
    assert restacked(order, [items[4]], RAISE) == order
    assert restacked(order, [items[0]], LOWER) == order


def test_one_step_skips_annotations_that_do_not_overlap(qapp) -> None:
    _scene, page, items = _page(3)
    items[1].set_rect(QRectF(500, 500, 20, 20))  # far away
    order = page_stack(page)
    # One click lifts item 0 above item 2, the next one it overlaps.
    assert [i.tag for i in restacked(order, [items[0]], RAISE)] == [1, 2, 0]
    assert can_restack(page, [items[0]], RAISE)
    assert not can_restack(page, [items[1]], RAISE)


def test_front_and_back_keep_relative_order(qapp) -> None:
    _scene, page, items = _page(5)
    order = page_stack(page)
    sel = [items[3], items[1]]
    assert [i.tag for i in restacked(order, sel, TO_FRONT)] == [0, 2, 4, 1, 3]
    assert [i.tag for i in restacked(order, sel, TO_BACK)] == [1, 3, 0, 2, 4]


def test_multi_selection_moves_as_a_block(qapp) -> None:
    _scene, page, items = _page(5)
    order = page_stack(page)
    sel = [items[1], items[2]]
    assert [i.tag for i in restacked(order, sel, RAISE)] == [0, 3, 1, 2, 4]
    assert [i.tag for i in restacked(order, sel, LOWER)] == [1, 2, 0, 3, 4]


def test_horizontal_line_overlaps_what_it_crosses(qapp) -> None:
    scene, page, items = _page(1)
    line = LineItem(QPointF(-10, 25), QPointF(80, 25))
    line.setParentItem(page)
    line.tag = 9
    assert [i.tag for i in restacked(page_stack(page), [line], LOWER)] == [9, 0]


def test_reorder_command_undo_merge_and_obsolete(qapp) -> None:
    _scene, page, items = _page(5)
    stack = QUndoStack()

    def push(move: str) -> None:
        old = page_stack(page)
        stack.push(ReorderCommand(page, old, restacked(old, [items[4]], move)))

    for _ in range(3):
        push(LOWER)
    assert _tags(page) == [0, 4, 1, 2, 3]
    assert stack.count() == 1  # three clicks, one undo step
    stack.undo()
    assert _tags(page) == [0, 1, 2, 3, 4]
    stack.redo()
    assert _tags(page) == [0, 4, 1, 2, 3]
    for _ in range(3):
        push(RAISE)
    # Back where it started: the step drops out of the history.
    assert _tags(page) == [0, 1, 2, 3, 4]
    assert stack.count() == 0


def test_new_annotation_still_lands_on_top_after_a_reorder(win) -> None:
    page = win._scene.page_item()
    a = RectangleItem(QRectF(10, 10, 50, 50))
    b = RectangleItem(QRectF(20, 20, 50, 50))
    win._scene.push_add(a)
    win._scene.push_add(b)
    b.setSelected(True)
    assert win._restack_selection(TO_BACK)
    c = RectangleItem(QRectF(30, 30, 50, 50))
    win._scene.push_add(c)
    assert page_stack(page) == [b, a, c]


def test_stacking_order_is_saved(win, tmp_path: Path) -> None:
    a = RectangleItem(QRectF(10, 10, 50, 50))
    b = CloudItem(QRectF(20, 20, 50, 50))
    win._scene.push_add(a)
    win._scene.push_add(b)
    b.setSelected(True)
    win._restack_selection(TO_BACK)
    target = tmp_path / "out.pdf"
    assert win._save_to(target)
    doc = fitz.open(str(target))
    kinds = [a.type[1] for a in doc[0].annots()]
    doc.close()
    # The cloud (a Polygon annotation) is now written first: drawn first,
    # i.e. underneath the rectangle, in any viewer.
    assert kinds.index("Polygon") < kinds.index("Square")


def test_shortcuts_for_one_step(win) -> None:
    assert win.act_raise.shortcut().toString() == "Ctrl+]"
    assert win.act_lower.shortcut().toString() == "Ctrl+["
    # Listed in the Edit menu, so the command palette finds them too.
    labels = [e.label for e in win._command_entries()]
    assert "Bring Forward" in labels and "Send Backward" in labels


# ----------------------------------------------------------------------
# the menu widget
# ----------------------------------------------------------------------
def test_icon_row_buttons_close_or_keep_the_menu(qapp) -> None:
    ran: list[str] = []
    menu = AnnotationContextMenu()
    icon = line_icon("cut", menu.palette().text().color())
    menu.add_icon_row(
        [
            IconCommand("once", icon, "Once", lambda: ran.append("once")),
            None,
            IconCommand(
                "again", icon, "Again", lambda: ran.append("again"),
                keep_open=True,
            ),
            IconCommand("off", icon, "Off", lambda: None, enabled=False),
        ],
        caption="Row",
    )
    menu.popup(QPoint(100, 100))
    assert menu.isVisible()
    menu.trigger("again")
    menu.trigger("again")
    assert menu.isVisible()
    assert not menu.button("off").isEnabled()
    menu.trigger("once")
    assert not menu.isVisible()
    assert ran == ["again", "again", "once"]
    assert menu.row_captions() == ["Row"]
    assert menu.button("once").toolTip() == "Once"


# ----------------------------------------------------------------------
# main window menus
# ----------------------------------------------------------------------
def _menu_at(win, item) -> AnnotationContextMenu:
    from annoter.controllers.geometry import item_scene_rect

    center = item_scene_rect(item).center()
    menu = win._build_context_menu(center)
    assert menu is not None
    return menu


def test_shape_menu_has_icon_rows_and_no_color_or_stroke(win) -> None:
    rect = RectangleItem(QRectF(10, 10, 60, 40))
    win._scene.push_add(rect)
    menu = _menu_at(win, rect)
    assert rect.isSelected()  # the right-click selected it
    keys = menu.keys()
    for key in ("cut", "copy", "paste", "duplicate", "delete"):
        assert key in keys
    for key in ("to_back", "lower", "raise", "to_front"):
        assert key in keys
    assert {"outline:straight", "outline:cloud", "fill"} <= set(keys)
    assert menu.button("outline:straight").isChecked()
    texts = menu.entry_texts()
    assert not any("Color" in t or "Stroke" in t for t in texts)
    assert "Properties" in texts
    assert not menu.button("paste").isEnabled()  # empty clipboard


def test_delete_from_the_top_row(win) -> None:
    rect = RectangleItem(QRectF(10, 10, 60, 40))
    win._scene.push_add(rect)
    menu = _menu_at(win, rect)
    menu.trigger("delete")
    assert rect.scene() is None


def test_line_menu_offers_both_ends_as_icons(win) -> None:
    arrow = ArrowItem(QPointF(50, 50), QPointF(250, 50))
    win._scene.push_add(arrow)
    menu = _menu_at(win, arrow)
    assert menu.row_captions() == ["", "Order", "Start", "End"]
    starts = [k for k in menu.keys() if k.startswith("start:")]
    ends = [k for k in menu.keys() if k.startswith("end:")]
    assert len(starts) == len(ends) == len(END_STYLE_LABELS)
    assert menu.button("end:" + EndStyle.OPEN_ARROW.value).isChecked()
    assert menu.button("start:" + EndStyle.NONE.value).isChecked()
    menu.trigger("start:" + EndStyle.CLOSED_ARROW.value)
    assert arrow.start_end() is EndStyle.CLOSED_ARROW
    assert "Add Bend Point" in menu.entry_texts()


def test_plain_line_end_icon_turns_it_into_an_arrow(win) -> None:
    line = LineItem(QPointF(50, 150), QPointF(250, 150))
    win._scene.push_add(line)
    menu = _menu_at(win, line)
    menu.trigger("end:" + EndStyle.CLOSED_ARROW.value)
    page = win._scene.page_item()
    arrows = [c for c in page.childItems() if isinstance(c, ArrowItem)]
    assert len(arrows) == 1
    assert arrows[0].end_end() is EndStyle.CLOSED_ARROW


def test_lower_keeps_the_menu_open_and_walks_down_the_pile(win) -> None:
    page = win._scene.page_item()
    pile = [RectangleItem(QRectF(10 + i, 10 + i, 80, 80)) for i in range(5)]
    for it in pile:
        win._scene.push_add(it)
    top = pile[-1]
    menu = _menu_at(win, top)
    menu.popup(QPoint(50, 50))
    stack = win._undo_group.activeStack()
    before = stack.count()
    for _ in range(3):
        menu.trigger("lower")
    assert menu.isVisible()
    assert page_stack(page).index(top) == 1
    assert menu.button("raise").isEnabled()
    menu.trigger("lower")
    assert page_stack(page).index(top) == 0
    assert not menu.button("lower").isEnabled()  # bottom reached
    assert not menu.button("to_back").isEnabled()
    menu.close()
    # Four clicks, one undo step.
    assert stack.count() == before + 1
    stack.undo()
    assert page_stack(page)[-1] is top


def test_multi_selection_menu_aligns_and_groups(win) -> None:
    a = RectangleItem(QRectF(10, 10, 30, 30))
    b = RectangleItem(QRectF(100, 50, 30, 30))
    c = RectangleItem(QRectF(200, 90, 30, 30))
    for it in (a, b, c):
        win._scene.push_add(it)
    for it in (a, b, c):
        it.setSelected(True)
    menu = _menu_at(win, a)
    assert "Align" in menu.row_captions()
    assert "align:align-left" in menu.keys()
    assert "align:distribute-h" in menu.keys()
    assert "Group" in menu.entry_texts()
    menu.trigger("align:align-left")
    assert a.pos().x() + 10 == pytest.approx(b.pos().x() + 100)


def test_empty_area_menu(win) -> None:
    menu = win._build_context_menu(QPointF(700, 500))
    assert menu.keys() == []
    assert menu.entry_texts() == ["Select All"]
    rect = RectangleItem(QRectF(10, 10, 30, 30))
    win._scene.push_add(rect)
    rect.setSelected(True)
    win._copy_selected()
    rect.setSelected(False)
    menu = win._build_context_menu(QPointF(700, 500))
    assert menu.entry_texts() == ["Paste", "Select All"]


def test_no_floating_selection_bar(win) -> None:
    rect = RectangleItem(QRectF(10, 10, 60, 40))
    win._scene.push_add(rect)
    rect.setSelected(True)
    assert not hasattr(win, "_selection_toolbar")


def test_restacking_repaints_at_once(qapp) -> None:
    """stackBefore alone does not schedule a repaint: the new order used
    to show only once something else redrew the canvas."""
    from PySide6.QtWidgets import QGraphicsView

    painted: list[int] = []

    class Probe(RectangleItem):
        def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
            painted.append(self.tag)
            super().paint(painter, option, widget)

    scene = QGraphicsScene(0, 0, 400, 400)
    page = QGraphicsPixmapItem()
    scene.addItem(page)
    items = []
    for i in range(3):
        it = Probe(QRectF(10 * i, 10 * i, 80, 80))
        it.tag = i
        it.setParentItem(page)
        items.append(it)
    view = QGraphicsView(scene)
    view.resize(420, 420)
    view.show()
    for _ in range(3):
        QApplication.processEvents()
    painted.clear()
    old = page_stack(page)
    stack = QUndoStack()
    stack.push(ReorderCommand(page, old, restacked(old, [items[2]], LOWER)))
    for _ in range(3):
        QApplication.processEvents()
    view.close()
    assert set(painted) == {0, 1, 2}


def test_add_bend_from_an_endpoint_lands_mid_segment(win) -> None:
    arrow = ArrowItem(QPointF(50, 50), QPointF(250, 50))
    win._scene.push_add(arrow)
    at_end = arrow.mapToScene(QPointF(250, 50))
    menu = win._build_context_menu(at_end)
    menu.entry("Add Bend Point").trigger()
    assert arrow.bends() == [QPointF(150, 50)]


# ----------------------------------------------------------------------
# right-click on a selected annotation hidden under another (2026-09-28)
# ----------------------------------------------------------------------
def test_right_click_targets_the_selection_under_another_item(win) -> None:
    under = RectangleItem(QRectF(100, 100, 200, 150))
    top = RectangleItem(QRectF(200, 150, 200, 150))
    for it in (under, top):
        it.set_fill_enabled(True)
        win._scene.push_add(it)
    for it in win._scene.selectedItems():
        it.setSelected(False)
    under.setSelected(True)
    overlap = under.mapToScene(QPointF(250, 200))
    menu = win._build_context_menu(overlap)
    assert under.isSelected() and not top.isSelected()
    menu.trigger("delete")
    assert under.scene() is None and top.scene() is win._scene


def test_right_click_on_a_hidden_selected_line_keeps_its_rows(win) -> None:
    line = ArrowItem(QPointF(50, 300), QPointF(400, 300))
    cover = RectangleItem(QRectF(150, 250, 100, 100))
    cover.set_fill_enabled(True)
    win._scene.push_add(line)
    win._scene.push_add(cover)
    for it in win._scene.selectedItems():
        it.setSelected(False)
    line.setSelected(True)
    menu = win._build_context_menu(line.mapToScene(QPointF(200, 300)))
    assert line.isSelected() and not cover.isSelected()
    assert {"Start", "End"} <= set(menu.row_captions())
    menu.entry("Add Bend Point").trigger()
    assert line.bends() == [QPointF(200, 300)]


def test_right_click_on_an_unselected_item_still_selects_it(win) -> None:
    a = RectangleItem(QRectF(100, 100, 50, 50))
    b = RectangleItem(QRectF(400, 100, 50, 50))
    for it in (a, b):
        win._scene.push_add(it)
    for it in win._scene.selectedItems():
        it.setSelected(False)
    a.setSelected(True)
    win._build_context_menu(b.mapToScene(QPointF(425, 125)))
    assert b.isSelected() and not a.isSelected()
