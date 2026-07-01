"""Live measurement HUD: px_to_pt conversion, PdfScene.current_measurement,
and the MeasurementHud widget itself."""

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
    QWidget,
)

from annoter.config import BASE_RENDER_DPI  # noqa: E402
from annoter.controllers.geometry import px_to_pt  # noqa: E402
from annoter.controllers.tools import Tool, ToolController  # noqa: E402
from annoter.views.items.lines import LineItem  # noqa: E402
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.measurement_hud import MeasurementHud  # noqa: E402
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


def test_px_to_pt_matches_base_dpi() -> None:
    assert px_to_pt(BASE_RENDER_DPI) == pytest.approx(72.0)
    assert px_to_pt(0) == 0.0


def test_no_measurement_when_idle(scene) -> None:
    assert scene.current_measurement() is None


def test_measurement_during_rect_draft(scene) -> None:
    scene._tool_controller.set_tool(Tool.RECTANGLE)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(10, 10)))
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, QPointF(60, 40)))
    kind, w, h = scene.current_measurement()
    assert kind == "rect"
    assert w == pytest.approx(50.0)
    assert h == pytest.approx(30.0)
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, QPointF(60, 40)))
    assert scene.current_measurement() is None


def test_measurement_during_line_draft(scene) -> None:
    scene._tool_controller.set_tool(Tool.LINE)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(0, 0)))
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, QPointF(30, 40)))
    kind, length, angle = scene.current_measurement()
    assert kind == "line"
    assert length == pytest.approx(50.0)  # 3-4-5 triangle
    assert angle == pytest.approx(math.degrees(math.atan2(40, 30)))


def test_measurement_during_existing_item_resize(scene) -> None:
    item = RectangleItem(QRectF(0, 0, 50, 50))
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    br = QPointF(50, 50)
    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, br))
    scene.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, QPointF(80, 20)))
    kind, w, h = scene.current_measurement()
    assert kind == "rect"
    assert w == pytest.approx(80.0)
    assert h == pytest.approx(20.0)
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, QPointF(80, 20)))


def test_measurement_ignores_unrelated_items(scene) -> None:
    """A resize of an item type the HUD doesn't describe (line vertex
    aside, e.g. a plain move) must not report a stale measurement."""
    item = LineItem(QPointF(0, 0), QPointF(10, 0))
    item.setParentItem(scene.page_item())
    item.setSelected(True)
    # No press/drag happened -- nothing in progress.
    assert scene.current_measurement() is None


def test_hud_widget_shows_expected_text(qapp) -> None:
    host = QWidget()
    hud = MeasurementHud(host)
    assert hud.isHidden() is True

    hud.show_rect(50.0, 30.0, QPoint(10, 10))
    assert "50.0" in hud.text() and "30.0" in hud.text()
    assert hud.isHidden() is False

    hud.show_line(72.0, 0.0, QPoint(10, 10))
    assert "72.0" in hud.text()
    assert "@" in hud.text()

    hud.hide()
    assert hud.isHidden() is True
