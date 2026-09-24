"""UI redesign, Lot E: left sidebar with Pages / Annotations tabs."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget  # noqa: E402

from annoter.model.styles import EndStyle  # noqa: E402
from annoter.views.annotation_list import describe  # noqa: E402
from annoter.views.document_sidebar import (  # noqa: E402
    TAB_ANNOTATIONS,
    TAB_PAGES,
    DocumentSidebar,
)
from annoter.views.items.lines import ArrowItem  # noqa: E402
from annoter.views.items.note import StickyNoteItem  # noqa: E402
from annoter.views.items.shapes import EllipseItem, RectangleItem  # noqa: E402
from annoter.views.items.stamp import StampItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def three_page_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "three.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def win(qapp, three_page_pdf: Path):
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.open_path(three_page_pdf)
    yield w
    w._on_close()
    w.close()


def _tree(win: MainWindow):
    return win._annotation_tree


# ----------------------------------------------------------------------
# layout
# ----------------------------------------------------------------------
def test_one_left_sidebar_replaces_two_docks(win) -> None:
    names = {d.objectName() for d in win.findChildren(QDockWidget)}
    assert "DocumentSidebar" in names
    assert "PageThumbnailDock" not in names
    assert "AnnotationListDock" not in names
    assert win.dockWidgetArea(win._sidebar) == Qt.LeftDockWidgetArea
    assert win.dockWidgetArea(win._properties_dock) == Qt.RightDockWidgetArea
    assert win.tabifiedDockWidgets(win._properties_dock) == []


def test_tabs_switch_and_show_counts(win) -> None:
    bar = win._sidebar
    assert bar.current_tab() == TAB_PAGES
    assert bar.pages_tab.text() == "Pages  3"
    assert bar.annotations_tab.text() == "Annotations"
    bar.annotations_tab.click()
    assert bar.current_tab() == TAB_ANNOTATIONS
    win._scene.push_add(RectangleItem(QRectF(10, 10, 40, 40)))
    assert bar.annotations_tab.text() == "Annotations  1"
    bar.pages_tab.click()
    assert bar.current_tab() == TAB_PAGES


def test_page_thumbnails_carry_annotation_counts(win) -> None:
    win._scene.push_add(RectangleItem(QRectF(10, 10, 40, 40)))
    win._scene.push_add(EllipseItem(QRectF(60, 60, 40, 40)))
    win._show_page(2)
    win._scene.push_add(RectangleItem(QRectF(10, 10, 40, 40)))
    pages = win._page_list
    assert pages.annotation_count(0) == 2
    assert pages.annotation_count(1) == 0
    assert pages.annotation_count(2) == 1


# ----------------------------------------------------------------------
# annotation tree
# ----------------------------------------------------------------------
def test_tree_groups_every_page_and_names_by_content(win) -> None:
    stamp = StampItem(QPointF(50, 50), "REJECTED")
    stamp.setParentItem(win._scene.page_item())
    win._scene.push_add(stamp)
    win._show_page(1)
    note = StickyNoteItem(QPointF(80, 80))
    note.set_text("Check PCD with supplier\nsecond line")
    note.setParentItem(win._scene.page_item())
    win._scene.push_add(note)

    tree = _tree(win)
    headers = tree.page_headers()
    assert headers == ["Page 1  ·  1", "Page 2  ·  1"]
    titles = tree.visible_titles()
    assert "REJECTED" in titles
    assert "Check PCD with supplier" in titles
    # Only the page on screen is expanded.
    top0 = tree._tree.topLevelItem(0)
    top1 = tree._tree.topLevelItem(1)
    assert not top0.isExpanded() and top1.isExpanded()


def test_describe_names_line_kinds(qapp) -> None:
    line = ArrowItem(QPointF(0, 0), QPointF(10, 10))
    line.set_end_end(EndStyle.NONE)
    assert describe(line)[0] == "Line"
    double = ArrowItem(QPointF(0, 0), QPointF(10, 10))
    double.set_start_end(EndStyle.OPEN_ARROW)
    assert describe(double)[0] == "Double arrow"
    arrow = ArrowItem(QPointF(0, 0), QPointF(10, 10))
    assert describe(arrow)[0] == "Arrow"


def test_filter_text_and_category_chips(win) -> None:
    win._scene.push_add(RectangleItem(QRectF(10, 10, 40, 40)))
    stamp = StampItem(QPointF(50, 50), "APPROVED")
    stamp.setParentItem(win._scene.page_item())
    win._scene.push_add(stamp)
    tree = _tree(win)
    assert sorted(tree.visible_titles()) == ["APPROVED", "Rectangle"]
    tree.set_filter_text("approv")
    assert tree.visible_titles() == ["APPROVED"]
    tree.set_filter_text("")
    tree.set_category(1)  # Shapes
    assert tree.visible_titles() == ["Rectangle"]
    tree.set_category(5)  # Stamps
    assert tree.visible_titles() == ["APPROVED"]
    tree.set_filter_text("zzz")
    assert tree.visible_titles() == []
    assert "No annotation matches" in tree._empty.text()


def test_empty_state_guides_the_user(win) -> None:
    tree = _tree(win)
    assert tree.visible_titles() == []
    assert "No annotations yet" in tree._empty.text()


def test_selection_syncs_both_ways(win) -> None:
    a = RectangleItem(QRectF(10, 10, 40, 40))
    b = EllipseItem(QRectF(80, 80, 40, 40))
    win._scene.push_add(a)
    win._scene.push_add(b)
    tree = _tree(win)
    # Tree -> scene.
    tree._rows[id(b)].setSelected(True)
    assert b.isSelected() and not a.isSelected()
    # Scene -> tree.
    b.setSelected(False)
    a.setSelected(True)
    assert tree._rows[id(a)].isSelected()
    assert not tree._rows[id(b)].isSelected()


def test_row_on_another_page_jumps_there(win) -> None:
    far = RectangleItem(QRectF(10, 10, 40, 40))
    win._show_page(2)
    win._scene.push_add(far)
    win._show_page(0)
    tree = _tree(win)
    tree.jumpRequested.emit(2, far)
    assert win._page_index == 2
    assert far.isSelected()
    assert tree._tree.topLevelItem(0).isExpanded()


def test_undo_refreshes_the_tree(win) -> None:
    win._scene.push_add(RectangleItem(QRectF(10, 10, 40, 40)))
    assert len(_tree(win).visible_titles()) == 1
    win.act_undo.trigger()
    assert _tree(win).visible_titles() == []
    assert win._sidebar.annotations_tab.text() == "Annotations"


def test_closing_with_a_selection_does_not_touch_deleted_items(win) -> None:
    """The old list crashed here: its rows outlived the scene items."""
    item = RectangleItem(QRectF(10, 10, 40, 40))
    win._scene.push_add(item)
    item.setSelected(True)
    win._on_close()
    assert _tree(win).visible_titles() == []
    assert win._sidebar.pages_tab.text() == "Pages"


def test_standalone_sidebar_defaults(qapp) -> None:
    bar = DocumentSidebar()
    assert bar.current_tab() == TAB_PAGES
    assert bar.pages.count() == 0
    assert bar.annotations.total() == 0
