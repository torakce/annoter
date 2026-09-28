"""Canvas chrome that does not depend on the zoom, the two ends of a
line told apart, and Alt+Click among stacked annotations (Lot K)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import fitz
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsSceneHoverEvent,
    QLabel,
)

from annoter.model.styles import HandleRole  # noqa: E402
from annoter.views.annotation_picker import HEADER, AnnotationPicker  # noqa: E402
from annoter.views.items.base import HANDLE_HALF, HANDLE_HIT_HALF  # noqa: E402
from annoter.views.items.gdt import GdtAnnotationItem  # noqa: E402
from annoter.views.items.lines import ArrowItem, LineItem  # noqa: E402
from annoter.views.items.shapes import CloudItem, RectangleItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.pdf_scene import PdfScene, pick_highlight_path  # noqa: E402
from annoter.views.pdf_view import PdfView  # noqa: E402
from annoter.views.properties_dock import PropertiesDock  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def canvas(qapp):
    """A bare scene + view on a blank page, Select tool (no MainWindow:
    nothing here may open a modal menu)."""
    scene = PdfScene()
    pm = QPixmap(1200, 900)
    pm.fill(QColor("white"))
    scene.set_page_pixmap(pm)
    view = PdfView()
    view.setScene(scene)
    view.resize(900, 700)
    view.show()
    yield scene, view
    view.close()


def _add(scene: PdfScene, item):
    item.setParentItem(scene.page_item())
    return item


# ----------------------------------------------------------------------
# handles keep one on-screen size
# ----------------------------------------------------------------------
@pytest.mark.parametrize("zoom", [0.1, 0.25, 1.0, 3.0])
def test_handles_have_a_constant_screen_size(canvas, zoom: float) -> None:
    scene, view = canvas
    rect = _add(scene, RectangleItem(QRectF(100, 100, 200, 120)))
    rect.setSelected(True)
    view.set_zoom(zoom)
    scale = view.transform().m11()
    corner = rect.handle_positions()[HandleRole.TOP_LEFT]
    on_screen = rect._handle_visual_rect(corner).width() * scale
    assert on_screen == pytest.approx(2 * HANDLE_HALF)
    hit = rect._handle_hit_rect(corner).width() * scale
    assert hit == pytest.approx(2 * HANDLE_HIT_HALF)


def test_zooming_out_grows_the_handle_margin_in_scene_units(canvas) -> None:
    scene, view = canvas
    rect = _add(scene, RectangleItem(QRectF(100, 100, 200, 120)))
    rect.setSelected(True)
    view.set_zoom(1.0)
    near = rect.boundingRect().width()
    view.set_zoom(0.2)
    far = rect.boundingRect().width()
    assert far > near
    # A press six screen pixels off the corner still grabs it.
    px = scene.screen_px()
    local = QPointF(100 - 6 * px, 100 - 6 * px)
    assert rect.hit_handle(local) is HandleRole.TOP_LEFT


def test_bend_hit_radius_follows_the_zoom(canvas) -> None:
    scene, view = canvas
    line = _add(scene, LineItem(QPointF(0, 0), QPointF(400, 0)))
    line.set_bends([QPointF(200, 100)])
    view.set_zoom(1.0)
    far = QPointF(200, 100 + 20 * scene.screen_px() * 0.25)
    assert line.bend_at(far) == 0  # 5 screen px away
    view.set_zoom(0.1)
    assert line.bend_at(QPointF(200, 100 + 5 * scene.screen_px())) == 0


def test_items_outside_a_view_keep_unit_scale(qapp) -> None:
    item = RectangleItem(QRectF(0, 0, 10, 10))
    assert item.screen_px() == 1.0


# ----------------------------------------------------------------------
# start and end of a line
# ----------------------------------------------------------------------
def test_direction_chevron_points_from_start_to_end(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(200, 0))
    barb1, tip, barb2 = line.direction_chevron()
    assert tip.x() > barb1.x() and tip.x() > barb2.x()
    assert 90 < tip.x() < 110  # halfway along
    reverse = LineItem(QPointF(200, 0), QPointF(0, 0))
    barb1, tip, _b = reverse.direction_chevron()
    assert tip.x() < barb1.x()


def test_direction_chevron_follows_a_bent_path(qapp) -> None:
    line = LineItem(QPointF(0, 0), QPointF(40, 200))
    line.set_bends([QPointF(40, 0)])
    _b1, tip, _b2 = line.direction_chevron()
    # On the longest run (the downward one), heading down.
    assert tip.x() == pytest.approx(40.0)
    assert tip.y() > 100


def test_no_chevron_on_a_tiny_line(qapp) -> None:
    assert LineItem(QPointF(0, 0), QPointF(20, 0)).direction_chevron() is None


def _hover(item, local: QPointF) -> None:
    ev = QGraphicsSceneHoverEvent(QEvent.GraphicsSceneHoverMove)
    ev.setPos(local)
    item.hoverMoveEvent(ev)


def test_end_handles_name_themselves(canvas) -> None:
    scene, _view = canvas
    arrow = _add(scene, ArrowItem(QPointF(100, 100), QPointF(400, 100)))
    arrow.set_bends([QPointF(250, 200)])
    arrow.setSelected(True)
    _hover(arrow, QPointF(101, 100))
    assert arrow.toolTip() == "Start point"
    _hover(arrow, QPointF(399, 101))
    assert arrow.toolTip() == "End point"
    _hover(arrow, QPointF(250, 199))
    assert arrow.toolTip() == "Bend point"
    _hover(arrow, QPointF(180, 150))
    assert arrow.toolTip() == ""


def test_start_and_end_handles_are_drawn_differently(qapp) -> None:
    """The start is a disc, the end a square. Painted 10x magnified, a
    point near the corner of each handle's box is inside the square but
    outside the disc."""
    line = LineItem(QPointF(5, 10), QPointF(90, 10))
    img = QImage(1000, 200, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    painter.scale(10, 10)
    line._draw_handles(painter)
    painter.end()
    off = -HANDLE_HALF * 10 * 0.78
    start = img.pixelColor(int(50 + off), int(100 + off))
    end = img.pixelColor(int(900 + off), int(100 + off))
    assert end.blue() > 150 and end.red() < 100  # inside the square
    assert start.lightness() > 240  # outside the disc
    center = img.pixelColor(50, 100)
    assert center.blue() > 150 and center.red() < 100  # the disc itself


# ----------------------------------------------------------------------
# Alt+Click among stacked annotations
# ----------------------------------------------------------------------
def test_annotations_at_lists_the_pile_topmost_first(canvas) -> None:
    scene, _view = canvas
    a = _add(scene, RectangleItem(QRectF(100, 100, 200, 200)))
    b = _add(scene, CloudItem(QRectF(150, 150, 200, 200)))
    c = _add(scene, LineItem(QPointF(0, 200), QPointF(500, 200)))
    far = _add(scene, RectangleItem(QRectF(800, 700, 50, 50)))
    under = scene.annotations_at(QPointF(200, 200))
    assert under == [c, b, a]
    assert far not in under
    assert scene.annotations_at(QPointF(5, 5)) == []


def test_alt_click_asks_for_the_list_but_alt_drag_does_not(canvas) -> None:
    scene, view = canvas
    view.set_zoom(1.0)
    _add(scene, RectangleItem(QRectF(100, 100, 200, 200)))
    _add(scene, RectangleItem(QRectF(150, 150, 200, 200)))
    asked: list[QPointF] = []
    scene.pickRequested.connect(lambda sp, _gp: asked.append(sp))
    at = view.mapFromScene(scene.page_item().mapToScene(QPointF(200, 200)))
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.AltModifier, at)
    assert len(asked) == 1
    assert asked[0].x() == pytest.approx(200, abs=2)
    # A plain click does not.
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    assert len(asked) == 1
    # Alt+drag moves (snapping off) and does not open the list either.
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.AltModifier, at)
    QTest.mouseMove(view.viewport(), at + type(at)(40, 30))
    QTest.mouseRelease(
        view.viewport(), Qt.LeftButton, Qt.AltModifier, at + type(at)(40, 30)
    )
    assert len(asked) == 1
    # The application keeps the modifiers of the last input event: leave
    # no Alt behind (it turns off snapping in the tests that follow).
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    assert QApplication.keyboardModifiers() == Qt.NoModifier


def test_picker_highlights_on_hover_and_selects_on_pick(canvas) -> None:
    scene, _view = canvas
    a = _add(scene, RectangleItem(QRectF(100, 100, 200, 200)))
    b = _add(scene, ArrowItem(QPointF(50, 200), QPointF(400, 200)))
    b.setSelected(True)
    picked: list = []

    def pick(item) -> None:
        picked.append(item)
        for it in scene.selectedItems():
            it.setSelected(False)
        item.setSelected(True)

    picker = AnnotationPicker(
        scene.annotations_at(QPointF(200, 200)),
        QColor("#333333"),
        scene.set_pick_highlight,
        pick,
    )
    assert picker.items() == [b, a]
    assert picker.labels()[1].startswith("Rectangle")
    assert picker.actions()[0].text() == HEADER
    assert picker.entry(0).font().bold() and not picker.entry(1).font().bold()

    picker.entry(1).hover()
    hl = scene.pick_highlight()
    assert hl is not None
    assert hl.path().contains(a.mapToScene(QPointF(200, 150)))
    picker.entry(0).hover()
    # One outline at a time, now along the arrow's shaft only.
    assert scene.pick_highlight() is not hl
    band = scene.pick_highlight().path()
    assert band.contains(b.mapToScene(QPointF(300, 200)))
    assert not band.contains(b.mapToScene(QPointF(300, 150)))

    picker.entry(1).trigger()
    assert picked == [a]
    assert scene.selectedItems() == [a]
    picker.aboutToHide.emit()
    assert scene.pick_highlight() is None


def test_highlight_path_of_a_frame_is_its_rectangle(qapp) -> None:
    item = GdtAnnotationItem(pos=QPointF(10, 10))
    path = pick_highlight_path(item, 1.0)
    assert path.contains(item.content_rect().center() + item.pos())
    assert path.boundingRect().width() == pytest.approx(
        item.content_rect().width() + 8.0
    )


# ----------------------------------------------------------------------
# main window wiring
# ----------------------------------------------------------------------
@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=842, height=595)
    doc.save(str(path))
    doc.close()
    return path


def test_main_window_picker(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        a = RectangleItem(QRectF(100, 100, 200, 200))
        b = RectangleItem(QRectF(150, 150, 200, 200))
        win._scene.push_add(a)
        win._scene.push_add(b)
        assert win._build_picker(QPointF(120, 120)) is None  # only one
        picker = win._build_picker(QPointF(200, 200))
        assert picker.items() == [b, a]
        picker.entry(1).trigger()
        assert win._selected_annotations() == [a]
        # The view pushes its zoom to the scene.
        win._view.set_zoom(0.5)
        assert win._scene.screen_px() == pytest.approx(
            1.0 / win._view.transform().m11()
        )
    finally:
        win._on_close()
        win.close()


def test_inspector_marks_start_and_end_rows(qapp) -> None:
    dock = PropertiesDock()
    dock.set_items([ArrowItem(QPointF(0, 0), QPointF(100, 0))])
    glyphs = dock.findChildren(QLabel, "InspectorLabelGlyph")
    assert len(glyphs) >= 2
    assert dock.field("Start") is not None and dock.field("End") is not None
