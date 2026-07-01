"""Session-level grouping (Ctrl+G / Ctrl+Shift+G) on PdfScene."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsSceneMouseEvent,
)

from annoter.controllers.tools import ToolController  # noqa: E402
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def scene(qapp):
    sc = PdfScene()
    sc.set_page_pixmap(QPixmap(400, 400))
    sc.set_tool_controller(ToolController())
    sc.set_undo_stack(QUndoStack())
    yield sc
    sc.clear_page()


def _ev(etype, pos: QPointF) -> QGraphicsSceneMouseEvent:
    ev = QGraphicsSceneMouseEvent(etype)
    ev.setScenePos(pos)
    ev.setButton(Qt.LeftButton)
    ev.setModifiers(Qt.NoModifier)
    ev.setScreenPos(QPoint(int(pos.x()), int(pos.y())))
    return ev


def test_group_requires_at_least_two(scene) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setParentItem(scene.page_item())
    a.setSelected(True)
    assert scene.group_selection() is False


def test_group_and_lookup(scene) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(20, 20, 10, 10))
    b.setParentItem(scene.page_item())
    a.setSelected(True)
    b.setSelected(True)

    assert scene.group_selection() is True
    group = scene.group_of(a)
    assert group is not None
    assert group == {a, b}
    assert scene.group_of(b) is group


def test_ungroup(scene) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(20, 20, 10, 10))
    b.setParentItem(scene.page_item())
    a.setSelected(True)
    b.setSelected(True)
    scene.group_selection()

    a.setSelected(True)
    assert scene.ungroup_selection() is True
    assert scene.group_of(a) is None
    assert scene.group_of(b) is None


def test_ungroup_noop_when_nothing_grouped(scene) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setParentItem(scene.page_item())
    a.setSelected(True)
    assert scene.ungroup_selection() is False


def test_regrouping_moves_item_out_of_old_group(scene) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(20, 20, 10, 10))
    b.setParentItem(scene.page_item())
    c = RectangleItem(QRectF(40, 40, 10, 10))
    c.setParentItem(scene.page_item())

    a.setSelected(True)
    b.setSelected(True)
    scene.group_selection()

    b.setSelected(True)
    c.setSelected(True)
    a.setSelected(False)
    scene.group_selection()

    # The old (a, b) group is gone -- it shrank below 2 once b left it.
    assert scene.group_of(a) is None
    assert scene.group_of(b) == {b, c}
    assert scene.group_of(c) == {b, c}


def test_clicking_a_grouped_member_selects_the_whole_group(scene) -> None:
    a = RectangleItem(QRectF(0, 0, 20, 20))
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(100, 100, 20, 20))
    b.setParentItem(scene.page_item())
    a.setSelected(True)
    b.setSelected(True)
    scene.group_selection()
    a.setSelected(False)
    b.setSelected(False)

    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(10, 10)))
    assert a.isSelected()
    assert b.isSelected()
    scene.mouseReleaseEvent(
        _ev(QEvent.GraphicsSceneMouseRelease, QPointF(10, 10))
    )


def test_has_group_in_selection(scene) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(20, 20, 10, 10))
    b.setParentItem(scene.page_item())
    a.setSelected(True)
    b.setSelected(True)
    assert scene.has_group_in_selection() is False
    scene.group_selection()
    assert scene.has_group_in_selection() is True


def test_clear_page_resets_groups(scene) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(20, 20, 10, 10))
    b.setParentItem(scene.page_item())
    a.setSelected(True)
    b.setSelected(True)
    scene.group_selection()
    scene.clear_page()
    assert scene._groups == []


def _group_two_far_apart(scene) -> tuple[RectangleItem, RectangleItem]:
    """Two 20x20 rects with a gap between them, grouped -- clicking the
    empty gap (e.g. scene (60, 10)) is inside the union bbox but on
    neither shape."""
    a = RectangleItem(QRectF(0, 0, 20, 20))
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(100, 0, 20, 20))
    b.setParentItem(scene.page_item())
    a.setSelected(True)
    b.setSelected(True)
    scene.group_selection()
    a.setSelected(False)
    b.setSelected(False)
    return a, b


def test_group_at_point_hits_empty_gap_inside_bbox(scene) -> None:
    a, b = _group_two_far_apart(scene)
    hit = scene._group_at_point(QPointF(60, 10))  # empty gap, inside bbox
    assert hit == {a, b}


def test_group_at_point_misses_outside_bbox(scene) -> None:
    _group_two_far_apart(scene)
    assert scene._group_at_point(QPointF(500, 500)) is None


def test_clicking_empty_gap_inside_group_bbox_selects_and_drags_it(scene) -> None:
    from annoter.controllers.geometry import item_scene_rect

    a, b = _group_two_far_apart(scene)
    a_before = item_scene_rect(a)
    b_before = item_scene_rect(b)

    scene.mousePressEvent(
        _ev(QEvent.GraphicsSceneMousePress, QPointF(60, 10))
    )
    assert a.isSelected() and b.isSelected()
    assert scene._group_drag_active is True

    scene.mouseMoveEvent(
        _ev(QEvent.GraphicsSceneMouseMove, QPointF(70, 30))
    )
    # Both shapes shift by the same (10, 20) delta, preserving the gap
    # between them.
    assert item_scene_rect(a).x() == pytest.approx(a_before.x() + 10)
    assert item_scene_rect(a).y() == pytest.approx(a_before.y() + 20)
    assert item_scene_rect(b).x() == pytest.approx(b_before.x() + 10)
    assert item_scene_rect(b).y() == pytest.approx(b_before.y() + 20)

    scene.mouseReleaseEvent(
        _ev(QEvent.GraphicsSceneMouseRelease, QPointF(70, 30))
    )
    assert scene._group_drag_active is False
    # One combined undo step for the whole group.
    assert scene._undo_stack.count() == 1
    scene._undo_stack.undo()
    assert item_scene_rect(a).x() == pytest.approx(a_before.x())
    assert item_scene_rect(b).x() == pytest.approx(b_before.x())


def test_group_box_shows_only_when_selection_matches_a_group_exactly(
    scene,
) -> None:
    a = RectangleItem(QRectF(0, 0, 20, 20))
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(100, 100, 20, 20))
    b.setParentItem(scene.page_item())
    a.setSelected(True)
    b.setSelected(True)
    scene.group_selection()
    a.setSelected(False)
    b.setSelected(False)

    assert scene._group_box is None or not scene._group_box.isVisible()

    a.setSelected(True)
    b.setSelected(True)
    assert scene._group_box is not None
    assert scene._group_box.isVisible()

    # Selecting just one member (not the whole group) hides it again.
    b.setSelected(False)
    assert not scene._group_box.isVisible()


def test_group_box_follows_the_group_during_a_drag(scene) -> None:
    a, b = _group_two_far_apart(scene)
    scene.mousePressEvent(
        _ev(QEvent.GraphicsSceneMousePress, QPointF(60, 10))
    )
    before = scene._group_box.rect()
    scene.mouseMoveEvent(
        _ev(QEvent.GraphicsSceneMouseMove, QPointF(160, 10))
    )
    after = scene._group_box.rect()
    assert after.x() > before.x()
    scene.mouseReleaseEvent(
        _ev(QEvent.GraphicsSceneMouseRelease, QPointF(160, 10))
    )
