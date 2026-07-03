"""Bend points on lines/arrows (Discussion #1, item 5)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.model.styles import EndStyle, HandleRole  # noqa: E402
from annoter.services.pdf_export import (  # noqa: E402
    read_annotations,
    write_annotations,
)
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def scene(qapp):
    sc = PdfScene()
    sc.set_page_pixmap(QPixmap(400, 400))
    sc.set_undo_stack(QUndoStack())
    yield sc
    sc.clear_page()


def test_insert_bend_projects_onto_nearest_segment(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    idx = line.insert_bend_near(QPointF(50, 10))  # near the middle
    assert idx == 0
    bends = line.bends()
    assert len(bends) == 1
    assert bends[0] == QPointF(50, 0)  # projected onto the segment
    assert line.path_points() == [
        QPointF(0, 0),
        QPointF(50, 0),
        QPointF(100, 0),
    ]


def test_insert_second_bend_keeps_path_order(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    line.set_bends([QPointF(50, 40)])
    # Click near the second segment (bend -> p2).
    idx = line.insert_bend_near(QPointF(80, 22))
    assert idx == 1
    pts = line.path_points()
    assert pts[1] == QPointF(50, 40)
    assert pts[2].x() > 50  # inserted after the existing bend


def test_bend_handles_and_resize(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    line.set_bends([QPointF(50, 0)])
    handles = line.handle_positions()
    assert HandleRole.P1 in handles and HandleRole.P2 in handles
    assert 0 in handles
    line.apply_resize(0, QPointF(50, 30))
    assert line.bends()[0] == QPointF(50, 30)


def test_remove_bend_and_bend_at(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    line.set_bends([QPointF(50, 20)])
    assert line.bend_at(QPointF(52, 22)) == 0
    assert line.bend_at(QPointF(90, 0)) is None
    line.remove_bend(0)
    assert line.bends() == []


def test_geom_snapshot_roundtrip_includes_bends(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    line.set_bends([QPointF(50, 20)])
    snap = line.geom_snapshot()
    line.set_bends([])
    line.set_line_points(QPointF(5, 5), QPointF(60, 60))
    line.apply_geom(snap)
    assert line.line_points() == (QPointF(0, 0), QPointF(100, 0))
    assert line.bends() == [QPointF(50, 20)]


def test_legacy_two_tuple_snapshot_still_applies(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(10, 10))
    line.set_bends([QPointF(5, 5)])
    line.apply_geom((QPointF(1, 1), QPointF(9, 9)))
    assert line.line_points() == (QPointF(1, 1), QPointF(9, 9))


def test_clone_copies_bends(qapp) -> None:
    arrow = ArrowItem(QPointF(0, 0), QPointF(100, 0))
    arrow.set_bends([QPointF(50, 30)])
    c = arrow.clone()
    assert c.bends() == [QPointF(50, 30)]


def test_shift_constrains_bend_to_axis(scene) -> None:
    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    line.setParentItem(scene.page_item())
    line.set_bends([QPointF(50, 0)])

    # Dragging the bend mostly horizontally locks it to the horizontal
    # of the previous path point (p1 at y=0).
    locked = scene._constrain_resize(line, 0, QPointF(70, 8))
    assert locked == QPointF(70, 0)
    # Mostly vertical drag locks to the vertical of p1.
    locked = scene._constrain_resize(line, 0, QPointF(6, 60))
    assert locked == QPointF(0, 60)


def test_shift_constrains_endpoint_of_bent_line_to_axis(scene) -> None:
    line = LineItem(QPointF(0, 0), QPointF(100, 0))
    line.setParentItem(scene.page_item())
    line.set_bends([QPointF(50, 40)])
    # P2's adjacent bend is at (50, 40): near-horizontal drag locks y=40.
    locked = scene._constrain_resize(line, HandleRole.P2, QPointF(120, 45))
    assert locked == QPointF(120, 40)


def test_bent_arrow_pdf_roundtrip(qapp, tmp_path: Path) -> None:
    pdf = tmp_path / "bends.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)

    arrow = ArrowItem(QPointF(50, 50), QPointF(250, 50))
    arrow.set_bends([QPointF(150, 120)])
    arrow.set_end_end(EndStyle.CLOSED_ARROW)
    line = LineItem(QPointF(20, 200), QPointF(220, 200))
    line.set_bends([QPointF(120, 260), QPointF(170, 230)])

    write_annotations(doc, {0: [arrow, line]}, 150)
    doc.save(str(pdf))
    doc.close()

    doc = fitz.open(str(pdf))
    try:
        items = read_annotations(doc, 150)[0]
    finally:
        doc.close()

    arrows = [it for it in items if isinstance(it, ArrowItem)]
    lines = [
        it for it in items if isinstance(it, LineItem)
        and not isinstance(it, ArrowItem)
    ]
    assert len(arrows) == 1 and len(lines) == 1

    a = arrows[0]
    ap1, ap2 = a.line_points()
    assert ap1.x() == pytest.approx(50, abs=1.0)
    assert ap2.x() == pytest.approx(250, abs=1.0)
    assert len(a.bends()) == 1
    assert a.bends()[0].x() == pytest.approx(150, abs=1.0)
    assert a.bends()[0].y() == pytest.approx(120, abs=1.0)
    assert a.end_end() is EndStyle.CLOSED_ARROW

    ln = lines[0]
    assert len(ln.bends()) == 2
    assert ln.bends()[0].y() == pytest.approx(260, abs=1.0)


def test_straight_line_still_saves_as_native_line(qapp, tmp_path: Path) -> None:
    pdf = tmp_path / "straight.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    write_annotations(
        doc, {0: [LineItem(QPointF(10, 10), QPointF(110, 10))]}, 150
    )
    doc.save(str(pdf))
    doc.close()

    doc = fitz.open(str(pdf))
    try:
        page = doc[0]  # keep the page alive while touching its annots
        subtypes = [a.type[1] for a in (page.annots() or [])]
        assert subtypes == ["Line"]
    finally:
        doc.close()
