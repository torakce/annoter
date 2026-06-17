"""Persistence: serialize annotation items to native PDF annotations and back.

Mapping (M4):
    RectangleItem      -> Square
    EllipseItem        -> Circle
    CloudItem          -> Polygon + cloudy border effect (/BE)
    PolylineItem       -> PolyLine
    PolygonItem        -> Polygon
    LineItem           -> Line
    ArrowItem          -> Line + endStyle OpenArrow
    FreehandItem       -> Ink
    TextAnnotationItem -> FreeText  (Helvetica only, for Acrobat compat)
    CalloutItem        -> FreeText + /IT /FreeTextCallout + /CL leader
                          line (leader geometry also in /Subject JSON, the
                          authoritative source on reopen)
    StickyNoteItem     -> Text (sticky note; note body in /Contents)
    StampItem          -> Stamp + rasterized appearance stream (text /
                          size in /Subject JSON)
    GdtAnnotationItem  -> Square + JSON in `Contents` + rasterized
                          appearance stream (so Acrobat/Foxit show the
                          actual feature control frame)

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
import math
from typing import Iterable

import fitz
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QStyleOptionGraphicsItem

from annoter.model.gdt import GdtState
from annoter.model.styles import DASH_PATTERNS, DashStyle, EndStyle, TextAlign
from annoter.views.items.base import AnnotationItem
from annoter.views.items.callout import CalloutItem
from annoter.views.items.freehand import FreehandItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.note import StickyNoteItem
from annoter.views.items.poly import PolygonItem, PolylineItem
from annoter.views.items.shapes import CloudItem, EllipseItem, RectangleItem
from annoter.views.items.stamp import StampItem
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
    if isinstance(item, (RectangleItem, EllipseItem, CloudItem)):
        r = item.rect()
        return QRectF(r.x() + pos.x(), r.y() + pos.y(), r.width(), r.height())
    if isinstance(item, GdtAnnotationItem):
        # content_rect, not boundingRect: the bounding rect includes the
        # selection/handle margin, which would shift the item's position
        # on every save/reopen cycle (the reader anchors on rect topleft).
        r = item.content_rect()
        return QRectF(r.x() + pos.x(), r.y() + pos.y(), r.width(), r.height())
    if isinstance(item, (CalloutItem, StampItem)):
        # The content box only (callout leader / stamp glyph aside).
        r = item.content_rect()
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


def _props_payload(item: AnnotationItem, dpi: int) -> dict:
    """Serialize the props that don't fit native PDF fields cleanly.

    Includes the exact geometry in PDF points ("rect_pt" / "pos_pt"):
    MuPDF pads the stored /Rect of Square and Circle annots by the
    border width, so reading /Rect back would drift the item on every
    save/reopen cycle. Foreign annots have no payload and keep the
    /Rect-based geometry.
    """
    p: dict[str, object] = {"dash": item.dash_style().value}
    if isinstance(
        item,
        (RectangleItem, EllipseItem, CloudItem, GdtAnnotationItem, StampItem),
    ):
        r = _scene_rect(item)
        p["rect_pt"] = [
            _pt(r.x(), dpi),
            _pt(r.y(), dpi),
            _pt(r.width(), dpi),
            _pt(r.height(), dpi),
        ]
    elif isinstance(item, TextAnnotationItem):
        p["pos_pt"] = [
            _pt(item.pos().x(), dpi),
            _pt(item.pos().y(), dpi),
        ]
    elif isinstance(item, StickyNoteItem):
        p["pos_pt"] = [
            _pt(item.pos().x(), dpi),
            _pt(item.pos().y(), dpi),
        ]
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
    elif isinstance(item, CloudItem):
        p["poly"] = "cloud"
        p["fill_enabled"] = bool(item.fill_enabled())
        p["fill_color"] = item.fill_color().name()
    elif isinstance(item, PolygonItem):
        p["poly"] = "polygon"
        p["fill_enabled"] = bool(item.fill_enabled())
        p["fill_color"] = item.fill_color().name()
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
    elif isinstance(item, StampItem):
        p["text"] = item.text()
        p["font_size"] = int(item.font_size())
    if isinstance(item, CalloutItem):
        # Authoritative leader geometry (scene coords, in points): the
        # native /CL is best-effort for external viewers only.
        tip = item.tip()
        pos = item.pos()
        p["callout_tip_pt"] = [
            _pt(pos.x() + tip.x(), dpi),
            _pt(pos.y() + tip.y(), dpi),
        ]
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
    elif isinstance(item, (CloudItem, PolygonItem)):
        if "fill_enabled" in props:
            item.set_fill_enabled(bool(props["fill_enabled"]))
        if "fill_color" in props:
            item.set_fill_color(QColor(str(props["fill_color"])))
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
    elif isinstance(item, StampItem):
        if "text" in props:
            item.set_text(str(props["text"]))
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
    props = _props_payload(item, dpi)
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

    if isinstance(item, CloudItem):
        r = _scene_rect(item)
        pts = [
            fitz.Point(_pt(r.left(), dpi), _pt(r.top(), dpi)),
            fitz.Point(_pt(r.right(), dpi), _pt(r.top(), dpi)),
            fitz.Point(_pt(r.right(), dpi), _pt(r.bottom(), dpi)),
            fitz.Point(_pt(r.left(), dpi), _pt(r.bottom(), dpi)),
        ]
        annot = page.add_polygon_annot(pts)
        colors = {"stroke": color}
        if item.fill_enabled():
            colors["fill"] = _qcolor_to_rgb01(item.fill_color())
        annot.set_colors(colors)
        # Cloudy border effect so Acrobat/Foxit draw the scallops too.
        annot.set_border(width=stroke, clouds=1)
        annot.set_info(title=_OWNER_TAG, subject=subject)
        annot.update()
        return

    if isinstance(item, (PolylineItem, PolygonItem)):
        pos = item.pos()
        pts = [
            _point_pt(QPointF(p.x() + pos.x(), p.y() + pos.y()), dpi)
            for p in item.points()
        ]
        if len(pts) < 2:
            return
        if isinstance(item, PolygonItem):
            annot = page.add_polygon_annot(pts)
            colors = {"stroke": color}
            if item.fill_enabled():
                colors["fill"] = _qcolor_to_rgb01(item.fill_color())
            annot.set_colors(colors)
        else:
            annot = page.add_polyline_annot(pts)
            annot.set_colors(stroke=color)
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
        try:
            _set_rasterized_appearance(annot, item, dpi)
        except Exception:
            # The appearance is cosmetic for external viewers; never let
            # it break a save. Acrobat falls back to a plain rectangle.
            pass
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

    if isinstance(item, StickyNoteItem):
        pos = item.pos()
        annot = page.add_text_annot(
            _point_pt(QPointF(pos.x(), pos.y()), dpi),
            item.text(),
        )
        annot.set_colors(stroke=color)
        annot.set_info(title=_OWNER_TAG, subject=subject)
        annot.update()
        return

    if isinstance(item, StampItem):
        rect_pt = _rect_pt(_scene_rect(item), dpi)
        annot = page.add_stamp_annot(rect_pt)
        annot.set_colors(stroke=color)
        annot.set_info(title=_OWNER_TAG, subject=subject)
        annot.update()
        try:
            _set_rasterized_appearance(annot, item, dpi)
        except Exception:
            # Appearance is cosmetic for external viewers; never let it
            # break a save. Annoter rebuilds the item from the JSON.
            pass
        return

    if isinstance(item, CalloutItem):
        rect = _scene_rect(item)
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
        # Best-effort native callout line for external viewers. MuPDF's
        # own appearance generator ignores /CL, but Acrobat honors it;
        # Annoter rebuilds the leader from the JSON regardless.
        try:
            _set_callout_line(annot, item, dpi)
        except Exception:
            pass
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
# callout leader line (native /CL for external viewers)
# ----------------------------------------------------------------------
def _set_callout_line(
    annot: fitz.Annot, item: CalloutItem, dpi: int
) -> None:
    """Write /IT /FreeTextCallout and the /CL leader line.

    /CL is in unrotated PDF user space (origin bottom-left, y up), so
    each point's y is flipped through the page height. MuPDF does not
    draw this in its generated appearance, but spec-compliant viewers
    (Acrobat) do; Annoter itself rebuilds the leader from the JSON.
    """
    page = annot.parent
    doc = page.parent
    height = page.rect.height
    pos = item.pos()
    tip = item.tip()
    conn = item.connection_point()

    def to_pdf(local_x: float, local_y: float) -> tuple[float, float]:
        x = _pt(pos.x() + local_x, dpi)
        y = _pt(pos.y() + local_y, dpi)
        return x, height - y

    tx, ty = to_pdf(tip.x(), tip.y())
    kx, ky = to_pdf(conn.x(), conn.y())
    doc.xref_set_key(annot.xref, "IT", "/FreeTextCallout")
    doc.xref_set_key(
        annot.xref,
        "CL",
        f"[{tx:.2f} {ty:.2f} {kx:.2f} {ky:.2f}]",
    )
    doc.xref_set_key(annot.xref, "LE", "/OpenArrow")


# ----------------------------------------------------------------------
# rasterized appearance stream (shared by GD&T frames and stamps)
# ----------------------------------------------------------------------
_GDT_AP_PX_PER_PT = 3.0  # raster density of the appearance image


def _rasterize_item_planes(
    item, dpi: int
) -> tuple[bytes, bytes, int, int]:
    """Rasterize an item's `content_rect` to raw (RGB, alpha) planes.

    Works for any item exposing `content_rect()` and `paint()`; used for
    GD&T frames and stamps, whose native annot appearance we replace so
    external viewers show the real glyphs."""
    src = item.content_rect()
    # Item units are page pixels at `dpi`; target density is
    # _GDT_AP_PX_PER_PT device pixels per PDF point.
    scale = _GDT_AP_PX_PER_PT * 72.0 / dpi
    w = max(1, math.ceil(src.width() * scale))
    h = max(1, math.ceil(src.height() * scale))
    img = QImage(w, h, QImage.Format_RGBA8888)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.scale(scale, scale)
    painter.translate(-src.left(), -src.top())
    was_selected = item.isSelected()
    if was_selected:
        item.setSelected(False)  # keep the selection marker out of the AP
    try:
        item.paint(painter, QStyleOptionGraphicsItem(), None)
    finally:
        painter.end()
        if was_selected:
            item.setSelected(True)

    stride = img.bytesPerLine()
    buf = bytes(img.constBits())[: stride * h]
    rgb = bytearray(w * h * 3)
    alpha = bytearray(w * h)
    for y in range(h):
        row = bytearray(buf[y * stride : y * stride + w * 4])
        alpha[y * w : (y + 1) * w] = row[3::4]
        del row[3::4]
        rgb[y * w * 3 : (y + 1) * w * 3] = row
    return bytes(rgb), bytes(alpha), w, h


def _set_rasterized_appearance(
    annot: fitz.Annot, item, dpi: int
) -> None:
    """Replace the annot's appearance stream with the rendered item.

    External viewers (Acrobat, Foxit) display whatever is in /AP/N, so
    they show the actual feature control frame / stamp instead of an
    empty rectangle. The image carries an /SMask so the page content
    stays visible around the glyphs. Annoter itself ignores the
    appearance and rebuilds the editable item from the JSON.
    """
    page = annot.parent
    doc = page.parent
    rgb, alpha, w, h = _rasterize_item_planes(item, dpi)

    smask_xref = doc.get_new_xref()
    doc.update_object(
        smask_xref,
        f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}"
        "/ColorSpace/DeviceGray/BitsPerComponent 8>>",
    )
    doc.update_stream(smask_xref, bytes(alpha))

    img_xref = doc.get_new_xref()
    doc.update_object(
        img_xref,
        f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}"
        f"/ColorSpace/DeviceRGB/BitsPerComponent 8"
        f"/SMask {smask_xref} 0 R>>",
    )
    doc.update_stream(img_xref, bytes(rgb))

    ap = doc.xref_get_key(annot.xref, "AP/N")
    if ap[0] != "xref":
        return
    ap_xref = int(ap[1].split()[0])
    rect = annot.rect
    w_pt, h_pt = rect.width, rect.height
    # /Rect is padded by MuPDF (border width); draw the image at the
    # exact frame rect so it lines up with the stored geometry. AP form
    # space has its origin at the rect's bottom-left with y up.
    exact = _rect_pt(_scene_rect(item), dpi)
    x = exact.x0 - rect.x0
    y = rect.y1 - exact.y1
    content = (
        f"q {exact.width:.4f} 0 0 {exact.height:.4f} "
        f"{x:.4f} {y:.4f} cm /AnnoterAP Do Q"
    ).encode()
    doc.update_stream(ap_xref, content)
    doc.xref_set_key(ap_xref, "BBox", f"[0 0 {w_pt:.4f} {h_pt:.4f}]")
    doc.xref_set_key(ap_xref, "Matrix", "[1 0 0 1 0 0]")
    doc.xref_set_key(
        ap_xref,
        "Resources",
        f"<</XObject<</AnnoterAP {img_xref} 0 R>>>>",
    )


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


def _polygon_has_cloud_border(annot: fitz.Annot) -> bool:
    """True if a Polygon annot carries a cloudy border effect (/BE /S /C).

    Used to map foreign polygons (no Annoter props) to the right item.
    """
    try:
        be = annot.parent.parent.xref_get_key(annot.xref, "BE")
    except Exception:
        return False
    return be[0] != "null" and "/C" in (be[1] or "")


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
    # Exact geometry written by us takes precedence over /Rect, which
    # MuPDF pads by the border width on Square/Circle annots.
    if "rect_pt" in props:
        try:
            x, y, w, h = (float(v) for v in props["rect_pt"])
            qrect = QRectF(
                _px(x, dpi), _px(y, dpi), _px(w, dpi), _px(h, dpi)
            )
        except (TypeError, ValueError):
            pass

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

    if subtype == "Text":
        pos = QPointF(qrect.x(), qrect.y())
        if "pos_pt" in props:
            try:
                px, py = (float(v) for v in props["pos_pt"])
                pos = QPointF(_px(px, dpi), _px(py, dpi))
            except (TypeError, ValueError):
                pass
        item = StickyNoteItem(pos, content)
        item.set_color(qcolor)
        item.set_stroke(width)
        _apply_props_to_item(item, props)
        return [item]

    if subtype == "Stamp":
        item = StampItem(
            QPointF(qrect.x(), qrect.y()),
            props.get("text", "") or content,
        )
        item.set_color(qcolor)
        item.set_stroke(width)
        _apply_props_to_item(item, props)
        return [item]

    if subtype == "PolyLine":
        verts = annot.vertices or []
        pts = [QPointF(_px(x, dpi), _px(y, dpi)) for x, y in verts]
        if len(pts) < 2:
            return []
        item = PolylineItem(pts)
        item.set_color(qcolor)
        item.set_stroke(width)
        _apply_props_to_item(item, props)
        return [item]

    if subtype == "Polygon":
        # Disambiguate our two Polygon-backed items. Explicit "poly" tag
        # wins; otherwise a cloudy border (or a stored rect_pt) means a
        # revision cloud, and a plain polygon maps to PolygonItem.
        kind = props.get("poly")
        is_cloud = (
            kind == "cloud"
            or (kind is None and "rect_pt" in props)
            or (kind is None and _polygon_has_cloud_border(annot))
        )
        if is_cloud:
            if "rect_pt" in props:
                item = CloudItem(
                    QRectF(0, 0, qrect.width(), qrect.height())
                )
                item.setPos(qrect.x(), qrect.y())
            else:
                verts = annot.vertices or []
                if not verts:
                    return []
                xs = [_px(x, dpi) for x, y in verts]
                ys = [_px(y, dpi) for x, y in verts]
                bx, by = min(xs), min(ys)
                item = CloudItem(
                    QRectF(0, 0, max(xs) - bx, max(ys) - by)
                )
                item.setPos(bx, by)
        else:
            verts = annot.vertices or []
            pts = [QPointF(_px(x, dpi), _px(y, dpi)) for x, y in verts]
            if len(pts) < 3:
                return []
            item = PolygonItem(pts)
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
        pos = QPointF(qrect.x(), qrect.y())
        if "pos_pt" in props:
            try:
                px, py = (float(v) for v in props["pos_pt"])
                pos = QPointF(_px(px, dpi), _px(py, dpi))
            except (TypeError, ValueError):
                pass
        if "callout_tip_pt" in props:
            item = CalloutItem(pos, text)
            try:
                tx, ty = (float(v) for v in props["callout_tip_pt"])
                item.set_tip(
                    QPointF(_px(tx, dpi) - pos.x(), _px(ty, dpi) - pos.y())
                )
            except (TypeError, ValueError):
                pass
        else:
            item = TextAnnotationItem(pos, text)
        item.set_color(qcolor)
        item.set_stroke(width)
        _apply_props_to_item(item, props)
        return [item]

    return []
