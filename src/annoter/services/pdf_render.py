"""PageRenderer: rasterizes PDF pages into QPixmaps with an LRU cache.

PDFs are vectorial, but Qt displays bitmaps; this module is responsible
for the rasterization step. Cache size is `config.PIXMAP_CACHE_PAGES`.
"""

from __future__ import annotations

from collections import OrderedDict

import fitz
from PySide6.QtGui import QImage, QPixmap

from annoter.model.document import PdfDocument


class PageRenderer:
    """Renders pages on demand and caches the most recent results.

    Cache key is (page_index, dpi, rotation). Changing the DPI evicts
    everything (mixed-DPI cache would explode in memory on A0 plans).
    """

    def __init__(self, document: PdfDocument, dpi: int, cache_size: int) -> None:
        self._doc = document
        self._dpi = dpi
        self._cache_size = cache_size
        self._cache: OrderedDict[tuple[int, int, int], QPixmap] = OrderedDict()

    @property
    def dpi(self) -> int:
        return self._dpi

    def set_dpi(self, dpi: int) -> None:
        if dpi != self._dpi:
            self._dpi = dpi
            self._cache.clear()

    def render(
        self, page_index: int, rotation: int = 0, scale: float = 1.0
    ) -> QPixmap:
        """Rasterize a page at `self._dpi * scale`.

        `scale > 1.0` produces a supersampled pixmap whose
        devicePixelRatio is set so its *logical* size stays the same as
        the base render. Scene geometry (and child annotation items)
        are therefore unaffected by a high-DPI re-render; only the
        pixel density changes.
        """
        rotation = rotation % 360
        key = (page_index, round(scale * 100), rotation)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        page = self._doc.page(page_index)
        zoom = self._dpi * scale / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        if rotation:
            matrix = matrix.prerotate(rotation)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        # The QImage must own its pixel data: PyMuPDF will free `pix` when GC'd.
        image = QImage(
            pix.samples,
            pix.width,
            pix.height,
            pix.stride,
            QImage.Format_RGB888,
        ).copy()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(scale)

        self._cache[key] = pixmap
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return pixmap

    def clear_cache(self) -> None:
        self._cache.clear()
