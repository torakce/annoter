"""ctypes binding of `native/include/annoter_engine.h`.

Declares the C structures and function signatures, and wraps them in
`NativeEngine` (one per process: threads and budgets) and
`NativeDocument` (open / reload / close, page bounds, hidden annotations,
flags, view requests, tile polling with copy-then-release, evictions).

All calls happen on the Qt UI thread; ctypes releases the GIL while the
library runs. This module does not import Qt: tiles come back as raw
premultiplied BGRA bytes (opaque), which `QImage.Format_RGB32` reads as is
on every little-endian machine.
"""

from __future__ import annotations

import ctypes as C
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

API_VERSION = 1

ANN_OK = 0
ANN_ERR_ARG = -1
ANN_ERR_OPEN = -2
ANN_ERR_PASSWORD = -3
ANN_ERR_MEMORY = -4
ANN_ERR_PAGE = -5
ANN_FLAG_GRAYSCALE = 1

_POLL_BATCH = 64


class EngineError(RuntimeError):
    """The native engine refused a call or could not open a document."""


class PasswordError(EngineError):
    """The document needs a password and none (or a wrong one) was given."""


def _raise_for(status: int, what: str) -> None:
    if status == ANN_OK:
        return
    if status == ANN_ERR_PASSWORD:
        raise PasswordError(f"{what}: password required or wrong")
    names = {
        ANN_ERR_ARG: "invalid argument",
        ANN_ERR_OPEN: "cannot open the document",
        ANN_ERR_MEMORY: "out of memory",
        ANN_ERR_PAGE: "no such page",
    }
    raise EngineError(f"{what}: {names.get(status, f'error {status}')}")


# --------------------------------------------------------------------------
# C structures (mirrors of annoter_engine.h)
# --------------------------------------------------------------------------
class _Config(C.Structure):
    _fields_ = [
        ("render_threads", C.c_int),
        ("loader_threads", C.c_int),
        ("tile_budget", C.c_longlong),
        ("list_budget", C.c_longlong),
    ]


class _View(C.Structure):
    _fields_ = [
        ("page", C.c_int),
        ("rotation", C.c_int),
        ("scale", C.c_double),
        ("grid_base", C.c_double),
        ("exact", C.c_int),
        ("visible", C.c_float * 4),
        ("prefetch_pages", C.c_int * 2),
    ]


class _Target(C.Structure):
    _fields_ = [
        ("level", C.c_int),
        ("scale", C.c_double),
        ("box", C.c_int * 4),
        ("tile_size", C.c_int),
    ]


class _Tile(C.Structure):
    _fields_ = [
        ("id", C.c_longlong),
        ("page", C.c_int),
        ("level", C.c_int),
        ("rotation", C.c_int),
        ("tx", C.c_int),
        ("ty", C.c_int),
        ("epoch", C.c_uint),
        ("x0", C.c_int),
        ("y0", C.c_int),
        ("x1", C.c_int),
        ("y1", C.c_int),
        ("scale", C.c_double),
        ("stride", C.c_int),
        ("pixels", C.c_void_p),
    ]


def _declare(lib: C.CDLL) -> None:
    """Signatures of every exported function (a missing one raises)."""
    p, i = C.c_void_p, C.c_int
    table = {
        "ann_api_version": (i, []),
        "ann_engine_new": (p, [C.POINTER(_Config)]),
        "ann_engine_free": (None, [p]),
        "ann_open_file": (p, [p, C.c_char_p, C.c_char_p, C.POINTER(i)]),
        "ann_open_memory": (p, [p, C.c_char_p, C.c_size_t, C.c_char_p, C.POINTER(i)]),
        "ann_reload_file": (i, [p, C.c_char_p, C.c_char_p]),
        "ann_reload_memory": (i, [p, C.c_char_p, C.c_size_t, C.c_char_p]),
        "ann_close": (None, [p]),
        "ann_page_count": (i, [p]),
        "ann_page_bounds": (i, [p, i, C.c_float * 4]),
        "ann_set_hidden_annots": (i, [p, i, C.POINTER(i), i]),
        "ann_set_flags": (i, [p, i]),
        "ann_request_view": (i, [p, C.POINTER(_View), C.POINTER(_Target)]),
        "ann_poll": (i, [p, C.POINTER(_Tile), i]),
        "ann_release_tile": (None, [p, C.c_longlong]),
        "ann_poll_evicted": (i, [p, C.POINTER(C.c_longlong), i]),
        "ann_busy": (i, [p]),
        "ann_view_complete": (i, [p]),
        "ann_cancel": (None, [p]),
        "ann_stats": (None, [p, C.c_char_p, i]),
    }
    for name, (res, args) in table.items():
        fn = getattr(lib, name)
        fn.restype = res
        fn.argtypes = args


# --------------------------------------------------------------------------
# Python-side values
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Target:
    """Tile level the engine renders for a view request."""

    level: int
    scale: float  # device pixels per PDF point at that level
    box: tuple[int, int, int, int]  # page box at that level (tile grid origin)
    tile_size: int


@dataclass(frozen=True)
class Tile:
    """A finished tile; `pixels` is premultiplied BGRA, opaque, `stride`
    bytes per row, covering `rect` (pixels of the rotated page at `scale`)."""

    id: int
    page: int
    level: int
    rotation: int
    tx: int
    ty: int
    epoch: int
    rect: tuple[int, int, int, int]
    scale: float
    stride: int
    pixels: bytes

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


# --------------------------------------------------------------------------
# Engine and documents
# --------------------------------------------------------------------------
class NativeEngine:
    """The library's engine: render and loader threads, memory budgets.
    One per process (the library refuses a second one)."""

    def __init__(
        self,
        library: str | Path,
        render_threads: int = 0,
        loader_threads: int = 0,
        tile_budget: int = 0,
        list_budget: int = 0,
    ) -> None:
        self.path = Path(library)
        self._lib = C.CDLL(str(self.path))
        _declare(self._lib)
        version = self._lib.ann_api_version()
        if version != API_VERSION:
            raise EngineError(f"engine API version {version}, expected {API_VERSION}")
        cfg = _Config(render_threads, loader_threads, tile_budget, list_budget)
        self._handle = self._lib.ann_engine_new(C.byref(cfg))
        if not self._handle:
            raise EngineError("cannot start the engine")
        self._docs: list[NativeDocument] = []

    @property
    def lib(self) -> C.CDLL:
        return self._lib

    def open_file(self, path: str | Path, password: str | None = None) -> NativeDocument:
        """Open a PDF file (read into memory, never kept open)."""
        self._check()
        status = C.c_int(ANN_OK)
        handle = self._lib.ann_open_file(
            self._handle, str(path).encode("utf-8"), _pw(password), C.byref(status)
        )
        if not handle:
            _raise_for(status.value or ANN_ERR_OPEN, f"open {path}")
        return self._adopt(handle)

    def open_bytes(self, data: bytes, password: str | None = None) -> NativeDocument:
        """Open a PDF held in memory (the bytes are copied)."""
        self._check()
        status = C.c_int(ANN_OK)
        handle = self._lib.ann_open_memory(
            self._handle, data, len(data), _pw(password), C.byref(status)
        )
        if not handle:
            _raise_for(status.value or ANN_ERR_OPEN, "open from memory")
        return self._adopt(handle)

    def close(self) -> None:
        """Close every document, stop and join the threads."""
        if not self._handle:
            return
        for doc in list(self._docs):
            doc.close()
        self._lib.ann_engine_free(self._handle)
        self._handle = None

    @property
    def closed(self) -> bool:
        return not self._handle

    def _adopt(self, handle: int) -> NativeDocument:
        doc = NativeDocument(self, handle)
        self._docs.append(doc)
        return doc

    def _forget(self, doc: NativeDocument) -> None:
        if doc in self._docs:
            self._docs.remove(doc)

    def _check(self) -> None:
        if not self._handle:
            raise EngineError("engine closed")


def _pw(password: str | None) -> bytes | None:
    return password.encode("utf-8") if password is not None else None


class NativeDocument:
    """One document open in the engine. Not thread-safe: UI thread only."""

    def __init__(self, engine: NativeEngine, handle: int) -> None:
        self._engine = engine
        self._lib = engine.lib
        self._h = handle
        self._tiles = (_Tile * _POLL_BATCH)()
        self._ids = (C.c_longlong * 1024)()

    # -- lifetime --------------------------------------------------------
    def close(self) -> None:
        if self._h:
            self._lib.ann_close(self._h)
            self._h = None
            self._engine._forget(self)

    @property
    def closed(self) -> bool:
        return not self._h

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def reload_file(self, path: str | Path, password: str | None = None) -> None:
        """Re-read the document from `path` (after Save / Save As). Every
        tile handed over before is reported by `evicted()`; hidden
        annotations must be set again."""
        h = self._handle()
        _raise_for(self._lib.ann_reload_file(h, str(path).encode("utf-8"), _pw(password)), f"reload {path}")

    def reload_bytes(self, data: bytes, password: str | None = None) -> None:
        """Re-read the document from memory (after in-memory page
        operations, e.g. `fitz.Document.tobytes()`)."""
        h = self._handle()
        _raise_for(self._lib.ann_reload_memory(h, data, len(data), _pw(password)), "reload from memory")

    # -- pages ---------------------------------------------------------------
    @property
    def page_count(self) -> int:
        return self._lib.ann_page_count(self._handle())

    def page_bounds(self, page: int) -> tuple[float, float, float, float]:
        """Page rect in points, same frame as PyMuPDF's `page.rect`."""
        out = (C.c_float * 4)()
        _raise_for(self._lib.ann_page_bounds(self._handle(), page, out), f"page {page}")
        return (out[0], out[1], out[2], out[3])

    # -- what is drawn ---------------------------------------------------------
    def set_hidden_annots(self, page: int, xrefs: Iterable[int]) -> None:
        """Annotations (PDF object numbers) the engine must not draw on
        `page`, because Annoter draws them as Qt items."""
        values = sorted({int(x) for x in xrefs})
        arr = (C.c_int * max(1, len(values)))(*values)
        _raise_for(
            self._lib.ann_set_hidden_annots(self._handle(), page, arr, len(values)),
            f"hidden annotations of page {page}",
        )

    def set_grayscale(self, enabled: bool) -> None:
        flags = ANN_FLAG_GRAYSCALE if enabled else 0
        _raise_for(self._lib.ann_set_flags(self._handle(), flags), "flags")

    # -- view and tiles ---------------------------------------------------------
    def request_view(
        self,
        page: int,
        scale: float,
        visible: tuple[float, float, float, float],
        rotation: int = 0,
        grid_base: float = 1.0,
        exact: bool = False,
        prefetch: tuple[int, int] = (-1, -1),
    ) -> Target:
        """Tell the engine what is on screen: `scale` in device pixels per
        point, `visible` the viewport in points of the rotated page frame
        (may extend beyond the page), `grid_base` the device pixels per
        point at 100 % (the devicePixelRatio). Returns the level rendered."""
        view = _View(
            page,
            rotation,
            scale,
            grid_base,
            1 if exact else 0,
            (C.c_float * 4)(*visible),
            (C.c_int * 2)(*prefetch),
        )
        target = _Target()
        _raise_for(
            self._lib.ann_request_view(self._handle(), C.byref(view), C.byref(target)),
            f"view of page {page}",
        )
        return Target(target.level, target.scale, tuple(target.box), target.tile_size)

    def poll(self, max_tiles: int = _POLL_BATCH) -> list[Tile]:
        """Finished tiles, most urgent first. Their pixels are copied and the
        engine's buffers released at once."""
        h = self._handle()
        n = self._lib.ann_poll(h, self._tiles, min(max_tiles, _POLL_BATCH))
        out: list[Tile] = []
        for k in range(n):
            t = self._tiles[k]
            height = t.y1 - t.y0
            try:
                data = C.string_at(t.pixels, t.stride * height)
            finally:
                self._lib.ann_release_tile(h, t.id)
            out.append(
                Tile(
                    t.id, t.page, t.level, t.rotation, t.tx, t.ty, t.epoch,
                    (t.x0, t.y0, t.x1, t.y1), t.scale, t.stride, data,
                )
            )
        return out

    def evicted(self) -> list[int]:
        """Ids of tiles the engine dropped from its cache since the last
        call: their copies must be forgotten."""
        h = self._handle()
        out: list[int] = []
        while True:
            n = self._lib.ann_poll_evicted(h, self._ids, len(self._ids))
            out.extend(self._ids[k] for k in range(n))
            if n < len(self._ids):
                return out

    def busy(self) -> bool:
        """True while anything is pending, prefetch included."""
        return bool(self._lib.ann_busy(self._handle()))

    def view_complete(self) -> bool:
        """True when every visible tile of the last request is sharp."""
        return bool(self._lib.ann_view_complete(self._handle()))

    def cancel(self) -> None:
        """Drop queued work and abort renders; the cache is kept."""
        self._lib.ann_cancel(self._handle())

    def stats(self) -> str:
        buf = C.create_string_buffer(512)
        self._lib.ann_stats(self._handle(), buf, len(buf))
        return buf.value.decode("ascii", "replace")

    def _handle(self) -> int:
        if not self._h:
            raise EngineError("document closed")
        return self._h
