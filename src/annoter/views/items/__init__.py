"""Graphics items: the visual representation of each annotation type.

All items are children of the page's QGraphicsPixmapItem so coordinates
are page-local and page transforms cascade automatically.
"""

from annoter.views.items.base import AnnotationItem
from annoter.views.items.callout import CalloutItem
from annoter.views.items.freehand import FreehandItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.note import StickyNoteItem
from annoter.views.items.poly import PolygonItem, PolylineItem
from annoter.views.items.shapes import CloudItem, EllipseItem, RectangleItem
from annoter.views.items.text import TextAnnotationItem

__all__ = [
    "AnnotationItem",
    "ArrowItem",
    "CalloutItem",
    "CloudItem",
    "EllipseItem",
    "FreehandItem",
    "GdtAnnotationItem",
    "LineItem",
    "PolygonItem",
    "PolylineItem",
    "RectangleItem",
    "StickyNoteItem",
    "TextAnnotationItem",
]
