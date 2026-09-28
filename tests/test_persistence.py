"""Round-trip tests for PDF annotation persistence (M4)."""

from __future__ import annotations

import json
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
    GdtRow,
    GdtState,
)
from annoter.model.styles import (  # noqa: E402
    DashStyle,
    EndStyle,
    TextAlign,
)
from annoter.model.tolerance import Tolerance  # noqa: E402
from annoter.model.tolerance import (  # noqa: E402
    ToleranceMode as InlineToleranceMode,
)
from annoter.services.pdf_export import (  # noqa: E402
    legacy_dimension_runs,
    read_annotations,
    write_annotations,
)
from annoter.views.items.callout import CalloutItem  # noqa: E402
from annoter.views.items.freehand import FreehandItem  # noqa: E402
from annoter.views.items.note import StickyNoteItem  # noqa: E402
from annoter.views.items.stamp import StampItem  # noqa: E402
from annoter.views.items.gdt import GdtAnnotationItem  # noqa: E402
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.items.poly import (  # noqa: E402
    PolygonItem,
    PolylineItem,
)
from annoter.views.items.shapes import (  # noqa: E402
    CloudItem,
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


def test_cloud_roundtrip(qapp, blank_doc) -> None:
    item = CloudItem(QRectF(40, 50, 160, 90))
    item.set_color(QColor("#FB8C00"))
    item.set_stroke(2.0)
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    restored = out[0][0]
    assert isinstance(restored, CloudItem)
    # Exact geometry must survive: we store rect_pt, so the padded /Rect
    # MuPDF computes for the cloudy border must not leak into the item.
    scene = restored.rect().translated(restored.pos())
    assert scene.x() == pytest.approx(40, abs=0.1)
    assert scene.y() == pytest.approx(50, abs=0.1)
    assert scene.width() == pytest.approx(160, abs=0.1)
    assert scene.height() == pytest.approx(90, abs=0.1)


def test_cloud_is_polygon_with_cloud_border(qapp, blank_doc) -> None:
    """The native annot must be a Polygon carrying a cloudy border
    effect so Acrobat/Foxit render the scallops."""
    item = CloudItem(QRectF(40, 50, 160, 90))
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = next(page.annots())
    assert annot.type[1] == "Polygon"
    be = reopened.xref_get_key(annot.xref, "BE")
    reopened.close()
    # /BE present with a cloudy style (/S /C).
    assert be[0] != "null"
    assert "/C" in be[1]


def test_cloud_fill_roundtrip(qapp, blank_doc) -> None:
    item = CloudItem(QRectF(40, 50, 160, 90))
    item.set_fill_enabled(True)
    item.set_fill_color(QColor("#FFEB3B"))
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    c = out[0][0]
    assert isinstance(c, CloudItem)
    assert c.fill_enabled() is True
    assert c.fill_color().name().lower() == "#ffeb3b"


def test_cloud_fill_opacity_roundtrip(qapp, blank_doc) -> None:
    item = CloudItem(QRectF(40, 50, 160, 90))
    item.set_fill_enabled(True)
    item.set_fill_color(QColor("#FFEB3B"))
    item.set_fill_opacity(0.4)
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = next(page.annots())
    # Approximated as a whole-annotation /CA opacity on export.
    assert annot.opacity == pytest.approx(0.4, abs=0.01)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    c = out[0][0]
    assert c.fill_opacity() == pytest.approx(0.4, abs=0.01)


def test_rect_fill_opacity_defaults_to_fully_opaque(qapp, blank_doc) -> None:
    """Untouched shapes (opacity 1.0, the default) must not carry a /CA
    at all -- PyMuPDF reports -1 ("unspecified", i.e. fully opaque) --
    so files saved before this feature keep round-tripping unchanged."""
    item = RectangleItem(QRectF(0, 0, 50, 50))
    item.set_fill_enabled(True)
    write_annotations(blank_doc, {0: [item]}, dpi=150)
    reopened = _save_then_reopen(blank_doc)
    annot = next(reopened[0].annots())
    assert annot.opacity == -1
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert out[0][0].fill_opacity() == pytest.approx(1.0, abs=0.01)


def test_polyline_roundtrip(qapp, blank_doc) -> None:
    pts = [QPointF(10, 20), QPointF(80, 40), QPointF(60, 120), QPointF(150, 90)]
    item = PolylineItem(pts)
    item.set_color(QColor("#8E24AA"))
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    assert next(page.annots()).type[1] == "PolyLine"
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    restored = out[0][0]
    assert isinstance(restored, PolylineItem)
    rp = restored.points()
    assert len(rp) == 4
    assert rp[0].x() == pytest.approx(10, abs=0.1)
    assert rp[2].y() == pytest.approx(120, abs=0.1)


def test_polygon_roundtrip_with_fill(qapp, blank_doc) -> None:
    pts = [QPointF(30, 30), QPointF(140, 50), QPointF(110, 150), QPointF(40, 120)]
    item = PolygonItem(pts)
    item.set_fill_enabled(True)
    item.set_fill_color(QColor("#26A69A"))
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    assert next(page.annots()).type[1] == "Polygon"
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    restored = out[0][0]
    assert isinstance(restored, PolygonItem)
    assert len(restored.points()) == 4
    assert restored.fill_enabled() is True
    assert restored.fill_color().name().lower() == "#26a69a"


def test_polygon_and_cloud_disambiguated(qapp, blank_doc) -> None:
    """Both map to a PDF Polygon; the reader must tell them apart."""
    cloud = CloudItem(QRectF(20, 20, 120, 80))
    poly = PolygonItem(
        [QPointF(200, 200), QPointF(300, 220), QPointF(250, 320)]
    )
    write_annotations(blank_doc, {0: [cloud, poly]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    kinds = sorted(type(it).__name__ for it in out[0])
    assert kinds == ["CloudItem", "PolygonItem"]


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


def test_foreign_shapes_without_interior_color_stay_unfilled(
    qapp, blank_doc
) -> None:
    """A Square / Circle with no /IC (Acrobat's unfilled rectangle) must
    not come back filled with black; one with /IC keeps its fill."""
    page = blank_doc[0]
    for add in (page.add_rect_annot, page.add_circle_annot):
        annot = add(fitz.Rect(10, 10, 60, 60))
        annot.set_colors(stroke=(0.85, 0.27, 0.2))
        annot.update()
    filled = page.add_rect_annot(fitz.Rect(100, 10, 150, 60))
    filled.set_colors(stroke=(0, 0, 1), fill=(1, 1, 0))
    filled.update()

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    rect, ellipse, yellow = out[0]
    assert isinstance(rect, RectangleItem)
    assert isinstance(ellipse, EllipseItem)
    assert not rect.fill_enabled()
    assert not ellipse.fill_enabled()
    assert yellow.fill_enabled()
    assert yellow.fill_color().name() == "#ffff00"


def test_text_roundtrip(qapp, blank_doc) -> None:
    item = TextAnnotationItem(QPointF(50, 50), "Hello world")
    write_annotations(blank_doc, {0: [item]}, dpi=150)
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    assert isinstance(out[0][0], TextAnnotationItem)
    assert "Hello world" in out[0][0].text()


def test_callout_roundtrip(qapp, blank_doc) -> None:
    item = CalloutItem(QPointF(120, 60), "See note")
    item.set_tip(QPointF(-60, 40))  # tip at scene (60, 100)
    item.set_color(QColor("#EF5350"))
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = next(page.annots())
    assert annot.type[1] == "FreeText"
    # Native callout metadata is present for external viewers.
    assert reopened.xref_get_key(annot.xref, "IT")[1] == "/FreeTextCallout"
    assert reopened.xref_get_key(annot.xref, "CL")[0] == "array"
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    restored = out[0][0]
    assert isinstance(restored, CalloutItem)
    assert "See note" in restored.text()
    # Tip survives in scene coordinates.
    tip_scene = restored.pos() + restored.tip()
    assert tip_scene.x() == pytest.approx(60, abs=0.5)
    assert tip_scene.y() == pytest.approx(100, abs=0.5)


def test_callout_is_not_read_as_plain_text(qapp, blank_doc) -> None:
    """A plain text annot must stay text, a callout must stay a callout."""
    txt = TextAnnotationItem(QPointF(40, 40), "plain")
    call = CalloutItem(QPointF(200, 200), "callout")
    write_annotations(blank_doc, {0: [txt, call]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    kinds = sorted(type(it).__name__ for it in out[0])
    assert kinds == ["CalloutItem", "TextAnnotationItem"]


def test_sticky_note_roundtrip(qapp, blank_doc) -> None:
    item = StickyNoteItem(QPointF(80, 120), "Check this dimension")
    item.set_color(QColor("#FFB300"))
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    assert next(page.annots()).type[1] == "Text"
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    restored = out[0][0]
    assert isinstance(restored, StickyNoteItem)
    assert restored.text() == "Check this dimension"
    assert restored.pos().x() == pytest.approx(80, abs=0.5)
    assert restored.pos().y() == pytest.approx(120, abs=0.5)


def test_stamp_roundtrip(qapp, blank_doc) -> None:
    item = StampItem(QPointF(100, 80), "BON POUR EXÉCUTION")
    item.set_color(QColor("#1565C0"))
    item.set_font_size(18)
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    assert next(page.annots()).type[1] == "Stamp"
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    restored = out[0][0]
    assert isinstance(restored, StampItem)
    assert restored.text() == "BON POUR EXÉCUTION"
    assert restored.font_size() == 18
    assert restored.pos().x() == pytest.approx(100, abs=0.5)
    assert restored.pos().y() == pytest.approx(80, abs=0.5)


def test_stamp_appearance_visible_in_external_viewer(qapp, blank_doc) -> None:
    """The Stamp must carry an appearance stream that draws the label,
    not just an empty rectangle (rendered via MuPDF as a viewer would)."""
    item = StampItem(QPointF(100, 100), "APPROVED")
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = next(page.annots())
    r = annot.rect
    pix = page.get_pixmap(clip=r, matrix=fitz.Matrix(3, 3))
    reopened.close()
    dark = sum(1 for b in pix.samples if b < 128)
    assert dark > 50, "appearance stream did not draw the stamp"


def test_gdt_roundtrip_preserves_state(qapp, blank_doc) -> None:
    state = GdtState(
        characteristic=Characteristic.POSITION,
        tolerance_prefix="SØ",
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


def test_gdt_composite_roundtrip(qapp, blank_doc) -> None:
    state = GdtState(
        characteristic=Characteristic.POSITION,
        tolerance_prefix="Ø",
        tolerance_value="2",
        tolerance_modifier="P",
        datum_primary=DatumRef(["A"]),
        datum_secondary=DatumRef(["B", "B"]),
        datum_tertiary=DatumRef(["C"], modifier="M"),
        additional_rows=[GdtRow(tolerance_prefix="Ø", tolerance_value="0.5CZ")],
        upper_runs=[{"t": "2x"}],
        lower_runs=[{"t": "VALID FOR BOTH PARTS"}],
        aux_symbol=Characteristic.PARALLELISM,
        aux_text="A-B",
    )
    item = GdtAnnotationItem(state, QPointF(100, 90))
    write_annotations(blank_doc, {0: [item]}, dpi=150)
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    restored = out[0][0]
    assert isinstance(restored, GdtAnnotationItem)
    assert restored.state() == state


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


# ----------------------------------------------------------------------
# inline tolerance runs inside a text annotation (three tiers)
# ----------------------------------------------------------------------
def _first_annot(page: fitz.Page) -> fitz.Annot:
    """The page has to stay referenced by the caller: an annot whose
    page is collected raises 'not bound to any page' on every access."""
    return next(page.annots())


def _subject_props(annot: fitz.Annot) -> dict:
    return json.loads((annot.info or {}).get("subject") or "{}")


def _appearance_resources(doc: fitz.Document, annot: fitz.Annot) -> str:
    """The /AP/N form's /Resources as text ('' when there is no form)."""
    ap = doc.xref_get_key(annot.xref, "AP/N")
    if ap[0] != "xref":
        return ""
    ap_xref = int(ap[1].split()[0])
    return doc.xref_get_key(ap_xref, "Resources")[1] or ""


def test_plain_text_writes_no_runs_payload(qapp, blank_doc) -> None:
    """Tier 1: text without runs must keep producing the exact same
    payload as before inline tolerances existed."""
    item = TextAnnotationItem(QPointF(50, 50), "Percer avant soudure")
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = _first_annot(page)
    assert annot.type[1] == "FreeText"
    assert "runs" not in _subject_props(annot)
    # No rasterized appearance either: the native FreeText is correct.
    assert "AnnoterAP" not in _appearance_resources(reopened, annot)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    restored = out[0][0]
    assert isinstance(restored, TextAnnotationItem)
    assert restored.text() == "Percer avant soudure"
    assert restored.has_tolerance_runs() is False


def test_text_symmetric_run_roundtrip(qapp, blank_doc) -> None:
    """Tier 2: symmetric runs stay native FreeText (no appearance
    override), but reopen as editable runs rather than flat text."""
    item = TextAnnotationItem(QPointF(50, 50), "Bore ")
    item.insert_tolerance(
        Tolerance(mode=InlineToleranceMode.SYMMETRIC, value="0.05")
    )
    expected_runs = item.rich_runs()
    expected_text = item.text()
    assert item.has_stacked_runs() is False
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = _first_annot(page)
    assert annot.type[1] == "FreeText"
    # /Contents keeps the plain form for external viewers and search.
    assert "±0.05" in (annot.info or {}).get("content", "")
    assert _subject_props(annot)["runs"] == expected_runs
    assert "AnnoterAP" not in _appearance_resources(reopened, annot)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    restored = out[0][0]
    assert isinstance(restored, TextAnnotationItem)
    assert restored.has_tolerance_runs() is True
    assert restored.has_stacked_runs() is False
    assert restored.rich_runs() == expected_runs
    assert restored.text() == expected_text


def test_text_bilateral_run_roundtrip(qapp, blank_doc) -> None:
    """Tier 3: a stacked run has no plain-string form, so the annot also
    carries a rasterized appearance of the real two-line layout."""
    item = TextAnnotationItem(QPointF(60, 70), "Percer 12 ")
    item.insert_tolerance(
        Tolerance(
            mode=InlineToleranceMode.BILATERAL, upper="0.10", lower="0.05"
        )
    )
    expected_runs = item.rich_runs()
    expected_text = item.text()
    assert item.has_stacked_runs() is True
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = _first_annot(page)
    assert annot.type[1] == "FreeText"
    assert "+0.10/-0.05" in (annot.info or {}).get("content", "")
    assert _subject_props(annot)["runs"] == expected_runs
    # Our raster, not MuPDF's flattened text rendering.
    assert "AnnoterAP" in _appearance_resources(reopened, annot)
    # And it actually draws something inside the rect.
    pix = reopened[0].get_pixmap(clip=annot.rect, matrix=fitz.Matrix(3, 3))
    dark = sum(1 for b in pix.samples if b < 128)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert dark > 50, "appearance stream did not draw the runs"
    restored = out[0][0]
    assert isinstance(restored, TextAnnotationItem)
    assert restored.has_tolerance_runs() is True
    assert restored.has_stacked_runs() is True
    assert restored.rich_runs() == expected_runs
    assert restored.text() == expected_text


def test_text_mixed_runs_roundtrip(qapp, blank_doc) -> None:
    """Text, both tolerance modes and a line break, in order."""
    item = TextAnnotationItem(QPointF(60, 70), "Percer ")
    item.insert_tolerance(
        Tolerance(mode=InlineToleranceMode.SYMMETRIC, value="0.05")
    )
    item.insert_symbol(" et ")
    item.insert_tolerance(
        Tolerance(
            mode=InlineToleranceMode.BILATERAL, upper="0.10", lower="0.05"
        )
    )
    item.insert_symbol("\navant soudure")
    expected_runs = item.rich_runs()
    # Guard the fixture itself: the assertions below are only meaningful
    # if the source really holds both modes and a break.
    assert expected_runs == [
        {"t": "Percer "},
        {"tol": {"mode": "symmetric", "value": "0.05"}},
        {"t": " et "},
        {"tol": {"mode": "bilateral", "upper": "0.10", "lower": "0.05"}},
        {"br": 1},
        {"t": "avant soudure"},
    ]
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    restored = out[0][0]
    assert isinstance(restored, TextAnnotationItem)
    assert restored.rich_runs() == expected_runs
    assert restored.text() == item.text()


def test_text_runs_restored_at_the_saved_font_size(
    qapp, blank_doc
) -> None:
    """Runs are rasterized against the item's current font, so the font
    props must be restored *before* them -- otherwise the reopened runs
    (and the text typed after one, which carries an explicit char
    format) come back at the default size and the frame shrinks."""
    item = TextAnnotationItem(QPointF(40, 40), "Percer ")
    item.set_font_size(20)
    item.insert_tolerance(
        Tolerance(
            mode=InlineToleranceMode.BILATERAL, upper="0.10", lower="0.05"
        )
    )
    item.insert_symbol(" avant soudure")
    expected = item.content_rect()
    assert expected.width() > 0 and expected.height() > 0
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    restored = out[0][0]
    assert isinstance(restored, TextAnnotationItem)
    assert restored.font_size() == 20
    got = restored.content_rect()
    assert got.width() == pytest.approx(expected.width(), abs=1.0)
    assert got.height() == pytest.approx(expected.height(), abs=1.0)


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


# ----------------------------------------------------------------------
# legacy Dimension markers (tool retired in Lot L): reopen as texts
# ----------------------------------------------------------------------
def _legacy_dimension(doc, data: dict, rect_pt, props: dict | None = None):
    """Write a marker exactly as the v0.2.0 Dimension tool did."""
    import json

    annot = doc[0].add_rect_annot(fitz.Rect(*rect_pt))
    annot.set_info(
        title="Annoter:dim",
        content="annoter.dim:" + json.dumps(data),
        subject=json.dumps(props or {}),
    )
    annot.set_colors(stroke=(0.1, 0.2, 0.7))
    annot.update()


def test_legacy_dimension_runs() -> None:
    assert legacy_dimension_runs(
        {
            "prefix": "\u00d8",
            "nominal": "45.00",
            "tolerance_mode": "symmetric",
            "tol_value": "0.05",
        }
    ) == [
        {"t": "\u00d845.00"},
        {"tol": Tolerance(InlineToleranceMode.SYMMETRIC, value="0.05").to_dict()},
    ]
    assert legacy_dimension_runs(
        {
            "nominal": "12.50",
            "tolerance_mode": "bilateral",
            "tol_upper": "0.10",
            "tol_lower": "0.05",
        }
    ) == [
        {"t": "12.50"},
        {
            "tol": Tolerance(
                InlineToleranceMode.BILATERAL, upper="0.10", lower="0.05"
            ).to_dict()
        },
    ]
    assert legacy_dimension_runs({"nominal": "30"}) == [{"t": "30"}]
    # An empty tolerance adds nothing; an empty dimension is nothing.
    assert legacy_dimension_runs(
        {"nominal": "8", "tolerance_mode": "symmetric"}
    ) == [{"t": "8"}]
    assert legacy_dimension_runs({"nominal": ""}) == []


def test_legacy_dimension_reopens_as_text(qapp, blank_doc) -> None:
    _legacy_dimension(
        blank_doc,
        {
            "prefix": "R",
            "nominal": "12.50",
            "tolerance_mode": "bilateral",
            "tol_upper": "0.10",
            "tol_lower": "0.05",
        },
        (57.6, 38.4, 110.0, 60.0),
        {"dash": "solid", "rect_pt": [57.6, 38.4, 52.4, 21.6], "font_size": 20},
    )
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert len(out[0]) == 1
    text = out[0][0]
    assert type(text) is TextAnnotationItem
    assert text.text().startswith("R12.50")
    assert text.tolerances() == [
        Tolerance(InlineToleranceMode.BILATERAL, upper="0.10", lower="0.05")
    ]
    assert text.font_size() == 20
    # Where the dimension was (57.6 pt, 38.4 pt = 120 px, 80 px).
    assert text.pos().x() == pytest.approx(120, abs=0.5)
    assert text.pos().y() == pytest.approx(80, abs=0.5)
    assert text.color().blue() > text.color().red()


def test_legacy_dimension_is_rewritten_as_a_text_on_save(
    qapp, blank_doc
) -> None:
    _legacy_dimension(
        blank_doc,
        {"nominal": "30.00", "tolerance_mode": "symmetric", "tol_value": "0.1"},
        (40.0, 40.0, 90.0, 60.0),
    )
    first = _save_then_reopen(blank_doc)
    items = read_annotations(first, dpi=150)
    write_annotations(first, items, dpi=150)
    second = _save_then_reopen(first)
    first.close()
    kinds = [(a.type[1], (a.info or {}).get("title")) for a in second[0].annots()]
    out = read_annotations(second, dpi=150)
    second.close()
    assert ("Square", "Annoter:dim") not in kinds
    assert [k for k, _t in kinds] == ["FreeText"]
    assert type(out[0][0]) is TextAnnotationItem
    assert out[0][0].text().startswith("30.00")


def test_foreign_square_with_a_dim_like_title_stays_a_rectangle(
    qapp, blank_doc
) -> None:
    annot = blank_doc[0].add_rect_annot(fitz.Rect(10, 10, 50, 50))
    annot.set_info(title="Annoter:dim", content="not a dimension")
    annot.update()
    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    assert isinstance(out[0][0], RectangleItem)


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


def test_boxed_text_saves_without_aborting(qapp, blank_doc) -> None:
    """A boxed text annotation must not take the whole save down.

    PyMuPDF 1.27 rejects `border_color` on a FreeText ("cannot set
    border_color if rich_text is False") while offering no `rich_text`
    argument, so an unguarded call aborts `write_annotations` for the
    entire page. The border is Annoter-side anyway (it paints the
    outline from the /Subject JSON), so the save degrades to a
    borderless native annot instead of failing.
    """
    from annoter.model.styles import TextBorder

    item = TextAnnotationItem(QPointF(40, 40), "Revision A")
    item.set_border(TextBorder.BOX)
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = _first_annot(page)
    assert annot.type[1] == "FreeText"
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    restored = out[0][0]
    assert isinstance(restored, TextAnnotationItem)
    assert restored.text() == "Revision A"
    # The outline itself round-trips through the JSON payload.
    assert restored.border() is TextBorder.BOX


def test_boxed_line_labels_save_without_aborting(qapp, blank_doc) -> None:
    """Same guard on the line-label companion FreeText annots."""
    from annoter.model.styles import TextBorder
    from annoter.views.items.lines import LineItem

    line = LineItem(QPointF(10, 10), QPointF(120, 60))
    line.set_end_label("A")
    line.set_end_label_border(TextBorder.BOX)
    write_annotations(blank_doc, {0: [line]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    assert len(list(page.annots())) >= 1
    reopened.close()


def test_gdt_note_with_tolerance_roundtrip(qapp, blank_doc) -> None:
    """A note above a GD&T frame is rich text: it carries symbols and
    inline tolerance runs, and comes back editable."""
    from annoter.model.gdt import GdtState
    from annoter.model.tolerance import Tolerance as InlineTolerance
    from annoter.model.tolerance import ToleranceMode as InlineMode
    from annoter.views.items.gdt import GdtAnnotationItem

    item = GdtAnnotationItem(
        GdtState(tolerance_value="0.05"), QPointF(60, 60)
    )
    note = item.ensure_sub_text("upper")
    note.insert_symbol("Ø12 ")
    note.insert_tolerance(
        InlineTolerance(mode=InlineMode.BILATERAL, upper="0.10", lower="0.05")
    )
    item.set_state(item.state_with_sub_text("upper", note.rich_runs()))
    expected_runs = item.state().upper_runs
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    out = read_annotations(reopened, dpi=150)
    reopened.close()
    restored = out[0][0]
    assert isinstance(restored, GdtAnnotationItem)
    assert restored.state().upper_runs == expected_runs
    back = restored.sub_text("upper")
    assert back is not None
    assert back.text() == "Ø12 +0.10/-0.05"
    assert back.has_stacked_runs()
    # The frame's own data survived alongside the note.
    assert restored.state().tolerance_value == "0.05"


def test_gdt_note_is_rasterized_into_the_appearance(qapp, blank_doc) -> None:
    """The note lives two levels down (frame -> sub text -> inner text
    item), so a non-recursive paint walk would write a blank stream."""
    from annoter.model.gdt import GdtState
    from annoter.views.items.gdt import GdtAnnotationItem

    item = GdtAnnotationItem(
        GdtState(tolerance_value="0.05"), QPointF(60, 60)
    )
    note = item.ensure_sub_text("upper")
    note.insert_symbol("2X NOTE")
    item.set_state(item.state_with_sub_text("upper", note.rich_runs()))
    write_annotations(blank_doc, {0: [item]}, dpi=150)

    reopened = _save_then_reopen(blank_doc)
    page = reopened[0]
    annot = _first_annot(page)
    pix = annot.get_pixmap(alpha=False)
    buf = pix.samples
    dark = sum(1 for i in range(0, len(buf), pix.n) if sum(buf[i : i + 3]) < 600)
    reopened.close()
    assert dark > 100, "appearance stream looks blank"


def test_legacy_gdt_plain_notes_still_open(qapp, blank_doc) -> None:
    """PDFs written before the notes became rich text stored them as
    plain strings; they must reopen as single text runs."""
    from annoter.model.gdt import GdtState

    state = GdtState.from_dict(
        {
            "characteristic": "position",
            "tolerance_value": "0.1",
            "upper_text": "4X",
            "lower_text": "SEE NOTE 3",
        }
    )
    assert state.upper_runs == [{"t": "4X"}]
    assert state.lower_runs == [{"t": "SEE NOTE 3"}]
