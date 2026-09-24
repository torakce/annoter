"""UI redesign, Lot F: inspector (Properties dock), palette, controls."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, QSettings  # noqa: E402
from PySide6.QtGui import QAction, QColor, QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from annoter.controllers.geometry import px_to_pt  # noqa: E402
from annoter.model.gdt import GdtState  # noqa: E402
from annoter.model.styles import DashStyle, TextAlign  # noqa: E402
from annoter.services.palette import (  # noqa: E402
    MAX_SWATCHES,
    PaletteStore,
    default_swatches,
)
from annoter.views.inspector_widgets import (  # noqa: E402
    STROKE_PRESETS,
    ColorSwatchRow,
    PaletteEditor,
    SegmentedControl,
    StrokeField,
)
from annoter.views.items.gdt import GdtAnnotationItem  # noqa: E402
from annoter.views.items.shapes import CloudItem, RectangleItem  # noqa: E402
from annoter.views.items.text import TextAnnotationItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402
from annoter.views.properties_dock import PropertiesDock  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat)


@pytest.fixture
def dock(qapp):
    d = PropertiesDock(palette=PaletteStore())
    stack = QUndoStack()
    d.set_undo_stack(stack)
    yield d, stack


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


def _labels(dock: PropertiesDock) -> list[str]:
    return [lbl.text() for lbl in dock.findChildren(QLabel)]


# ----------------------------------------------------------------------
# palette store
# ----------------------------------------------------------------------
def test_palette_defaults_and_persistence(qapp, settings) -> None:
    store = PaletteStore(settings)
    assert store.swatches() == default_swatches()
    assert store.is_default()
    fired: list[bool] = []
    store.changed.connect(lambda: fired.append(True))
    store.set_swatches([("Datum blue", "#1565c0"), ("Ink", "#000000")])
    assert fired == [True]
    assert store.swatches() == [("Datum blue", "#1565C0"), ("Ink", "#000000")]
    # A new store on the same settings reads the edited palette back.
    again = PaletteStore(settings)
    assert again.swatches() == store.swatches()
    again.reset()
    assert PaletteStore(settings).is_default()


def test_palette_cleans_bad_input(qapp) -> None:
    store = PaletteStore()
    many = [(f"c{i}", "#112233") for i in range(MAX_SWATCHES + 5)]
    store.set_swatches([("bad", "not-a-color"), *many])
    assert len(store.swatches()) == MAX_SWATCHES
    store.set_swatches([])  # empty -> defaults, never an empty palette
    assert store.is_default()


def test_palette_ignores_corrupt_settings(qapp, settings) -> None:
    settings.setValue("ui/palette", "{not json")
    assert PaletteStore(settings).is_default()


# ----------------------------------------------------------------------
# palette editor
# ----------------------------------------------------------------------
def test_palette_editor_edits_reorders_and_saves(qapp) -> None:
    store = PaletteStore()
    ed = PaletteEditor(store)
    rows = ed.rows()
    assert len(rows) == len(default_swatches())
    rows[0].name_edit.setText("Revision red")
    rows[0].hex_edit.setText("C62828")
    ed.move_row(rows[0], 1)  # first color moves to second place
    ed.remove_row(ed.rows()[-1])
    ed.accept()
    names = [n for n, _h in store.swatches()]
    assert names[1] == "Revision red"
    assert store.swatches()[1][1] == "#C62828"
    assert len(store.swatches()) == len(default_swatches()) - 1


def test_palette_editor_limit_and_reset(qapp) -> None:
    store = PaletteStore()
    ed = PaletteEditor(store)
    while ed.add_row("x", "#808080") is not None:
        pass
    assert len(ed.rows()) == MAX_SWATCHES
    assert not ed.add_button.isEnabled()
    ed.reset_button.click()
    assert len(ed.rows()) == len(default_swatches())
    # Keeps at least one color.
    for row in ed.rows():
        ed.remove_row(row)
    assert len(ed.rows()) == 1


# ----------------------------------------------------------------------
# controls
# ----------------------------------------------------------------------
def test_segmented_control_emits_on_click_only(qapp) -> None:
    seg = SegmentedControl([(1, "One", None, ""), (2, "Two", None, "")])
    got: list[object] = []
    seg.valueChanged.connect(got.append)
    seg.set_value(2)
    assert seg.value() == 2 and got == []
    seg.button_for(1).click()
    assert got == [1] and seg.value() == 1
    seg.set_value(99)  # no match: nothing checked
    assert seg.value() is None


def test_swatch_row_follows_the_palette(qapp) -> None:
    store = PaletteStore()
    row = ColorSwatchRow(store)
    assert len(row.swatches()) == len(default_swatches())
    row.set_current(QColor(default_swatches()[2][1]))
    assert [s.isChecked() for s in row.swatches()].index(True) == 2
    picked: list[QColor] = []
    row.colorPicked.connect(picked.append)
    row.swatches()[0].click()
    assert picked == [QColor(default_swatches()[0][1])]
    store.set_swatches([("Only", "#123456")])
    assert len(row.swatches()) == 1


def test_stroke_field_presets_and_exact_value(qapp) -> None:
    field = StrokeField()
    got: list[float] = []
    field.presetPicked.connect(got.append)
    field.presets.button_for(STROKE_PRESETS[-1]).click()
    assert got == [float(STROKE_PRESETS[-1])]
    assert field.spin.value() == STROKE_PRESETS[-1]
    field.spin.setValue(7)  # not a preset: no segment checked
    assert field.presets.value() is None
    field.set_width(2.0)
    assert field.presets.value() == 2


# ----------------------------------------------------------------------
# inspector content
# ----------------------------------------------------------------------
def test_empty_state_shows_defaults_and_tips(dock) -> None:
    d, _stack = dock
    d.set_defaults(QColor("#1E88E5"), 3.0)
    assert d.header_text() == "Nothing selected"
    assert d.field("Color").current() == QColor("#1E88E5")
    assert d.field("Stroke").value() == 3
    labels = _labels(d)
    assert "NEXT ANNOTATION" in labels and "GOOD TO KNOW" in labels
    assert "Ctrl K" in labels


def test_rectangle_sections_and_header(dock) -> None:
    d, _stack = dock
    item = RectangleItem(QRectF(0, 0, 40, 20))
    d.set_items([item])
    assert d.header_text() == "Rectangle"
    for label in ("Color", "Stroke", "Line", "Outline", "Fill", "Corners",
                  "Text", "X", "Y", "Width", "Height", "Duplicate", "Delete"):
        assert d.has_field(label), label
    titles = _labels(d)
    for title in ("STYLE", "SHAPE", "LABEL", "POSITION & SIZE"):
        assert title in titles


def test_multi_selection_header(dock) -> None:
    d, _stack = dock
    a, b = RectangleItem(QRectF(0, 0, 5, 5)), RectangleItem(QRectF(9, 9, 5, 5))
    d.set_items([a, b])
    assert d.header_text() == "2 x Rectangle"
    assert not d.has_field("X")  # geometry is single-item only
    t = TextAnnotationItem(QPointF(0, 0), "hi")
    d.set_items([a, t])
    assert d.header_text() == "2 annotations"
    assert d.has_field("Color") and not d.has_field("Outline")


def test_line_style_segments_push_undoable_changes(dock) -> None:
    d, stack = dock
    item = RectangleItem(QRectF(0, 0, 40, 20))
    d.set_items([item])
    d.field("Line").button_for(DashStyle.DASHED).click()
    assert item.dash_style() is DashStyle.DASHED
    assert stack.count() == 1
    stack.undo()
    assert item.dash_style() is DashStyle.SOLID


def test_outline_segment_converts_to_cloud(qapp) -> None:
    from PySide6.QtGui import QPixmap

    scene = PdfScene()
    scene.set_page_pixmap(QPixmap(300, 300))
    stack = QUndoStack()
    scene.set_undo_stack(stack)
    rect = RectangleItem(QRectF(10, 10, 60, 40))
    rect.setParentItem(scene.page_item())
    d = PropertiesDock(palette=PaletteStore())
    d.set_undo_stack(stack)
    d.set_items([rect])
    d.field("Outline").button_for(True).click()
    kids = scene.page_item().childItems()
    assert any(isinstance(k, CloudItem) for k in kids)
    assert rect not in kids
    stack.undo()
    assert rect in scene.page_item().childItems()
    scene.clear_page()


def test_text_controls_align_and_bold(dock) -> None:
    d, stack = dock
    item = TextAnnotationItem(QPointF(0, 0), "note")
    d.set_items([item])
    d.field("Align").button_for(TextAlign.CENTER).click()
    assert item.align() is TextAlign.CENTER
    d.field("Bold").click()
    assert item.bold() is True
    assert stack.count() == 2


def test_position_unit_switch(dock) -> None:
    d, _stack = dock
    item = RectangleItem(QRectF(72.0, 10, 30, 30))
    d.set_items([item])
    x_pt = px_to_pt(72.0)
    assert d.unit() == "mm"
    assert d.field("X").value() == pytest.approx(x_pt * 25.4 / 72.0, abs=0.01)
    assert d.field("X").suffix() == " mm"
    d.field("Unit").button_for("pt").click()
    assert d.unit() == "pt"
    assert d.field("X").value() == pytest.approx(x_pt, abs=0.05)


def test_footer_and_gdt_edit_emit_signals(dock) -> None:
    d, _stack = dock
    gdt = GdtAnnotationItem(GdtState(), QPointF(10, 10))
    d.set_items([gdt])
    got: list[str] = []
    d.editRequested.connect(lambda: got.append("edit"))
    d.duplicateRequested.connect(lambda: got.append("dup"))
    d.deleteRequested.connect(lambda: got.append("del"))
    d.field("Content").click()
    d.field("Duplicate").click()
    d.field("Delete").click()
    assert got == ["edit", "dup", "del"]


def test_arrange_buttons_trigger_actions(dock) -> None:
    d, _stack = dock
    front, back = QAction("Bring to Front"), QAction("Send to Back")
    painter = QAction("Format Painter")
    painter.setCheckable(True)
    fired: list[str] = []
    front.triggered.connect(lambda: fired.append("front"))
    back.triggered.connect(lambda: fired.append("back"))
    d.set_actions(bring_front=front, send_back=back, format_painter=painter)
    d.set_items([RectangleItem(QRectF(0, 0, 9, 9))])
    d.field("To front").click()
    d.field("To back").click()
    d.field("Copy style").click()
    assert fired == ["front", "back"]
    assert painter.isChecked()


# ----------------------------------------------------------------------
# main window wiring
# ----------------------------------------------------------------------
def test_inspector_color_recolors_selection_and_sets_default(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        item = RectangleItem(QRectF(10, 10, 40, 40))
        win._scene.push_add(item)
        item.setSelected(True)
        swatch = win._properties_dock.field("Color").swatches()[1]
        swatch.click()
        assert item.color() == swatch.color()
        assert win._tool_controller.color() == swatch.color()
        win._properties_dock.field("Stroke").setValue(6)
        win._properties_dock.field("Stroke").editingFinished.emit()
        assert item.stroke() == pytest.approx(6.0)
        assert win._tool_controller.stroke() == pytest.approx(6.0)
    finally:
        win.close()


def test_edit_palette_opens_the_editor(qapp, monkeypatch) -> None:
    seen: list[PaletteEditor] = []

    def fake_exec(self) -> int:
        seen.append(self)
        self.rows()[0].name_edit.setText("Mine")
        self.accept()
        return 1

    monkeypatch.setattr(PaletteEditor, "exec", fake_exec)
    win = MainWindow()
    try:
        before = win._palette.swatches()
        win._palette.set_swatches(default_swatches())
        win._properties_dock.editPaletteRequested.emit()
        assert seen and win._palette.swatches()[0][0] == "Mine"
    finally:
        win._palette.set_swatches(before)
        win.close()
