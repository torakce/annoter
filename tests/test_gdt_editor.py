"""Tests for the in-place GD&T editor (GdtInlineEditor)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from annoter.model.gdt import (  # noqa: E402
    Characteristic,
    DatumRef,
    GdtRow,
    GdtState,
)
from annoter.views.gdt_editor import GdtInlineEditor  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def host(qapp):
    w = QWidget()
    yield w
    w.deleteLater()


def _sample_state() -> GdtState:
    return GdtState(
        characteristic=Characteristic.POSITION,
        tolerance_prefix="SR",
        tolerance_value="0.1",
        tolerance_modifier="M",
        datum_primary=DatumRef(["A"]),
        datum_secondary=DatumRef(["B", "C"], modifier="L"),
        datum_tertiary=DatumRef([]),
    )


def test_initial_state_roundtrip(host) -> None:
    state = _sample_state()
    editor = GdtInlineEditor(state, host)
    assert editor.current_state() == state


def test_field_edits_reflected_in_state(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    row = editor._row_editors[0]
    row._value_edit.setText("0.25")
    row._set_prefix("Ø")
    row._datum_edits[0].setText("a-b")
    out = editor.current_state()
    assert out.tolerance_value == "0.25"
    assert out.tolerance_prefix == "Ø"
    assert out.datum_primary.letters == ["A", "B"]


def test_prefix_setter_cycles_all_values(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    row = editor._row_editors[0]
    for prefix in ("Ø", "R", "SØ", "SR", ""):
        row._set_prefix(prefix)
        assert editor.current_state().tolerance_prefix == prefix
    # The button label falls back to the em dash (plus the drop-down
    # arrow) when empty.
    assert row._prefix_btn.text().startswith("—")


def test_state_edited_emitted_live(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    seen: list[GdtState] = []
    editor.stateEdited.connect(seen.append)
    editor._row_editors[0]._value_edit.setText("0.5")
    assert seen
    assert seen[-1].tolerance_value == "0.5"


def test_characteristic_menu_updates_state(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    editor._row_editors[0]._set_characteristic(Characteristic.FLATNESS)
    assert (
        editor.current_state().characteristic
        is Characteristic.FLATNESS
    )


def test_per_row_characteristic(host) -> None:
    """Each row keeps its own symbol; only same-symbol rows merge later."""
    editor = GdtInlineEditor(GdtState(), host)
    editor._row_editors[0]._set_characteristic(Characteristic.POSITION)
    editor._add_row_editor(GdtRow())
    editor._row_editors[1]._set_characteristic(Characteristic.PARALLELISM)
    state = editor.current_state()
    rows = state.all_rows()
    assert rows[0].characteristic is Characteristic.POSITION
    assert rows[1].characteristic is Characteristic.PARALLELISM


def test_modifier_setters(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    row = editor._row_editors[0]
    row._set_tol_modifier("M")
    row._set_datum_modifier(1, "L")
    out = editor.current_state()
    assert out.tolerance_modifier == "M"
    assert out.datum_secondary.modifier == "L"
    row._set_tol_modifier(None)
    assert editor.current_state().tolerance_modifier is None


def test_enter_commits_once(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    commits: list[int] = []
    editor.committed.connect(lambda: commits.append(1))
    editor._row_editors[0]._value_edit.returnPressed.emit()
    # second Enter is a no-op
    editor._row_editors[0]._value_edit.returnPressed.emit()
    assert len(commits) == 1


def test_escape_cancels(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    cancels: list[int] = []
    commits: list[int] = []
    editor.cancelled.connect(lambda: cancels.append(1))
    editor.committed.connect(lambda: commits.append(1))
    event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    editor.keyPressEvent(event)
    assert cancels == [1]
    assert commits == []


def test_focus_check_skips_when_popup_open(qapp, host) -> None:
    # Regression: opening a dropdown bounces focus to the view; the
    # deferred check must not commit while the popup is active.
    host.show()
    editor = GdtInlineEditor(GdtState(), host)
    editor.show()
    commits: list[int] = []
    editor.committed.connect(lambda: commits.append(1))
    menu = editor._row_editors[0]._symbol_btn.menu()
    menu.popup(host.mapToGlobal(host.rect().center()))
    qapp.processEvents()
    assert QApplication.activePopupWidget() is menu
    editor._maybe_commit_on_focus_loss()
    assert commits == []
    menu.hide()
    qapp.processEvents()


def test_focus_check_commits_when_focus_outside(qapp, host) -> None:
    host.show()
    editor = GdtInlineEditor(GdtState(), host)
    editor.show()
    outside = QWidget(host)
    outside.setFocusPolicy(Qt.StrongFocus)
    outside.show()
    outside.setFocus()
    qapp.processEvents()
    assert QApplication.focusWidget() is outside
    commits: list[int] = []
    editor.committed.connect(lambda: commits.append(1))
    editor._maybe_commit_on_focus_loss()
    assert commits == [1]


def test_focus_check_ignores_focus_inside(qapp, host) -> None:
    host.show()
    editor = GdtInlineEditor(GdtState(), host)
    editor.show()
    editor._row_editors[0]._value_edit.setFocus()
    qapp.processEvents()
    commits: list[int] = []
    editor.committed.connect(lambda: commits.append(1))
    editor._maybe_commit_on_focus_loss()
    assert commits == []


def test_add_and_remove_rows(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    assert len(editor._row_editors) == 1
    editor._add_row_editor(GdtRow(tolerance_value="0.5"))
    assert len(editor._row_editors) == 2
    assert len(editor.current_state().all_rows()) == 2
    # Removing returns to a single row; the first row cannot be removed.
    editor._remove_row_editor(editor._row_editors[-1])
    assert len(editor._row_editors) == 1
    editor._remove_row_editor(editor._row_editors[0])  # no-op at one row
    assert len(editor._row_editors) == 1


def test_upper_lower_aux_in_state(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    editor._upper_edit.setText("2x")
    editor._lower_edit.setText("VALID FOR BOTH PARTS")
    editor._set_aux_symbol(Characteristic.PARALLELISM)
    editor._aux_edit.setText("A-B")
    out = editor.current_state()
    assert out.upper_text == "2x"
    assert out.lower_text == "VALID FOR BOTH PARTS"
    assert out.aux_symbol is Characteristic.PARALLELISM
    assert out.aux_text == "A-B"


def test_cancel_after_commit_is_noop(host) -> None:
    editor = GdtInlineEditor(GdtState(), host)
    fired: list[str] = []
    editor.committed.connect(lambda: fired.append("commit"))
    editor.cancelled.connect(lambda: fired.append("cancel"))
    editor._commit()
    editor._cancel()
    assert fired == ["commit"]
