"""Callout drag-drafting and inline-edit commit / rollback in PdfScene."""

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
from annoter.views.items.callout import CalloutItem  # noqa: E402
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


def _drag_callout(sc: PdfScene, tip: QPointF, box: QPointF) -> CalloutItem:
    sc.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, tip))
    sc.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, box))
    sc.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, box))
    callouts = [
        c
        for c in sc.page_item().childItems()
        if isinstance(c, CalloutItem)
    ]
    assert len(callouts) == 1
    return callouts[0]


def test_callout_drag_then_commit(scene) -> None:
    sc, tc, stack = scene
    tc.set_tool(Tool.CALLOUT)
    item = _drag_callout(sc, QPointF(60, 100), QPointF(120, 60))
    # The tip stays pinned where the drag started (scene coords).
    tip_scene = item.pos() + item.tip()
    assert tip_scene.x() == pytest.approx(60, abs=0.5)
    assert tip_scene.y() == pytest.approx(100, abs=0.5)
    # Finishing the edit with text commits an Add command. In the real
    # flow the inner text item already holds the typed string and the
    # signal relays it; mirror that here.
    item.set_text("Note")
    item.editingFinished.emit("Note")
    assert "Note" in item.text()
    assert stack.count() == 1
    assert tc.tool() is Tool.SELECT
    committed = [
        c
        for c in sc.page_item().childItems()
        if isinstance(c, CalloutItem)
    ]
    assert committed == [item]


def test_callout_empty_text_rolls_back(scene) -> None:
    sc, tc, stack = scene
    tc.set_tool(Tool.CALLOUT)
    item = _drag_callout(sc, QPointF(60, 100), QPointF(120, 60))
    item.editingFinished.emit("   ")  # whitespace only -> rollback
    remaining = [
        c
        for c in sc.page_item().childItems()
        if isinstance(c, CalloutItem)
    ]
    assert remaining == []
    assert stack.count() == 0
