"""Linear dimensions: two points, a dimension line placed by a third
click, extension lines, ends and the measured value (2026-09-28)."""

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

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsSceneMouseEvent,
    QLabel,
)

from annoter.config import BASE_RENDER_DPI  # noqa: E402
from annoter.controllers.commands import ResizeCommand  # noqa: E402
from annoter.controllers.tools import Tool, ToolController  # noqa: E402
from annoter.model.styles import DimOrientation, EndStyle, HandleRole  # noqa: E402
from annoter.services.palette import PaletteStore  # noqa: E402
from annoter.services.pdf_export import (  # noqa: E402
    read_annotations,
    write_annotations,
)
from annoter.views.annotation_list import describe  # noqa: E402
from annoter.views.items.dimension import (  # noqa: E402
    TEXT_HANDLE,
    VALUE_RUN,
    DimensionItem,
    end_primitives,
    format_mm,
)
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402
from annoter.views.properties_dock import PropertiesDock  # noqa: E402

MM = BASE_RENDER_DPI / 25.4  # item units (page pixels) per millimeter


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _dim(p1=(100.0, 200.0), p2=(100.0 + 50 * MM, 200.0), through=None,
         orientation=DimOrientation.ALIGNED) -> DimensionItem:
    d = DimensionItem(QPointF(*p1), QPointF(*p2), orientation=orientation)
    if through is not None:
        d.place_through(QPointF(*through), orientation)
    return d


def _close(a: QPointF, b: QPointF, tol: float = 1e-6) -> bool:
    return math.hypot(a.x() - b.x(), a.y() - b.y()) <= tol


# ----------------------------------------------------------------------
# value text
# ----------------------------------------------------------------------
def test_format_mm_rounds_half_up_and_drops_trailing_zeros() -> None:
    assert format_mm(25.45, 1) == "25.5"
    assert format_mm(25.44, 1) == "25.4"
    assert format_mm(30.0, 1) == "30"
    assert format_mm(12.3456, 2) == "12.35"
    assert format_mm(99.96, 1) == "100"
    assert format_mm(7.5, 0) == "8"
    assert format_mm(0.04, 1) == "0"
    assert format_mm(1.23456, 9) == "1.235"  # at most 3 places


def test_measured_value_is_the_length_on_the_sheet_in_mm(qapp) -> None:
    d = _dim(through=(200.0, 150.0))
    assert d.value_mm() == pytest.approx(50.0)
    assert d.value_text() == "50"
    assert d.text() == "50"
    assert d.is_measured()
    d.set_points(QPointF(100, 200), QPointF(100 + 25.44 * MM, 200))
    assert d.text() == "25.4"  # follows the points
    d.set_decimals(2)
    assert d.text() == "25.44"
    assert d.label() == "Dimension 25.44"


def test_edited_text_keeps_the_measured_number_live(qapp) -> None:
    d = _dim(through=(200.0, 150.0))
    tol = {"tol": {"mode": "symmetric", "upper": "0.1", "lower": ""}}
    runs = d.runs_from_label([{"t": "Ø50 "}, tol])
    assert runs == [{"t": "Ø"}, VALUE_RUN, {"t": " "}, tol]
    d.set_value_runs(runs)
    assert d.is_measured()
    d.set_points(QPointF(100, 200), QPointF(100 + 42 * MM, 200))
    assert d.text().startswith("Ø42 ")
    # The number changed by hand: an override, kept as typed.
    over = d.runs_from_label([{"t": "Ø45"}])
    assert over == [{"t": "Ø45"}]
    d.set_value_runs(over)
    assert not d.is_measured()
    d.set_points(QPointF(100, 200), QPointF(100 + 30 * MM, 200))
    assert d.text() == "Ø45"
    # An emptied text shows the measured value again.
    assert d.runs_from_label([{"t": "  "}]) == [VALUE_RUN]
    # "5" is not found inside "25" nor "5.5".
    d.set_points(QPointF(100, 200), QPointF(100 + 5 * MM, 200))
    assert d.value_text() == "5"
    assert d.runs_from_label([{"t": "25"}]) == [{"t": "25"}]
    assert d.runs_from_label([{"t": "5.5"}]) == [{"t": "5.5"}]
    assert d.runs_from_label([{"t": "2x 5"}]) == [{"t": "2x "}, VALUE_RUN]


# ----------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------
def test_aligned_dimension_layout(qapp) -> None:
    d = _dim(through=(200.0, 150.0))
    geo = d.geometry()
    assert d.offset() == pytest.approx(50.0)
    assert _close(geo.d1, QPointF(100, 150)) and _close(geo.d2, QPointF(100 + 50 * MM, 150))
    assert geo.inside and geo.angle == pytest.approx(0.0)
    # Extension lines: a gap at the feature, past the dimension line.
    (a1, b1), (a2, b2) = geo.ext_lines
    assert a1.x() == pytest.approx(100) and 190 < a1.y() < 200
    assert b1.y() < 150
    # Arrowheads point outwards, onto the extension lines.
    (anchor1, towards1), (anchor2, towards2) = geo.ends
    assert _close(anchor1, geo.d1) and towards1.x() > anchor1.x()
    assert _close(anchor2, geo.d2) and towards2.x() < anchor2.x()
    # The value sits above the line, centered.
    poly = d.label_polygon()
    assert max(p.y() for p in poly) < 150
    xs = [p.x() for p in poly]
    assert (min(xs) + max(xs)) / 2 == pytest.approx(100 + 25 * MM, abs=1.0)


def test_horizontal_and_vertical_measure_the_projection(qapp) -> None:
    p1, p2 = (100.0, 100.0), (100.0 + 40 * MM, 100.0 + 30 * MM)
    h = _dim(p1, p2, through=(150.0, 60.0), orientation=DimOrientation.HORIZONTAL)
    d1, d2 = h.feet()
    assert d1.y() == pytest.approx(60.0) and d2.y() == pytest.approx(60.0)
    assert h.value_text() == "40"
    v = _dim(p1, p2, through=(400.0, 150.0), orientation=DimOrientation.VERTICAL)
    d1, d2 = v.feet()
    assert d1.x() == pytest.approx(400.0) and d2.x() == pytest.approx(400.0)
    assert v.value_text() == "30"
    # Read from the right: turned a quarter left, on the left of its line.
    assert v.geometry().angle == pytest.approx(-90.0)
    assert max(p.x() for p in v.label_polygon()) < 400.0
    a = _dim(p1, p2, through=(100.0, 300.0))
    assert a.value_text() == "50"  # 3-4-5


def test_the_value_never_reads_upside_down(qapp) -> None:
    right_to_left = _dim((400.0, 100.0), (100.0, 100.0), through=(250, 60))
    assert right_to_left.geometry().angle == pytest.approx(0.0)
    down = _dim((100.0, 100.0), (100.0, 400.0), through=(60, 250))
    assert down.geometry().angle == pytest.approx(-90.0)
    # Falling steeply to the right: read upwards, top to the left.
    steep = _dim((100.0, 100.0), (120.0, 400.0), through=(60, 250))
    assert steep.geometry().angle == pytest.approx(
        math.degrees(math.atan2(300, 20)) - 180.0
    )
    falling = _dim((100.0, 100.0), (400.0, 400.0), through=(300, 100))
    assert falling.geometry().angle == pytest.approx(45.0)
    rising = _dim((100.0, 400.0), (400.0, 100.0), through=(300, 400))
    assert rising.geometry().angle == pytest.approx(-45.0)


def test_short_dimensions_put_their_arrows_outside(qapp) -> None:
    d = _dim((100.0, 100.0), (100.0 + 3 * MM, 100.0), through=(110, 80))
    geo = d.geometry()
    assert not geo.inside
    (anchor1, towards1), (anchor2, towards2) = geo.ends
    assert towards1.x() < anchor1.x() and towards2.x() > anchor2.x()
    a, b = geo.dim_line
    assert a.x() < geo.d1.x() - geo.arrow and b.x() > geo.d2.x() + geo.arrow
    # Strokes and dots stay on the extension lines.
    d.set_end_style(EndStyle.SLASH)
    assert d.geometry().inside
    assert len(d.end_shapes()) == 2


def test_end_shapes() -> None:
    a, t = QPointF(0, 0), QPointF(10, 0)
    fill = end_primitives(a, t, EndStyle.CLOSED_ARROW, 10.0)
    assert fill[0][0] == "fill" and len(fill[0][1]) == 3
    tip, h1, h2 = fill[0][1]
    assert tip == a and h1.x() == pytest.approx(10 * math.cos(math.radians(15)))
    assert end_primitives(a, t, EndStyle.OPEN_ARROW, 10.0)[0][0] == "stroke"
    slash = end_primitives(a, t, EndStyle.SLASH, 10.0)[0][1]
    assert slash[0].x() == pytest.approx(-slash[1].x())  # centered, 45 deg
    assert abs(slash[0].x()) == pytest.approx(abs(slash[0].y()))
    assert end_primitives(a, t, EndStyle.CIRCLE, 10.0)[0][0] == "dot"
    assert end_primitives(a, a, EndStyle.CLOSED_ARROW, 10.0) == []


def test_moving_a_value_along_its_line(qapp) -> None:
    d = _dim(through=(200.0, 150.0))
    mid = d.geometry().center
    d.apply_resize(TEXT_HANDLE, QPointF(mid.x() + 80, 120))
    assert d.offset() == pytest.approx(80.0)
    assert d.shift() == pytest.approx(80.0)
    # The line runs on under a value pushed past its end.
    d.apply_resize(TEXT_HANDLE, QPointF(100 + 50 * MM + 100, 120))
    assert d.geometry().dim_line[1].x() > 100 + 50 * MM + 100
    # Close to the middle: magnetic.
    d.apply_resize(TEXT_HANDLE, QPointF(mid.x() + 3, 130))
    assert d.shift() == 0.0
    # Shift on the handle: the line moves, the value stays put.
    d.set_shift(40.0)
    center = d.geometry().center
    target = d.constrain_line_drag(QPointF(center.x() + 70, 90))
    d.apply_resize(TEXT_HANDLE, target)
    assert d.shift() == pytest.approx(40.0) and d.offset() == pytest.approx(110.0)


def test_moving_a_point(qapp) -> None:
    h = _dim((100.0, 100.0), (300.0, 200.0), through=(200, 50),
             orientation=DimOrientation.HORIZONTAL)
    level = h.feet()[0].y()
    h.apply_resize(HandleRole.P1, QPointF(80.0, 150.0))
    assert h.feet()[0].y() == pytest.approx(level)  # the line stays put
    assert h.points()[0] == QPointF(80.0, 150.0)
    a = _dim(through=(200.0, 150.0))
    a.apply_resize(HandleRole.P2, QPointF(100 + 60 * MM, 200.0))
    assert a.text() == "60" and a.offset() == pytest.approx(50.0)


def test_switching_orientation_keeps_the_line_outside(qapp) -> None:
    d = _dim((100.0, 100.0), (400.0, 300.0), through=(100, 400))
    old = d.geom_snapshot()
    d.switch_orientation(DimOrientation.VERTICAL)
    x = d.feet()[0].x()
    assert not (100.0 <= x <= 400.0)  # not across the points
    assert d.value_text() == format_mm(200 / MM, 1)
    cmd = ResizeCommand(d, old, d.geom_snapshot())
    cmd.undo()
    assert d.orientation() is DimOrientation.ALIGNED and d.geom_snapshot() == old
    cmd.redo()
    assert d.orientation() is DimOrientation.VERTICAL


def test_clone_and_scale(qapp) -> None:
    d = _dim(through=(200.0, 150.0))
    d.set_end_style(EndStyle.OPEN_ARROW)
    d.set_decimals(2)
    d.set_font_size(16)
    d.set_shift(12.0)
    d.set_value_runs([{"t": "2x "}, VALUE_RUN])
    d.set_color(QColor("#1565C0"))
    c = d.clone()
    assert c.geom_snapshot() == d.geom_snapshot()
    assert (c.end_style(), c.decimals(), c.font_size()) == (EndStyle.OPEN_ARROW, 2, 16)
    assert c.value_runs() == d.value_runs() and c.text() == "2x 50"
    assert c.label_item().color() == QColor("#1565C0")
    c.scale_geometry(2.0)
    assert c.offset() == pytest.approx(100.0) and c.shift() == pytest.approx(24.0)
    assert c.font_size() == 32 and c.value_text() == "100"


def test_shape_covers_lines_and_value(qapp) -> None:
    d = _dim(through=(200.0, 150.0))
    shape = d.shape()
    assert shape.contains(QPointF(250, 150))  # on the dimension line
    assert shape.contains(d.label_polygon().boundingRect().center())
    assert not shape.contains(QPointF(250, 175))  # between line and feature
    assert d.content_rect().contains(d.label_polygon().boundingRect())


def test_annotation_list_row(qapp) -> None:
    d = _dim(through=(200.0, 150.0))
    assert describe(d) == ("50", "Dimension", "dimension")


# ----------------------------------------------------------------------
# placing a dimension: click, click, click
# ----------------------------------------------------------------------
def _mouse(scene, etype, pos: QPointF, mods=Qt.NoModifier, screen=None) -> None:
    ev = QGraphicsSceneMouseEvent(etype)
    ev.setScenePos(pos)
    ev.setButton(Qt.LeftButton)
    ev.setButtons(Qt.LeftButton)
    ev.setModifiers(mods)
    s = screen if screen is not None else pos
    ev.setScreenPos(QPoint(int(s.x()), int(s.y())))
    {
        QEvent.GraphicsSceneMousePress: scene.mousePressEvent,
        QEvent.GraphicsSceneMouseMove: scene.mouseMoveEvent,
        QEvent.GraphicsSceneMouseRelease: scene.mouseReleaseEvent,
    }[etype](ev)


def _click(scene, pos: QPointF, mods=Qt.NoModifier) -> None:
    _mouse(scene, QEvent.GraphicsSceneMousePress, pos, mods)
    _mouse(scene, QEvent.GraphicsSceneMouseRelease, pos, mods)


def _move(scene, pos: QPointF, mods=Qt.NoModifier) -> None:
    _mouse(scene, QEvent.GraphicsSceneMouseMove, pos, mods)


@pytest.fixture
def board(qapp):
    scene = PdfScene()
    pm = QPixmap(1200, 900)
    pm.fill(QColor("white"))
    scene.set_page_pixmap(pm)
    stack = QUndoStack()
    scene.set_undo_stack(stack)
    tc = ToolController()
    scene.set_tool_controller(tc)
    tc.set_tool(Tool.DIMENSION)
    yield scene, stack, tc


def _dims(scene) -> list[DimensionItem]:
    return [
        c for c in scene.page_item().childItems()
        if isinstance(c, DimensionItem)
    ]


def test_three_clicks_place_a_dimension(board) -> None:
    scene, stack, tc = board
    _click(scene, QPointF(100, 300))
    assert scene.dimension_stage() == 1
    _move(scene, QPointF(250, 300))
    draft = _dims(scene)[0]
    assert draft.text() == format_mm(150 / MM, 1)  # measures live
    _click(scene, QPointF(100 + 80 * MM, 300))
    assert scene.dimension_stage() == 2
    _move(scene, QPointF(300, 240))
    assert draft.offset() == pytest.approx(60.0)
    assert stack.count() == 0
    _click(scene, QPointF(300, 250))
    assert scene.dimension_stage() == 0
    assert stack.count() == 1
    (dim,) = _dims(scene)
    assert dim.text() == "80" and dim.offset() == pytest.approx(50.0)
    assert dim.orientation() is DimOrientation.ALIGNED
    assert dim.isSelected() and tc.tool() is Tool.SELECT
    stack.undo()
    assert _dims(scene) == []


def test_shift_picks_horizontal_or_vertical(board) -> None:
    scene, stack, tc = board
    p1, p2 = QPointF(100, 300), QPointF(400, 500)
    _click(scene, p1)
    _click(scene, p2)
    draft = _dims(scene)[0]
    _move(scene, QPointF(250, 200), Qt.ShiftModifier)  # above: horizontal
    assert draft.orientation() is DimOrientation.HORIZONTAL
    assert draft.feet()[0].y() == pytest.approx(200.0)
    _move(scene, QPointF(520, 420), Qt.ShiftModifier)  # beside: vertical
    assert draft.orientation() is DimOrientation.VERTICAL
    _move(scene, QPointF(520, 420))  # released: aligned again
    assert draft.orientation() is DimOrientation.ALIGNED
    _click(scene, QPointF(250, 200), Qt.ShiftModifier)
    (dim,) = _dims(scene)
    assert dim.orientation() is DimOrientation.HORIZONTAL
    assert dim.text() == format_mm(300 / MM, 1)
    assert scene.dimension_orientation_at(p1, QPointF(100, 500), QPointF(0, 0)) is (
        DimOrientation.VERTICAL
    )


def test_a_drag_gives_the_second_point(board) -> None:
    scene, stack, tc = board
    a, b = QPointF(100, 300), QPointF(300, 300)
    _mouse(scene, QEvent.GraphicsSceneMousePress, a)
    _move(scene, b)
    _mouse(scene, QEvent.GraphicsSceneMouseRelease, b)
    assert scene.dimension_stage() == 2
    _click(scene, QPointF(200, 250))
    assert _dims(scene)[0].text() == format_mm(200 / MM, 1)


def test_same_point_twice_waits_for_another(board) -> None:
    scene, stack, tc = board
    _click(scene, QPointF(100, 300))
    _click(scene, QPointF(100.4, 300))
    assert scene.dimension_stage() == 1


def test_escape_and_other_tools_cancel(board) -> None:
    scene, stack, tc = board
    _click(scene, QPointF(100, 300))
    _click(scene, QPointF(300, 300))
    scene.cancel_current_action()
    assert _dims(scene) == [] and stack.count() == 0
    assert tc.tool() is Tool.DIMENSION  # still armed
    _click(scene, QPointF(100, 300))
    tc.set_tool(Tool.RECTANGLE)
    assert _dims(scene) == [] and not scene.dimension_draft_active()
    tc.set_tool(Tool.DIMENSION)
    _click(scene, QPointF(100, 300))
    assert scene.detach_children() == []  # a draft never leaves the page


def test_points_snap_onto_shapes(board) -> None:
    scene, stack, tc = board
    rect = RectangleItem(QRectF(500, 400, 200, 100))
    rect.setParentItem(scene.page_item())
    _click(scene, QPointF(503, 402))  # near the top-left corner
    _click(scene, QPointF(698, 403))  # near the top-right corner
    draft = _dims(scene)[0]
    assert draft.points() == (QPointF(500, 400), QPointF(700, 400))
    _move(scene, QPointF(600, 350))
    _click(scene, QPointF(600, 350), Qt.NoModifier)
    assert _dims(scene)[0].text() == format_mm(200 / MM, 1)


def test_dragging_a_point_snaps_and_squares(board) -> None:
    scene, stack, tc = board
    tc.set_tool(Tool.SELECT)
    d = _dim((100.0, 300.0), (400.0, 300.0), through=(250, 250))
    d.setParentItem(scene.page_item())
    d.setSelected(True)
    rect = RectangleItem(QRectF(500, 500, 100, 100))
    rect.setParentItem(scene.page_item())
    _mouse(scene, QEvent.GraphicsSceneMousePress, QPointF(400, 300))
    _move(scene, QPointF(502, 497))
    _mouse(scene, QEvent.GraphicsSceneMouseRelease, QPointF(502, 497))
    assert d.points()[1] == QPointF(500, 500)
    assert stack.count() == 1
    _mouse(scene, QEvent.GraphicsSceneMousePress, QPointF(500, 500))
    _move(scene, QPointF(620, 310), Qt.ShiftModifier | Qt.AltModifier)
    _mouse(scene, QEvent.GraphicsSceneMouseRelease, QPointF(620, 310))
    assert d.points()[1].y() == pytest.approx(300.0)  # squared to p1
    stack.undo()
    stack.undo()
    assert d.points()[1] == QPointF(400, 300)


def test_the_value_stands_for_its_dimension(board) -> None:
    scene, stack, tc = board
    d = _dim(through=(200.0, 150.0))
    d.setParentItem(scene.page_item())
    center = d.mapToScene(d.label_polygon().boundingRect().center())
    assert scene._topmost_annotation_at(center) is d


# ----------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------
def _reopen(doc: fitz.Document) -> fitz.Document:
    tmp = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".pdf")
    tmp.close()
    doc.save(tmp.name, garbage=3, deflate=True)
    return fitz.open(tmp.name)


def test_dimensions_round_trip_through_the_pdf(qapp) -> None:
    doc = fitz.open()
    doc.new_page(width=600, height=400)
    a = _dim((100.0, 300.0), (100.0 + 70 * MM, 300.0), through=(200, 250))
    a.set_value_runs([{"t": "Ø"}, VALUE_RUN, {"t": " ±0.1"}])
    a.set_shift(15.0)
    b = _dim((700.0, 200.0), (900.0, 500.0), through=(1000, 300),
             orientation=DimOrientation.VERTICAL)
    b.set_end_style(EndStyle.SLASH)
    b.set_decimals(2)
    b.set_font_size(16)
    b.set_color(QColor("#1565C0"))
    b.set_stroke(1.5)
    b.setPos(10, 20)
    c = _dim((300.0, 600.0), (320.0, 600.0), through=(310, 560))
    c.set_value_runs([{"t": "REF"}])
    write_annotations(doc, {0: [a, b, c]}, dpi=BASE_RENDER_DPI)
    first = _reopen(doc)
    page = first[0]
    streams = []
    for annot in page.annots():
        assert annot.type[1] == "Square"
        assert "dimension" in json.loads(annot.info["subject"])
        ap = first.xref_get_key(annot.xref, "AP/N")
        streams.append(first.xref_stream(int(ap[1].split()[0])).decode())
    # External viewers get the lines as vector paths and the value.
    assert all(" m " in s and " S" in s and "/DimValue Do" in s for s in streams)
    assert " h f" in streams[0]  # filled arrowheads
    items = read_annotations(first, dpi=BASE_RENDER_DPI)[0]
    write_annotations(first, {0: items}, dpi=BASE_RENDER_DPI)  # again
    second = _reopen(first)
    first.close()
    assert len(list(second[0].annots())) == 3  # nothing piles up
    out = read_annotations(second, dpi=BASE_RENDER_DPI)[0]
    second.close()
    assert [type(i) for i in out] == [DimensionItem] * 3
    ra, rb, rc = out
    assert ra.text() == "Ø70 ±0.1" and ra.is_measured()
    assert ra.shift() == pytest.approx(15.0, abs=0.01)
    assert ra.offset() == pytest.approx(a.offset(), abs=0.01)
    p1, p2 = rb.points()
    assert (p1.x(), p1.y()) == pytest.approx((710.0, 220.0), abs=0.01)
    assert rb.orientation() is DimOrientation.VERTICAL
    assert (rb.end_style(), rb.decimals(), rb.font_size()) == (EndStyle.SLASH, 2, 16)
    assert rb.color() == QColor("#1565C0") and rb.stroke() == pytest.approx(1.5)
    assert rb.text() == b.text()
    assert rc.text() == "REF" and not rc.is_measured()


# ----------------------------------------------------------------------
# main window: menu, value edits, inspector
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


def test_rail_and_hint(win) -> None:
    win._tool_controller.set_tool(Tool.DIMENSION)
    assert win._view._tool_cursor == Qt.CrossCursor
    from annoter.views.canvas_overlays import TOOL_HINTS

    assert "Shift" in TOOL_HINTS[Tool.DIMENSION][1]


def test_context_menu_on_a_dimension(win) -> None:
    d = _dim(through=(200.0, 150.0))
    win._scene.push_add(d)
    stack = win._undo_group.activeStack()
    on_line = d.mapToScene(QPointF(150, 150))
    menu = win._build_context_menu(on_line)
    assert {"Measure", "Ends"} <= set(menu.row_captions())
    assert menu.button("dim:aligned").isChecked()
    assert menu.button("dim-end:closed_arrow").isChecked()
    assert "Edit Value" in menu.entry_texts()
    assert "Use Measured Value" not in menu.entry_texts()
    depth = stack.count()
    menu.trigger("dim:vertical")
    assert d.orientation() is DimOrientation.VERTICAL
    assert stack.count() == depth + 1
    stack.undo()
    assert d.orientation() is DimOrientation.ALIGNED
    menu = win._build_context_menu(on_line)
    menu.trigger("dim-end:circle")
    assert d.end_style() is EndStyle.CIRCLE
    d.set_value_runs([{"t": "52"}])
    menu = win._build_context_menu(on_line)
    menu.entry("Use Measured Value").trigger()
    assert d.is_measured() and d.text() == "50"


def test_editing_the_value_in_place(win) -> None:
    d = _dim(through=(200.0, 150.0))
    win._scene.push_add(d)
    stack = win._undo_group.activeStack()
    label = d.label_item()
    d.begin_text_edit()
    assert win._text_edit_item is label  # the edit bar is up
    label.set_rich_runs([{"t": "2x Ø50"}])
    depth = stack.count()
    # The session ends (what losing the focus does).
    label._inner.setTextInteractionFlags(Qt.NoTextInteraction)
    label.editingFinished.emit(label.text())
    assert win._text_edit_item is None
    assert d.value_runs() == [{"t": "2x Ø"}, VALUE_RUN]
    assert stack.count() == depth + 1
    stack.undo()
    assert d.value_runs() == [VALUE_RUN] and d.text() == "50"


def test_right_click_drops_a_half_placed_dimension(win) -> None:
    win._tool_controller.set_tool(Tool.DIMENSION)
    _click(win._scene, QPointF(100, 300))
    assert win._scene.dimension_draft_active()
    win._show_context_menu(None, QPointF(100, 300))
    assert not win._scene.dimension_draft_active()


@pytest.fixture
def dock(qapp):
    d = PropertiesDock(palette=PaletteStore())
    stack = QUndoStack()
    d.set_undo_stack(stack)
    yield d, stack


def test_inspector_dimension_section(dock) -> None:
    panel, stack = dock
    d = _dim(through=(200.0, 150.0))
    panel.set_items([d])
    measured = panel.field("Measured")
    assert isinstance(measured, QLabel) and measured.text() == "50 mm"
    assert not panel.has_field("Use measured value")
    for label in ("Measure", "Rounding", "Ends", "Text size", "Offset"):
        assert panel.has_field(label), label

    panel.field("Measure").valueChanged.emit(DimOrientation.HORIZONTAL)
    assert d.orientation() is DimOrientation.HORIZONTAL and stack.count() == 1

    rounding = panel.field("Rounding")
    rounding.setCurrentIndex(0)
    assert d.decimals() == 0 and stack.count() == 2

    ends = panel.field("Ends")
    ends.setCurrentIndex(ends.findData(EndStyle.OPEN_ARROW))
    assert d.end_style() is EndStyle.OPEN_ARROW

    panel.set_unit("mm")
    offset = panel.field("Offset")
    offset.setValue(10.0)
    offset.editingFinished.emit()
    assert abs(d.offset()) == pytest.approx(10 * MM)

    d.set_value_runs([{"t": "52"}])
    panel.set_items([d])
    button = panel.field("Use measured value")
    button.click()
    assert d.is_measured()


def test_inspector_follows_the_value(win) -> None:
    d = _dim(through=(200.0, 150.0))
    win._scene.push_add(d)
    d.setSelected(True)
    dock = win._properties_dock
    assert dock.field("Measured").text() == "50 mm"
    old = d.geom_snapshot()
    d.apply_resize(HandleRole.P2, QPointF(100 + 60 * MM, 200))
    win._undo_group.activeStack().push(ResizeCommand(d, old, d.geom_snapshot()))
    QApplication.processEvents()
    assert dock.field("Measured").text() == "60 mm"
