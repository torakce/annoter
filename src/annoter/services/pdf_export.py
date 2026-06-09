"""Persistence: serialize annotation items to native PDF annotations and back.

Mapping (M4):
    RectangleItem      -> Square
    EllipseItem        -> Circle
    LineItem           -> Line
    ArrowItem          -> Line + endStyle OpenArrow
    FreehandItem       -> Ink
    TextAnnotationItem -> FreeText  (Helvetica only, for Acrobat compat)
    GdtAnnotationItem  -> Square + JSON in `Contents`

Coordinates are in page-local pixel space at the renderer's base DPI;
this module converts to PDF points (1pt = 1/72 in) on write and back on
read. We do not currently transform across page rotation -- annotations
are expected to live in the unrotated frame.

Annotations we own are tagged via `/T = "Annoter"` (or "Annoter:gdt" for
the JSON-bearing GD&T marker) so a Save can wipe-and-rewrite without
clobbering annotations the user opened from Acrobat. Annotations
without our tag are left untouched on save and reconstructed on open
when their type maps to a known item.
"""

from __future__ import annotations

import json
from typing import Iterable

import fitz
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor

from annoter.model.gdt import GdtState
from annoter.model.styles import DASH_PATTERNS, DashStyle, EndStyle, TextAlign
from annoter.views.items.base import AnnotationItem
from annoter.views.items.freehand import FreehandItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.shapes import EllipseItem, RectangleItem
from annoter.views.items.text import TextAnnotationItem


_OWNER_TAG = "Annoter"
_GDT_TAG = "Annoter:gdt"
_GDT_CONTENT_PREFIX = "annoter.gdt:"


# ----------------------------------------------------------------------
# enum <-> PDF mapping
# ----------------------------------------------------------------------
_END_TO_PDF: dict[EndStyle, int] = {
    EndStyle.NONE: fitz.PDF_ANNOT_LE_NONE,
    EndStyle.OPEN_ARROW: fitz.PDF_ANNOT_LE_OPEN_ARROW,
    EndStyle.CLOSED_ARROW: fitz.PDF_ANNOT_LE_CLOSED_ARROW,
    EndStyle.BUTT: fitz.PDF_ANNOT_LE_BUTT,
    EndStyle.DIAMOND: fitz.PDF_ANNOT_LE_DIAMOND,
    EndStyle.CIRCLE: fitz.PDF_ANNOT_LE_CIRCLE,
    EndStyle.SQUARE: fitz.PDF_ANNOT_LE_SQUARE,
    EndStyle.SLASH: fitz.PDF_ANNOT_LE_SLASH,
}
_PDF_TO_END: dict[int, EndStyle] = {v: k for k, v in _END_TO_PDF.items()}


def _dash_pattern_pt(style: DashStyle, stroke: float) -> list[float]:
    """PDF dash arrays are in points; our patterns are in stroke widths."""
    pat = DASH_PATTERNS.get(style, [])
    if not pat:
        return []
    return [max(0.1, float(seg) * float(stroke)) for seg in pat]


# ----------------------------------------------------------------------
# pixel <-> point helpers
# ----------------------------------------------------------------------
def _pt(value: float, dpi: int) -> float:
    return value * 72.0 / dpi


def _px(value: float, dpi: int) -> float:
    return value * dpi / 72.0


def _rect_pt(rect: QRectF, dpi: int) -> fitz.Rect:
    return fitz.Rect(
        _pt(rect.x(), dpi),
        _pt(rect.y(), dpi),
        _pt(rect.x() + rect.width(), dpi),
        _pt(rect.y() + rect.height(), dpi),
    )


def _point_pt(p: QPointF, dpi: int) -> fitz.Point:
    return fitz.Point(_pt(p.x(), dpi), _pt(p.y(), dpi))


def _qcolor_to_rgb01(c: QColor) -> tuple[float, float, float]:
    return (c.red() / 255.0, c.green() / 255.0, c.blue() / 255.0)


def _rgb01_to_qcolor(rgb: Iterable[float]) -> QColor:
    vals = (list(rgb) + [0.0, 0.0, 0.0])[:3]
    r, g, b = vals
    return QColor(int(r * 255), int(g * 255), int(b * 255))


# ----------------------------------------------------------------------
# write
# ----------------------------------------------------------------------
def write_annotations(
    doc: fitz.Document,
    page_items: dict[int, list[AnnotationItem]],
    dpi: int,
) -> None:
    """Replace owned annotations on each page with the given items.

    Annotations whose `/T` equals `_OWNER_TAG` (or starts with it) are
    deleted first; foreign annotations are preserved. The document is
    mutated in place; the caller saves it.
    """
    for page_index in range(doc.page_count):
        page = doc[page_index]
        _clear_owned(page)
        items = page_items.get(page_index, [])
        for item in items:
            _write_item(page, item, dpi)


def _clear_owned(page: fitz.Page) -> None:
    annots = list(page.annots() or [])
    for a in annots:
        title = (a.info or {}).get("title", "") or ""
        if title.startswith(_OWNER_TAG):
            page.delete_annot(a)


def _scene_rect(item) -> QRectF:
    """Return the item's *scene* (= page-local) rectangle.

    Annotations live in pixel space relative to the page pixmap item;
    `item.pos()` is the translation to apply to the local geometry.
    """
    pos = item.pos()
    if isinstance(item, (RectangleItem, EllipseItem)):
        r = item.rect()
        return QRectF(r.x() + pos.x(), r.y() + pos.y(), r.width(), r.height())
    if isinstance(item, GdtAnnotationItem):
        r = item.boundingRect()
        return QRectF(r.x() + pos.x(), r.y() + pos.y(), r.width(), r.height())
    if isinstance(item, TextAnnotationItem):
        r = item.boundingRect()
        return QRectF(r.x() + pos.x(), r.y() + pos.y(), r.width(), r.height())
    return item.boundingRect().translated(pos)


_TEXT_FONT_TO_PDF = {
    "Helvetica": "Helv",
    "Times New Roman": "TiRo",
    "Courier New": "Cour",
}
_PDF_TO_TEXT_FONT = {v: k for k, v in _TEXT_FONT_TO_PDF.items()}


def _props_payload(item: AnnotationItem) -> dict:
    """Serialize the props that don't fit native PDF fields cleanly."""
    p: dict[str, object] = {"dash": item.dash_style().value}
    if isinstance(item, RectangleItem) and not isinstance(item, EllipseItem):
        p["fill_enabled"] = bool(item.fill_enabled())
        p["fill_color"] = item.fill_color().name()
        p["corner_radius"] = float(item.corner_radius())
        if item.text():
            p["text"] = item.text()
            p["label_font_size"] = int(item.label_font_size())
    elif isinstance(item, EllipseItem):
        p["fill_enabled"] = bool(item.fill_enabled())
        p["fill_color"] = item.fill_color().name()
        if item.text():
            p["text"] = item.text()
            p["label_font_size"] = int(item.label_font_size())
    elif isinstance(item, ArrowItem):
        p["start_end"] = item.start_end().value
        p["end_end"] = item.end_end().value
    elif isinstance(item, TextAnnotationItem):
        p["font_family"] = item.font_family()
        p["font_size"] = int(item.font_size())
        p["bold"] = bool(item.bold())
        p["italic"] = bool(item.italic())
        p["align"] = item.align().value
    elif isinstance(item, GdtAnnotationItem):
        p["font_size"] = int(item.font_size())
    return p


def _apply_props_to_item(item: AnnotationItem, props: dict) -> None:
    """Restore extras saved by `_props_payload`. Tolerant to missing keys."""
    dash = props.get("dash")
    if dash is not None:
        try:
            item.set_dash_style(DashStyle(dash))
        except ValueError:
            pass
    if isinstance(item, RectangleItem) and not isinstance(item, EllipseItem):
        if "fill_enabled" in props:
            item.set_fill_enabled(bool(props["fill_enabled"]))
        if "fill_color" in props:
            item.set_fill_color(QColor(str(props["fill_color"])))
        if "corner_radius" in props:
            item.set_corner_radius(float(props["corner_radius"]))
        if "label_font_size" in props:
            item.set_label_font_size(int(props["label_font_size"]))
        if "text" in props:
            item.set_text(str(props["text"]))
    elif isinstance(item, EllipseItem):
        if "fill_enabled" in props:
            item.set_fill_enabled(bool(props["fill_enabled"]))
        if "fill_color" in props:
            item.set_fill_color(QColor(str(props["fill_color"])))
        if "label_font_size" in props:
            item.set_label_font_size(int(props["label_font_size"]))
        if "text" in props:
            item.set_text(str(props["text"]))
    elif isinstance(item, ArrowItem):
        try:
            if "start_end" in props:
                item.set_start_end(EndStyle(props["start_end"]))
            if "end_end" in props:
                item.set_end_end(EndStyle(props["end_end"]))
        except ValueError:
            pass
    elif isinstance(item, TextAnnotationItem):
        if "font_family" in props:
            item.set_font_family(str(props["font_family"]))
        if "font_size" in props:
            item.set_font_size(int(props["font_size"]))
        if "bold" in props:
            item.set_bold(bool(props["bold"]))
        if "italic" in props:
            item.set_italic(bool(props["italic"]))
        if "align" in props:
            try:
                item.set_align(TextAlign(props["align"]))
            except ValueError:
                pass
    elif isinstance(item, GdtAnnotationItem):
        if "font_size" in props:
            item.set_font_size(int(props["font_size"]))


def _set_dash_border(
    annot: fitz.Annot, stroke: float, style: DashStyle
) -> None:
    dashes = _dash_pattern_pt(style, stroke)
    if dashes:
        annot.set_border(width=stroke, dashes=dashes)
    else:
        annot.set_border(width=stroke)


def _write_item(page: fitz.Page, item: AnnotationItem, dpi: int) -> None:
    color = _qcolor_to_rgb01(item.color())
    stroke = float(item.stroke())
    props = _props_payload(item)
    subject = json.dumps(props)

    if isinstance(item, RectangleItem) and not isinstance(
        item, EllipseItem
    ):
        rect_pt = _rect_pt(_scene_rect(item), dpi)
        annot = page.add_rect_annot(rect_pt)
        colors = {"stroke": color}
        if item.fill_enabled():
            colors["fill"] = _qcolor_to_rgb01(item.fill_color())
        annot.set_colors(colors)
        _set_dash_border(annot, stroke, item.dash_style())
        annot.set_info(title=_OWNER_TAG, subject=subject)
        annot.update()
        return

    if isinstance(item, EllipseItem):
        rect_pt = _rect_pt(_scene_rect(item), dpi)
        annot = page.add_circle_annot(rect_pt)
        colors = {"stroke": color}
        if item.fill_enabled():
            colors["fill"] = _qcolor_to_rgb01(item.fill_color())
        annot.set_colors(colors)
        _set_dash_border(annot, stroke, item.dash_style())
        annot.set_info(title=_OWNER_TAG, subject=subject)
        annot.update()
        return

    if isinstance(item, GdtAnnotationItem):
        rect_pt = _rect_pt(_scene_rect(item), dpi)
        annot = page.add_rect_annot(rect_pt)
        payload = _GDT_CONTENT_PREFIX + json.dumps(item.state().to_dict())
        annot.set_info(title=_GDT_TAG, content=payload, subject=subject)
        annot.set_colors(stroke=color)
        _set_dash_border(annot, stroke, item.dash_style())
        annot.update()
        return

    if isinstance(item, ArrowItem):
        p1, p2 = item.line_points()
        pos = item.pos()
        a = _point_pt(QPointF(p1.x() + pos.x(), p1.y() + pos.y()), dpi)
        b = _point_pt(QPointF(p2.x() + pos.x(), p2.y() + pos.y()), dpi)
        annot = page.add_line_annot(a, b)
        annot.set_line_ends(
            _END_TO_PDF.get(item.start_end(), fitz.PDF_ANNOT_LE_NONE),
            _END_TO_PDF.get(item.end_end(), fitz.PDF_ANNOT_LE_OPEN_ARROW),
        )
        annot.set_colors(stroke=color)
        _set_dash_border(annot, stroke, item.dash_style())
        annot.set_info(title=_OWNER_TAG, subject=subject)
        annot.update()
        return

    if isinstance(item, LineItem):
        p1, p2 = item.line_points()
        pos = item.pos()
        a = _point_pt(QPointF(p1.x() + pos.x(), p1.y() + pos.y()), dpi)
        b = _point_pt(QPointF(p2.x() + pos.x(), p2.y() + pos.y()), dpi)
        annot = page.add_line_annot(a, b)
        annot.set_colors(stroke=color)
        _set_dash_border(annot, stroke, item.dash_style())
        annot.set_info(title=_OWNER_TAG, subject=subject)
        annot.update()
        return

    if isinstance(item, FreehandItem):
        pos = item.pos()
        stroke_pts = [
            (_pt(p.x() + pos.x(), dpi), _pt(p.y() + pos.y(), dpi))
            for p in item.points()
        ]
        if len(stroke_pts) < 2:
            return
        annot = page.add_ink_annot([stroke_pts])
        annot.set_colors(stroke=color)
        _set_dash_border(annot, stroke, item.dash_style())
        annot.set_info(title=_OWNER_TAG, subject=subject)
        annot.update()
        return

    if isinstance(item, TextAnnotationItem):
        rect = _scene_rect(item)
        # Pad the rect a touch so Acrobat doesn't clip ascenders/descenders.
        rect.adjust(0, 0, 4.0, 4.0)
        rect_pt = _rect_pt(rect, dpi)
        fontname = _TEXT_FONT_TO_PDF.get(item.font_family(), "Helv")
        annot = page.add_freetext_annot(
            rect_pt,
            item.text(),
            fontsize=int(item.font_size()),
            fontname=fontname,
            text_color=color,
            border_color=None,
            fill_color=None,
        )
        annot.set_info(title=_OWNER_TAG, subject=subject)
        annot.update()
        return


# ----------------------------------------------------------------------
# read
# ----------------------------------------------------------------------
def read_annotations(
    doc: fitz.Document, dpi: int
) -> dict[int, list[AnnotationItem]]:
    """Reconstruct items from each page's PDF annotations.

    Foreign annotations (created by Acrobat etc.) are mapped by type
    when possible. Unknown subtypes are silently skipped in v1.
    """
    out: dict[int, list[AnnotationItem]] = {}
    for page_index in range(doc.page_count):
        page = doc[page_index]
        items: list[AnnotationItem] = []
        for annot in page.annots() or []:
            items.extend(_annot_to_items(annot, dpi))
        out[page_index] = items
    return out


def _annot_to_items(
    annot: fitz.Annot, dpi: int
) -> list[AnnotationItem]:
    subtype = annot.type[1] if annot.type else ""
    info = annot.info or {}
    title = info.get("title", "") or ""
    content = info.get("content", "") or ""
    subject = info.get("subject", "") or ""
    rect = annot.rect  # fitz.Rect in PDF points
    colors = annot.colors or {}
    stroke_rgb = colors.get("stroke") or (0.0, 0.0, 0.0)
    fill_rgb = colors.get("fill")
    border = annot.border or {}
    width = float(border.get("width") or 1.0)
    if width < 0.0:  # PyMuPDF returns -1.0 when undefined
        width = 1.0

    props: dict = {}
    if title.startswith(_OWNER_TAG) and subject:
        try:
            props = json.loads(subject)
            if not isinstance(props, dict):
                props = {}
        except ValueError:
            props = {}

    qcolor = _rgb01_to_qcolor(stroke_rgb)
    qrect = QRectF(
        _px(rect.x0, dpi),
        _px(rect.y0, dpi),
        _px(rect.width, dpi),
        _px(rect.height, dpi),
    )

    # GD&T marker: Square + JSON.
    if (
        subtype == "Square"
        and title.startswith(_GDT_TAG)
        and content.startswith(_GDT_CONTENT_PREFIX)
    ):
        try:
            data = json.loads(content[len(_GDT_CONTENT_PREFIX) :])
            state = GdtState.from_dict(data)
        except (ValueError, KeyError):
            return []
        item = GdtAnnotationItem(state, QPointF(qrect.x(), qrect.y()))
        item.set_color(qcolor)
        item.set_stroke(width)
        _apply_props_to_item(item, props)
        return [item]

    if subtype == "Square":
        item = RectangleItem(QRectF(0, 0, qrect.width(), qrect.height()))
        item.setPos(qrect.x(), qrect.y())
        item.set_color(qcolor)
        item.set_stroke(width)
        if fill_rgb is not None and "fill_enabled" not in props:
            item.set_fill_enabled(True)
            item.set_fill_color(_rgb01_to_qcolor(fill_rgb))
        _apply_props_to_item(item, props)
        return [item]

    if subtype == "Circle":
        item = EllipseItem(QRectF(0, 0, qrect.width(), qrect.height()))
        item.setPos(qrect.x(), qrect.y())
        item.set_color(qcolor)
        item.set_stroke(width)
        if fill_rgb is not None and "fill_enabled" not in props:
            item.set_fill_enabled(True)
            item.set_fill_color(_rgb01_to_qcolor(fill_rgb))
        _apply_props_to_item(item, props)
        return [item]

    if subtype == "Line":
        verts = annot.vertices or []
        if len(verts) < 2:
            return []
        (x1, y1), (x2, y2) = verts[0], verts[1]
        p1 = QPointF(_px(x1, dpi), _px(y1, dpi))
        p2 = QPointF(_px(x2, dpi), _px(y2, dpi))
        ends = annot.line_ends or (
            fitz.PDF_ANNOT_LE_NONE,
            fitz.PDF_ANNOT_LE_NONE,
        )
        is_arrow = ends[0] != fitz.PDF_ANNOT_LE_NONE or ends[1] != fitz.PDF_ANNOT_LE_NONE
        cls = ArrowItem if is_arrow else LineItem
        item = cls(p1, p2)
        item.set_color(qcolor)
        item.set_stroke(width)
        if is_arrow and "start_end" not in props and "end_end" not in props:
            try:
                item.set_start_end(
                    _PDF_TO_END.get(int(ends[0]), EndStyle.NONE)
                )
                item.set_end_end(
                    _PDF_TO_END.get(int(ends[1]), EndStyle.OPEN_ARROW)
                )
            except (TypeError, ValueError):
                pass
        _apply_props_to_item(item, props)
        return [item]

    if subtype == "Ink":
        # `annot.vertices` is a list of strokes (each a list of (x, y)
        # pairs) for Ink annots; older PyMuPDF versions returned one
        # flat list. Each stroke becomes its own FreehandItem so the
        # strokes are not joined by spurious connecting segments.
        verts = annot.vertices or []
        if verts and verts[0] and isinstance(verts[0][0], (list, tuple)):
            strokes = verts
        else:
            strokes = [verts]
        out: list[AnnotationItem] = []
        for stroke in strokes:
            points = [
                QPointF(_px(x, dpi), _px(y, dpi)) for x, y in stroke
            ]
            if len(points) < 2:
                continue
            item = FreehandItem(points)
            item.set_color(qcolor)
            item.set_stroke(width)
            _apply_props_to_item(item, props)
            out.append(item)
        return out

    if subtype == "FreeText":
        text = content
        item = TextAnnotationItem(QPointF(qrect.x(), qrect.y()), text)
        item.set_color(qcolor)
        item.set_stroke(width)
        _apply_props_to_item(item, props)
        return [item]

    return []
