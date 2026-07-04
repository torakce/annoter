"""Line/arrow rework follow-up: end labels on both sides (bend-proof)
and the endpoint 'Extremity shape' context actions."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.model.styles import EndStyle, HandleRole  # noqa: E402
from annoter.services.pdf_export import (  # noqa: E402
    read_annotations,
    write_annotations,
)
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


# ----------------------------------------------------------------------
# labels on the item
# ----------------------------------------------------------------------
def test_labels_coexist_with_bends_and_extend_bounds(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(200, 0))
    line.set_bends([QPointF(100, 60)])
    base = line.boundingRect()

    line.set_start_label("start here")
    line.set_end_label("end here")
    assert line.bends() == [QPointF(100, 60)]  # untouched
    grown = line.boundingRect()
    assert grown.width() > base.width()

    rects = line._label_rects()
    assert len(rects) == 2
    # Start label sits beyond p1 (to the left), end label beyond p2.
    assert rects[0][0].center().x() < 0
    assert rects[1][0].center().x() > 200


def test_label_follows_end_segment_direction_on_bent_line(qapp) -> None:
    # End segment goes DOWN toward p2: the end label must sit below p2,
    # not along the horizontal p1->p2 chord.
    line = LineItem(QPointF(0, 0), QPointF(200, 100))
    line.set_bends([QPointF(200, 0)])
    line.set_end_label("X")
    (rect, _text, _b), = line._label_rects()
    assert rect.center().y() > 100


def test_empty_labels_draw_nothing(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    assert line._label_rects() == []


# ----------------------------------------------------------------------
# endpoint extremity-shape actions
# ----------------------------------------------------------------------
def test_endpoint_hit_detection(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        line = LineItem(QPointF(10, 10), QPointF(110, 10))
        win._scene.push_add(line)
        assert win._endpoint_at(line, QPointF(12, 11)) is HandleRole.P1
        assert win._endpoint_at(line, QPointF(108, 9)) is HandleRole.P2
        assert win._endpoint_at(line, QPointF(60, 10)) is None
    finally:
        win.close()


def test_set_endpoint_style_on_arrow_is_undoable(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        arrow = ArrowItem(QPointF(0, 0), QPointF(100, 0))
        win._scene.push_add(arrow)
        win._set_endpoint_style(arrow, HandleRole.P1, EndStyle.CIRCLE)
        assert arrow.start_end() is EndStyle.CIRCLE
        assert arrow.end_end() is EndStyle.OPEN_ARROW  # untouched
        win._undo_group.activeStack().undo()
        assert arrow.start_end() is EndStyle.NONE
    finally:
        win.close()


def test_set_endpoint_style_promotes_plain_line_to_arrow(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        line = LineItem(QPointF(0, 0), QPointF(100, 0))
        line.set_bends([QPointF(50, 30)])
        line.set_end_label("lbl")
        win._scene.push_add(line)

        win._set_endpoint_style(line, HandleRole.P2, EndStyle.CLOSED_ARROW)
        page_children = win._scene.page_item().childItems()
        arrows = [c for c in page_children if isinstance(c, ArrowItem)]
        assert len(arrows) == 1 and line not in page_children
        a = arrows[0]
        assert a.start_end() is EndStyle.NONE
        assert a.end_end() is EndStyle.CLOSED_ARROW
        # Bends and labels survived the promotion.
        assert a.bends() == [QPointF(50, 30)]
        assert a.end_label() == "lbl"

        win._undo_group.activeStack().undo()
        assert line in win._scene.page_item().childItems()
    finally:
        win.close()


def test_set_endpoint_style_none_on_plain_line_is_noop(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        line = LineItem(QPointF(0, 0), QPointF(100, 0))
        win._scene.push_add(line)
        before = win._undo_group.activeStack().count()
        win._set_endpoint_style(line, HandleRole.P1, EndStyle.NONE)
        assert win._undo_group.activeStack().count() == before
    finally:
        win.close()


# ----------------------------------------------------------------------
# label / text borders and datum triangles (Discussion #1, 2026-07-03)
# ----------------------------------------------------------------------
def test_label_border_box_and_ellipse_grow_bounds(qapp) -> None:
    from annoter.model.styles import TextBorder

    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    line.set_end_label("A")
    plain = line.boundingRect()
    line.set_label_border(TextBorder.ELLIPSE)
    assert line.boundingRect().width() > plain.width()


def test_text_border_grows_bounds_and_clones(qapp) -> None:
    from annoter.model.styles import TextBorder
    from annoter.views.items.text import TextAnnotationItem

    t = TextAnnotationItem(QPointF(0, 0), "3")
    plain = t.boundingRect()
    t.set_border(TextBorder.ELLIPSE)
    assert t.boundingRect().width() > plain.width()
    assert t.clone().border() is TextBorder.ELLIPSE


def test_boxed_label_hugs_bare_line_end(qapp) -> None:
    """Datum frames must sit flush against the line end (user report):
    the gap between the endpoint and the box edge stays a few pixels,
    not a head-size + diagonal clearance."""
    from annoter.model.styles import TextBorder

    arrow = ArrowItem(QPointF(0, 0), QPointF(200, 0))
    arrow.set_stroke(4.0)
    arrow.set_start_end(EndStyle.TRIANGLE_FILLED)
    arrow.set_end_end(EndStyle.NONE)  # bare end carries the frame
    arrow.set_end_label("A")
    arrow.set_label_border(TextBorder.BOX)

    (rect, _, _b), = arrow._label_rects()
    gap = rect.left() - 200.0
    # A BORDERED label touches the line: zero gap (the box edge is on
    # the endpoint).
    assert gap == pytest.approx(0.0, abs=0.5)

    # An end WITH a head keeps clearing it.
    arrow.set_end_end(EndStyle.OPEN_ARROW)
    (rect2, _, _b2), = arrow._label_rects()
    assert rect2.left() - 200.0 >= arrow._head_size()

    # Circle outline: the CIRCLE touches the endpoint, so the text rect
    # itself starts further right (circle radius > rect half-width).
    from annoter.model.styles import TextBorder

    arrow.set_end_end(EndStyle.NONE)
    arrow.set_label_border(TextBorder.ELLIPSE)
    (rect3, _, _b3), = arrow._label_rects()
    import math

    radius = math.hypot(rect3.width(), rect3.height()) / 2.0
    center_dist = rect3.center().x() - 200.0
    assert center_dist == pytest.approx(radius, abs=0.5)


def test_label_borders_are_independent_per_end(qapp) -> None:
    """User report: framing ONE label must not frame the other."""
    from annoter.model.styles import TextBorder

    line = LineItem(QPointF(0, 0), QPointF(200, 0))
    line.set_start_label("A")
    line.set_end_label("B")
    line.set_start_label_border(TextBorder.BOX)

    rects = line._label_rects()
    borders = {text: border for _r, text, border in rects}
    assert borders["A"] is TextBorder.BOX
    assert borders["B"] is TextBorder.NONE

    # And the boxed end hugs the line while the bare one keeps its gap.
    by_text = {text: r for r, text, _b in rects}
    assert by_text["A"].right() == pytest.approx(0.0, abs=0.5)
    assert by_text["B"].left() - 200.0 > 2.0


def test_mixed_label_borders_pdf_roundtrip(qapp, tmp_path: Path) -> None:
    from annoter.model.styles import TextBorder

    pdf = tmp_path / "mixed.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    line = LineItem(QPointF(50, 50), QPointF(250, 50))
    line.set_start_label("A")
    line.set_start_label_border(TextBorder.BOX)
    line.set_end_label("B")
    line.set_end_label_border(TextBorder.ELLIPSE)
    write_annotations(doc, {0: [line]}, 150)
    doc.save(str(pdf))
    doc.close()

    doc = fitz.open(str(pdf))
    try:
        items = read_annotations(doc, 150)[0]
    finally:
        doc.close()
    lines = [it for it in items if isinstance(it, LineItem)]
    assert len(lines) == 1
    assert lines[0].start_label_border() is TextBorder.BOX
    assert lines[0].end_label_border() is TextBorder.ELLIPSE


def test_triangle_end_styles_pdf_roundtrip(qapp, tmp_path: Path) -> None:
    from annoter.model.styles import TextBorder

    pdf = tmp_path / "datum.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    datum = ArrowItem(QPointF(100, 50), QPointF(100, 250))
    datum.set_start_end(EndStyle.NONE)
    datum.set_end_end(EndStyle.TRIANGLE_FILLED)
    datum.set_start_label("A")
    datum.set_label_border(TextBorder.BOX)
    write_annotations(doc, {0: [datum]}, 150)
    doc.save(str(pdf))
    doc.close()

    doc = fitz.open(str(pdf))
    try:
        items = read_annotations(doc, 150)[0]
    finally:
        doc.close()
    arrows = [it for it in items if isinstance(it, ArrowItem)]
    assert len(arrows) == 1
    a = arrows[0]
    # The exact style survives via the JSON payload even though the
    # native /LE can only say ClosedArrow.
    assert a.end_end() is EndStyle.TRIANGLE_FILLED
    assert a.start_label() == "A"
    assert a.label_border() is TextBorder.BOX


def test_text_border_pdf_roundtrip(qapp, tmp_path: Path) -> None:
    from annoter.model.styles import TextBorder
    from annoter.views.items.text import TextAnnotationItem

    pdf = tmp_path / "circled.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    t = TextAnnotationItem(QPointF(100, 100), "rev 3")
    t.set_border(TextBorder.ELLIPSE)
    write_annotations(doc, {0: [t]}, 150)
    doc.save(str(pdf))
    doc.close()

    doc = fitz.open(str(pdf))
    try:
        items = read_annotations(doc, 150)[0]
    finally:
        doc.close()
    texts = [it for it in items if isinstance(it, TextAnnotationItem)]
    assert len(texts) == 1
    assert texts[0].border() is TextBorder.ELLIPSE


def test_selection_pill_has_real_size_after_rebuild(qapp) -> None:
    """Regression for the 'tiny square' bug: a freshly rebuilt (still
    hidden) pill must already have its laid-out size."""
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QWidget

    from annoter.views.items.shapes import RectangleItem
    from annoter.views.selection_toolbar import SelectionToolbar

    host = QWidget()
    pill = SelectionToolbar(host)
    rect = RectangleItem(QRectF(0, 0, 10, 10))
    pill.set_context([rect])
    assert pill.width() > 80
    assert pill.height() > 15


# ----------------------------------------------------------------------
# persistence
# ----------------------------------------------------------------------
def test_labeled_bent_arrow_pdf_roundtrip(qapp, tmp_path: Path) -> None:
    pdf = tmp_path / "labels.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)

    arrow = ArrowItem(QPointF(50, 50), QPointF(250, 50))
    arrow.set_bends([QPointF(150, 120)])
    arrow.set_start_label("from")
    arrow.set_end_label("to")
    line = LineItem(QPointF(20, 300), QPointF(220, 300))
    line.set_end_label("straight+label")

    write_annotations(doc, {0: [arrow, line]}, 150)
    doc.save(str(pdf))
    doc.close()

    doc = fitz.open(str(pdf))
    try:
        page = doc[0]
        subtypes = sorted(a.type[1] for a in (page.annots() or []))
        # 1 PolyLine (bent arrow) + 1 Line + 3 FreeText companions.
        assert subtypes == ["FreeText", "FreeText", "FreeText", "Line", "PolyLine"]
        items = read_annotations(doc, 150)[0]
    finally:
        doc.close()

    # Companions are skipped: only the two editable items come back.
    assert len(items) == 2
    arrows = [it for it in items if isinstance(it, ArrowItem)]
    lines = [
        it for it in items
        if isinstance(it, LineItem) and not isinstance(it, ArrowItem)
    ]
    assert len(arrows) == 1 and len(lines) == 1
    assert arrows[0].start_label() == "from"
    assert arrows[0].end_label() == "to"
    assert len(arrows[0].bends()) == 1
    assert lines[0].end_label() == "straight+label"
    assert lines[0].start_label() == ""
