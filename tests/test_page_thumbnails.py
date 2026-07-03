"""Page thumbnail dock and thumbnail rendering (Discussion #1, item 10)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.model.document import PdfDocument  # noqa: E402
from annoter.services.pdf_render import PageRenderer  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.page_thumbnails import (  # noqa: E402
    THUMB_MAX_PX,
    PageThumbnailDock,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def three_page_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "three.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


def test_render_thumbnail_fits_and_caches(qapp, three_page_pdf: Path) -> None:
    doc = PdfDocument(three_page_pdf)
    try:
        renderer = PageRenderer(doc, 150, 3)
        pm = renderer.render_thumbnail(0, THUMB_MAX_PX)
        assert max(pm.width(), pm.height()) <= THUMB_MAX_PX + 1
        # Cached: second call returns the identical pixmap object.
        assert renderer.render_thumbnail(0, THUMB_MAX_PX) is pm
        # The full-page LRU is untouched by thumbnail renders.
        assert len(renderer._cache) == 0
    finally:
        doc.close()


def test_dock_builds_one_item_per_page(qapp, three_page_pdf: Path) -> None:
    doc = PdfDocument(three_page_pdf)
    dock = PageThumbnailDock()
    try:
        renderer = PageRenderer(doc, 150, 3)
        dock.set_document(renderer, doc.page_count)
        assert dock._list.count() == 3
        # Drain the lazy queue synchronously.
        while dock._pending:
            dock._render_next()
        assert all(
            not dock._list.item(i).icon().isNull() for i in range(3)
        )
        dock.set_document(None)
        assert dock._list.count() == 0
    finally:
        doc.close()


def test_click_thumbnail_navigates(qapp, three_page_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(three_page_pdf)
        assert win._thumbnail_dock._list.count() == 3
        win._thumbnail_dock.pageClicked.emit(2)
        assert win._page_index == 2
        # Page switches keep the dock's current row in sync.
        win._show_page(1)
        assert win._thumbnail_dock._list.currentRow() == 1
    finally:
        win.close()


def test_dock_cleared_on_close(qapp, three_page_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(three_page_pdf)
        win._on_close()
        assert win._thumbnail_dock._list.count() == 0
    finally:
        win.close()


def test_page_indicator_is_a_button_wired_to_goto(qapp, three_page_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(three_page_pdf)
        assert win._lbl_page.isEnabled()
        assert win._lbl_page.text() == "Page 1 / 3"
        win._on_close()
        assert not win._lbl_page.isEnabled()
    finally:
        win.close()
