"""Tests for PageRenderer: full-page render and hi-res clip render."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.model.document import PdfDocument  # noqa: E402
from annoter.services.pdf_render import PageRenderer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def pdf_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("render") / "page.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((100, 100), "render test")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def renderer(pdf_path):
    doc = PdfDocument(pdf_path)
    yield PageRenderer(doc, dpi=150, cache_size=3)
    doc.close()


class TestRenderClip:
    def test_logical_geometry(self, qapp, renderer):
        clip = QRectF(100.0, 50.0, 200.0, 100.0)
        scale = 4.0
        pixmap, pos = renderer.render_clip(0, 0, clip, scale)
        assert pixmap.devicePixelRatio() == pytest.approx(scale)
        # Device pixels = logical clip * scale (within irect rounding).
        assert pixmap.width() == pytest.approx(
            clip.width() * scale, abs=scale + 1
        )
        assert pixmap.height() == pytest.approx(
            clip.height() * scale, abs=scale + 1
        )
        # Logical position lands on the clip origin.
        assert pos.x() == pytest.approx(clip.left(), abs=1.0)
        assert pos.y() == pytest.approx(clip.top(), abs=1.0)

    def test_rotated_clip(self, qapp, renderer):
        # Rotated page: logical space is the rotated raster. The full
        # page at 90 degrees is (h, w) in logical units.
        full = renderer.render(0, rotation=90)
        clip = QRectF(0.0, 0.0, full.width() / 2.0, full.height() / 2.0)
        pixmap, pos = renderer.render_clip(0, 90, clip, 2.0)
        assert not pixmap.isNull()
        assert pos.x() == pytest.approx(0.0, abs=1.0)
        assert pos.y() == pytest.approx(0.0, abs=1.0)

    def test_matches_full_render_pixels(self, qapp, renderer):
        # A scale-1 clip must reproduce the same pixels as the full
        # render cropped to the clip.
        full = renderer.render(0).toImage()
        clip = QRectF(90.0, 90.0, 60.0, 30.0)
        pixmap, pos = renderer.render_clip(0, 0, clip, 1.0)
        part = pixmap.toImage()
        ox, oy = int(pos.x()), int(pos.y())
        for dx, dy in ((0, 0), (10, 5), (30, 20)):
            assert part.pixel(dx, dy) == full.pixel(ox + dx, oy + dy)
