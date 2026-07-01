"""Smart alignment guides: snap-while-dragging (PdfScene.maybe_snap_move).

Qt's native "drag a selected+movable item" translation depends on
internal mouse-grab state that's flaky to reproduce with synthetic
QTest events under the offscreen platform. These tests instead drive
the real press/release code path (so `_interactive_drag_active` and
the guide lifecycle are exercised exactly as in the app) and call
`item.setPos(...)` directly to stand in for whatever delta Qt's drag
machinery would have produced -- that still goes through the real
`AnnotationItem.itemChange` -> `PdfScene.maybe_snap_move` hook, just
without depending on synthetic mouse-move delivery.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsSceneMouseEvent,
)

from annoter.controllers.align import AlignMode, compute_align_moves  # noqa: E402
from annoter.controllers.commands import MoveAnnotationsCommand  # noqa: E402
from annoter.controllers.geometry import item_scene_rect  # noqa: E402
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


def _ev(etype, pos: QPointF) -> QGraphicsSceneMouseEvent:
    ev = QGraphicsSceneMouseEvent(etype)
    ev.setScenePos(pos)
    ev.setButton(Qt.LeftButton)
    ev.setModifiers(Qt.NoModifier)
    ev.setScreenPos(QPoint(int(pos.x()), int(pos.y())))
    return ev


def test_snaps_to_sibling_left_edge_within_threshold(scene) -> None:
    anchor = RectangleItem(QRectF(0, 0, 40, 40))
    anchor.setParentItem(scene.page_item())

    moving = RectangleItem(QRectF(0, 0, 40, 40))
    moving.setPos(120, 120)
    moving.setParentItem(scene.page_item())
    moving.setSelected(True)

    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(130, 130)))
    assert scene._interactive_drag_active is True

    # 2px shy of a perfect left-edge match with the anchor (x=0) --
    # well inside the snap threshold.
    moving.setPos(2, 102)
    assert item_scene_rect(moving).left() == pytest.approx(0.0, abs=0.01)
    assert scene._guide_v is not None
    assert scene._guide_v.isVisible()

    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, QPointF(12, 112)))
    assert scene._interactive_drag_active is False
    assert not scene._guide_v.isVisible()


def test_snaps_to_page_center(scene) -> None:
    # Page is 400x400 (see fixture); center is (200, 200).
    moving = RectangleItem(QRectF(0, 0, 40, 40))
    moving.setPos(50, 50)
    moving.setParentItem(scene.page_item())
    moving.setSelected(True)

    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(70, 70)))
    moving.setPos(182, 182)  # center would land at (202, 202), within threshold
    r = item_scene_rect(moving)
    assert r.center().x() == pytest.approx(200.0, abs=0.01)
    assert r.center().y() == pytest.approx(200.0, abs=0.01)
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, QPointF(202, 202)))


def test_no_snap_when_far_from_any_target(scene) -> None:
    anchor = RectangleItem(QRectF(0, 0, 20, 20))
    anchor.setParentItem(scene.page_item())

    moving = RectangleItem(QRectF(0, 0, 20, 20))
    moving.setPos(150, 150)
    moving.setParentItem(scene.page_item())
    moving.setSelected(True)

    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(155, 155)))
    moving.setPos(151, 151)
    r = item_scene_rect(moving)
    assert r.x() == pytest.approx(151.0, abs=0.01)
    assert r.y() == pytest.approx(151.0, abs=0.01)
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, QPointF(156, 156)))


def test_no_snap_with_multi_selection(scene) -> None:
    anchor = RectangleItem(QRectF(0, 0, 20, 20))
    anchor.setParentItem(scene.page_item())

    a = RectangleItem(QRectF(0, 0, 20, 20))
    a.setPos(120, 120)
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(0, 0, 20, 20))
    b.setPos(200, 200)
    b.setParentItem(scene.page_item())
    a.setSelected(True)
    b.setSelected(True)

    scene.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, QPointF(130, 130)))
    # 2px from the anchor's left edge -- would snap with a single
    # selection, but two items are selected.
    a.setPos(2, 112)
    assert item_scene_rect(a).x() == pytest.approx(2.0, abs=0.01)
    scene.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, QPointF(12, 112)))


def test_no_snap_outside_interactive_drag(scene) -> None:
    """Without a press first (no real drag in progress), a plain
    setPos() must not be snapped even if it lands within threshold."""
    anchor = RectangleItem(QRectF(0, 0, 20, 20))
    anchor.setParentItem(scene.page_item())
    moving = RectangleItem(QRectF(0, 0, 20, 20))
    moving.setPos(150, 150)
    moving.setParentItem(scene.page_item())
    moving.setSelected(True)

    assert scene._interactive_drag_active is False
    moving.setPos(2, 150)  # 2px from anchor's left edge
    assert moving.pos().x() == pytest.approx(2.0)


def test_programmatic_move_is_never_snapped(scene) -> None:
    """Align/Undo/etc. call setPos() directly -- outside an interactive
    drag, itemChange's snap hook must be a complete no-op."""
    anchor = RectangleItem(QRectF(0, 0, 20, 20))
    anchor.setParentItem(scene.page_item())

    moving = RectangleItem(QRectF(0, 0, 20, 20))
    moving.setPos(2, 150)  # 2px from anchor's left edge -- within threshold
    moving.setParentItem(scene.page_item())
    moving.setSelected(True)

    stack = QUndoStack()
    cmd = MoveAnnotationsCommand(
        [(moving, QPointF(2, 150), QPointF(3, 151))]
    )
    stack.push(cmd)
    # Exact position from the command, NOT snapped to x=0.
    assert moving.pos().x() == pytest.approx(3.0)
    assert moving.pos().y() == pytest.approx(151.0)


def test_align_command_is_not_perturbed_by_snapping(scene) -> None:
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.setPos(0, 0)
    a.setParentItem(scene.page_item())
    b = RectangleItem(QRectF(0, 0, 10, 10))
    b.setPos(3, 80)  # left edge at x=3, close to a's x=0 but not equal
    b.setParentItem(scene.page_item())

    moves = compute_align_moves([a, b], AlignMode.LEFT)
    for it, _old, new in moves:
        it.setPos(new)
    assert item_scene_rect(a).left() == pytest.approx(
        item_scene_rect(b).left()
    )
