"""Tests for the resize-handle infrastructure on annotation items."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.commands import ResizeCommand  # noqa: E402
from annoter.model.styles import HandleRole  # noqa: E402
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.items.shapes import (  # noqa: E402
    EllipseItem,
    RectangleItem,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_rectangle_exposes_eight_handles(qapp) -> None:
    item = RectangleItem(QRectF(10, 20, 100, 50))
    positions = item.handle_positions()
    assert set(positions) == {
        HandleRole.TOP_LEFT,
        HandleRole.TOP,
        HandleRole.TOP_RIGHT,
        HandleRole.RIGHT,
        HandleRole.BOTTOM_RIGHT,
        HandleRole.BOTTOM,
        HandleRole.BOTTOM_LEFT,
        HandleRole.LEFT,
    }
    assert positions[HandleRole.TOP_LEFT] == QPointF(10, 20)
    assert positions[HandleRole.BOTTOM_RIGHT] == QPointF(110, 70)


def test_rectangle_resize_top_left(qapp) -> None:
    item = RectangleItem(QRectF(10, 20, 100, 50))
    item.apply_resize(HandleRole.TOP_LEFT, QPointF(30, 35))
    r = item.rect()
    assert r.left() == 30
    assert r.top() == 35
    assert r.right() == 110
    assert r.bottom() == 70


def test_rectangle_resize_right_only_x(qapp) -> None:
    item = RectangleItem(QRectF(10, 20, 100, 50))
    item.apply_resize(HandleRole.RIGHT, QPointF(150, 999))
    r = item.rect()
    assert r.left() == 10
    assert r.top() == 20  # unchanged
    assert r.right() == 150
    assert r.bottom() == 70  # unchanged


def test_ellipse_handles_match_bounding_rect(qapp) -> None:
    item = EllipseItem(QRectF(0, 0, 80, 40))
    positions = item.handle_positions()
    assert positions[HandleRole.TOP_LEFT] == QPointF(0, 0)
    assert positions[HandleRole.BOTTOM_RIGHT] == QPointF(80, 40)


def test_line_has_two_endpoint_handles(qapp) -> None:
    item = LineItem(QPointF(5, 6), QPointF(70, 80))
    positions = item.handle_positions()
    assert set(positions) == {HandleRole.P1, HandleRole.P2}
    assert positions[HandleRole.P1] == QPointF(5, 6)


def test_line_resize_p2(qapp) -> None:
    item = LineItem(QPointF(5, 6), QPointF(70, 80))
    item.apply_resize(HandleRole.P2, QPointF(120, 130))
    _p1, p2 = item.line_points()
    assert p2 == QPointF(120, 130)


def test_arrow_inherits_handles(qapp) -> None:
    item = ArrowItem(QPointF(0, 0), QPointF(50, 0))
    positions = item.handle_positions()
    assert HandleRole.P1 in positions
    assert HandleRole.P2 in positions


def test_hit_handle_detects_corner(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 100, 50))
    # Anywhere near the top-left corner should hit.
    assert item.hit_handle(QPointF(0, 0)) is HandleRole.TOP_LEFT
    assert item.hit_handle(QPointF(2, 2)) is HandleRole.TOP_LEFT
    # Far from any handle: None.
    assert item.hit_handle(QPointF(50, 25)) is None


def test_hit_handle_returns_none_when_no_handles(qapp) -> None:
    """Items whose base class doesn't opt in to handles report no hit."""
    from annoter.views.items.base import AnnotationItem

    class _Stub(AnnotationItem):
        KIND = "stub"

        def boundingRect(self):
            return QRectF(0, 0, 10, 10)

        def paint(self, *_args, **_kwargs):  # pragma: no cover
            return

    item = _Stub()
    assert item.hit_handle(QPointF(0, 0)) is None


def test_resize_command_roundtrip(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 100, 50))
    old = item.geom_snapshot()
    item.apply_resize(HandleRole.BOTTOM_RIGHT, QPointF(200, 100))
    new = item.geom_snapshot()
    cmd = ResizeCommand(item, old, new)
    cmd.undo()
    assert item.rect() == QRectF(0, 0, 100, 50)
    cmd.redo()
    assert item.rect().right() == 200
    assert item.rect().bottom() == 100


def test_rectangle_minimum_size_preserved(qapp) -> None:
    """Dragging a handle past the opposite edge should keep a tiny rect."""
    item = RectangleItem(QRectF(10, 10, 100, 50))
    item.apply_resize(HandleRole.BOTTOM_RIGHT, QPointF(10, 10))
    r = item.rect()
    assert r.width() >= 2.0
    assert r.height() >= 2.0
