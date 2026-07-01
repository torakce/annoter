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


def test_line_endpoint_snaps_to_angle_when_shift_held(scene) -> None:
    item = LineItem(QPointF(50, 50), QPointF(150, 53))  # near-horizontal
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    p2 = QPointF(150, 53)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    # A tiny vertical nudge would normally tilt the line off-axis...
    drag_to = QPointF(200, 40)
    scene.mouseMoveEvent(
        _ev(QEvent.GraphicsSceneMouseMove, drag_to, Qt.ShiftModifier)
    )
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, drag_to))

    p1, p2_final = item.line_points()
    angle = math.degrees(
        math.atan2(p2_final.y() - p1.y(), p2_final.x() - p1.x())
    )
    # ...but Shift must have snapped it to the nearest 45-degree step.
    nearest_step = round(angle / 45.0) * 45.0
    assert angle == pytest.approx(nearest_step, abs=0.5)


def test_line_resize_without_shift_is_unconstrained(scene) -> None:
    item = LineItem(QPointF(50, 50), QPointF(150, 53))
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    p2 = QPointF(150, 53)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p2))
    drag_to = QPointF(200, 40)
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, drag_to))
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, drag_to))

    _, p2_final = item.line_points()
    assert p2_final.x() == pytest.approx(200, abs=0.5)
    assert p2_final.y() == pytest.approx(40, abs=0.5)


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
    line = LineItem(QPointF(0, 0), QPointF(100, 4))
    snapped = scene._constrain_resize(
        line, HandleRole.P2, QPointF(100, 4)
    )
    assert snapped.y() == pytest.approx(0.0, abs=0.5)

    rect = RectangleItem(QRectF(0, 0, 50, 50))
    snapped_corner = scene._constrain_resize(
        rect, HandleRole.BOTTOM_RIGHT, QPointF(90, 40)
    )
    assert snapped_corner.x() == pytest.approx(snapped_corner.y(), abs=0.5)
