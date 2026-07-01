"""Alt (disable snap) and Shift (axis-lock) while moving an item."""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.tools import ToolController  # noqa: E402
from annoter.views.items.shapes import RectangleItem  # noqa: E402
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


def _no_mods():
    return Qt.NoModifier


def _alt():
    return Qt.AltModifier


def _shift():
    return Qt.ShiftModifier


def test_alt_disables_guide_snapping(scene) -> None:
    anchor = RectangleItem(QRectF(0, 0, 40, 40))
    anchor.setParentItem(scene.page_item())
    moving = RectangleItem(QRectF(0, 0, 40, 40))
    moving.setPos(120, 120)
    moving.setParentItem(scene.page_item())
    moving.setSelected(True)

    scene._interactive_drag_active = True
    scene._move_origins = {moving: QPointF(120, 120)}
    with patch.object(
        QApplication, "keyboardModifiers", staticmethod(_alt)
    ):
        # 2px shy of a perfect left-edge match -- would normally snap.
        result = scene.maybe_snap_move(moving, QPointF(2, 102))
    assert result == QPointF(2, 102)  # unperturbed


def test_without_alt_the_same_drag_still_snaps(scene) -> None:
    anchor = RectangleItem(QRectF(0, 0, 40, 40))
    anchor.setParentItem(scene.page_item())
    moving = RectangleItem(QRectF(0, 0, 40, 40))
    moving.setPos(120, 120)
    moving.setParentItem(scene.page_item())
    moving.setSelected(True)

    scene._interactive_drag_active = True
    scene._move_origins = {moving: QPointF(120, 120)}
    with patch.object(
        QApplication, "keyboardModifiers", staticmethod(_no_mods)
    ):
        result = scene.maybe_snap_move(moving, QPointF(2, 102))
    assert result.x() == pytest.approx(0.0, abs=0.01)


def test_shift_locks_to_dominant_axis_horizontal(scene) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.setPos(100, 100)
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    scene._interactive_drag_active = True
    scene._move_origins = {item: QPointF(100, 100)}
    with patch.object(
        QApplication, "keyboardModifiers", staticmethod(_shift)
    ):
        # Moved 30px right, 5px down -- horizontal dominates.
        result = scene.maybe_snap_move(item, QPointF(130, 105))
    assert result.x() == pytest.approx(130.0)
    assert result.y() == pytest.approx(100.0)  # y locked to origin


def test_shift_locks_to_dominant_axis_vertical(scene) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.setPos(100, 100)
    item.setParentItem(scene.page_item())
    item.setSelected(True)

    scene._interactive_drag_active = True
    scene._move_origins = {item: QPointF(100, 100)}
    with patch.object(
        QApplication, "keyboardModifiers", staticmethod(_shift)
    ):
        # Moved 5px right, 40px down -- vertical dominates.
        result = scene.maybe_snap_move(item, QPointF(105, 140))
    assert result.x() == pytest.approx(100.0)  # x locked to origin
    assert result.y() == pytest.approx(140.0)


def test_shift_axis_lock_works_with_multi_selection(scene) -> None:
    """Unlike guide-snapping, axis-lock is not restricted to a single
    selected item -- it locks every dragged item to the same axis."""
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setPos(100, 100)
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(0, 0, 10, 10))
    b.setPos(200, 200)
    b.setParentItem(scene.page_item())
    a.setSelected(True)
    b.setSelected(True)

    scene._interactive_drag_active = True
    scene._move_origins = {a: QPointF(100, 100), b: QPointF(200, 200)}
    with patch.object(
        QApplication, "keyboardModifiers", staticmethod(_shift)
    ):
        result_a = scene.maybe_snap_move(a, QPointF(130, 105))
    assert result_a.y() == pytest.approx(100.0)


def test_apply_axis_lock_unit(scene) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.setParentItem(scene.page_item())
    scene._move_origins = {item: QPointF(50, 50)}

    horiz = scene._apply_axis_lock(item, QPointF(90, 55))
    assert horiz == QPointF(90, 50)

    vert = scene._apply_axis_lock(item, QPointF(55, 90))
    assert vert == QPointF(50, 90)


def test_axis_lock_noop_without_captured_origin(scene) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.setParentItem(scene.page_item())
    scene._move_origins = {}
    result = scene._apply_axis_lock(item, QPointF(42, 42))
    assert result == QPointF(42, 42)
