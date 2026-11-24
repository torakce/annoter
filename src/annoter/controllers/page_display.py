"""EngineDisplay: drives the native engine for the open document.

Owns the engine document of the open file and the `TiledPageItem` that
shows it. It builds the view request from `PdfView` (zoom, scroll,
viewport size, devicePixelRatio, rotation), asks for the previous and
next page as prefetch, polls with a `QTimer` that runs only while the
engine has work in progress, hands finished tiles to the item and
repaints only their area, forgets evicted ones, reloads after page
operations, and passes the grayscale flag and the hidden annotations
(option A of D3: every annotation Annoter draws as a Qt item).

The engine's threads never call back: everything here runs on the UI
thread, from Qt signals and timers.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QTimer

from annoter.config import BASE_RENDER_DPI
from annoter.engine import NativeDocument, NativeEngine
from annoter.views.pdf_view import PdfView
from annoter.views.tiled_page_item import TiledPageItem, page_box_150

POLL_INTERVAL_MS = 8       # while the engine works
POLL_BUDGET_S = 0.008      # time spent integrating tiles per tick
POLL_BATCH = 32


class EngineDisplay(QObject):
    """See module docstring. One instance per open document."""

    def __init__(self, engine: NativeEngine, view: PdfView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._view = view
        self._doc: NativeDocument | None = None
        self._item = TiledPageItem()
        self._page = -1
        self._rotation = 0
        self._request_pending = False

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._tick)
        # Many scroll / zoom signals arrive per frame: one request per
        # event-loop pass is enough.
        self._request_timer = QTimer(self)
        self._request_timer.setSingleShot(True)
        self._request_timer.setInterval(0)
        self._request_timer.timeout.connect(self._request)
        view.viewport().installEventFilter(self)

    # ------------------------------------------------------------------
    # document
    # ------------------------------------------------------------------
    def open(self, path: str | Path) -> None:
        """Open `path` in the engine (raises `EngineError`)."""
        self._doc = self._engine.open_file(path)

    @property
    def item(self) -> TiledPageItem:
        return self._item

    @property
    def document(self) -> NativeDocument | None:
        return self._doc

    def page_count(self) -> int:
        return self._doc.page_count if self._doc is not None else 0

    def reload_bytes(self, data: bytes) -> None:
        """The document changed in memory (insert / move / resize pages)."""
        if self._doc is None:
            return
        self._doc.reload_bytes(data)
        self._forget_all()

    def reload_file(self, path: str | Path) -> None:
        if self._doc is None:
            return
        self._doc.reload_file(path)
        self._forget_all()

    def close(self) -> None:
        self._poll_timer.stop()
        self._request_timer.stop()
        self._view.viewport().removeEventFilter(self)
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._item.clear_tiles()

    # ------------------------------------------------------------------
    # what is shown
    # ------------------------------------------------------------------
    def show_page(self, page: int, rotation: int) -> None:
        """Put `page` (view `rotation`) in the item; tiles follow."""
        if self._doc is None:
            return
        self._page = page
        self._rotation = rotation % 360
        box = page_box_150(self._doc.page_bounds(page), self._rotation)
        self._item.set_page(page, self._rotation, box)
        self.view_changed()

    def view_changed(self) -> None:
        """Zoom, scroll, viewport size or page changed: request soon."""
        if self._doc is None or self._page < 0:
            return
        self._request_pending = True
        self._request_timer.start()

    def set_grayscale(self, enabled: bool) -> None:
        if self._doc is None:
            return
        self._doc.set_grayscale(enabled)
        self._integrate_evictions()
        self.view_changed()

    def set_hidden_annots(self, page: int, xrefs: Iterable[int]) -> None:
        """Annotations of `page` Annoter draws itself: not rasterized."""
        if self._doc is None:
            return
        self._doc.set_hidden_annots(page, xrefs)
        self._integrate_evictions()
        if page == self._page:
            self.view_changed()

    def settled(self) -> bool:
        """The visible area is sharp and nothing is waiting to be
        integrated (prefetch may still run)."""
        return (
            self._doc is not None
            and not self._request_pending
            and self._doc.view_complete()
        )

    def stats(self) -> str:
        return self._doc.stats() if self._doc is not None else "no document"

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Resize:
            self.view_changed()
        return False

    def _request(self) -> None:
        self._request_pending = False
        if self._doc is None or self._page < 0:
            return
        view = self._view
        vp = view.viewport()
        dpr = vp.devicePixelRatioF() or 1.0
        visible = self._item.mapFromScene(view.mapToScene(vp.rect())).boundingRect()
        o = self._item.origin()
        k = 72.0 / BASE_RENDER_DPI
        rect_pt = (
            (visible.left() + o.x()) * k,
            (visible.top() + o.y()) * k,
            (visible.right() + o.x()) * k,
            (visible.bottom() + o.y()) * k,
        )
        n = self._doc.page_count
        prefetch = (
            self._page + 1 if self._page + 1 < n else -1,
            self._page - 1 if self._page > 0 else -1,
        )
        target = self._doc.request_view(
            self._page,
            view.zoom() * dpr,
            rect_pt,
            rotation=self._rotation,
            grid_base=dpr,
            exact=view.zoom_is_exact(),
            prefetch=prefetch,
        )
        self._item.set_target(target)
        if not self._poll_timer.isActive():
            self._poll_timer.start()
        self._tick()

    def _tick(self) -> None:
        if self._doc is None:
            self._poll_timer.stop()
            return
        t0 = time.perf_counter()
        dirty = QRectF()
        got = 0
        while True:
            tiles = self._doc.poll(POLL_BATCH)
            got += len(tiles)
            for tile in tiles:
                r = self._item.add_tile(tile)
                if r is not None:
                    dirty = dirty.united(r)
            if len(tiles) < POLL_BATCH or time.perf_counter() - t0 > POLL_BUDGET_S:
                break
        dirty = dirty.united(self._item.drop_tiles(self._doc.evicted()))
        if not dirty.isEmpty():
            self._item.update(dirty)
        if not got and not self._doc.busy():
            self._poll_timer.stop()

    def _integrate_evictions(self) -> None:
        if self._doc is None:
            return
        dirty = self._item.drop_tiles(self._doc.evicted())
        if not dirty.isEmpty():
            self._item.update(dirty)

    def _forget_all(self) -> None:
        if self._doc is not None:
            self._doc.evicted()  # everything handed over before is gone
        self._item.clear_tiles()
