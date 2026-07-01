"""Line/arrow endpoint snapping to nearby shapes' connection points."""

from __future__ import annotations

import math
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

from annoter.controllers.tools import Tool, ToolController  # noqa: E402
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
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


def _ev(etype, pos: QPointF, modifiers=Qt.NoModifier) -> QGraphicsSceneMouseEvent:
    ev = QGraphicsSceneMouseEvent(etype)
    ev.setScenePos(pos)
    ev.setButton(Qt.LeftButton)
    ev.setModifiers(modifiers)
    ev.setScreenPos(QPoint(int(pos.x()), int(pos.y())))
    return ev


def test_direct_snap_point_lookup_corner(scene) -> None:
    target = RectangleItem(QRectF(100, 100, 50, 50))
    target.setParentItem(scene.page_item())
    probe = LineItem(QPointF(0, 0), QPointF(0, 0))

    hit = scene._nearest_shape_snap_point(QPointF(103, 97), exclude=probe)
    assert hit == QPointF(100, 100)  # nearest corner


def test_direct_snap_point_lookup_edge_midpoint(scene) -> None:
    target = RectangleItem(QRectF(100, 100, 50, 50))
    target.setParentItem(scene.page_item())
    probe = LineItem(QPointF(0, 0), QPointF(0, 0))

    # Midpoint of the top edge is (125, 100).
    hit = scene._nearest_shape_snap_point(QPointF(126, 103), exclude=probe)
    assert hit == QPointF(125, 100)


def test_no_snap_point_beyond_threshold(scene) -> None:
    target = RectangleItem(QRectF(100, 100, 50, 50))
    target.setParentItem(scene.page_item())
    probe = LineItem(QPointF(0, 0), QPointF(0, 0))

    assert scene._nearest_shape_snap_point(QPointF(80, 80), exclude=probe) is None


def test_snap_point_excludes_the_item_itself(scene) -> None:
    line = LineItem(QPointF(100, 100), QPointF(150, 150))
    line.setParentItem(scene.page_item())
    # A point that would coincide with the line's own bounding corner
    # must not "snap to itself".
    hit = scene._nearest_shape_snap_point(QPointF(101, 101), exclude=line)
    assert hit is None


def test_line_draft_endpoint_snaps_to_rect_corner(scene) -> None:
    target = RectangleItem(QRectF(200, 200, 40, 40))
    target.setParentItem(scene.page_item())

    scene._tool_controller.set_tool(Tool.LINE)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(0, 0)))
    # 3px shy of the rect's top-left corner (200, 200).
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, QPointF(203, 197)))

    drafted = scene._draft_item
    assert drafted is not None
    _p1, p2 = drafted.line_points()
    assert p2 == QPointF(200, 200)


def test_alt_disables_endpoint_snap_while_drafting(scene) -> None:
    target = RectangleItem(QRectF(200, 200, 40, 40))
    target.setParentItem(scene.page_item())

    scene._tool_controller.set_tool(Tool.LINE)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(0, 0)))
    scene.mouseMoveEvent(
        _ev(
            QEvent.GraphicsSceneMouseMove,
            QPointF(203, 197),
            modifiers=Qt.AltModifier,
        )
    )
    _p1, p2 = scene._draft_item.line_points()
    assert p2 == QPointF(203, 197)  # NOT snapped to (200, 200)


def test_snap_takes_priority_over_shift_angle(scene) -> None:
    target = RectangleItem(QRectF(200, 200, 40, 40))
    target.setParentItem(scene.page_item())

    scene._tool_controller.set_tool(Tool.LINE)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(0, 0)))
    # Near the rect's corner but at an angle Shift-snapping would NOT
    # naturally produce (not a multiple of 45 degrees from origin).
    scene.mouseMoveEvent(
        _ev(
            QEvent.GraphicsSceneMouseMove,
            QPointF(203, 197),
            modifiers=Qt.ShiftModifier,
        )
    )
    _p1, p2 = scene._draft_item.line_points()
    assert p2 == QPointF(200, 200)


def test_arrow_resize_endpoint_snaps(scene) -> None:
    target = RectangleItem(QRectF(300, 50, 40, 40))
    target.setParentItem(scene.page_item())

    arrow = ArrowItem(QPointF(0, 0), QPointF(100, 0))
    arrow.setParentItem(scene.page_item())
    arrow.setSelected(True)

    p2 = QPointF(100, 0)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    assert scene._resize_item is arrow
    # Drag near the target's bottom-right corner (340, 90).
    scene.mouseMoveEvent(
        _ev(QEvent.GraphicsSceneMouseMove, QPointF(343, 87))
    )
    _p1, new_p2 = arrow.line_points()
    assert new_p2 == QPointF(340, 90)
    scene.mouseReleaseEvent(
        _ev(QEvent.GraphicsSceneMouseRelease, QPointF(343, 87))
    )


def test_resize_falls_back_to_shift_constrain_without_nearby_shape(scene) -> None:
    arrow = ArrowItem(QPointF(0, 0), QPointF(100, 5))
    arrow.setParentItem(scene.page_item())
    arrow.setSelected(True)

    p2 = QPointF(100, 5)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    scene.mouseMoveEvent(
        _ev(
            QEvent.GraphicsSceneMouseMove,
            QPointF(140, 30),
            modifiers=Qt.ShiftModifier,
        )
    )
    p1, new_p2 = arrow.line_points()
    angle = math.degrees(math.atan2(new_p2.y() - p1.y(), new_p2.x() - p1.x()))
    nearest_step = round(angle / 45.0) * 45.0
    assert angle == pytest.approx(nearest_step, abs=0.5)
    scene.mouseReleaseEvent(
        _ev(QEvent.GraphicsSceneMouseRelease, QPointF(140, 30))
    )
