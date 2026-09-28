"""Leaders on texts and GD&T frames: CATIA's "Add Leader" (Lot M)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
from pathlib import Path

import fitz
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QUndoStack  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.commands import ResizeCommand  # noqa: E402
from annoter.model.gdt import GdtState  # noqa: E402
from annoter.model.styles import EndStyle  # noqa: E402
from annoter.services import pdf_export  # noqa: E402
from annoter.services.pdf_export import (  # noqa: E402
    read_annotations,
    write_annotations,
)
from annoter.views.items.callout import CalloutItem  # noqa: E402
from annoter.views.items.gdt import GdtAnnotationItem  # noqa: E402
from annoter.views.items.leaders import (  # noqa: E402
    BEND,
    DEFAULT_LEADER_END,
    TARGET,
    Leader,
    anchor_on_rect,
)
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.items.text import TextAnnotationItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402
from annoter.views.pdf_view import PdfView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _text(pos=QPointF(300, 200), text="Chamfer 1x45") -> TextAnnotationItem:
    return TextAnnotationItem(QPointF(pos), text)


def _parent_pt(item, local: QPointF) -> tuple[float, float]:
    p = item.mapToParent(local)
    return (round(p.x(), 6), round(p.y(), 6))


# ----------------------------------------------------------------------
# model
# ----------------------------------------------------------------------
def test_anchor_follows_the_side_facing_the_target() -> None:
    r = QRectF(100, 100, 80, 20)
    assert anchor_on_rect(r, QPointF(0, 300)) == QPointF(100, 110)
    assert anchor_on_rect(r, QPointF(400, 0)) == QPointF(180, 110)
    assert anchor_on_rect(r, QPointF(150, 0)) == QPointF(140, 100)
    assert anchor_on_rect(r, QPointF(150, 400)) == QPointF(140, 120)


def test_leader_dict_round_trip_with_units() -> None:
    ld = Leader((10.0, 20.0), ((5.0, 6.0),), EndStyle.CIRCLE)
    data = ld.to_dict(lambda v: v / 2)
    assert data == {"target": [5.0, 10.0], "end": "circle", "bends": [[2.5, 3.0]]}
    assert Leader.from_dict(data, lambda v: v * 2) == ld
    assert Leader.from_dict({"target": [1, 2], "end": "bogus"}).end is (
        DEFAULT_LEADER_END
    )
    assert "bends" not in Leader((1.0, 2.0)).to_dict()


# ----------------------------------------------------------------------
# item geometry
# ----------------------------------------------------------------------
def test_text_leader_bounds_shape_and_frame(qapp) -> None:
    t = _text()
    frame = t.frame_bounding_rect()
    assert t.boundingRect() == frame
    t.set_leaders([Leader((100.0, 400.0))])
    target_local = t.mapFromParent(QPointF(100, 400))
    assert t.boundingRect().contains(target_local)
    assert t.frame_bounding_rect() == frame
    shape = t.shape()
    path = t.leader_path(0)
    mid = (path[0] + path[-1]) / 2
    assert shape.contains(mid)  # on the leader
    # Inside the bounding rect but away from both frame and leader.
    far = QPointF(target_local.x() + 10, frame.top() + 20)
    assert t.boundingRect().contains(far)
    assert not shape.contains(far)


def test_moving_the_text_keeps_the_target_on_the_page(qapp) -> None:
    t = _text()
    t.set_leaders([Leader((100.0, 400.0))])
    start_before = _parent_pt(t, t.leader_path(0)[0])
    t.setPos(t.pos() + QPointF(50, -30))
    path = t.leader_path(0)
    assert _parent_pt(t, path[-1]) == (100.0, 400.0)
    assert _parent_pt(t, path[0]) != start_before
    # The start is on the frame side facing the target (left, here).
    frame = t.leader_frame_rect()
    assert path[0].x() == pytest.approx(frame.left())


def test_gdt_leader_attaches_to_the_cells_not_the_notes(qapp) -> None:
    g = GdtAnnotationItem(GdtState(), QPointF(200, 200))
    g.set_leaders([Leader((600.0, 100.0))])
    start = g.leader_path(0)[0]
    cells = QRectF()
    for r in g._border_rects:
        cells = QRectF(r) if cells.isNull() else cells.united(r)
    assert start.x() == pytest.approx(cells.right())
    assert start.y() == pytest.approx(cells.center().y())


def test_target_handle_drag_is_undoable(qapp) -> None:
    t = _text()
    t.set_leaders([Leader((100.0, 400.0), ((150.0, 380.0),))])
    handles = t.handle_positions()
    assert (TARGET, 0) in handles and (BEND, 0, 0) in handles
    before = t.geom_snapshot()
    t.apply_resize((TARGET, 0), t.mapFromParent(QPointF(90, 420)))
    t.apply_resize((BEND, 0, 0), t.mapFromParent(QPointF(160, 390)))
    assert t.leaders()[0].target == (90.0, 420.0)
    assert t.leaders()[0].bends == ((160.0, 390.0),)
    stack = QUndoStack()
    stack.push(ResizeCommand(t, before, t.geom_snapshot()))
    stack.undo()
    assert t.leaders() == [Leader((100.0, 400.0), ((150.0, 380.0),))]
    stack.redo()
    assert t.leaders()[0].target == (90.0, 420.0)


def test_leader_hit_bend_insertion_and_bend_hit(qapp) -> None:
    t = _text()
    t.set_leaders([Leader((100.0, 400.0))])
    path = t.leader_path(0)
    mid = (path[0] + path[-1]) / 2
    assert t.leader_at(mid) == 0
    assert t.leader_at(mid + QPointF(40, 40)) is None  # off to the side
    leaders = t.leaders_with_bend_added(0, mid + QPointF(1, 0))
    assert len(leaders[0].bends) == 1
    t.set_leaders(leaders)
    assert t.leader_bend_at(0, t.mapFromParent(QPointF(*leaders[0].bends[0]))) == 0


def test_clone_and_document_resize_carry_the_leaders(qapp) -> None:
    t = _text()
    t.set_leaders([Leader((100.0, 400.0), end=EndStyle.CIRCLE)])
    assert t.clone().leaders() == t.leaders()
    g = GdtAnnotationItem(GdtState(), QPointF(10, 10))
    g.set_leaders([Leader((50.0, 60.0))])
    assert g.clone().leaders() == g.leaders()
    g.scale_geometry(2.0)
    assert g.leaders()[0].target == (100.0, 120.0)


def test_callouts_and_frame_notes_have_no_leader_handles(qapp) -> None:
    c = CalloutItem(QPointF(10, 10), "x")
    assert not any(isinstance(k, tuple) for k in c.handle_positions())


# ----------------------------------------------------------------------
# placing a leader on the canvas
# ----------------------------------------------------------------------
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
    yield scene, view, stack
    view.close()


def test_click_places_a_leader_in_one_undo_step(canvas) -> None:
    scene, view, stack = canvas
    t = _text()
    t.setParentItem(scene.page_item())
    t.setSelected(True)
    states: list[bool] = []
    scene.leaderPlacementChanged.connect(states.append)
    scene.begin_leader_placement(t)
    assert scene.leader_placement_owner() is t
    at = view.mapFromScene(scene.page_item().mapToScene(QPointF(120, 420)))
    QTest.mouseMove(view.viewport(), at)
    assert t.leader_preview() is not None
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    assert states == [True, False]
    assert t.leader_preview() is None
    assert len(t.leaders()) == 1
    tx, ty = t.leaders()[0].target
    assert tx == pytest.approx(120, abs=1.5) and ty == pytest.approx(420, abs=1.5)
    assert t.isSelected()  # the click did not change the selection
    assert stack.count() == 1
    stack.undo()
    assert t.leaders() == []


def test_escape_and_page_change_cancel_the_placement(canvas) -> None:
    scene, _view, stack = canvas
    t = _text()
    t.setParentItem(scene.page_item())
    t.setSelected(True)
    scene.begin_leader_placement(t)
    t.set_leader_preview(QPointF(10, 10))
    scene.cancel_current_action()
    assert scene.leader_placement_owner() is None
    assert t.leader_preview() is None
    assert t.isSelected()  # Esc only left the mode
    scene.begin_leader_placement(t)
    scene.detach_children()
    assert scene.leader_placement_owner() is None
    assert stack.count() == 0


def test_only_items_that_can_carry_leaders_enter_the_mode(canvas) -> None:
    scene, _view, _stack = canvas
    rect = RectangleItem(QRectF(0, 0, 10, 10))
    rect.setParentItem(scene.page_item())
    scene.begin_leader_placement(rect)
    assert scene.leader_placement_owner() is None


# ----------------------------------------------------------------------
# persistence
# ----------------------------------------------------------------------
@pytest.fixture
def blank_doc():
    doc = fitz.open()
    doc.new_page(width=600, height=400)
    yield doc
    doc.close()


def _reopen(doc: fitz.Document) -> fitz.Document:
    tmp = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".pdf")
    tmp.close()
    doc.save(tmp.name, garbage=3, deflate=True)
    return fitz.open(tmp.name)


def test_text_leaders_round_trip(qapp, blank_doc) -> None:
    t = _text(QPointF(300, 200))
    leaders = [
        Leader((100.0, 400.0)),
        Leader((600.0, 120.0), ((520.0, 120.0),), EndStyle.CIRCLE),
    ]
    t.set_leaders(leaders)
    frame_before = pdf_export._scene_rect(t)
    write_annotations(blank_doc, {0: [t]}, dpi=150)
    doc = _reopen(blank_doc)
    annots = [(a.type[1], a.line_ends) for a in doc[0].annots()]
    items = read_annotations(doc, dpi=150)[0]
    doc.close()
    # The FreeText keeps the size of the text; each leader is a separate
    # line annotation (a PolyLine when bent) for other viewers.
    assert [k for k, _e in annots] == ["FreeText", "Line", "PolyLine"]
    assert annots[1][1][1] == fitz.PDF_ANNOT_LE_CLOSED_ARROW
    assert annots[2][1][1] == fitz.PDF_ANNOT_LE_CIRCLE
    assert frame_before.width() < 200
    # ...but Annoter reads back one text carrying both leaders.
    assert len(items) == 1
    back = items[0]
    assert type(back) is TextAnnotationItem
    assert len(back.leaders()) == 2
    for got, want in zip(back.leaders(), leaders):
        assert got.end is want.end
        assert got.target == pytest.approx(want.target, abs=0.01)
        assert len(got.bends) == len(want.bends)


def test_gdt_leader_round_trip_and_second_save(qapp, blank_doc) -> None:
    g = GdtAnnotationItem(GdtState(), QPointF(200, 200))
    g.set_leaders([Leader((500.0, 350.0), end=EndStyle.TRIANGLE_FILLED)])
    write_annotations(blank_doc, {0: [g]}, dpi=150)
    first = _reopen(blank_doc)
    items = read_annotations(first, dpi=150)
    write_annotations(first, items, dpi=150)  # save again
    second = _reopen(first)
    first.close()
    kinds = [a.type[1] for a in second[0].annots()]
    out = read_annotations(second, dpi=150)[0]
    second.close()
    assert kinds == ["Square", "Line"]  # companions are not piled up
    assert isinstance(out[0], GdtAnnotationItem)
    assert out[0].leaders()[0].end is EndStyle.TRIANGLE_FILLED
    assert out[0].leaders()[0].target == pytest.approx((500.0, 350.0), abs=0.01)


def test_frame_picture_leaves_the_leaders_out(qapp) -> None:
    g = GdtAnnotationItem(GdtState(), QPointF(200, 200))
    plain = pdf_export._rasterize_item_planes(g, 150)
    g.set_leaders([Leader((500.0, 350.0))])
    with_leader = pdf_export._rasterize_item_planes(g, 150)
    assert with_leader[:4] == plain[:4]  # same picture, same size
    assert not getattr(g, "_leaders_hidden", False)


# ----------------------------------------------------------------------
# main window: menus and command
# ----------------------------------------------------------------------
@pytest.fixture
def win(qapp, tmp_path: Path):
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=842, height=595)
    doc.save(str(path))
    doc.close()
    w = MainWindow()
    w.open_path(path)
    yield w
    w._on_close()
    w.close()


def test_context_menu_offers_add_leader_on_texts_and_frames(win) -> None:
    t = _text(QPointF(300, 200))
    win._scene.push_add(t)
    menu = win._build_context_menu(t.mapToScene(t.content_rect().center()))
    assert "Add Leader" in menu.entry_texts()
    assert "Remove Leaders" not in menu.entry_texts()
    menu.entry("Add Leader").trigger()
    assert win._scene.leader_placement_owner() is t
    # A right-click while placing cancels instead of opening a menu.
    win._show_context_menu(None, QPointF(10, 10))
    assert win._scene.leader_placement_owner() is None

    rect = RectangleItem(QRectF(600, 400, 40, 40))
    win._scene.push_add(rect)
    menu = win._build_context_menu(QPointF(620, 420))
    assert "Add Leader" not in menu.entry_texts()


def test_context_menu_on_a_leader(win) -> None:
    t = _text(QPointF(300, 200))
    win._scene.push_add(t)
    t.set_leaders([Leader((100.0, 400.0))])
    path = t.leader_path(0)
    on_leader = t.mapToScene((path[0] + path[-1]) / 2)
    menu = win._build_context_menu(on_leader)
    assert "Leader" in menu.row_captions()
    assert menu.button("leader:" + EndStyle.CLOSED_ARROW.value).isChecked()
    assert {"Add Leader", "Add Bend Point", "Remove Leader"} <= set(
        menu.entry_texts()
    )
    menu.trigger("leader:" + EndStyle.CIRCLE.value)
    assert t.leaders()[0].end is EndStyle.CIRCLE

    menu = win._build_context_menu(on_leader)
    menu.entry("Add Bend Point").trigger()
    assert len(t.leaders()[0].bends) == 1

    menu = win._build_context_menu(on_leader)
    menu.entry("Remove Leader").trigger()
    assert t.leaders() == []
    stack = win._undo_group.activeStack()
    stack.undo()
    assert len(t.leaders()) == 1


def test_add_leader_command(win) -> None:
    labels = [e.label for e in win._command_entries()]
    assert "Add Leader" in labels
    win.act_add_leader.trigger()  # nothing selected
    assert win._scene.leader_placement_owner() is None
    g = GdtAnnotationItem(GdtState(), QPointF(200, 200))
    win._scene.push_add(g)
    for it in win._scene.selectedItems():
        it.setSelected(False)
    g.setSelected(True)
    win.act_add_leader.trigger()
    assert win._scene.leader_placement_owner() is g
    assert win._tool_hint.text().startswith("Add leader")
    win._scene.end_leader_placement()
    assert not win._tool_hint.text().startswith("Add leader")


def test_leader_bend_added_at_the_tip_goes_mid_segment(qapp) -> None:
    t = _text()
    t.set_leaders([Leader((100.0, 400.0))])
    path = t.leader_path(0)
    leaders = t.leaders_with_bend_added(0, path[-1])  # on the tip
    t.set_leaders(leaders)
    bend = t.mapFromParent(QPointF(*leaders[0].bends[0]))
    mid = (path[0] + path[-1]) / 2
    assert bend.x() == pytest.approx(mid.x(), abs=0.01)
    assert bend.y() == pytest.approx(mid.y(), abs=0.01)


# ----------------------------------------------------------------------
# right angles on leaders, like on bent lines (2026-09-28)
# ----------------------------------------------------------------------
def _mouse(scene, etype, pos: QPointF, mods=Qt.NoModifier) -> None:
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    ev = QGraphicsSceneMouseEvent(etype)
    ev.setScenePos(pos)
    ev.setButton(Qt.LeftButton)
    ev.setButtons(Qt.LeftButton)
    ev.setModifiers(mods)
    ev.setScreenPos(QPoint(int(pos.x()), int(pos.y())))
    handler = {
        QEvent.GraphicsSceneMousePress: scene.mousePressEvent,
        QEvent.GraphicsSceneMouseMove: scene.mouseMoveEvent,
        QEvent.GraphicsSceneMouseRelease: scene.mouseReleaseEvent,
    }[etype]
    handler(ev)


def _drag_handle(scene, item, start_parent, end_parent, mods) -> None:
    from PySide6.QtCore import QEvent

    page = scene.page_item()
    a = page.mapToScene(QPointF(*start_parent))
    b = page.mapToScene(QPointF(*end_parent))
    _mouse(scene, QEvent.GraphicsSceneMousePress, a)
    _mouse(scene, QEvent.GraphicsSceneMouseMove, b, mods)
    _mouse(scene, QEvent.GraphicsSceneMouseRelease, b, mods)


@pytest.fixture
def leader_scene(qapp):
    scene = PdfScene()
    pm = QPixmap(1200, 900)
    pm.fill(QColor("white"))
    scene.set_page_pixmap(pm)
    stack = QUndoStack()
    scene.set_undo_stack(stack)
    yield scene, stack


def test_shift_squares_a_leader_bend_and_tip(leader_scene) -> None:
    scene, stack = leader_scene
    g = GdtAnnotationItem(GdtState(), QPointF(200, 200))
    g.setParentItem(scene.page_item())
    g.set_leaders([Leader((600.0, 400.0), ((450.0, 215.0),))])
    g.setSelected(True)
    start = g.mapToParent(g.leader_path(0)[0])  # frame attachment
    # Bend dragged a little off the horizontal, Shift held: the first
    # segment comes out horizontal (the classic frame + stub leader).
    _drag_handle(scene, g, (450.0, 215.0), (470.0, start.y() + 9), Qt.ShiftModifier)
    bend = g.leaders()[0].bends[0]
    assert bend[1] == pytest.approx(start.y(), abs=0.01)
    assert bend[0] == pytest.approx(470.0, abs=0.01)
    # Tip dragged with Shift below the bend: the second segment is
    # vertical.
    _drag_handle(scene, g, (600.0, 400.0), (478.0, 420.0), Qt.ShiftModifier)
    tip = g.leaders()[0].target
    assert tip[0] == pytest.approx(bend[0], abs=0.01)
    assert tip[1] == pytest.approx(420.0, abs=0.01)
    assert stack.count() == 2  # each drag is one undo step


def test_leader_tip_is_magnetic_to_right_angles(leader_scene) -> None:
    scene, _stack = leader_scene
    t = _text(QPointF(300, 200))
    t.setParentItem(scene.page_item())
    t.set_leaders([Leader((500.0, 400.0), ((500.0, 215.0),))])
    t.setSelected(True)
    bend = t.leaders()[0].bends[0]
    # Released 2 px off the vertical under the bend, no modifier.
    _drag_handle(scene, t, (500.0, 400.0), (bend[0] + 2, 420.0), Qt.NoModifier)
    assert t.leaders()[0].target[0] == pytest.approx(bend[0], abs=0.01)
    # Alt turns the magnet off, as everywhere else.
    _drag_handle(
        scene, t, (bend[0], 420.0), (bend[0] + 2, 430.0), Qt.AltModifier
    )
    assert t.leaders()[0].target[0] == pytest.approx(bend[0] + 2, abs=0.01)
