"""Round-trip tests for PDF annotation persistence (M4)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.model.gdt import (  # noqa: E402
    Characteristic,
    DatumRef,
    GdtState,
)
from annoter.model.styles import (  # noqa: E402
    DashStyle,
    EndStyle,
    TextAlign,
)
from annoter.services.pdf_export import (  # noqa: E402
    read_annotations,
    write_annotations,
)
from annoter.views.items.freehand import FreehandItem  # noqa: E402
from annoter.views.items.gdt import GdtAnnotationItem  # noqa: E402
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.items.shapes import (  # noqa: E402
    EllipseItem,
    RectangleItem,
)
from annoter.views.items.text import TextAnnotationItem  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def blank_doc():
    doc = fitz.open()
    doc.new_page(width=600, height=400)
    yield doc
    doc.close()


def _save_then_reopen(doc: fitz.Document) -> fitz.Document:
    """Round-trip through disk so we exercise the actual PDF parser."""
    tmp = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".pdf")
    tmp.close()
    path = Path(tmp.name)
    try:
        doc.save(str(path), garbage=3, deflate=True)
        return fitz.open(str(path))
    finally:
        # We can't unlink while the new fitz.Document holds the file
        # open on Windows; the test will close it then drop the path.
        pass


def test_rectangle_roundtrip(qapp, blank_doc) -> None:
    item = RectangleItem(QRectF(20, 30, 100, 50))
    item.set_color(QColor("#1E88E5"))
    item.set_stroke(2.0)
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    restored = out[0][0]
    assert isinstance(restored, RectangleItem)
    # Exact geometry round-trip (scene rect = local rect + pos): the
    # padded /Rect must not leak into the restored item.
    scene = restored.rect().translated(restored.pos())
    assert scene.x() == pytest.approx(20, abs=0.05)
    assert scene.y() == pytest.approx(30, abs=0.05)
    assert scene.width() == pytest.approx(100, abs=0.05)
    assert scene.height() == pytest.approx(50, abs=0.05)


def test_ellipse_roundtrip(qapp, blank_doc) -> None:
    item = EllipseItem(QRectF(40, 60, 120, 80))
    write_annotations(blank_doc, {0: [item]}, dpi=150)
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    assert isinstance(out[0][0], EllipseItem)


def test_line_roundtrip(qapp, blank_doc) -> None:
    item = LineItem(QPointF(10, 10), QPointF(200, 100))
    write_annotations(blank_doc, {0: [item]}, dpi=150)
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    assert isinstance(out[0][0], LineItem)
    assert not isinstance(out[0][0], ArrowItem)


def test_arrow_roundtrip(qapp, blank_doc) -> None:
    item = ArrowItem(QPointF(10, 10), QPointF(200, 100))
    write_annotations(blank_doc, {0: [item]}, dpi=150)
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    assert isinstance(out[0][0], ArrowItem)


def test_freehand_roundtrip(qapp, blank_doc) -> None:
    pts = [QPointF(10, 50), QPointF(20, 60), QPointF(30, 55), QPointF(50, 70)]
    item = FreehandItem(pts)
    write_annotations(blank_doc, {0: [item]}, dpi=150)
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    assert isinstance(out[0][0], FreehandItem)
    assert len(out[0][0].points()) >= 2


def test_foreign_multistroke_ink_reads_one_item_per_stroke(
    qapp, blank_doc
) -> None:
    """An Ink annot with several strokes (e.g. from Acrobat) must not be
    flattened into a single polyline joined by spurious segments."""
    page = blank_doc[0]
    stroke_a = [(10.0, 10.0), (20.0, 15.0), (30.0, 12.0)]
    stroke_b = [(100.0, 100.0), (110.0, 105.0)]
    page.add_ink_annot([stroke_a, stroke_b])

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    items = out[0]
    assert len(items) == 2
    assert all(isinstance(it, FreehandItem) for it in items)
    assert len(items[0].points()) == 3
    assert len(items[1].points()) == 2


def test_text_roundtrip(qapp, blank_doc) -> None:
    item = TextAnnotationItem(QPointF(50, 50), "Hello world")
    write_annotations(blank_doc, {0: [item]}, dpi=150)
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    assert isinstance(out[0][0], TextAnnotationItem)
    assert "Hello world" in out[0][0].text()


def test_gdt_roundtrip_preserves_state(qapp, blank_doc) -> None:
    state = GdtState(
        characteristic=Characteristic.POSITION,
        diameter_prefix=True,
        tolerance_value="0.1",
        tolerance_modifier="M",
        datum_primary=DatumRef(["A", "B"], modifier="M"),
        datum_secondary=DatumRef(["C"]),
    )
    item = GdtAnnotationItem(state, QPointF(120, 80))
    write_annotations(blank_doc, {0: [item]}, dpi=150)
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    restored = out[0][0]
    assert isinstance(restored, GdtAnnotationItem)
    assert restored.state() == state
    # Position must not drift across save/reopen cycles (the writer
    # stores the content rect, whose topleft is exactly item.pos()).
    assert restored.pos().x() == pytest.approx(120, abs=0.5)
    assert restored.pos().y() == pytest.approx(80, abs=0.5)


def test_gdt_appearance_visible_in_external_viewer(qapp, blank_doc) -> None:
    """The GD&T annot must carry an appearance stream that actually
    draws the frame: rendering the page region through MuPDF (as any
    external viewer would) must produce dark pixels inside the rect,
    not just an empty rectangle outline."""
    state = GdtState(
        characteristic=Characteristic.FLATNESS,
        tolerance_value="0.05",
        datum_primary=DatumRef(["A"]),
    )
    item = GdtAnnotationItem(state, QPointF(100, 100))
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = next(page.annots())
    r = annot.rect
    # Sample the interior only: a bare Square outline would leave it
    # blank, while the frame draws cell separators and glyphs there.
    interior = fitz.Rect(
        r.x0 + r.width * 0.15,
        r.y0 + r.height * 0.25,
        r.x1 - r.width * 0.15,
        r.y1 - r.height * 0.25,
    )
    pix = page.get_pixmap(clip=interior, matrix=fitz.Matrix(3, 3))
    reopened.close()
    dark = sum(1 for b in pix.samples if b < 128)
    assert dark > 50, "appearance stream did not draw the frame"


def test_owned_annotations_are_overwritten(qapp, blank_doc) -> None:
    """A second write replaces our annots, not duplicates them."""
    a = RectangleItem(QRectF(0, 0, 50, 50))
    write_annotations(blank_doc, {0: [a]}, dpi=150)
    b = RectangleItem(QRectF(100, 100, 50, 50))
    write_annotations(blank_doc, {0: [b]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1


def test_rect_props_roundtrip(qapp, blank_doc) -> None:
    item = RectangleItem(QRectF(20, 30, 100, 50))
    item.set_dash_style(DashStyle.DASH_DOT_DOT)
    item.set_fill_enabled(True)
    item.set_fill_color(QColor("#FFEB3B"))
    item.set_corner_radius(8.0)
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    r = out[0][0]
    assert isinstance(r, RectangleItem)
    assert r.dash_style() is DashStyle.DASH_DOT_DOT
    assert r.fill_enabled() is True
    assert r.corner_radius() == 8.0
    assert r.fill_color().name().lower() == "#ffeb3b"


def test_arrow_ends_roundtrip(qapp, blank_doc) -> None:
    item = ArrowItem(QPointF(10, 10), QPointF(200, 100))
    item.set_start_end(EndStyle.DIAMOND)
    item.set_end_end(EndStyle.CLOSED_ARROW)
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    a = out[0][0]
    assert isinstance(a, ArrowItem)
    assert a.start_end() is EndStyle.DIAMOND
    assert a.end_end() is EndStyle.CLOSED_ARROW


def test_text_props_roundtrip(qapp, blank_doc) -> None:
    item = TextAnnotationItem(QPointF(50, 50), "Hello")
    item.set_font_family("Times New Roman")
    item.set_font_size(18)
    item.set_bold(True)
    item.set_italic(True)
    item.set_align(TextAlign.CENTER)
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    t = out[0][0]
    assert isinstance(t, TextAnnotationItem)
    assert t.font_family() == "Times New Roman"
    assert t.font_size() == 18
    assert t.bold() is True
    assert t.italic() is True
    assert t.align() is TextAlign.CENTER


def test_gdt_font_size_roundtrip(qapp, blank_doc) -> None:
    state = GdtState(
        characteristic=Characteristic.POSITION,
        tolerance_value="0.05",
    )
    item = GdtAnnotationItem(state, QPointF(120, 80))
    item.set_font_size(20)
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    g = out[0][0]
    assert isinstance(g, GdtAnnotationItem)
    assert g.font_size() == 20


def test_color_roundtrip(qapp, blank_doc) -> None:
    item = RectangleItem(QRectF(20, 30, 100, 50))
    item.set_color(QColor("#43A047"))
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    restored_color = out[0][0].color()
    # Allow 1-bit per channel error (PDF uses float in [0,1]).
    assert abs(restored_color.red() - 0x43) <= 2
    assert abs(restored_color.green() - 0xA0) <= 2
    assert abs(restored_color.blue() - 0x47) <= 2
