"""Tests for QUndoCommand subclasses: each command + round-trip."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem  # noqa: E402

from annoter.controllers.commands import (  # noqa: E402
    AddAnnotationCommand,
    ChangeColorCommand,
    ChangePropsCommand,
    ChangeStrokeCommand,
    DeleteAnnotationsCommand,
    MoveAnnotationsCommand,
)
from annoter.model.styles import DashStyle  # noqa: E402
from annoter.views.items import RectangleItem  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def scene_with_page(qapp):
    scene = PdfScene()
    pixmap = QPixmap(200, 200)
    pixmap.fill()
    scene.set_page_pixmap(pixmap)
    page = scene.page_item()
    assert isinstance(page, QGraphicsPixmapItem)
    yield scene, page


def test_add_command_roundtrip(scene_with_page) -> None:
    scene, page = scene_with_page
    item = RectangleItem(QRectF(10, 10, 50, 30))
    cmd = AddAnnotationCommand(scene, page, item)
    cmd.redo()
    assert item.scene() is scene
    assert item.parentItem() is page
    cmd.undo()
    assert item.scene() is None
    cmd.redo()
    assert item.scene() is scene
    assert item.parentItem() is page


def test_delete_command_roundtrip(scene_with_page) -> None:
    scene, page = scene_with_page
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.setParentItem(page)
    assert item.scene() is scene
    cmd = DeleteAnnotationsCommand(scene, [item])
    cmd.redo()
    assert item.scene() is None
    cmd.undo()
    assert item.scene() is scene
    assert item.parentItem() is page


def test_move_command_roundtrip(scene_with_page) -> None:
    scene, page = scene_with_page
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.setParentItem(page)
    item.setPos(QPointF(5, 5))
    cmd = MoveAnnotationsCommand(
        [(item, QPointF(5, 5), QPointF(60, 80))]
    )
    cmd.redo()
    assert item.pos() == QPointF(60, 80)
    cmd.undo()
    assert item.pos() == QPointF(5, 5)


def test_change_color_command_roundtrip(scene_with_page) -> None:
    scene, page = scene_with_page
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.setParentItem(page)
    item.set_color(QColor("#111111"))
    cmd = ChangeColorCommand([item], QColor("#999999"))
    cmd.redo()
    assert item.color().name() == "#999999"
    cmd.undo()
    assert item.color().name() == "#111111"


def test_change_stroke_command_roundtrip(scene_with_page) -> None:
    scene, page = scene_with_page
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.setParentItem(page)
    item.set_stroke(2.0)
    cmd = ChangeStrokeCommand([item], 3.5)
    cmd.redo()
    assert item.stroke() == 3.5
    cmd.undo()
    assert item.stroke() == 2.0


def test_change_props_command_roundtrip(scene_with_page) -> None:
    scene, page = scene_with_page
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.setParentItem(page)
    item.set_dash_style(DashStyle.SOLID)
    item.set_corner_radius(0.0)
    cmd = ChangePropsCommand(
        [
            (item, "dash_style", DashStyle.SOLID, DashStyle.DASH_DOT_DOT),
            (item, "corner_radius", 0.0, 12.0),
            (item, "fill_enabled", False, True),
        ]
    )
    cmd.redo()
    assert item.dash_style() is DashStyle.DASH_DOT_DOT
    assert item.corner_radius() == 12.0
    assert item.fill_enabled() is True
    cmd.undo()
    assert item.dash_style() is DashStyle.SOLID
    assert item.corner_radius() == 0.0
    assert item.fill_enabled() is False


def test_undo_stack_chains_commands(scene_with_page) -> None:
    scene, page = scene_with_page
    stack = QUndoStack()
    item1 = RectangleItem(QRectF(0, 0, 10, 10))
    item2 = RectangleItem(QRectF(20, 20, 10, 10))
    stack.push(AddAnnotationCommand(scene, page, item1))
    stack.push(AddAnnotationCommand(scene, page, item2))
    assert item1.scene() is scene
    assert item2.scene() is scene
    stack.undo()
    assert item2.scene() is None
    assert item1.scene() is scene
    stack.undo()
    assert item1.scene() is None
    stack.redo()
    stack.redo()
    assert item1.scene() is scene
    assert item2.scene() is scene
