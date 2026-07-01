"""Format Painter: scene-level click dispatch (PdfScene)."""

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

from annoter.controllers.tools import Tool, ToolController  # noqa: E402
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


def test_clicking_annotation_emits_format_paint_requested(scene) -> None:
    target = RectangleItem(QRectF(0, 0, 40, 40))
    target.setParentItem(scene.page_item())

    scene._tool_controller.set_tool(Tool.FORMAT_PAINTER)
    seen = []
    scene.formatPaintRequested.connect(seen.append)
    p = QPointF(10, 10)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p))
    assert seen == [target]


def test_clicking_empty_area_emits_nothing(scene) -> None:
    scene._tool_controller.set_tool(Tool.FORMAT_PAINTER)
    seen = []
    scene.formatPaintRequested.connect(seen.append)
    p = QPointF(200, 200)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p))
    assert seen == []


def test_format_painter_does_not_start_a_move_drag(scene) -> None:
    """A click-drag while painting must not relocate the clicked item
    (format painter clicks apply style, they never move annotations)."""
    target = RectangleItem(QRectF(0, 0, 40, 40))
    target.setParentItem(scene.page_item())
    start_pos = target.pos()

    scene._tool_controller.set_tool(Tool.FORMAT_PAINTER)
    p = QPointF(10, 10)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p))
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, QPointF(80, 80)))
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, QPointF(80, 80)))
    assert target.pos() == start_pos
