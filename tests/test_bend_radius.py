"""A corner radius on each bend, for every kind of bent path, and the
GD&T frame's notes set close to the frame (2026-09-28)."""

from __future__ import annotations

import json
import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
from pathlib import Path

import fitz
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QUndoStack  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDoubleSpinBox,
    QGraphicsScene,
)

from annoter.controllers import bend_radius  # noqa: E402
from annoter.controllers.commands import ResizeCommand  # noqa: E402
from annoter.controllers.convert import (  # noqa: E402
    polygon_to_polyline,
    polyline_to_polygon,
)
from annoter.controllers.geometry import pt_to_px, px_to_pt  # noqa: E402
from annoter.model.gdt import GdtState  # noqa: E402
from annoter.services.palette import PaletteStore  # noqa: E402
from annoter.services.pdf_export import (  # noqa: E402
    read_annotations,
    write_annotations,
)
from annoter.views.context_menu import MenuSpinBox  # noqa: E402
from annoter.views.items.gdt import _NOTE_GAP, GdtAnnotationItem  # noqa: E402
from annoter.views.items.leaders import Leader  # noqa: E402
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.items.poly import PolygonItem, PolylineItem  # noqa: E402
from annoter.views.items.rounding import (  # noqa: E402
    has_rounding,
    rounded_path,
    rounded_points,
)
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.items.text import TextAnnotationItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.properties_dock import PropertiesDock  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def _corner_line() -> LineItem:
    """A line turning a right angle at (110, 10)."""
    line = LineItem(QPointF(10, 10), QPointF(110, 110))
    line.set_bends([QPointF(110, 10)])
    return line


def _text_with_leaders() -> TextAnnotationItem:
    t = TextAnnotationItem(QPointF(300, 200), "Chamfer 1x45")
    t.set_leaders(
        [
            Leader((100.0, 400.0)),
            Leader((600.0, 60.0), ((520.0, 120.0), (560.0, 60.0))),
        ]
    )
    return t


# ----------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------
def test_fillet_is_tangent_and_centered(qapp) -> None:
    pts = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100)]
    out = rounded_points(pts, [0.0, 20.0, 0.0])
    assert out[0] == QPointF(0, 0) and out[-1] == QPointF(100, 100)
    arc = out[1:-1]
    assert len(arc) >= 3
    assert arc[0].x() == pytest.approx(80.0) and arc[0].y() == pytest.approx(0.0)
    assert arc[-1].x() == pytest.approx(100.0)
    assert arc[-1].y() == pytest.approx(20.0)
    center = QPointF(80, 20)
    for p in arc:
        assert _dist(p, center) == pytest.approx(20.0, abs=1e-6)
    # The drawn path keeps clear of the sharp corner by r * (sqrt2 - 1).
    path = rounded_path(pts, [0.0, 20.0, 0.0])
    nearest = min(
        _dist(path.pointAtPercent(k / 400), QPointF(100, 0))
        for k in range(401)
    )
    assert nearest == pytest.approx(20.0 * (math.sqrt(2) - 1), abs=0.3)


def test_radius_is_clamped_to_half_the_segments(qapp) -> None:
    pts = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100)]
    arc = rounded_points(pts, [0.0, 1000.0, 0.0])[1:-1]
    assert arc[0].x() == pytest.approx(50.0)  # half the first segment
    assert arc[-1].y() == pytest.approx(50.0)
    # Straight or zero-radius corners stay sharp; ends are never rounded.
    straight = [QPointF(0, 0), QPointF(50, 0), QPointF(100, 0)]
    assert rounded_points(straight, [0, 10, 0]) == straight
    assert rounded_points(pts, [30.0, 0.0, 30.0]) == pts
    assert not has_rounding([0.0, 0.0]) and has_rounding([0.0, 0.1])


def test_closed_path_rounds_every_vertex(qapp) -> None:
    square = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100), QPointF(0, 100)]
    out = rounded_points(square, [10.0] * 4, closed=True)
    assert all(p not in out for p in square)
    path = rounded_path(square, [10.0] * 4, closed=True)
    assert path.boundingRect() == QRectF(0, 0, 100, 100)
    assert not path.contains(QPointF(0.5, 0.5))
    assert path.contains(QPointF(50, 50))


def _render(item) -> QImage:
    scene = QGraphicsScene()
    scene.setSceneRect(0, 0, 200, 200)
    scene.addItem(item)
    img = QImage(200, 200, QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    scene.render(painter, QRectF(0, 0, 200, 200), QRectF(0, 0, 200, 200))
    painter.end()
    scene.removeItem(item)
    return img


def test_a_rounded_bend_is_drawn_round(qapp) -> None:
    line = _corner_line()
    line.set_color(QColor("black"))
    line.set_stroke(2.0)
    assert _render(line).pixelColor(110, 10).alpha() > 0
    line.set_bend_radii([40.0])
    img = _render(line)
    assert img.pixelColor(110, 10).alpha() == 0  # the corner is cut
    # ...by an arc through the fillet's middle.
    mid = QPointF(70 + 40 / math.sqrt(2), 50 - 40 / math.sqrt(2))
    assert img.pixelColor(round(mid.x()), round(mid.y())).alpha() > 0


# ----------------------------------------------------------------------
# items: storage, edits, undo, scaling, copies
# ----------------------------------------------------------------------
def test_line_radii_follow_bend_edits(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(300, 0))
    line.set_bends([QPointF(100, 50), QPointF(200, -50)])
    assert line.bend_radii() == [0.0, 0.0]
    line.set_bend_radii([5.0, 7.0])
    line.set_bends([QPointF(100, 60), QPointF(200, -60)])  # moved
    assert line.bend_radii() == [5.0, 7.0]
    line.remove_bend(0)
    assert line.bend_radii() == [7.0]
    line.insert_bend_near(QPointF(40, 20))
    assert sorted(line.bend_radii()) == [0.0, 7.0]
    old = line.geom_snapshot()
    line.set_bend_radii([3.0, 3.0])
    cmd = ResizeCommand(line, old, line.geom_snapshot())
    cmd.undo()
    assert sorted(line.bend_radii()) == [0.0, 7.0]
    cmd.redo()
    assert line.bend_radii() == [3.0, 3.0]
    line.scale_geometry(2.0)
    assert line.bend_radii() == [6.0, 6.0]
    assert line.clone().bend_radii() == [6.0, 6.0]
    arrow = ArrowItem(QPointF(0, 0), QPointF(100, 100))
    arrow.set_bends([QPointF(100, 0)], [12.0])
    assert arrow.clone().bend_radii() == [12.0]


def test_poly_radii_and_conversions(qapp) -> None:
    pts = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100), QPointF(0, 100)]
    pl = PolylineItem(pts)
    assert pl.corner_indices() == [1, 2]
    pl.set_bend_radii([9.0, 10.0, 10.0, 9.0])
    assert pl.bend_radii() == [9.0, 10.0, 10.0, 9.0]  # ends kept, not drawn
    old = pl.geom_snapshot()
    pl.set_points([QPointF(p.x() + 1, p.y()) for p in pts])  # moved
    assert pl.bend_radii()[1] == 10.0
    pl.set_points(pts + [QPointF(-50, 50)])  # a vertex added: sharp again
    assert pl.bend_radii() == [0.0] * 5
    pl.apply_geom(old)
    assert pl.bend_radii()[2] == 10.0
    pl.apply_geom([QPointF(0, 0), QPointF(5, 5)])  # pre-radius snapshot
    assert pl.bend_radii() == [0.0, 0.0]

    pl = PolylineItem(pts)
    pl.set_bend_radii([0.0, 8.0, 4.0, 0.0])
    pg = polyline_to_polygon(pl)
    assert isinstance(pg, PolygonItem)
    assert pg.bend_radii() == [0.0, 8.0, 4.0, 0.0]
    assert pg.corner_indices() == [0, 1, 2, 3]
    assert polygon_to_polyline(pg).bend_radii() == [0.0, 8.0, 4.0, 0.0]
    pg.scale_geometry(0.5)
    assert pg.bend_radii() == [0.0, 4.0, 2.0, 0.0]
    assert pg.clone().bend_radii() == [0.0, 4.0, 2.0, 0.0]


def test_leader_radii(qapp) -> None:
    ld = Leader((100.0, 0.0), ((50.0, 0.0), (50.0, 50.0)))
    assert ld.bend_radii() == [0.0, 0.0]
    ld = ld.with_bend_radius(1, 6.0)
    assert ld.bend_radii() == [0.0, 6.0]
    assert ld.without_bend(0).bend_radii() == [6.0]
    assert ld.scaled(2.0).bend_radii() == [0.0, 12.0]
    data = ld.to_dict(lambda v: v / 2)
    assert data["radii"] == [0.0, 3.0]
    assert Leader.from_dict(data, lambda v: v * 2) == ld
    assert "radii" not in Leader((1.0, 2.0), ((3.0, 4.0),)).to_dict()

    t = _text_with_leaders()
    sharp = len(t.leader_drawn_points(1))
    leaders = t.leaders()
    leaders[1] = leaders[1].with_bend_radius(0, 15.0)
    t.set_leaders(leaders)
    assert len(t.leader_drawn_points(1)) > sharp
    local = t.mapFromParent(QPointF(500, 110))
    added = t.leaders_with_bend_added(1, local)
    assert added[1].bend_radii() == [0.0, 15.0, 0.0]


# ----------------------------------------------------------------------
# one vocabulary for every kind (controllers/bend_radius.py)
# ----------------------------------------------------------------------
def test_corners_of_every_kind(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(300, 0))
    line.set_bends([QPointF(100, 50), QPointF(200, -50)])
    assert bend_radius.corners(line) == [("bend", 0), ("bend", 1)]
    pts = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100), QPointF(0, 100)]
    assert bend_radius.corners(PolylineItem(pts)) == [
        ("vertex", 1), ("vertex", 2)
    ]
    assert len(bend_radius.corners(PolygonItem(pts))) == 4
    t = _text_with_leaders()
    assert bend_radius.corners(t) == [("leader", 1, 0), ("leader", 1, 1)]
    g = GdtAnnotationItem(GdtState(), QPointF(200, 200))
    assert not bend_radius.has_corners(g)
    g.set_leaders([Leader((500.0, 350.0), ((500.0, 250.0),))])
    assert bend_radius.corners(g) == [("leader", 0, 0)]
    assert not bend_radius.has_corners(RectangleItem(QRectF(0, 0, 9, 9)))
    assert not bend_radius.has_corners(LineItem(QPointF(0, 0), QPointF(9, 9)))


def test_set_one_or_all_radii(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(300, 0))
    line.set_bends([QPointF(100, 50), QPointF(200, -50)])
    bend_radius.set_radius(line, ("bend", 1), 5.0)
    assert line.bend_radii() == [0.0, 5.0]
    assert bend_radius.common_radius(line) is None
    bend_radius.set_all_radii(line, 3.0)
    assert line.bend_radii() == [3.0, 3.0]
    assert bend_radius.common_radius(line) == 3.0

    pl = PolylineItem(
        [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100), QPointF(0, 100)]
    )
    bend_radius.set_all_radii(pl, 4.0)
    assert pl.bend_radii() == [0.0, 4.0, 4.0, 0.0]  # never the ends

    t = _text_with_leaders()
    bend_radius.set_radius(t, ("leader", 1, 1), 8.0)
    assert t.leaders()[1].bend_radii() == [0.0, 8.0]
    old = t.geom_snapshot()
    bend_radius.set_all_radii(t, 2.0)
    assert bend_radius.radii(t) == [2.0, 2.0]
    ResizeCommand(t, old, t.geom_snapshot()).undo()
    assert t.leaders()[1].bend_radii() == [0.0, 8.0]


def test_corner_under_the_pointer(qapp) -> None:
    line = _corner_line()
    assert bend_radius.corner_at(line, QPointF(112, 11)) == ("bend", 0)
    assert bend_radius.corner_at(line, QPointF(60, 10)) is None
    pts = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100)]
    pl = PolylineItem(pts)
    assert bend_radius.corner_at(pl, QPointF(101, 1)) == ("vertex", 1)
    assert bend_radius.corner_at(pl, QPointF(1, 1)) is None  # an end
    pg = PolygonItem(pts)
    assert bend_radius.corner_at(pg, QPointF(1, 1)) == ("vertex", 0)
    t = _text_with_leaders()
    on_bend = t.mapFromParent(QPointF(561, 61))
    assert bend_radius.corner_at(t, on_bend) == ("leader", 1, 1)


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


def _close(a: list[QPointF], b: list[QPointF], tol: float = 0.05) -> bool:
    return len(a) == len(b) and all(_dist(p, q) <= tol for p, q in zip(a, b))


def test_rounded_bends_round_trip(qapp, blank_doc) -> None:
    line = LineItem(QPointF(20, 20), QPointF(220, 220))
    line.set_bends([QPointF(220, 20)], [30.0])
    arrow = ArrowItem(QPointF(300, 20), QPointF(500, 200))
    arrow.set_bends([QPointF(400, 100), QPointF(500, 100)], [0.0, 12.5])
    pl = PolylineItem(
        [QPointF(20, 300), QPointF(200, 300), QPointF(200, 450), QPointF(60, 500)]
    )
    pl.set_bend_radii([0.0, 25.0, 10.0, 0.0])
    pg = PolygonItem(
        [QPointF(600, 300), QPointF(800, 300), QPointF(800, 500), QPointF(600, 500)]
    )
    pg.set_bend_radii([20.0, 20.0, 0.0, 20.0])
    plain = PolylineItem([QPointF(900, 50), QPointF(950, 90), QPointF(990, 50)])
    t = _text_with_leaders()
    leaders = t.leaders()
    leaders[1] = leaders[1].with_bend_radius(0, 18.0)
    t.set_leaders(leaders)

    items = [line, arrow, pl, pg, plain, t]
    write_annotations(blank_doc, {0: items}, dpi=150)
    doc = _reopen(blank_doc)
    page = doc[0]
    counts, subjects = [], []
    for a in page.annots():
        counts.append((a.type[1], len(a.vertices or [])))
        subjects.append(a.info.get("subject", ""))
    back = read_annotations(doc, dpi=150)[0]
    doc.close()

    # External viewers get the arcs as extra vertices...
    assert counts[0] == ("PolyLine", counts[0][1]) and counts[0][1] > 3
    assert counts[2][1] > 4 and counts[3][1] > 4
    assert counts[4] == ("PolyLine", 3)  # no radius: unchanged
    assert "vertices_pt" not in json.loads(subjects[4])
    leader_lines = [n for kind, n in counts if kind == "PolyLine"][-1:]
    assert leader_lines and leader_lines[0] > 4  # the bent, rounded leader

    # ...Annoter reads back the sharp vertices and the radii.
    by_type = {}
    for it in back:
        by_type.setdefault(type(it), []).append(it)
    got_line = by_type[LineItem][0]
    assert _close(got_line.path_points(), line.path_points())
    assert got_line.bend_radii() == pytest.approx([30.0], abs=0.01)
    got_arrow = by_type[ArrowItem][0]
    assert _close(got_arrow.bends(), arrow.bends())
    assert got_arrow.bend_radii() == pytest.approx([0.0, 12.5], abs=0.01)
    polylines = sorted(by_type[PolylineItem], key=lambda it: len(it.points()))
    assert _close(polylines[0].points(), plain.points())
    assert polylines[0].bend_radii() == [0.0, 0.0, 0.0]
    assert _close(polylines[1].points(), pl.points())
    assert polylines[1].bend_radii() == pytest.approx(pl.bend_radii(), abs=0.01)
    got_pg = by_type[PolygonItem][0]
    assert _close(got_pg.points(), pg.points())
    assert got_pg.bend_radii() == pytest.approx(pg.bend_radii(), abs=0.01)
    got_t = by_type[TextAnnotationItem][0]
    assert got_t.leaders()[1].bend_radii() == pytest.approx([18.0, 0.0], abs=0.01)


# ----------------------------------------------------------------------
# context menu
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


def _spin(menu) -> MenuSpinBox:
    spin = menu.findChild(QDoubleSpinBox, "ContextRadiusSpin")
    assert spin is not None
    return spin


def test_radius_row_on_a_line_bend(win) -> None:
    line = LineItem(QPointF(100, 100), QPointF(400, 100))
    line.set_bends([QPointF(200, 200), QPointF(300, 50)])
    win._scene.push_add(line)
    stack = win._undo_group.activeStack()
    win._properties_dock.set_unit("mm")

    # Not on a bend: no Radius row.
    menu = win._build_context_menu(line.mapToScene(QPointF(150, 150)))
    assert "Radius" not in menu.row_captions()

    menu = win._build_context_menu(line.mapToScene(QPointF(300, 50)))
    assert "Radius" in menu.row_captions()
    spin = _spin(menu)
    assert spin.suffix() == " mm" and spin.value() == 0.0
    depth = stack.count()
    spin.setValue(5.0)  # previews live on that bend only
    assert line.bend_radii()[0] == 0.0
    assert px_to_pt(line.bend_radii()[1]) == pytest.approx(5.0 * 72 / 25.4, abs=0.01)
    spin.setValue(6.0)
    menu.aboutToHide.emit()  # closing lands one undo step
    assert stack.count() == depth + 1
    menu.aboutToHide.emit()
    assert stack.count() == depth + 1
    stack.undo()
    assert line.bend_radii() == [0.0, 0.0]
    stack.redo()
    assert line.bend_radii()[1] == pytest.approx(pt_to_px(6.0 * 72 / 25.4))

    # The menu opens on the current radius; "every bend" copies it.
    menu = win._build_context_menu(line.mapToScene(QPointF(300, 50)))
    assert _spin(menu).value() == pytest.approx(6.0)
    assert menu.button("radius_all").isEnabled()
    menu.trigger("radius_all")
    assert line.bend_radii()[0] == pytest.approx(line.bend_radii()[1])
    assert stack.count() == depth + 2


def test_escape_restores_the_radius(win) -> None:
    pl = PolylineItem(
        [QPointF(100, 300), QPointF(250, 300), QPointF(250, 450)]
    )
    win._scene.push_add(pl)
    stack = win._undo_group.activeStack()
    menu = win._build_context_menu(pl.mapToScene(QPointF(250, 300)))
    spin = _spin(menu)
    assert not menu.button("radius_all").isEnabled()  # a single corner
    depth = stack.count()
    spin.setValue(8.0)
    assert pl.bend_radii()[1] > 0.0
    QTest.keyClick(spin, Qt.Key_Escape)
    assert pl.bend_radii() == [0.0, 0.0, 0.0]
    menu.aboutToHide.emit()
    assert stack.count() == depth

    menu = win._build_context_menu(pl.mapToScene(QPointF(250, 300)))
    spin = _spin(menu)
    spin.setValue(4.0)
    QTest.keyClick(spin, Qt.Key_Return)  # Enter applies
    assert stack.count() == depth + 1
    assert pl.bend_radii()[1] > 0.0


def test_radius_row_on_a_leader_bend(win) -> None:
    t = _text_with_leaders()
    win._scene.push_add(t)
    stack = win._undo_group.activeStack()
    bend = t.mapToScene(t.mapFromParent(QPointF(520, 120)))
    menu = win._build_context_menu(bend)
    assert "Radius" in menu.row_captions()
    assert "Remove Bend Point" in menu.entry_texts()
    _spin(menu).setValue(3.0)
    menu.aboutToHide.emit()
    assert t.leaders()[1].bend_radii()[0] > 0.0
    assert t.leaders()[1].bend_radii()[1] == 0.0
    stack.undo()
    assert t.leaders()[1].bend_radii() == [0.0, 0.0]


# ----------------------------------------------------------------------
# inspector
# ----------------------------------------------------------------------
@pytest.fixture
def dock(qapp):
    d = PropertiesDock(palette=PaletteStore())
    stack = QUndoStack()
    d.set_undo_stack(stack)
    yield d, stack


def test_inspector_bend_radius_field(dock) -> None:
    d, stack = dock
    line = LineItem(QPointF(0, 0), QPointF(300, 0))
    d.set_items([line])
    assert not d.has_field("Bend radius")

    line.set_bends([QPointF(100, 50), QPointF(200, -50)], [0.0, 9.0])
    d.set_items([line])
    d.set_unit("pt")
    field = d.field("Bend radius")
    assert field.value() == pytest.approx(px_to_pt(9.0), abs=0.05)
    assert "different radii" in field.toolTip()
    field.setValue(4.0)
    field.editingFinished.emit()
    assert line.bend_radii() == pytest.approx([pt_to_px(4.0)] * 2)
    assert stack.count() == 1
    stack.undo()
    assert line.bend_radii() == [0.0, 9.0]

    for item in (
        PolygonItem([QPointF(0, 0), QPointF(50, 0), QPointF(50, 50)]),
        _text_with_leaders(),
    ):
        d.set_items([item])
        assert d.has_field("Bend radius")
    d.set_items([_text_with_leaders(), _text_with_leaders()])
    assert not d.has_field("Bend radius")  # single selection only


def test_inspector_row_appears_with_the_first_bend(win) -> None:
    line = LineItem(QPointF(100, 100), QPointF(400, 100))
    win._scene.push_add(line)
    line.setSelected(True)
    dock = win._properties_dock
    assert not dock.has_field("Bend radius")
    win._add_bend_at(line, QPointF(250, 100))
    QApplication.processEvents()
    assert dock.has_field("Bend radius")
    win._undo_group.activeStack().undo()
    QApplication.processEvents()
    assert not dock.has_field("Bend radius")


# ----------------------------------------------------------------------
# GD&T: the notes sit close to the frame
# ----------------------------------------------------------------------
def _ink_rows(item, scene_rect: QRectF) -> list[int]:
    """Rows of `item`'s own picture holding ink, in scene pixels from the
    top of `scene_rect` (rendered at 4x for precision, then scaled)."""
    scale = 4
    w, h = int(scene_rect.width() * scale), int(scene_rect.height() * scale)
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0)
    scene = item.scene()
    painter = QPainter(img)
    scene.render(painter, QRectF(0, 0, w, h), scene_rect)
    painter.end()
    rows = []
    for y in range(h):
        if any(img.pixelColor(x, y).alpha() > 60 for x in range(0, w, 2)):
            rows.append(y)
    return [r / scale for r in rows]


def test_gdt_upper_note_sits_just_above_the_frame(qapp) -> None:
    g = GdtAnnotationItem(GdtState(tolerance_value="0.1"), QPointF(0, 0))
    g.set_state(GdtState(tolerance_value="0.1", upper_runs=[{"t": "2X"}]))
    scene = QGraphicsScene()
    scene.addItem(g)
    g.setSelected(False)
    frame_top = g.leader_frame_rect().top()
    note = g.sub_text("upper")
    assert note is not None
    area = g.mapRectToScene(g.boundingRect())
    # Ink of the note alone: hide the frame's cells by rendering only the
    # band above the frame.
    band = QRectF(area.left(), area.top(), area.width(),
                  g.mapToScene(QPointF(0, frame_top)).y() - area.top())
    rows = _ink_rows(g, band)
    assert rows, "the note is drawn above the frame"
    gap = band.height() - (rows[-1] + 0.25)
    assert 0.0 <= gap <= _NOTE_GAP + 1.5
    # The lower note sits as close under the frame.
    g.set_state(GdtState(tolerance_value="0.1", lower_runs=[{"t": "CZ"}]))
    bottom = g.mapToScene(QPointF(0, g.leader_frame_rect().bottom())).y()
    area = g.mapRectToScene(g.boundingRect())
    below = QRectF(area.left(), bottom + 1.0, area.width(),
                   area.bottom() - bottom - 1.0)
    rows = _ink_rows(g, below)
    # Capitals keep half the room above them (for accents): a little
    # more than the upper note's gap, far below the old row-third.
    assert rows and rows[0] + 1.0 <= _NOTE_GAP + 3.5
