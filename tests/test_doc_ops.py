"""Document-level operations batch (Discussion #2): images as input,
merge, page reorder, resize to paper format, image export, grayscale."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.model.document import PdfDocument  # noqa: E402
from annoter.services.pdf_render import PageRenderer  # noqa: E402
from annoter.views.items.lines import ArrowItem  # noqa: E402
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_pdf(path: Path, sizes: list[tuple[float, float]]) -> None:
    doc = fitz.open()
    for w, h in sizes:
        doc.new_page(width=w, height=h)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def a4_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "a4.pdf"
    _make_pdf(p, [(595, 842)])
    return p


# ----------------------------------------------------------------------
# images as input
# ----------------------------------------------------------------------
def test_open_image_becomes_untitled_pdf(qapp, tmp_path: Path) -> None:
    from PIL import Image

    img_path = tmp_path / "photo.png"
    Image.new("RGB", (320, 200), (200, 30, 30)).save(str(img_path))

    win = MainWindow()
    try:
        win.open_path(img_path)
        assert win._doc is not None
        assert win._is_untitled is True
        assert win._doc.page_count == 1
        w, h = win._doc.page_size_pt(0)
        assert w > 0 and h > 0
        assert w / h == pytest.approx(320 / 200, abs=0.05)
    finally:
        win._on_close()
        win.close()


def test_openable_file_filter(qapp) -> None:
    assert MainWindow._is_openable_file("plan.pdf")
    assert MainWindow._is_openable_file("scan.TIF")
    assert MainWindow._is_openable_file("photo.jpeg")
    assert not MainWindow._is_openable_file("notes.txt")


# ----------------------------------------------------------------------
# merge (insert pages from PDF)
# ----------------------------------------------------------------------
def test_insert_pdf_appends_pages_and_reads_their_annots(
    qapp, a4_pdf: Path, tmp_path: Path, monkeypatch
) -> None:
    from annoter.services.pdf_export import write_annotations

    other = tmp_path / "other.pdf"
    doc = fitz.open()
    doc.new_page(width=300, height=300)
    rect = RectangleItem(QRectF(10, 10, 50, 50))
    write_annotations(doc, {0: [rect]}, 150)
    doc.save(str(other))
    doc.close()

    win = MainWindow()
    try:
        win.open_path(a4_pdf)
        from PySide6.QtWidgets import QFileDialog

        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            staticmethod(lambda *a, **k: (str(other), "")),
        )
        win._on_insert_pdf()
        assert win._doc.page_count == 2
        assert win._has_unsaved_changes() is True
        # The inserted page's annotation is editable.
        items = win._page_items.get(1, [])
        assert any(isinstance(it, RectangleItem) for it in items)
        assert win._thumbnail_dock._list.count() == 2
    finally:
        win._on_close()
        win.close()


# ----------------------------------------------------------------------
# page reorder
# ----------------------------------------------------------------------
def test_page_reorder_moves_page_and_remaps_items(
    qapp, tmp_path: Path
) -> None:
    p = tmp_path / "three.pdf"
    _make_pdf(p, [(300, 300), (400, 400), (500, 500)])

    win = MainWindow()
    try:
        win.open_path(p)
        # Tag page 0 with an item, then move page 0 to position 2.
        item = RectangleItem(QRectF(1, 1, 5, 5))
        win._scene.push_add(item)
        win._on_page_reordered(0, 2)

        sizes = [
            round(win._doc.page_size_pt(i)[0]) for i in range(3)
        ]
        assert sizes == [400, 500, 300]
        # The item followed its page to index 2 (it was on screen, so it
        # is either stashed at 2 or currently attached to the scene
        # showing page 2).
        assert win._page_index == 2
        page_children = win._scene.page_item().childItems()
        stashed = win._page_items.get(2, [])
        assert item in page_children or item in stashed
        assert win._has_unsaved_changes() is True
    finally:
        win._on_close()
        win.close()


def test_rows_moved_mapping_emits_final_index(qapp) -> None:
    from annoter.views.page_thumbnails import PageThumbnailDock

    dock = PageThumbnailDock()
    got: list[tuple[int, int]] = []
    dock.pageMoved.connect(lambda f, t: got.append((f, t)))
    # Qt reports destination in pre-removal indexing: moving row 0
    # after row 2 arrives as (start=0, row=3) -> final index 2.
    dock._on_rows_moved(None, 0, 0, None, 3)
    QApplication.processEvents()
    assert got == [(0, 2)]
    # Moving row 2 before row 0 arrives as (start=2, row=0) -> final 0.
    got.clear()
    dock._on_rows_moved(None, 2, 2, None, 0)
    QApplication.processEvents()
    assert got == [(2, 0)]


# ----------------------------------------------------------------------
# resize document
# ----------------------------------------------------------------------
def test_resize_document_scales_pages_and_items(
    qapp, a4_pdf: Path, monkeypatch
) -> None:
    win = MainWindow()
    try:
        win.open_path(a4_pdf)
        item = RectangleItem(QRectF(100, 100, 50, 50))
        item.set_stroke(2.0)
        win._scene.push_add(item)
        arrow = ArrowItem(QPointF(10, 10), QPointF(110, 10))
        arrow.set_bends([QPointF(60, 40)])
        win._scene.push_add(arrow)

        from PySide6.QtWidgets import QInputDialog

        monkeypatch.setattr(
            QInputDialog,
            "getItem",
            staticmethod(
                lambda *a, **k: ("A0 (841 x 1189 mm)", True)
            ),
        )
        win._on_resize_document()

        w, h = win._doc.page_size_pt(0)
        assert (round(w), round(h)) == (2384, 3370)
        s = 2384 / 595  # A4 -> A0 factor
        # Items scaled uniformly (they are on-screen after the refresh).
        assert item.rect().x() == pytest.approx(100 * s, rel=0.01)
        assert item.rect().width() == pytest.approx(50 * s, rel=0.01)
        assert item.stroke() == pytest.approx(2.0 * s, rel=0.01)
        assert arrow.bends()[0].x() == pytest.approx(60 * s, rel=0.01)
        assert win._has_unsaved_changes() is True
    finally:
        win._on_close()
        win.close()


# ----------------------------------------------------------------------
# export as images
# ----------------------------------------------------------------------
def test_export_images_tiff_multipage(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    from PIL import Image

    p = tmp_path / "two.pdf"
    _make_pdf(p, [(300, 300), (300, 300)])
    out = tmp_path / "export.tif"

    win = MainWindow()
    try:
        win.open_path(p)
        win._scene.push_add(RectangleItem(QRectF(10, 10, 50, 50)))

        from PySide6.QtWidgets import QFileDialog, QInputDialog

        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            staticmethod(
                lambda *a, **k: (str(out), "TIFF, multi-page (*.tif)")
            ),
        )
        monkeypatch.setattr(
            QInputDialog,
            "getInt",
            staticmethod(lambda *a, **k: (96, True)),
        )
        win._on_export_images()

        assert out.exists()
        with Image.open(str(out)) as img:
            assert getattr(img, "n_frames", 1) == 2
    finally:
        win._on_close()
        win.close()


def test_export_images_png_per_page(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    p = tmp_path / "two.pdf"
    _make_pdf(p, [(300, 300), (300, 300)])
    out = tmp_path / "pages.png"

    win = MainWindow()
    try:
        win.open_path(p)
        from PySide6.QtWidgets import QFileDialog, QInputDialog

        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            staticmethod(lambda *a, **k: (str(out), "PNG (*.png)")),
        )
        monkeypatch.setattr(
            QInputDialog,
            "getInt",
            staticmethod(lambda *a, **k: (96, True)),
        )
        win._on_export_images()
        assert (tmp_path / "pages_p1.png").exists()
        assert (tmp_path / "pages_p2.png").exists()
    finally:
        win._on_close()
        win.close()


# ----------------------------------------------------------------------
# grayscale display
# ----------------------------------------------------------------------
def test_grayscale_renderer(qapp, a4_pdf: Path) -> None:
    doc = PdfDocument(a4_pdf)
    try:
        renderer = PageRenderer(doc, 96, 3)
        color = renderer.render(0)
        renderer.set_grayscale(True)
        gray = renderer.render(0)
        assert gray.toImage().isGrayscale()
        assert not color.cacheKey() == gray.cacheKey()
    finally:
        doc.close()


def test_grayscale_toggle_keeps_annotations(qapp, a4_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(a4_pdf)
        item = RectangleItem(QRectF(10, 10, 50, 50))
        win._scene.push_add(item)
        win.act_grayscale.setChecked(True)
        assert win._renderer.grayscale() is True
        # The annotation survived the re-render (still on the page).
        assert item in win._scene.page_item().childItems()
        win.act_grayscale.setChecked(False)
        assert win._renderer.grayscale() is False
    finally:
        win._on_close()
        win.close()
