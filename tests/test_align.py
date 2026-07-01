"""Align & distribute geometry (controllers/align.py, geometry.py)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.align import AlignMode, compute_align_moves  # noqa: E402
from annoter.controllers.geometry import (  # noqa: E402
    item_local_rect,
    item_scene_rect,
)
from annoter.views.items.lines import LineItem  # noqa: E402
from annoter.views.items.shapes import RectangleItem  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _apply(moves) -> None:
    for it, _old, new in moves:
        it.setPos(new)


def test_item_local_rect_matches_shape_rect(qapp) -> None:
    item = RectangleItem(QRectF(10, 20, 30, 40))
    assert item_local_rect(item) == QRectF(10, 20, 30, 40)


def test_item_local_rect_for_line_uses_point_bbox(qapp) -> None:
    item = LineItem(QPointF(10, 40), QPointF(50, 10))
    r = item_local_rect(item)
    assert r == QRectF(10, 10, 40, 30)


def test_item_scene_rect_adds_position(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 20, 20))
    item.setPos(100, 200)
    assert item_scene_rect(item) == QRectF(100, 200, 20, 20)


def test_align_left(qapp) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setPos(0, 0)
    b = RectangleItem(QRectF(0, 0, 10, 10))
    b.setPos(50, 80)
    moves = compute_align_moves([a, b], AlignMode.LEFT)
    _apply(moves)
    assert item_scene_rect(a).left() == item_scene_rect(b).left() == 0


def test_align_center_h(qapp) -> None:
    a = RectangleItem(QRectF(0, 0, 20, 20))
    a.setPos(0, 0)
    b = RectangleItem(QRectF(0, 0, 40, 40))
    b.setPos(100, 0)
    moves = compute_align_moves([a, b], AlignMode.CENTER_H)
    _apply(moves)
    assert item_scene_rect(a).center().x() == pytest.approx(
        item_scene_rect(b).center().x()
    )


def test_align_right(qapp) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setPos(0, 0)
    b = RectangleItem(QRectF(0, 0, 30, 30))
    b.setPos(200, 0)
    moves = compute_align_moves([a, b], AlignMode.RIGHT)
    _apply(moves)
    assert item_scene_rect(a).right() == pytest.approx(
        item_scene_rect(b).right()
    )


def test_align_top_middle_bottom(qapp) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setPos(0, 0)
    b = RectangleItem(QRectF(0, 0, 10, 50))
    b.setPos(0, 200)

    moves = compute_align_moves([a, b], AlignMode.TOP)
    _apply(moves)
    assert item_scene_rect(a).top() == pytest.approx(item_scene_rect(b).top())

    a.setPos(0, 0)
    b.setPos(0, 200)
    moves = compute_align_moves([a, b], AlignMode.BOTTOM)
    _apply(moves)
    assert item_scene_rect(a).bottom() == pytest.approx(
        item_scene_rect(b).bottom()
    )

    a.setPos(0, 0)
    b.setPos(0, 200)
    moves = compute_align_moves([a, b], AlignMode.MIDDLE_V)
    _apply(moves)
    assert item_scene_rect(a).center().y() == pytest.approx(
        item_scene_rect(b).center().y()
    )


def test_align_single_item_is_noop(qapp) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    assert compute_align_moves([a], AlignMode.LEFT) == []


def test_distribute_horizontal_equalizes_gaps(qapp) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setPos(0, 0)
    b = RectangleItem(QRectF(0, 0, 10, 10))
    b.setPos(40, 0)  # will be pushed to the exact middle gap
    c = RectangleItem(QRectF(0, 0, 10, 10))
    c.setPos(100, 0)
    moves = compute_align_moves([a, b, c], AlignMode.DISTRIBUTE_H)
    _apply(moves)
    ra, rb, rc = (item_scene_rect(x) for x in (a, b, c))
    gap1 = rb.left() - ra.right()
    gap2 = rc.left() - rb.right()
    assert gap1 == pytest.approx(gap2)
    # Endpoints never move.
    assert ra.left() == pytest.approx(0)
    assert rc.left() == pytest.approx(100)


def test_distribute_needs_at_least_three(qapp) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    b = RectangleItem(QRectF(0, 0, 10, 10))
    b.setPos(50, 0)
    assert compute_align_moves([a, b], AlignMode.DISTRIBUTE_H) == []
