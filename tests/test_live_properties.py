"""Properties dock: live preview on every change, single commit on finish."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication, QFormLayout  # noqa: E402

from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.properties_dock import PropertiesDock  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dock(qapp):
    d = PropertiesDock()
    stack = QUndoStack()
    d.set_undo_stack(stack)
    yield d, stack


def _find_row(dock: PropertiesDock, label: str):
    """Find the input widget for a given row label in the built form."""
    host = dock._body_layout.itemAt(0).widget()
    form = host.layout()
    for i in range(form.rowCount()):
        lbl_item = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
        if lbl_item is not None and lbl_item.widget().text() == label:
            field_item = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            return field_item.widget()
    raise AssertionError(f"row {label!r} not found")


def test_spin_arrow_clicks_update_live_without_undo(dock) -> None:
    d, stack = dock
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_stroke(2.0)
    d.set_items([item])

    stroke_spin = _find_row(d, "Stroke")
    stroke_spin.setValue(5)  # simulates clicking the up-arrow several times
    # Live preview happened immediately -- no neutral click needed.
    assert item.stroke() == pytest.approx(5.0)
    # But nothing is undoable yet: the session is still open.
    assert stack.count() == 0


def test_editing_finished_commits_a_single_undo_step(dock) -> None:
    d, stack = dock
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_stroke(2.0)
    d.set_items([item])

    stroke_spin = _find_row(d, "Stroke")
    stroke_spin.setValue(3)
    stroke_spin.setValue(5)
    stroke_spin.setValue(7)
    assert item.stroke() == pytest.approx(7.0)
    stroke_spin.editingFinished.emit()

    assert stack.count() == 1
    stack.undo()
    # Undo restores the TRUE original (2.0), not an intermediate tick.
    assert item.stroke() == pytest.approx(2.0)


def test_second_edit_session_does_not_reuse_stale_baseline(dock) -> None:
    d, stack = dock
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_stroke(2.0)
    d.set_items([item])
    stroke_spin = _find_row(d, "Stroke")

    stroke_spin.setValue(5)
    stroke_spin.editingFinished.emit()
    assert stack.count() == 1

    stroke_spin.setValue(9)
    stroke_spin.editingFinished.emit()
    assert stack.count() == 2

    stack.undo()  # undoes 5 -> 9
    assert item.stroke() == pytest.approx(5.0)
    stack.undo()  # undoes 2 -> 5
    assert item.stroke() == pytest.approx(2.0)


def test_finishing_without_any_change_pushes_nothing(dock) -> None:
    d, stack = dock
    item = RectangleItem(QRectF(0, 0, 10, 10))
    item.set_stroke(2.0)
    d.set_items([item])
    stroke_spin = _find_row(d, "Stroke")
    stroke_spin.editingFinished.emit()  # never touched
    assert stack.count() == 0


def test_text_field_live_preview(dock) -> None:
    d, stack = dock
    item = RectangleItem(QRectF(0, 0, 40, 40))
    item.set_text("old")
    d.set_items([item])

    text_edit = _find_row(d, "Text")
    text_edit.setText("new label")
    assert item.text() == "new label"
    assert stack.count() == 0
    text_edit.editingFinished.emit()
    assert stack.count() == 1
    stack.undo()
    assert item.text() == "old"


def test_multi_selection_live_preview_and_commit(dock) -> None:
    d, stack = dock
    a = RectangleItem(QRectF(0, 0, 10, 10))
    a.set_stroke(1.0)
    b = RectangleItem(QRectF(0, 0, 10, 10))
    b.set_stroke(3.0)
    d.set_items([a, b])

    stroke_spin = _find_row(d, "Stroke")
    stroke_spin.setValue(6)
    assert a.stroke() == pytest.approx(6.0)
    assert b.stroke() == pytest.approx(6.0)

    stroke_spin.editingFinished.emit()
    assert stack.count() == 1
    stack.undo()
    assert a.stroke() == pytest.approx(1.0)
    assert b.stroke() == pytest.approx(3.0)


def test_geometry_x_field_live_preview_and_single_commit(dock) -> None:
    from annoter.controllers.geometry import item_scene_rect, px_to_pt

    d, stack = dock
    item = RectangleItem(QRectF(10, 20, 30, 40))
    d.set_items([item])

    x_spin = _find_row(d, "X (pt)")
    original_x_pt = px_to_pt(10.0)
    x_spin.setValue(original_x_pt + 50)
    assert item_scene_rect(item).x() > 10.0  # moved live
    assert stack.count() == 0

    x_spin.editingFinished.emit()
    assert stack.count() == 1
    stack.undo()
    assert item_scene_rect(item).x() == pytest.approx(10.0, abs=0.1)
