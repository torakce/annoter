"""Fill opacity: item paint, clone, and Properties dock wiring."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.views.items.poly import PolygonItem  # noqa: E402
from annoter.views.items.shapes import CloudItem, RectangleItem  # noqa: E402
from annoter.views.properties_dock import PropertiesDock  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_default_opacity_is_fully_opaque(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_fill_enabled(True)
    assert item.fill_opacity() == pytest.approx(1.0)
    assert item._brush().color().alphaF() == pytest.approx(1.0)


def test_set_fill_opacity_clamped(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_fill_opacity(1.5)
    assert item.fill_opacity() == pytest.approx(1.0)
    item.set_fill_opacity(-0.5)
    assert item.fill_opacity() == pytest.approx(0.0)


def test_brush_alpha_reflects_opacity(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_fill_enabled(True)
    item.set_fill_color(QColor("#FF0000"))
    item.set_fill_opacity(0.25)
    brush_color = item._brush().color()
    assert brush_color.alphaF() == pytest.approx(0.25, abs=0.01)
    assert (brush_color.red(), brush_color.green(), brush_color.blue()) == (
        255,
        0,
        0,
    )


def test_polygon_and_cloud_support_opacity(qapp) -> None:
    poly = PolygonItem([QPointF(0, 0), QPointF(10, 0), QPointF(5, 10)])
    poly.set_fill_enabled(True)
    poly.set_fill_opacity(0.6)
    assert poly._brush().color().alphaF() == pytest.approx(0.6, abs=0.01)

    cloud = CloudItem(QRectF(0, 0, 40, 40))
    cloud.set_fill_enabled(True)
    cloud.set_fill_opacity(0.6)
    assert cloud._brush().color().alphaF() == pytest.approx(0.6, abs=0.01)


def test_clone_preserves_opacity(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_fill_opacity(0.3)
    clone = item.clone()
    assert clone.fill_opacity() == pytest.approx(0.3, abs=0.01)


def test_properties_dock_edits_opacity(qapp) -> None:
    dock = PropertiesDock()
    stack = QUndoStack()
    dock.set_undo_stack(stack)
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_fill_enabled(True)
    dock.set_items([item])

    dock._push_prop("fill_opacity", 0.5)
    assert item.fill_opacity() == pytest.approx(0.5, abs=0.01)
    assert stack.count() == 1
    stack.undo()
    assert item.fill_opacity() == pytest.approx(1.0, abs=0.01)
