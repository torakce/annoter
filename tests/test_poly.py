"""Multi-click drafting for polyline / polygon tools in PdfScene."""

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
from annoter.views.items.base import AnnotationItem  # noqa: E402
from annoter.views.items.poly import (  # noqa: E402
    PolygonItem,
    PolylineItem,
)
from annoter.views.pdf_scene import PdfScene  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def scene(qapp):
    sc = PdfScene()
    sc.set_page_pixmap(QPixmap(400, 400))
    tc = ToolController()
    stack = QUndoStack()
    sc.set_tool_controller(tc)
    sc.set_undo_stack(stack)
    yield sc, tc, stack
    sc.clear_page()


def _ev(etype, pos: QPointF) -> QGraphicsSceneMouseEvent:
    ev = QGraphicsSceneMouseEvent(etype)
    ev.setScenePos(pos)
    ev.setButton(Qt.LeftButton)
    ev.setModifiers(Qt.NoModifier)
    ev.setScreenPos(QPoint(int(pos.x()), int(pos.y())))
    return ev


def _click(sc: PdfScene, x: float, y: float) -> None:
    p = QPointF(x, y)
    sc.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p))
    sc.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, p))


def _committed(sc: PdfScene) -> list[AnnotationItem]:
    page = sc.page_item()
    return [
        c
        for c in page.childItems()
        if isinstance(c, AnnotationItem)
    ]


def test_polyline_multiclick_then_enter(scene) -> None:
    sc, tc, stack = scene
    tc.set_tool(Tool.POLYLINE)
    _click(sc, 20, 20)
    _click(sc, 80, 40)
    _click(sc, 60, 120)
    assert sc.poly_draft_active()
    sc.finish_poly_draft()  # what the Enter key triggers

    items = _committed(sc)
    assert len(items) == 1
    assert isinstance(items[0], PolylineItem)
    assert len(items[0].points()) == 3
    # An Add command landed and the tool returned to Select.
    assert stack.count() == 1
    assert tc.tool() is Tool.SELECT


def test_polygon_double_click_finishes(scene) -> None:
    sc, tc, stack = scene
    tc.set_tool(Tool.POLYGON)
    _click(sc, 30, 30)
    _click(sc, 120, 40)
    p = QPointF(90, 130)
    # The double-click's leading press places the last vertex, then the
    # double-click event finishes the polygon.
    sc.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p))
    sc.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, p))
    sc.mouseDoubleClickEvent(_ev(QEvent.GraphicsSceneMouseDoubleClick, p))

    items = _committed(sc)
    assert len(items) == 1
    assert isinstance(items[0], PolygonItem)
    assert len(items[0].points()) == 3
    assert not sc.poly_draft_active()


def test_escape_discards_poly_draft(scene) -> None:
    sc, tc, stack = scene
    tc.set_tool(Tool.POLYGON)
    _click(sc, 30, 30)
    _click(sc, 120, 40)
    assert sc.poly_draft_active()
    sc.cancel_current_action()  # what the Escape key triggers
    assert not sc.poly_draft_active()
    assert _committed(sc) == []
    assert stack.count() == 0


def test_polygon_dropped_when_too_few_vertices(scene) -> None:
    sc, tc, stack = scene
    tc.set_tool(Tool.POLYGON)
    _click(sc, 30, 30)
    _click(sc, 120, 40)  # only 2 vertices: not a valid polygon
    sc.finish_poly_draft()
    assert _committed(sc) == []
    assert stack.count() == 0
