"""Dragging moves the selection, even under another annotation.

User report (2026-09-28): with one annotation selected, starting a drag
on a spot where an unselected annotation lies on top of it used to drop
the selection, select the top one and move that instead. A drag now
moves what is selected; a plain click there still selects the top one.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QUndoStack  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402
from annoter.views.pdf_view import PdfView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def canvas(qapp):
    scene = PdfScene()
    pm = QPixmap(1200, 900)
    pm.fill(QColor("white"))
    scene.set_page_pixmap(pm)
    stack = QUndoStack()
    scene.set_undo_stack(stack)
    view = PdfView()
    view.setScene(scene)
    view.resize(900, 700)
    view.show()
    view.set_zoom(1.0)
    view.centerOn(0, 0)
    yield scene, view, stack
    view.close()


def _rect(scene: PdfScene, rect: QRectF) -> RectangleItem:
    item = RectangleItem(rect)
    item.set_fill_enabled(True)  # hit anywhere inside
    item.setParentItem(scene.page_item())
    return item


def _vp(view: PdfView, scene: PdfScene, x: float, y: float) -> QPoint:
    return view.mapFromScene(scene.page_item().mapToScene(QPointF(x, y)))


def _drag(view: PdfView, start: QPoint, end: QPoint, mods=Qt.NoModifier) -> None:
    vp = view.viewport()
    QTest.mousePress(vp, Qt.LeftButton, mods, start)
    mid = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)
    QTest.mouseMove(vp, mid)
    QTest.mouseMove(vp, end)
    QTest.mouseRelease(vp, Qt.LeftButton, mods, end)


@pytest.fixture
def pile(canvas):
    """`under` selected, `top` (unselected) covering part of it."""
    scene, view, stack = canvas
    under = _rect(scene, QRectF(100, 100, 200, 150))
    top = _rect(scene, QRectF(200, 150, 200, 150))
    under.setSelected(True)
    return scene, view, stack, under, top


def test_drag_on_the_overlap_moves_the_selected_item(pile) -> None:
    scene, view, stack, under, top = pile
    _drag(view, _vp(view, scene, 250, 200), _vp(view, scene, 310, 240))
    assert under.pos().x() == pytest.approx(60, abs=2)
    assert under.pos().y() == pytest.approx(40, abs=2)
    assert top.pos() == QPointF(0, 0)
    assert under.isSelected() and not top.isSelected()
    assert stack.count() == 1  # one undoable move
    stack.undo()
    assert under.pos() == QPointF(0, 0)


def test_plain_click_on_the_overlap_selects_the_top_item(pile) -> None:
    scene, view, stack, under, top = pile
    QTest.mouseClick(
        view.viewport(), Qt.LeftButton, Qt.NoModifier, _vp(view, scene, 250, 200)
    )
    assert top.isSelected() and not under.isSelected()
    assert top.pos() == QPointF(0, 0) and under.pos() == QPointF(0, 0)
    assert stack.count() == 0


def test_the_whole_selection_moves(pile) -> None:
    scene, view, _stack, under, top = pile
    other = _rect(scene, QRectF(600, 600, 50, 50))
    other.setSelected(True)
    _drag(view, _vp(view, scene, 250, 200), _vp(view, scene, 280, 200))
    assert under.pos().x() == pytest.approx(30, abs=2)
    assert other.pos().x() == pytest.approx(30, abs=2)
    assert top.pos() == QPointF(0, 0)


def test_dragging_an_unselected_item_elsewhere_is_unchanged(pile) -> None:
    scene, view, _stack, under, top = pile
    # On `top` only (outside `under`): it gets selected and moved.
    _drag(view, _vp(view, scene, 380, 280), _vp(view, scene, 400, 280))
    assert top.isSelected() and not under.isSelected()
    assert top.pos().x() == pytest.approx(20, abs=2)
    assert under.pos() == QPointF(0, 0)


def test_dragging_the_selected_item_on_top_is_unchanged(canvas) -> None:
    scene, view, _stack = canvas
    under = _rect(scene, QRectF(100, 100, 200, 150))
    top = _rect(scene, QRectF(200, 150, 200, 150))
    top.setSelected(True)
    _drag(view, _vp(view, scene, 250, 200), _vp(view, scene, 270, 200))
    assert top.pos().x() == pytest.approx(20, abs=2)
    assert under.pos() == QPointF(0, 0)


def test_alt_click_on_the_overlap_still_opens_the_list(pile) -> None:
    scene, view, _stack, _under, _top = pile
    asked: list = []
    scene.pickRequested.connect(lambda sp, gp: asked.append(sp))
    QTest.mouseClick(
        view.viewport(), Qt.LeftButton, Qt.AltModifier, _vp(view, scene, 250, 200)
    )
    assert len(asked) == 1
    _release_modifiers(view)


def _release_modifiers(view: PdfView) -> None:
    """The application keeps the modifiers of the last input event: a
    stray Alt would turn off snapping in the tests that follow."""
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(1, 1))
    assert QApplication.keyboardModifiers() == Qt.NoModifier
