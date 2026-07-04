"""Discussion #1 follow-up feedback (2026-07-02 comment):

- toolbar quick color/stroke also restyle the current selection
- Shift-resize keeps a shape's ORIGINAL aspect ratio (not force-square)
- Office-style quick color picker menu
- theme-aware property combo icons
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.model.styles import HandleRole  # noqa: E402
from annoter.views.color_picker import (  # noqa: E402
    STANDARD_COLORS,
    ColorPickerMenu,
)
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def scene(qapp):
    sc = PdfScene()
    sc.set_page_pixmap(QPixmap(400, 400))
    sc.set_undo_stack(QUndoStack())
    yield sc
    sc.clear_page()


# ----------------------------------------------------------------------
# toolbar quick styles apply to the selection
# ----------------------------------------------------------------------
def test_quick_color_restyles_selection_and_controller(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        item = RectangleItem(QRectF(10, 10, 40, 40))
        win._scene.push_add(item)
        item.setSelected(True)

        win._on_quick_color_picked(QColor("#123456"))
        assert item.color() == QColor("#123456")
        assert win._tool_controller.color() == QColor("#123456")
        # Undoable: the recolor is its own undo step.
        win._undo_group.activeStack().undo()
        assert item.color() != QColor("#123456")
    finally:
        win.close()


def test_quick_color_without_selection_only_sets_controller(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        before = win._undo_group.activeStack().count()
        win._on_quick_color_picked(QColor("#654321"))
        assert win._tool_controller.color() == QColor("#654321")
        assert win._undo_group.activeStack().count() == before
    finally:
        win.close()


def test_quick_stroke_restyles_selection(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        item = RectangleItem(QRectF(10, 10, 40, 40))
        item.set_stroke(2.0)
        win._scene.push_add(item)
        item.setSelected(True)

        win._on_quick_stroke_picked(3.5)
        assert item.stroke() == pytest.approx(3.5)
        assert win._tool_controller.stroke() == pytest.approx(3.5)
        win._undo_group.activeStack().undo()
        assert item.stroke() == pytest.approx(2.0)
    finally:
        win.close()


# ----------------------------------------------------------------------
# Shift-resize keeps the original aspect ratio
# ----------------------------------------------------------------------
def test_shift_resize_preserves_original_aspect_ratio(scene) -> None:
    item = RectangleItem(QRectF(0, 0, 100, 50))  # 2:1
    item.setParentItem(scene.page_item())
    scene._resize_snapshot = QRectF(0, 0, 100, 50)

    snapped = scene._constrain_resize(
        item, HandleRole.BOTTOM_RIGHT, QPointF(160, 90)
    )
    w = snapped.x() - 0.0
    h = snapped.y() - 0.0
    assert w / h == pytest.approx(2.0, abs=0.01)


def test_shift_resize_square_shape_stays_square(scene) -> None:
    item = RectangleItem(QRectF(0, 0, 50, 50))
    item.setParentItem(scene.page_item())
    scene._resize_snapshot = QRectF(0, 0, 50, 50)
    snapped = scene._constrain_resize(
        item, HandleRole.BOTTOM_RIGHT, QPointF(90, 40)
    )
    assert snapped.x() == pytest.approx(snapped.y(), abs=0.01)


def test_scale_keep_ratio_helper(scene) -> None:
    out = scene._scale_keep_ratio(QPointF(0, 0), QPointF(30, 100), 40.0, 20.0)
    # Vertical pull dominates: scale = 100/20 = 5 -> (200, 100).
    assert out.x() == pytest.approx(200.0)
    assert out.y() == pytest.approx(100.0)


# ----------------------------------------------------------------------
# Office-style color picker
# ----------------------------------------------------------------------
def test_color_picker_menu_emits_selected_color(qapp) -> None:
    menu = ColorPickerMenu(QColor("#E53935"))
    got: list[QColor] = []
    menu.colorSelected.connect(got.append)
    menu._choose(QColor(STANDARD_COLORS[4]))
    assert got == [QColor(STANDARD_COLORS[4])]


def test_color_picker_menu_has_swatches_and_more_entry(qapp) -> None:
    menu = ColorPickerMenu()
    labels = [a.text() for a in menu.actions()]
    assert "More colors..." in labels
    assert len(STANDARD_COLORS) == 16


# ----------------------------------------------------------------------
# stroke ladder spin box
# ----------------------------------------------------------------------
def test_stroke_spin_steps_along_ladder(qapp) -> None:
    from annoter.views.stroke_spin import StrokeSpinBox

    spin = StrokeSpinBox()
    spin.setValue(6)
    spin.stepBy(1)
    assert spin.value() == 8  # not 7: ladder jumps 6 -> 8
    spin.stepBy(1)
    assert spin.value() == 10
    spin.stepBy(-1)
    assert spin.value() == 8
    # Multi-step (page up) walks several ladder rungs at once.
    spin.stepBy(2)
    assert spin.value() == 12


def test_stroke_spin_off_ladder_value_snaps_to_neighbors(qapp) -> None:
    from annoter.views.stroke_spin import StrokeSpinBox

    spin = StrokeSpinBox()
    spin.setValue(7)  # typed, not on the ladder
    spin.stepBy(1)
    assert spin.value() == 8
    spin.setValue(7)
    spin.stepBy(-1)
    assert spin.value() == 6


def test_stroke_spin_zero_minimum_for_properties_dock(qapp) -> None:
    from annoter.views.stroke_spin import StrokeSpinBox

    spin = StrokeSpinBox(minimum=0)
    spin.setValue(1)
    spin.stepBy(-1)
    assert spin.value() == 0  # below the ladder floor: plain decrement
    spin.stepBy(1)
    assert spin.value() == 1


def test_dock_palette_is_tools_only(qapp) -> None:
    """Color and stroke controls live in the top toolbar only; the
    Tools dock must not duplicate them (user follow-up)."""
    from annoter.controllers.tools import ToolController
    from annoter.views.tool_palette import ToolPalette

    from PySide6.QtWidgets import QLabel

    tc = ToolController()
    palette = ToolPalette(tc)
    assert not hasattr(palette, "_stroke_spin")
    assert not hasattr(palette, "_color_buttons")
    section_titles = [
        lbl.text() for lbl in palette.findChildren(QLabel)
    ]
    assert "Tool" in section_titles
    assert "Color" not in section_titles
    assert "Stroke" not in section_titles
    # The tool grid itself is intact.
    assert len(palette._tool_buttons) == 10


# ----------------------------------------------------------------------
# theme-aware property icons
# ----------------------------------------------------------------------
def test_properties_dock_icon_color_follows_theme(qapp) -> None:
    from annoter.views.properties_dock import PropertiesDock

    dock = PropertiesDock()
    item = RectangleItem(QRectF(0, 0, 10, 10))
    dock.set_items([item])
    dock.set_icon_color(QColor("#e0e0e0"))  # dark-theme glyph color
    assert dock._icon_color == QColor("#e0e0e0")
    # The form rebuilt without error and still shows the item's rows.
    assert dock._body_layout.count() > 0
