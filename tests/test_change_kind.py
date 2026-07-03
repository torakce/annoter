"""Kind conversions between merged tools (Discussion #1, item 3)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.commands import ReplaceAnnotationCommand  # noqa: E402
from annoter.controllers.convert import (  # noqa: E402
    arrow_to_line,
    callout_to_arrow,
    cloud_to_rect,
    convert_poly_closed,
    convert_shape_outline,
    line_to_arrow,
    line_to_callout,
    polygon_to_polyline,
    polyline_to_polygon,
    rect_to_cloud,
)
from annoter.model.styles import DashStyle  # noqa: E402
from annoter.views.items.callout import CalloutItem  # noqa: E402
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.items.poly import PolygonItem, PolylineItem  # noqa: E402
from annoter.views.items.shapes import CloudItem, RectangleItem  # noqa: E402
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


def test_rect_cloud_roundtrip_preserves_geometry_and_style(qapp) -> None:
    rect = RectangleItem(QRectF(10, 20, 60, 40))
    rect.set_color(QColor("#123456"))
    rect.set_stroke(3.0)
    rect.set_dash_style(DashStyle.DASHED)
    rect.set_fill_enabled(True)
    rect.set_fill_color(QColor("#AABBCC"))
    rect.set_fill_opacity(0.5)
    rect.setPos(QPointF(5, 6))

    cloud = rect_to_cloud(rect)
    assert isinstance(cloud, CloudItem)
    assert cloud.rect() == rect.rect()
    assert cloud.pos() == rect.pos()
    assert cloud.color() == rect.color()
    assert cloud.stroke() == pytest.approx(3.0)
    assert cloud.dash_style() is DashStyle.DASHED
    assert cloud.fill_enabled() and cloud.fill_opacity() == pytest.approx(0.5)

    back = cloud_to_rect(cloud)
    assert isinstance(back, RectangleItem)
    assert back.rect() == rect.rect()
    assert back.fill_color() == rect.fill_color()


def test_polyline_polygon_roundtrip(qapp) -> None:
    pts = [QPointF(0, 0), QPointF(50, 0), QPointF(25, 40)]
    pl = PolylineItem(pts)
    pl.set_stroke(2.5)

    pg = polyline_to_polygon(pl)
    assert isinstance(pg, PolygonItem)
    assert pg.points() == pts
    assert pg.stroke() == pytest.approx(2.5)

    back = polygon_to_polyline(pg)
    assert isinstance(back, PolylineItem)
    assert back.points() == pts


def test_line_arrow_roundtrip(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(80, 20))
    arrow = line_to_arrow(line)
    assert isinstance(arrow, ArrowItem)
    assert arrow.line_points() == line.line_points()
    back = arrow_to_line(arrow)
    assert type(back) is LineItem
    assert back.line_points() == line.line_points()


def test_arrow_callout_roundtrip_geometry(qapp) -> None:
    arrow = ArrowItem(QPointF(10, 10), QPointF(110, 60))
    arrow.setPos(QPointF(3, 4))  # moved after drawing

    callout = line_to_callout(arrow, "check this")
    assert isinstance(callout, CalloutItem)
    assert callout.text() == "check this"
    # Box anchored at the tail (p1), tip at the head (p2), both in page
    # coordinates (item pos folded in).
    assert callout.pos() == QPointF(13, 14)
    assert callout.tip() == QPointF(100, 50)

    back = callout_to_arrow(callout)
    assert isinstance(back, ArrowItem)
    # The tip end must land exactly where the callout pointed.
    _p1, p2 = back.line_points()
    assert p2 == QPointF(113, 64)
    # Text is dropped by design.


def test_convert_helpers_noop_when_already_target(qapp) -> None:
    rect = RectangleItem(QRectF(0, 0, 10, 10))
    assert convert_shape_outline(rect, cloudy=False) is None
    cloud = CloudItem(QRectF(0, 0, 10, 10))
    assert convert_shape_outline(cloud, cloudy=True) is None
    pl = PolylineItem([QPointF(0, 0), QPointF(1, 1)])
    assert convert_poly_closed(pl, closed=False) is None


def test_dock_outline_combo_converts_rect_to_cloud(scene) -> None:
    from annoter.views.properties_dock import PropertiesDock

    rect = RectangleItem(QRectF(0, 0, 40, 40))
    rect.setParentItem(scene.page_item())

    dock = PropertiesDock()
    dock.set_undo_stack(scene._undo_stack)
    dock.set_items([rect])
    dock._on_outline_changed(rect, cloudy=True)

    children = scene.page_item().childItems()
    clouds = [c for c in children if isinstance(c, CloudItem)]
    assert len(clouds) == 1 and rect not in children
    scene._undo_stack.undo()
    assert rect in scene.page_item().childItems()


def test_dock_label_field_converts_arrow_to_callout(scene) -> None:
    from PySide6.QtWidgets import QLineEdit

    from annoter.views.properties_dock import PropertiesDock

    arrow = ArrowItem(QPointF(0, 0), QPointF(100, 0))
    arrow.setParentItem(scene.page_item())

    dock = PropertiesDock()
    dock.set_undo_stack(scene._undo_stack)
    dock.set_items([arrow])
    edit = QLineEdit()
    edit.setText("note here")
    dock._on_line_label_committed(arrow, edit)

    children = scene.page_item().childItems()
    callouts = [c for c in children if isinstance(c, CalloutItem)]
    assert len(callouts) == 1 and arrow not in children
    assert callouts[0].text() == "note here"
    # A second editingFinished (Qt often fires it twice) is a no-op.
    dock._on_line_label_committed(arrow, edit)
    assert scene._undo_stack.count() == 1


def test_replace_command_swaps_and_undoes(scene) -> None:
    rect = RectangleItem(QRectF(0, 0, 40, 40))
    rect.setParentItem(scene.page_item())
    cloud = rect_to_cloud(rect)

    stack = scene._undo_stack
    stack.push(
        ReplaceAnnotationCommand(scene, scene.page_item(), rect, cloud)
    )
    children = scene.page_item().childItems()
    assert cloud in children and rect not in children
    assert cloud.isSelected()

    stack.undo()
    children = scene.page_item().childItems()
    assert rect in children and cloud not in children
    assert rect.isSelected()

    stack.redo()
    assert cloud in scene.page_item().childItems()
