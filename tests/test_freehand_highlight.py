"""A selected freehand stroke is highlighted, not boxed (2026-09-28)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsScene,
    QStyleOptionGraphicsItem,
)

from annoter.views.items.freehand import HIGHLIGHT_PX, FreehandItem  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _render(item: FreehandItem) -> QImage:
    img = QImage(300, 200, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    item.paint(p, QStyleOptionGraphicsItem(), None)
    p.end()
    return img


def _stroke(qapp) -> FreehandItem:
    item = FreehandItem(
        [QPointF(20, 100), QPointF(80, 40), QPointF(150, 100), QPointF(250, 60)]
    )
    item.set_color(QColor("#000000"))
    item.set_stroke(2.0)
    scene = QGraphicsScene()  # selection needs a scene
    scene.addItem(item)
    item._keep_scene = scene
    return item


def test_selected_stroke_is_highlighted_not_boxed(qapp) -> None:
    item = _stroke(qapp)
    item.setSelected(True)
    img = _render(item)
    # No dashed box: the bounding rect's corner stays blank.
    r = item.boundingRect()
    corner = img.pixelColor(int(r.left()) + 1, int(r.top()) + 1)
    assert corner == QColor("white")
    # A translucent blue band hugs the stroke on both sides.
    beside = img.pixelColor(20 + 30, 100 - 30 + int(HIGHLIGHT_PX))
    assert beside.blue() > beside.red() + 30
    # The stroke itself is still drawn on top, in its own color.
    on = img.pixelColor(50, 70)
    assert on.lightness() < 80


def test_unselected_stroke_has_no_highlight(qapp) -> None:
    item = _stroke(qapp)
    img = _render(item)
    beside = img.pixelColor(20 + 30, 100 - 30 + int(HIGHLIGHT_PX))
    assert abs(beside.blue() - beside.red()) < 10


def test_bounds_make_room_for_the_band(qapp) -> None:
    item = _stroke(qapp)
    plain = item.boundingRect()
    item.setSelected(True)
    grown = item.boundingRect()
    assert grown.contains(plain) and grown != plain
