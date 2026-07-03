"""Freehand tool stays armed across consecutive strokes (Discussion #1, item 13).

Every other drawing tool auto-returns to Select after one insertion (a
PowerPoint-style affordance); Freehand is the exception since a single
stroke is not a one-shot action -- the user expects to keep sketching
until Escape or another tool is picked.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsSceneMouseEvent,
)

from annoter.controllers.tools import Tool, ToolController  # noqa: E402
from annoter.views.items.freehand import FreehandItem  # noqa: E402
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


def _draw_stroke(scene: PdfScene, start: QPointF, end: QPointF) -> None:
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, start))
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, end))
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, end))


def test_freehand_stays_active_after_one_stroke(scene) -> None:
    scene._tool_controller.set_tool(Tool.FREEHAND)
    _draw_stroke(scene, QPointF(10, 10), QPointF(50, 50))

    assert scene._tool_controller.tool() is Tool.FREEHAND
    assert scene._undo_stack.count() == 1


def test_freehand_can_draw_several_strokes_without_reselecting_tool(
    scene,
) -> None:
    scene._tool_controller.set_tool(Tool.FREEHAND)
    _draw_stroke(scene, QPointF(10, 10), QPointF(50, 50))
    _draw_stroke(scene, QPointF(60, 60), QPointF(100, 100))
    _draw_stroke(scene, QPointF(110, 110), QPointF(150, 150))

    assert scene._tool_controller.tool() is Tool.FREEHAND
    assert scene._undo_stack.count() == 3
    strokes = [
        it
        for it in scene.page_item().childItems()
        if isinstance(it, FreehandItem)
    ]
    assert len(strokes) == 3


def test_escape_still_leaves_freehand(scene) -> None:
    scene._tool_controller.set_tool(Tool.FREEHAND)
    _draw_stroke(scene, QPointF(10, 10), QPointF(50, 50))
    assert scene._tool_controller.tool() is Tool.FREEHAND

    scene.cancel_current_action()
    assert scene._tool_controller.tool() is Tool.SELECT


def test_other_tools_still_auto_return_to_select(scene) -> None:
    from annoter.views.items.shapes import RectangleItem

    scene._tool_controller.set_tool(Tool.RECTANGLE)
    _draw_stroke(scene, QPointF(10, 10), QPointF(50, 50))

    assert scene._tool_controller.tool() is Tool.SELECT
    rects = [
        it
        for it in scene.page_item().childItems()
        if isinstance(it, RectangleItem)
    ]
    assert len(rects) == 1
    assert rects[0].isSelected()
