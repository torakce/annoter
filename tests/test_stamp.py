"""Stamp placement (scene) and preset application (properties dock)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsSceneMouseEvent,
)

from annoter.controllers.tools import Tool, ToolController  # noqa: E402
from annoter.views.items.base import AnnotationItem  # noqa: E402
from annoter.views.items.stamp import StampItem  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402
from annoter.views.properties_dock import PropertiesDock  # noqa: E402


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


def test_stamp_click_places_default_stamp(scene) -> None:
    sc, tc, stack = scene
    tc.set_tool(Tool.STAMP)
    p = QPointF(120, 90)
    sc.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p))
    sc.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, p))

    stamps = [
        c
        for c in sc.page_item().childItems()
        if isinstance(c, StampItem)
    ]
    assert len(stamps) == 1
    assert stamps[0].text() == "APPROVED"
    assert stack.count() == 1
    # Placement returns to the Select tool, like every other insertion.
    assert tc.tool() is Tool.SELECT


def test_stamp_preset_sets_text_and_color(qapp) -> None:
    dock = PropertiesDock()
    stack = QUndoStack()
    dock.set_undo_stack(stack)
    item = StampItem(QPointF(0, 0), "APPROVED")
    item.set_color(QColor("#2E7D32"))
    dock.set_items([item])

    dock._apply_stamp_preset("REJECTED")
    assert item.text() == "REJECTED"
    assert item.color().name().lower() == "#c62828"
    # Text + color land as a single undo step.
    assert stack.count() == 1
