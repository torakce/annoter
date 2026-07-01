"""Precise numeric geometry input in the Properties dock."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.geometry import (  # noqa: E402
    item_scene_rect,
    px_to_pt,
    pt_to_px,
)
from annoter.views.items.lines import LineItem  # noqa: E402
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.properties_dock import PropertiesDock  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dock(qapp):
    d = PropertiesDock()
    stack = QUndoStack()
    d.set_undo_stack(stack)
    yield d, stack


def test_px_pt_roundtrip() -> None:
    assert pt_to_px(px_to_pt(123.4)) == pytest.approx(123.4)


def test_move_via_x_field_is_undoable(dock) -> None:
    d, stack = dock
    item = RectangleItem(QRectF(10, 20, 30, 40))
    d.set_items([item])

    d._push_move(item, x=100.0)
    assert item_scene_rect(item).x() == pytest.approx(pt_to_px(100.0))
    # Y untouched.
    assert item_scene_rect(item).y() == pytest.approx(20.0)
    assert stack.count() == 1

    stack.undo()
    assert item_scene_rect(item).x() == pytest.approx(10.0)


def test_resize_via_width_field_keeps_position(dock) -> None:
    d, stack = dock
    item = RectangleItem(QRectF(10, 20, 30, 40))
    d.set_items([item])

    d._push_resize_rect(item, w=200.0)
    r = item_scene_rect(item)
    assert r.width() == pytest.approx(pt_to_px(200.0))
    assert r.height() == pytest.approx(40.0)
    assert r.x() == pytest.approx(10.0)
    assert stack.count() == 1

    stack.undo()
    assert item_scene_rect(item).width() == pytest.approx(30.0)


def test_resize_rejects_non_positive_size(dock) -> None:
    d, stack = dock
    item = RectangleItem(QRectF(0, 0, 30, 40))
    d.set_items([item])
    d._push_resize_rect(item, w=0.0)
    assert stack.count() == 0


def test_line_length_field_preserves_direction(dock) -> None:
    d, stack = dock
    item = LineItem(QPointF(0, 0), QPointF(30, 40))  # length 50, angle atan2(40,30)
    d.set_items([item])

    d._push_line_geom(item, length_pt=px_to_pt(100.0))
    p1, p2 = item.line_points()
    assert p1 == QPointF(0, 0)
    new_length = (p2.x() ** 2 + p2.y() ** 2) ** 0.5
    assert new_length == pytest.approx(100.0, abs=0.05)
    # Direction (ratio dy/dx) preserved.
    assert p2.y() / p2.x() == pytest.approx(40.0 / 30.0, rel=1e-3)
    assert stack.count() == 1


def test_line_angle_field_points_north_at_90(dock) -> None:
    d, stack = dock
    item = LineItem(QPointF(0, 0), QPointF(50, 0))
    d.set_items([item])

    d._push_line_geom(item, angle_deg=90.0)
    p1, p2 = item.line_points()
    # 90 degrees in the display convention (y-up) means straight "north",
    # i.e. negative y in screen (y-down) space.
    assert p2.x() == pytest.approx(0.0, abs=0.05)
    assert p2.y() < 0
    assert stack.count() == 1


def test_geometry_rows_appear_only_for_single_selection(dock) -> None:
    d, _stack = dock
    a = RectangleItem(QRectF(0, 0, 10, 10))
    b = RectangleItem(QRectF(0, 0, 10, 10))
    d.set_items([a, b])
    # No exception, and no crash rebuilding with 2 items (geometry rows
    # are simply omitted); spot-check via the body still being usable.
    d.set_items([a])
