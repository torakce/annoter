"""Shift-constrained resize of existing annotations (PdfScene)."""

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

from annoter.model.styles import HandleRole  # noqa: E402
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


def test_line_endpoint_preserves_original_angle_when_shift_held(scene) -> None:
    item = LineItem(QPointF(50, 50), QPointF(150, 53))  # near-horizontal
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    p1, p2 = item.line_points()
    original_angle = math.degrees(
        math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
    )

    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    # A drag that would normally tilt the line to a very different angle...
    drag_to = QPointF(200, 40)
    scene.mouseMoveEvent(
        _ev(QEvent.GraphicsSceneMouseMove, drag_to, Qt.ShiftModifier)
    )
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, drag_to))

    p1_final, p2_final = item.line_points()
    angle = math.degrees(
        math.atan2(p2_final.y() - p1_final.y(), p2_final.x() - p1_final.x())
    )
    # ...but Shift must keep the ORIGINAL angle exactly, not re-snap it
    # to a 45-degree step.
    assert angle == pytest.approx(original_angle, abs=0.5)
    # The endpoint still actually moved (length changed along that ray).
    assert (p2_final - p2).manhattanLength() > 1.0


def test_line_resize_without_shift_is_free_beyond_magnet_range(scene) -> None:
    item = LineItem(QPointF(50, 50), QPointF(150, 53))
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    p2 = QPointF(150, 53)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    # ~15 degrees off horizontal: well outside the 4-degree magnet.
    drag_to = QPointF(200, 90)
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, drag_to))
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, drag_to))

    _, p2_final = item.line_points()
    assert p2_final.x() == pytest.approx(200, abs=0.5)
    assert p2_final.y() == pytest.approx(90, abs=0.5)


def test_line_resize_without_shift_magnets_near_important_angles(
    scene,
) -> None:
    item = LineItem(QPointF(50, 50), QPointF(150, 53))
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    p2 = QPointF(150, 53)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    # ~3.8 degrees off horizontal: inside the magnet -> snaps flat.
    drag_to = QPointF(200, 40)
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, drag_to))
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, drag_to))

    p1_final, p2_final = item.line_points()
    assert p2_final.y() == pytest.approx(p1_final.y(), abs=0.5)


def test_alt_disables_angle_magnet(scene) -> None:
    item = LineItem(QPointF(50, 50), QPointF(150, 53))
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    p2 = QPointF(150, 53)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    drag_to = QPointF(200, 40)  # inside the magnet range...
    scene.mouseMoveEvent(
        _ev(QEvent.GraphicsSceneMouseMove, drag_to, Qt.AltModifier)
    )
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, drag_to))

    _, p2_final = item.line_points()
    # ...but Alt keeps the drag completely free.
    assert p2_final.y() == pytest.approx(40, abs=0.5)


def test_bent_line_endpoint_magnet_uses_adjacent_bend(scene) -> None:
    item = LineItem(QPointF(0, 0), QPointF(100, 40))
    item.setParentItem(scene.page_item())
    item.set_bends([QPointF(60, 43)])
    item.setSelected(True)

    p2 = QPointF(100, 40)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    # End segment bend(60,43)->cursor(160,40): ~1.7 degrees off flat ->
    # magnets onto the bend's horizontal, NOT the p1->p2 chord.
    drag_to = QPointF(160, 40)
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, drag_to))
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, drag_to))

    _, p2_final = item.line_points()
    assert p2_final.y() == pytest.approx(43, abs=0.5)


def test_arrow_endpoint_also_snaps(scene) -> None:
    item = ArrowItem(QPointF(0, 0), QPointF(100, 0))
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    p2 = QPointF(100, 0)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    drag_to = QPointF(100, 96)  # would be a steep, non-45 angle
    scene.mouseMoveEvent(
        _ev(QEvent.GraphicsSceneMouseMove, drag_to, Qt.ShiftModifier)
    )
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, drag_to))

    p1, p2_final = item.line_points()
    angle = math.degrees(
        math.atan2(p2_final.y() - p1.y(), p2_final.x() - p1.x())
    )
    nearest_step = round(angle / 45.0) * 45.0
    assert angle == pytest.approx(nearest_step, abs=0.5)


def test_rectangle_corner_resize_stays_square_when_shift_held(scene) -> None:
    item = RectangleItem(QRectF(0, 0, 50, 50))
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    br = QPointF(50, 50)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, br))
    drag_to = QPointF(90, 40)  # not square without the constraint
    scene.mouseMoveEvent(
        _ev(QEvent.GraphicsSceneMouseMove, drag_to, Qt.ShiftModifier)
    )
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, drag_to))

    rect = item.rect()
    assert rect.width() == pytest.approx(rect.height(), abs=0.5)


def test_constrain_resize_helper_direct(scene) -> None:
    """Unit-level check of the constraint math in isolation."""
    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    # Simulate a resize already in progress: the pre-drag endpoints come
    # from the press-time snapshot, not the (possibly already dragged)
    # live geometry.
    scene._resize_snapshot = (QPointF(0, 0), QPointF(100, 0))
    snapped = scene._constrain_resize(
        line, HandleRole.P2, QPointF(100, 4)
    )
    # The original (horizontal) angle is preserved even though the
    # cursor drifted off-axis -- it is not re-snapped elsewhere.
    assert snapped.y() == pytest.approx(0.0, abs=0.5)

    rect = RectangleItem(QRectF(0, 0, 50, 50))
    snapped_corner = scene._constrain_resize(
        rect, HandleRole.BOTTOM_RIGHT, QPointF(90, 40)
    )
    assert snapped_corner.x() == pytest.approx(snapped_corner.y(), abs=0.5)
