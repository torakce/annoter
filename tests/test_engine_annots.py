"""Tests of the annotation rule with the native engine (step 5 of PLAN.md).

D3, option A: the annotations Annoter draws as Qt items are never
rasterized by the engine, so there is no duplicate and no ghost when one
is moved or deleted; the subtypes Annoter does not map (Highlight,
Underline, form fields...) are still drawn by the engine; and the screen
differs from v0.3.0's (PyMuPDF path, every annotation baked in the page)
only where managed annotations are. Skipped without the engine library.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.config import BASE_RENDER_DPI  # noqa: E402
from annoter.engine import load_engine  # noqa: E402
from annoter.services.pdf_export import (  # noqa: E402
    managed_annot_xrefs,
    read_annotations,
    write_annotations,
)
from annoter.views.items import RectangleItem  # noqa: E402
from annoter.views.items.stamp import StampItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402

HAS_ENGINE = os.environ.get("ANNOTER_RENDERER") != "pymupdf" and load_engine() is not None
pytestmark = pytest.mark.skipif(not HAS_ENGINE, reason="native engine not available")

K = BASE_RENDER_DPI / 72.0  # scene units per point


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def annotated(qapp, tmp_path: Path) -> Path:
    """Page 0: an Annoter rectangle (owned), a foreign square, a foreign
    stamp and a highlight. Page 1: a foreign square only."""
    path = tmp_path / "annotated.pdf"
    doc = fitz.open()
    page = doc.new_page(width=800, height=600)
    page.insert_text((420, 420), "highlighted words", fontsize=24)
    doc.new_page(width=800, height=600)
    own = RectangleItem(QRectF(0, 0, 150 * K, 100 * K))
    own.setPos(50 * K, 50 * K)
    own.set_color(QColor("#1E88E5"))
    own.set_stroke(4.0)
    write_annotations(doc, {0: [own]}, BASE_RENDER_DPI)
    page = doc[0]
    sq = page.add_rect_annot(fitz.Rect(300, 50, 450, 150))
    sq.set_colors(stroke=(1, 0, 0))
    sq.set_border(width=4)
    sq.update()
    st = page.add_stamp_annot(fitz.Rect(500, 50, 780, 170), stamp=0)
    st.update()
    hl = page.add_highlight_annot(fitz.Rect(416, 398, 640, 428))
    hl.update()
    sq2 = doc[1].add_rect_annot(fitz.Rect(100, 100, 300, 200))
    sq2.set_colors(stroke=(0, 0.6, 0))
    sq2.set_border(width=4)
    sq2.update()
    doc.save(str(path))
    doc.close()
    return path


def _window(qapp, path: Path, renderer: str, monkeypatch) -> MainWindow:
    monkeypatch.setenv("ANNOTER_RENDERER", renderer)
    win = MainWindow()
    win.resize(1100, 900)
    win.show()
    win.open_path(path)
    win._view.set_zoom(1.0)
    _settle(qapp, win)
    return win


def _settle(qapp, win: MainWindow, timeout: float = 60.0) -> None:
    t0 = time.monotonic()
    while win._display is not None and not win._display.settled():
        qapp.processEvents()
        assert time.monotonic() - t0 < timeout
        time.sleep(0.002)
    for _ in range(3):
        qapp.processEvents()


def _render(win: MainWindow) -> QImage:
    """The page and its annotations at 1 pixel per point."""
    src = win._scene.page_item().sceneBoundingRect()
    s = 1 / K
    img = QImage(round(src.width() * s), round(src.height() * s), QImage.Format_RGB32)
    img.fill(QColor("magenta"))
    p = QPainter(img)
    win._scene.render(p, QRectF(0, 0, src.width() * s, src.height() * s), src)
    p.end()
    return img


def _ink(img: QImage, rect: tuple[int, int, int, int]) -> int:
    """Non-white pixels inside `rect` (points == pixels here)."""
    n = 0
    for y in range(rect[1], rect[3]):
        for x in range(rect[0], rect[2]):
            c = QColor(img.pixel(x, y))
            if min(c.red(), c.green(), c.blue()) < 200:
                n += 1
    return n


def _close(win: MainWindow) -> None:
    win._on_close()
    win.close()


# --------------------------------------------------------------------------
def test_managed_xrefs_are_the_annotations_turned_into_items(annotated) -> None:
    with fitz.open(str(annotated)) as doc:
        kinds = {a.xref: a.type[1] for p in doc for a in p.annots()}
        managed = managed_annot_xrefs(doc, BASE_RENDER_DPI)
        also: dict[int, list[int]] = {}
        read_annotations(doc, BASE_RENDER_DPI, also)
    assert also == managed
    assert sorted(kinds[x] for x in managed[0]) == ["Square", "Square", "Stamp"]
    assert [kinds[x] for x in managed[1]] == ["Square"]
    assert all(kinds[x] != "Highlight" for xs in managed.values() for x in xs)


def test_no_ghost_after_moving_or_deleting(qapp, annotated, monkeypatch) -> None:
    win = _window(qapp, annotated, "engine", monkeypatch)
    try:
        img = _render(win)
        assert _ink(img, (50, 50, 202, 152)) > 0  # Annoter's rectangle
        assert _ink(img, (300, 50, 452, 152)) > 0  # foreign square
        items = win._scene.page_item().childItems()
        own = next(i for i in items if isinstance(i, RectangleItem) and i.pos().x() < 100 * K)
        foreign = next(i for i in items if isinstance(i, RectangleItem) and i.pos().x() > 250 * K)
        own.setPos(own.pos() + QPointF(0, 300 * K))
        win._scene.removeItem(foreign)
        qapp.processEvents()
        img = _render(win)
        assert _ink(img, (48, 48, 204, 154)) == 0  # nothing left where it was
        assert _ink(img, (298, 48, 454, 154)) == 0  # deleted: gone
        assert _ink(img, (50, 350, 202, 452)) > 0  # drawn where it went
    finally:
        _close(win)


def test_highlight_still_drawn_and_stamp_drawn_once(qapp, annotated, monkeypatch) -> None:
    win = _window(qapp, annotated, "engine", monkeypatch)
    try:
        img = _render(win)
        c = QColor(img.pixel(630, 412))  # highlight, past the words
        assert c.red() > 200 and c.green() > 200 and c.blue() < 120
        # The foreign stamp shows Annoter's StampItem only: Acrobat's big
        # "APPROVED" appearance (bottom part of its rect) is not drawn.
        stamp = next(i for i in win._scene.page_item().childItems() if isinstance(i, StampItem))
        r = stamp.mapRectToParent(stamp.boundingRect())
        below = (500, int(r.bottom() / K) + 4, 780, 170)
        assert _ink(img, below) == 0
    finally:
        _close(win)


def test_screen_matches_v030_outside_managed_annotations(qapp, annotated, monkeypatch) -> None:
    images = {}
    for renderer in ("pymupdf", "engine"):
        win = _window(qapp, annotated, renderer, monkeypatch)
        try:
            images[renderer] = _render(win)
        finally:
            _close(win)
    a, b = images["pymupdf"], images["engine"]
    assert a.size() == b.size()
    # Managed annotations' areas (with a margin for strokes): may differ.
    excluded = [(44, 44, 208, 158), (294, 44, 458, 158), (494, 44, 786, 176)]

    def inside(x: int, y: int) -> bool:
        return any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in excluded)

    total = n = 0
    for y in range(0, a.height(), 2):
        for x in range(0, a.width(), 2):
            if inside(x, y):
                continue
            ca, cb = QColor(a.pixel(x, y)), QColor(b.pixel(x, y))
            total += abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + abs(ca.blue() - cb.blue())
            n += 3
    assert total / n < 1.0


def test_other_pages_and_inserted_pages(qapp, annotated, tmp_path, monkeypatch) -> None:
    win = _window(qapp, annotated, "engine", monkeypatch)
    try:
        win._goto_next_page()
        _settle(qapp, win)
        item = next(i for i in win._scene.page_item().childItems() if isinstance(i, RectangleItem))
        item.setPos(item.pos() + QPointF(0, 250 * K))
        qapp.processEvents()
        assert _ink(_render(win), (98, 98, 302, 202)) == 0  # page 2: no ghost either
        win._goto_prev_page()
        # Insert a copy of the document: its pages bring their annotations.
        other = tmp_path / "other.pdf"
        other.write_bytes(annotated.read_bytes())
        monkeypatch.setattr(
            "annoter.views.main_window.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **k: (str(other), "")),
        )
        win._on_insert_pdf()
        assert win._display.page_count() == 4
        win._show_page(2)
        win._view.set_zoom(1.0)
        _settle(qapp, win)
        items = win._scene.page_item().childItems()
        moved = next(i for i in items if isinstance(i, RectangleItem) and i.pos().x() < 100 * K)
        moved.setPos(moved.pos() + QPointF(0, 300 * K))
        qapp.processEvents()
        assert _ink(_render(win), (48, 48, 204, 154)) == 0
    finally:
        _close(win)
