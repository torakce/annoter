"""A stroke width of 0 hides the outline entirely (Discussion #1, item 9).

Qt draws `QPen(color, 0)` as a cosmetic 1px hairline, not "no pen" -- each
item's `_pen()` must explicitly switch to `Qt.NoPen` so a filled shape can
show only its fill.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.items.poly import PolygonItem, PolylineItem  # noqa: E402
from annoter.views.items.shapes import (  # noqa: E402
    CloudItem,
    EllipseItem,
    RectangleItem,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize(
    "item",
    [
        RectangleItem(QRectF(0, 0, 10, 10)),
        EllipseItem(QRectF(0, 0, 10, 10)),
        CloudItem(QRectF(0, 0, 10, 10)),
        LineItem(QPointF(0, 0), QPointF(10, 10)),
        ArrowItem(QPointF(0, 0), QPointF(10, 10)),
        PolylineItem([QPointF(0, 0), QPointF(10, 10)]),
        PolygonItem([QPointF(0, 0), QPointF(10, 0), QPointF(5, 10)]),
    ],
)
def test_zero_stroke_yields_no_pen(qapp, item) -> None:
    item.set_stroke(2.0)
    assert item._pen().style() != Qt.NoPen

    item.set_stroke(0.0)
    assert item._pen().style() == Qt.NoPen


def test_positive_stroke_after_zero_restores_pen(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_stroke(0.0)
    assert item._pen().style() == Qt.NoPen

    item.set_stroke(3.0)
    assert item._pen().style() != Qt.NoPen
    assert item._pen().widthF() == pytest.approx(3.0)


def test_zero_stroke_rectangle_still_shows_fill(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_fill_enabled(True)
    item.set_stroke(0.0)
    assert item._pen().style() == Qt.NoPen
    assert item._brush().style() != Qt.NoBrush
