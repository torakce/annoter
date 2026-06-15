"""Multi-selection: Shift+click, Ctrl+click toggle vs Ctrl+drag
duplicate, and rubber-band selection in the view."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsSceneMouseEvent,
)

from annoter.views.items import RectangleItem  # noqa: E402
from annoter.views.items.base import AnnotationItem  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402
from annoter.views.pdf_view import PdfView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def scene_with_two_rects(qapp):
    scene = PdfScene()
    scene.set_page_pixmap(QPixmap(400, 400))
    r1 = RectangleItem(QRectF(20, 20, 60, 40))
    r2 = RectangleItem(QRectF(150, 150, 60, 40))
    for r in (r1, r2):
        scene.addItem(r)
        r.setParentItem(scene.page_item())
    yield scene, r1, r2
    scene.clear_page()


def _mouse_event(
    etype,
    scene_pos: QPointF,
    *,
    modifiers=Qt.NoModifier,
    button=Qt.LeftButton,
    screen_pos: QPoint | None = None,
) -> QGraphicsSceneMouseEvent:
    ev = QGraphicsSceneMouseEvent(etype)
    ev.setScenePos(scene_pos)
    ev.setButton(button)
    ev.setModifiers(modifiers)
    ev.setScreenPos(
        screen_pos
        if screen_pos is not None
        else QPoint(int(scene_pos.x()), int(scene_pos.y()))
    )
    return ev


def _press(scene, pos, **kw):
    scene.mousePressEvent(
        _mouse_event(QEvent.GraphicsSceneMousePress, pos, **kw)
    )


def _move(scene, pos, **kw):
    scene.mouseMoveEvent(
        _mouse_event(QEvent.GraphicsSceneMouseMove, pos, **kw)
    )


def _release(scene, pos, **kw):
    scene.mouseReleaseEvent(
        _mouse_event(QEvent.GraphicsSceneMouseRelease, pos, **kw)
    )


def _annotations(scene) -> list[AnnotationItem]:
    return [
        c
        for c in scene.page_item().childItems()
        if isinstance(c, AnnotationItem)
    ]


def test_shift_click_adds_to_selection(scene_with_two_rects) -> None:
    scene, r1, r2 = scene_with_two_rects
    r1.setSelected(True)
    inside_r2 = QPointF(180, 170)
    _press(scene, inside_r2, modifiers=Qt.ShiftModifier)
    _release(scene, inside_r2, modifiers=Qt.ShiftModifier)
    assert r1.isSelected() and r2.isSelected()


def test_shift_click_removes_from_selection(scene_with_two_rects) -> None:
    scene, r1, r2 = scene_with_two_rects
    r1.setSelected(True)
    r2.setSelected(True)
    inside_r2 = QPointF(180, 170)
    _press(scene, inside_r2, modifiers=Qt.ShiftModifier)
    _release(scene, inside_r2, modifiers=Qt.ShiftModifier)
    assert r1.isSelected() and not r2.isSelected()


def test_ctrl_click_without_drag_toggles_selection(
    scene_with_two_rects,
) -> None:
    scene, r1, r2 = scene_with_two_rects
    r1.setSelected(True)
    inside_r2 = QPointF(180, 170)
    _press(scene, inside_r2, modifiers=Qt.ControlModifier)
    _release(scene, inside_r2, modifiers=Qt.ControlModifier)
    # No clone was created, both originals selected.
    assert len(_annotations(scene)) == 2
    assert r1.isSelected() and r2.isSelected()
    # A second Ctrl+click removes it again.
    _press(scene, inside_r2, modifiers=Qt.ControlModifier)
    _release(scene, inside_r2, modifiers=Qt.ControlModifier)
    assert r1.isSelected() and not r2.isSelected()


def test_ctrl_drag_still_duplicates(qapp, scene_with_two_rects) -> None:
    scene, r1, r2 = scene_with_two_rects
    start = QPointF(180, 170)
    far = QPointF(280, 270)
    far_screen = QPoint(
        180 + QApplication.startDragDistance() + 100,
        170 + QApplication.startDragDistance() + 100,
    )
    _press(scene, start, modifiers=Qt.ControlModifier)
    _move(
        scene, far, modifiers=Qt.ControlModifier, screen_pos=far_screen
    )
    _release(scene, far, modifiers=Qt.ControlModifier)
    annots = _annotations(scene)
    assert len(annots) == 3  # the clone was added
    clone = next(a for a in annots if a not in (r1, r2))
    assert clone.isSelected()
    assert not r2.isSelected()
    # The clone followed the drag delta.
    assert clone.pos().x() == pytest.approx(100, abs=1.0)
    assert clone.pos().y() == pytest.approx(100, abs=1.0)


def test_rubber_band_selects_items(qapp, scene_with_two_rects) -> None:
    scene, r1, r2 = scene_with_two_rects
    view = PdfView()
    view.setScene(scene)
    view.resize(500, 500)
    view.show()
    qapp.processEvents()

    # Drag from an empty corner across both rectangles.
    start = view.mapFromScene(QPointF(390, 5))
    mid = view.mapFromScene(QPointF(100, 200))
    end = view.mapFromScene(QPointF(10, 390))
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(view.viewport(), mid)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end)
    qapp.processEvents()
    assert r1.isSelected() and r2.isSelected()
    view.hide()
    view.deleteLater()
