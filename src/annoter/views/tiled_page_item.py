"""TiledPageItem: the page shown as tiles from the native engine.

Replaces the page's `QGraphicsPixmapItem` when the engine is active and
is reused across pages the same way. Its `boundingRect()` is exactly the
logical rect the 150-DPI pixmap had (rotated page box, rounded like
`get_pixmap`), so annotations stay its children in page-local
coordinates and the zoom convention, undo history and save path are
unchanged.

`paint()` draws, for the exposed area: the white page, then -- only where
the tiles of the target level are incomplete -- the low-resolution
preview and the tiles of other levels, stretched (furthest level first,
closest on top), then the sharp tiles of the target level aligned on
device pixels. The item keeps a copy (`QPixmap`) of every tile the engine
handed over, for every page, until the engine reports it evicted; the
engine's budget therefore bounds this memory too.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from annoter.config import BASE_RENDER_DPI
from annoter.engine import Target, Tile

PREVIEW_LEVEL = -(2**31)  # the engine's whole-page low-resolution tile


def page_box_150(rect_pt: tuple[float, float, float, float], rotation: int) -> tuple[int, int, int, int]:
    """Pixel box of a page rendered at BASE_RENDER_DPI with the view
    rotation, rounded exactly like `page.get_pixmap()`: the logical rect of
    the page item, whichever renderer draws it."""
    z = BASE_RENDER_DPI / 72.0
    r = (fitz.Rect(rect_pt) * fitz.Matrix(z, z).prerotate(rotation)).irect
    return (r.x0, r.y0, r.x1, r.y1)


@dataclass
class _Entry:
    page: int
    level: int
    tx: int
    ty: int
    rotation: int
    scale: float
    rect_px: tuple[int, int, int, int]
    pixmap: QPixmap


class TiledPageItem(QGraphicsItem):
    """Page item drawn from engine tiles (see module docstring)."""

    def __init__(self, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)
        self._rect = QRectF()
        self._origin = QPointF()  # top-left of the 150-DPI box (rotated pixels)
        self._page = -1
        self._rotation = 0
        self._target: Target | None = None
        self._tiles: dict[int, _Entry] = {}  # id -> tile
        self._by_page: dict[int, dict[tuple[int, int, int], int]] = {}  # page -> (level, tx, ty) -> id
        self._k = BASE_RENDER_DPI / 72.0  # item units per point

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    def boundingRect(self) -> QRectF:  # noqa: N802 (Qt override)
        return QRectF(self._rect)

    def page(self) -> int:
        return self._page

    def rotation_degrees(self) -> int:
        return self._rotation

    def origin(self) -> QPointF:
        """Top-left of the page box at 150 DPI, in rotated pixels: item
        coordinate (0, 0)."""
        return QPointF(self._origin)

    def set_page(self, page: int, rotation: int, box150: tuple[int, int, int, int]) -> None:
        """Show `page` with the view `rotation`; `box150` is its
        `page_box_150()`. Tiles already received for that page appear at
        once."""
        rect = QRectF(0, 0, box150[2] - box150[0], box150[3] - box150[1])
        if rect != self._rect:
            self.prepareGeometryChange()
            self._rect = rect
        self._origin = QPointF(box150[0], box150[1])
        if (page, rotation) != (self._page, self._rotation):
            self._target = None
        self._page = page
        self._rotation = rotation
        self.update()

    def set_target(self, target: Target) -> None:
        """Level the engine renders for the current view."""
        if target != self._target:
            self._target = target
            self.update()

    def target(self) -> Target | None:
        return self._target

    def item_rect(self, rect_px: tuple[int, int, int, int], scale: float) -> QRectF:
        """Item rect covered by pixels `rect_px` of a level at `scale`."""
        f = self._k / scale
        return QRectF(
            rect_px[0] * f - self._origin.x(),
            rect_px[1] * f - self._origin.y(),
            (rect_px[2] - rect_px[0]) * f,
            (rect_px[3] - rect_px[1]) * f,
        )

    # ------------------------------------------------------------------
    # tiles
    # ------------------------------------------------------------------
    def add_tile(self, tile: Tile) -> QRectF | None:
        """Keep a copy of `tile`; returns the item rect to repaint when it
        belongs to the page on screen."""
        image = QImage(tile.pixels, tile.width, tile.height, tile.stride, QImage.Format_RGB32)
        pixmap = QPixmap.fromImage(image)  # copies: `tile.pixels` can go
        old = self._by_page.setdefault(tile.page, {}).get((tile.level, tile.tx, tile.ty))
        if old is not None:
            self._tiles.pop(old, None)
        self._tiles[tile.id] = _Entry(
            tile.page, tile.level, tile.tx, tile.ty, tile.rotation, tile.scale, tile.rect, pixmap
        )
        self._by_page[tile.page][(tile.level, tile.tx, tile.ty)] = tile.id
        if tile.page == self._page and tile.rotation == self._rotation:
            return self.item_rect(tile.rect, tile.scale)
        return None

    def drop_tiles(self, ids: list[int]) -> QRectF:
        """Forget evicted tiles; returns the item area to repaint."""
        dirty = QRectF()
        for i in ids:
            e = self._tiles.pop(i, None)
            if e is None:
                continue
            keys = self._by_page.get(e.page)
            if keys is not None and keys.get((e.level, e.tx, e.ty)) == i:
                del keys[(e.level, e.tx, e.ty)]
            if e.page == self._page and e.rotation == self._rotation:
                dirty = dirty.united(self.item_rect(e.rect_px, e.scale))
        return dirty

    def clear_tiles(self) -> None:
        self._tiles.clear()
        self._by_page.clear()
        self._target = None
        self.update()

    def tile_count(self) -> int:
        return len(self._tiles)

    def target_complete(self, area: QRectF | None = None) -> bool:
        """Every target-level tile covering `area` (default: the whole
        page) is here."""
        t = self._target
        if t is None:
            return False
        area = self._rect if area is None else area.intersected(self._rect)
        if area.isEmpty():
            return True
        keys = self._by_page.get(self._page, {})
        for tx, ty in self._tiles_over(area, t):
            i = keys.get((t.level, tx, ty))
            if i is None or self._tiles[i].rotation != self._rotation:
                return False
        return True

    def _tiles_over(self, area: QRectF, t: Target) -> list[tuple[int, int]]:
        """Indices of the target-level tiles intersecting item `area`."""
        f = t.scale / self._k  # level pixels per item unit
        bx0, by0, bx1, by1 = t.box
        size = t.tile_size
        x0 = max(bx0, (area.left() + self._origin.x()) * f)
        y0 = max(by0, (area.top() + self._origin.y()) * f)
        x1 = min(bx1, (area.right() + self._origin.x()) * f)
        y1 = min(by1, (area.bottom() + self._origin.y()) * f)
        if x1 <= x0 or y1 <= y0:
            return []
        nx, ny = (bx1 - bx0 - 1) // size, (by1 - by0 - 1) // size
        tx0 = min(nx, max(0, int((x0 - bx0) // size)))
        tx1 = min(nx, max(0, int((x1 - bx0 - 1e-3) // size)))
        ty0 = min(ny, max(0, int((y0 - by0) // size)))
        ty1 = min(ny, max(0, int((y1 - by0 - 1e-3) // size)))
        return [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------
    def paint(  # noqa: D401 (Qt override)
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        exposed = option.exposedRect.intersected(self._rect)
        if exposed.isEmpty():
            return
        painter.fillRect(exposed, Qt.white)
        keys = self._by_page.get(self._page)
        if not keys:
            return
        entries = [self._tiles[i] for i in keys.values() if self._tiles[i].rotation == self._rotation]
        t = self._target
        level = t.level if t is not None else None
        if t is None or not self.target_complete(exposed):
            # Stretch what other levels already have, furthest first.
            others = [e for e in entries if e.level != level]
            ref = level if level is not None else 0
            others.sort(key=lambda e: -abs(e.level - ref) if e.level != PREVIEW_LEVEL else -(2**40))
            painter.save()
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            for e in others:
                r = self.item_rect(e.rect_px, e.scale)
                if r.intersects(exposed):
                    painter.drawPixmap(r, e.pixmap, QRectF(e.pixmap.rect()))
            painter.restore()
        if level is None:
            return
        self._paint_sharp(painter, exposed, [e for e in entries if e.level == level])

    def _paint_sharp(self, painter: QPainter, exposed: QRectF, entries: list[_Entry]) -> None:
        """Target-level tiles, one tile pixel per device pixel when the
        view shows that very scale (the usual case: the zoom is always on
        the engine's level grid)."""
        world = painter.worldTransform()
        dpr = painter.device().devicePixelRatioF() if painter.device() is not None else 1.0
        plain = world.m12() == 0 and world.m21() == 0 and world.m11() > 0 and world.m22() > 0 and world.isAffine()
        painter.save()
        if plain and entries:
            device_per_item = world.m11() * dpr
            level_per_item = entries[0].scale / self._k
            aligned = abs(device_per_item / level_per_item - 1.0) < 1e-4
        else:
            aligned = False
        if aligned:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.setWorldTransform(QTransform())
            for e in entries:
                r = self.item_rect(e.rect_px, e.scale)
                if not r.intersects(exposed):
                    continue
                p = world.map(r.topLeft())
                x = round(p.x() * dpr) / dpr
                y = round(p.y() * dpr) / dpr
                w = (e.rect_px[2] - e.rect_px[0]) / dpr
                h = (e.rect_px[3] - e.rect_px[1]) / dpr
                painter.drawPixmap(QRectF(x, y, w, h), e.pixmap, QRectF(e.pixmap.rect()))
        else:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            for e in entries:
                r = self.item_rect(e.rect_px, e.scale)
                if r.intersects(exposed):
                    painter.drawPixmap(r, e.pixmap, QRectF(e.pixmap.rect()))
        painter.restore()
