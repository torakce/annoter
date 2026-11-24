"""Tests of TiledPageItem and the native-engine display (step 4 of PLAN.md).

Geometry identical to the 150-DPI pixmap item for several page sizes and
rotations, annotation children and their coordinates, undo, save, tile
placement against PyMuPDF, stretched fallback while tiles arrive, the
notch grid of the zoom, grayscale, page operations, and the PyMuPDF
fallback. Engine-dependent tests are skipped when the library is not
built.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QStyleOptionGraphicsItem,
)

from annoter.config import BASE_RENDER_DPI  # noqa: E402
from annoter.engine import Target, Tile, load_engine  # noqa: E402
from annoter.model.document import PdfDocument  # noqa: E402
from annoter.services.pdf_render import PageRenderer  # noqa: E402
from annoter.views.items import RectangleItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.pdf_view import PdfView  # noqa: E402
from annoter.views.tiled_page_item import PREVIEW_LEVEL, TiledPageItem, page_box_150  # noqa: E402

HAS_ENGINE = os.environ.get("ANNOTER_RENDERER") != "pymupdf" and load_engine() is not None
needs_engine = pytest.mark.skipif(not HAS_ENGINE, reason="native engine not available")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> Path:
    """Odd sizes on purpose: A4, a landscape page with fractional points,
    a page with /Rotate 90 and one whose MediaBox is not at the origin."""
    path = tmp_path_factory.mktemp("tiled") / "pages.pdf"
    doc = fitz.open()
    for w, h in ((595, 842), (1191.37, 842.61)):
        p = doc.new_page(width=w, height=h)
        p.draw_rect(fitz.Rect(20, 20, w - 20, h - 20), color=(0, 0, 0), width=1)
        p.draw_circle((w / 2, h / 2), min(w, h) / 4, color=(0.7, 0, 0), fill=(0.8, 0.9, 1), width=2)
        p.insert_text((40, 60), f"page {doc.page_count}", fontsize=24)
    p = doc.new_page(width=600, height=400)
    p.draw_line((10, 10), (590, 390), color=(0, 0, 1), width=3)
    p.set_rotation(90)
    p = doc.new_page(width=500, height=300)
    p.set_mediabox(fitz.Rect(50, 40, 550, 340))
    p.draw_rect(fitz.Rect(10, 10, 300, 200), color=(0, 0.5, 0), fill=(0.9, 1, 0.9))
    doc.save(str(path))
    doc.close()
    return path


def _pump_until(qapp, cond, timeout: float = 60.0) -> None:
    t0 = time.monotonic()
    while not cond():
        qapp.processEvents()
        assert time.monotonic() - t0 < timeout, "timed out"
        time.sleep(0.002)


def _settle(qapp, win: MainWindow) -> None:
    _pump_until(qapp, lambda: win._display is not None and win._display.settled())
    qapp.processEvents()


def _window(qapp, pdf: Path, monkeypatch=None, renderer: str | None = None) -> MainWindow:
    if monkeypatch is not None and renderer is not None:
        monkeypatch.setenv("ANNOTER_RENDERER", renderer)
    win = MainWindow()
    win.resize(1200, 900)
    win.show()
    win.open_path(pdf)
    qapp.processEvents()
    return win


def _close(win: MainWindow) -> None:
    win._on_close()
    win.close()


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_page_box_matches_the_pixmap_item(qapp, pdf, rotation) -> None:
    doc = PdfDocument(pdf)
    try:
        renderer = PageRenderer(doc, BASE_RENDER_DPI, 1)
        for i in range(doc.page_count):
            pm = renderer.render(i, rotation)
            x0, y0, x1, y1 = page_box_150(tuple(doc.page(i).rect), rotation)
            assert (x1 - x0, y1 - y0) == (pm.width(), pm.height()), (i, rotation)
    finally:
        doc.close()


@needs_engine
@pytest.mark.parametrize("rotation", [0, 90])
def test_page_item_geometry_is_the_same_in_both_renderers(qapp, pdf, monkeypatch, rotation) -> None:
    rects = {}
    for renderer in ("pymupdf", "engine"):
        win = _window(qapp, pdf, monkeypatch, renderer)
        try:
            if rotation:
                win._rotate(rotation)
            got = []
            for i in range(4):
                win._show_page(i)
                got.append(QRectF(win._scene.page_item().boundingRect()))
                assert win._scene.sceneRect() == got[-1]
            rects[renderer] = got
            kind = type(win._scene.page_item())
            assert kind is (QGraphicsPixmapItem if renderer == "pymupdf" else TiledPageItem)
        finally:
            _close(win)
    assert rects["engine"] == rects["pymupdf"]


# --------------------------------------------------------------------------
# annotations, undo, save
# --------------------------------------------------------------------------
@needs_engine
def test_annotations_stay_children_across_pages_undo_and_save(qapp, pdf, tmp_path) -> None:
    path = tmp_path / "work.pdf"
    path.write_bytes(pdf.read_bytes())
    win = _window(qapp, path)
    try:
        page = win._scene.page_item()
        assert isinstance(page, TiledPageItem)
        item = RectangleItem(QRectF(0, 0, 120, 60))
        item.setPos(200, 150)
        win._scene.push_add(item)
        assert item.parentItem() is page
        win._goto_next_page()
        assert item.scene() is None
        win._goto_prev_page()
        assert item.parentItem() is win._scene.page_item() is page
        assert item.pos() == QPointF(200, 150)
        stack = win._undo_group.activeStack()
        stack.undo()
        assert item.scene() is None
        stack.redo()
        assert item.parentItem() is page
        assert win._save_to(path)
    finally:
        _close(win)
    with fitz.open(str(path)) as doc:
        annots = list(doc[0].annots())
        assert len(annots) == 1
        props = json.loads(annots[0].info["subject"])
        k = 72.0 / BASE_RENDER_DPI
        assert props["rect_pt"] == pytest.approx([200 * k, 150 * k, 120 * k, 60 * k], abs=1e-3)


# --------------------------------------------------------------------------
# tile placement: the scene shows the page exactly where PyMuPDF draws it
# --------------------------------------------------------------------------
def _scene_image(win: MainWindow, scale_px_per_pt: float) -> QImage:
    page = win._scene.page_item()
    src = page.sceneBoundingRect()
    k = scale_px_per_pt * 72.0 / BASE_RENDER_DPI
    img = QImage(round(src.width() * k), round(src.height() * k), QImage.Format_RGB32)
    img.fill(QColor("magenta"))
    p = QPainter(img)
    win._scene.render(p, QRectF(0, 0, src.width() * k, src.height() * k), src)
    p.end()
    return img


def _mean_diff(img: QImage, pix: fitz.Pixmap) -> float:
    assert (img.width(), img.height()) == (pix.width, pix.height)
    rgb = img.convertToFormat(QImage.Format_RGB888)
    total = n = 0
    s = pix.samples
    for y in range(0, pix.height, 3):
        row = bytes(rgb.constScanLine(y))[: pix.width * 3]
        ref = s[y * pix.stride: y * pix.stride + pix.width * 3]
        total += sum(abs(a - b) for a, b in zip(row, ref))
        n += len(ref)
    return total / n


@needs_engine
@pytest.mark.parametrize("page,rotation", [(0, 0), (1, 0), (2, 0), (3, 0), (1, 90), (1, 270)])
def test_tiles_land_where_pymupdf_draws(qapp, pdf, page, rotation) -> None:
    win = _window(qapp, pdf)
    before = fitz.TOOLS.show_aa_level()
    try:
        if rotation:
            win._rotate(rotation)
        win._show_page(page)
        win._view.set_zoom(1.0)  # 1 device pixel per point here
        _settle(qapp, win)
        img = _scene_image(win, 1.0)
        fitz.TOOLS.set_aa_level(6)
        fitz.mupdf.fz_set_text_aa_level(8)
        with fitz.open(str(pdf)) as ref:
            pix = ref[page].get_pixmap(matrix=fitz.Matrix(1, 1).prerotate(rotation), alpha=False)
        assert _mean_diff(img, pix) < 2.0
    finally:
        fitz.TOOLS.set_aa_level(before["graphics"])
        fitz.mupdf.fz_set_text_aa_level(before["text"])
        _close(win)


# --------------------------------------------------------------------------
# painting from synthetic tiles: stretched fallback, then sharp tiles
# --------------------------------------------------------------------------
def _solid_tile(tid: int, level: int, scale: float, rect, rgb: tuple[int, int, int], tx: int = 0) -> Tile:
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    px = bytes((rgb[2], rgb[1], rgb[0], 255)) * (w * h)
    return Tile(tid, 0, level, 0, tx, 0, 1, rect, scale, w * 4, px)


def _paint(item: TiledPageItem, size: tuple[int, int]) -> QImage:
    img = QImage(size[0], size[1], QImage.Format_RGB32)
    img.fill(QColor("magenta"))
    p = QPainter(img)
    opt = QStyleOptionGraphicsItem()
    opt.exposedRect = item.boundingRect()
    item.paint(p, opt, None)
    p.end()
    return img


def test_fallback_is_stretched_until_the_sharp_tiles_arrive(qapp) -> None:
    item = TiledPageItem()
    scene = QGraphicsScene()
    scene.addItem(item)
    # A 300 x 200 pt page: 625 x 417 item units at 150 DPI.
    box = page_box_150((0, 0, 300, 200), 0)
    item.set_page(0, 0, box)
    k = BASE_RENDER_DPI / 72.0
    size = (box[2], box[3])
    # Nothing yet: white page.
    assert QColor(_paint(item, size).pixel(10, 10)) == QColor("white")
    # A low-resolution preview (whole page, 0.5 px/pt), red.
    item.add_tile(_solid_tile(1, PREVIEW_LEVEL, 0.5, (0, 0, 150, 100), (255, 0, 0)))
    # Target: level 0 at k px/pt (exactly the item's own scale), not here yet.
    item.set_target(Target(0, k, box, 512))
    img = _paint(item, size)
    assert QColor(img.pixel(10, 10)) == QColor("red")
    assert QColor(img.pixel(size[0] - 5, size[1] - 5)) == QColor("red")
    # One sharp tile (top-left 512 x 417), blue, drawn over the stretch.
    item.add_tile(_solid_tile(2, 0, k, (0, 0, 512, box[3]), (0, 0, 255)))
    img = _paint(item, size)
    assert QColor(img.pixel(10, 10)) == QColor("blue")
    assert QColor(img.pixel(size[0] - 5, 10)) == QColor("red")  # still stretched there
    assert not item.target_complete()
    # The last sharp tile completes the page: the fallback is no longer drawn.
    item.add_tile(_solid_tile(3, 0, k, (512, 0, box[2], box[3]), (0, 255, 0), tx=1))
    assert item.target_complete()
    img = _paint(item, size)
    assert QColor(img.pixel(size[0] - 5, 10)) == QColor("lime")
    # Evicted tiles go away.
    dirty = item.drop_tiles([2, 3])
    assert not dirty.isEmpty()
    assert QColor(_paint(item, size).pixel(10, 10)) == QColor("red")


# --------------------------------------------------------------------------
# zoom on the notch grid (D4)
# --------------------------------------------------------------------------
def test_zoom_notches_follow_the_grid(qapp) -> None:
    view = PdfView()
    view.set_zoom(1.0)
    view.zoom_in()
    assert view.zoom() == pytest.approx(2 ** (1 / 3))
    assert not view.zoom_is_exact()
    view.zoom_in()
    view.zoom_in()
    assert view.zoom() == pytest.approx(2.0)
    view.zoom_out()
    assert view.zoom() == pytest.approx(2 ** (2 / 3))
    # From an exact zoom between two notches, in then out comes back to it.
    view.set_zoom(0.37)
    exact = view.zoom()
    assert view.zoom_is_exact()
    assert exact == pytest.approx(0.37, rel=1e-3)
    view.zoom_in()
    assert view.zoom() == pytest.approx(2 ** (math.floor(3 * math.log2(0.37)) / 3 + 1 / 3))
    view.zoom_out()
    assert view.zoom() == exact and view.zoom_is_exact()
    view.zoom_out()
    view.zoom_in()
    assert view.zoom() == exact
    # Every zoom is a power of 2^(1/768), the engine's level grid.
    for z in (0.1234, 1.7, 3.0, 15.0):
        view.set_zoom(z)
        n = math.log2(view.zoom()) * 768
        assert n == pytest.approx(round(n), abs=1e-6)


# --------------------------------------------------------------------------
# grayscale, page operations, fallback
# --------------------------------------------------------------------------
@needs_engine
def test_grayscale_with_the_engine(qapp, pdf) -> None:
    win = _window(qapp, pdf)
    try:
        win._view.set_zoom(1.0)
        _settle(qapp, win)
        win.act_grayscale.setChecked(True)
        _settle(qapp, win)
        img = _scene_image(win, 1.0)
        for x, y in ((297, 421), (150, 300), (400, 600)):  # inside the coloured disc
            c = QColor(img.pixel(x, y))
            assert c.red() == c.green() == c.blue(), (x, y, c.name())
        win.act_grayscale.setChecked(False)
    finally:
        _close(win)


@needs_engine
def test_page_operation_reloads_the_engine(qapp, pdf, tmp_path) -> None:
    path = tmp_path / "ops.pdf"
    path.write_bytes(pdf.read_bytes())
    win = _window(qapp, path)
    try:
        _settle(qapp, win)
        win._stash_current_page_items()
        win._doc.raw.move_page(1, 0)
        win._refresh_after_structure_change(0)
        assert win._display.page_count() == 4
        _settle(qapp, win)
        # Page 0 is now the landscape page.
        r = win._scene.page_item().boundingRect()
        assert r.width() > r.height()
    finally:
        _close(win)


def test_fallback_to_pymupdf(qapp, pdf, monkeypatch) -> None:
    win = _window(qapp, pdf, monkeypatch, "pymupdf")
    try:
        assert win._display is None
        assert isinstance(win._scene.page_item(), QGraphicsPixmapItem)
    finally:
        _close(win)


@needs_engine
def test_the_view_is_requested_again_on_zoom_scroll_and_resize(qapp, pdf) -> None:
    win = _window(qapp, pdf)
    try:
        _settle(qapp, win)
        first = win._display.item.target()
        win._view.set_zoom(3.0)
        _settle(qapp, win)
        assert win._display.item.target().scale > first.scale
        win._view.horizontalScrollBar().setValue(win._view.horizontalScrollBar().maximum())
        _settle(qapp, win)
        win.resize(700, 500)
        _settle(qapp, win)
        assert win._display.item.target_complete(
            win._display.item.mapFromScene(
                win._view.mapToScene(win._view.viewport().rect())
            ).boundingRect()
        )
    finally:
        _close(win)


def test_tiled_item_takes_no_mouse_of_its_own(qapp) -> None:
    item = TiledPageItem()
    assert not (item.flags() & QGraphicsItem.ItemIsSelectable)
    assert not (item.flags() & QGraphicsItem.ItemIsMovable)
    assert item.acceptedMouseButtons() == QGraphicsPixmapItem().acceptedMouseButtons()
