"""Contextual edit bar shown while a text annotation is being edited.

The bar is what replaces a standalone dimension tool: inserting a
symbol or a tolerance is something you do while writing the text that
carries it. These tests drive the widget directly (like
test_gdt_editor.py) and then end-to-end through MainWindow.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import fitz
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from annoter.controllers.tools import Tool  # noqa: E402
from annoter.model.styles import TextAlign, TextBorder  # noqa: E402
from annoter.model.tolerance import Tolerance, ToleranceMode  # noqa: E402
from annoter.views.edit_toolbar import (  # noqa: E402
    FONT_SIZE_LADDER,
    EditToolbar,
    _ToleranceForm,
)
from annoter.views.items.text import TextAnnotationItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    QCoreApplication.setOrganizationName("AnnoterTest")
    QCoreApplication.setApplicationName("AnnoterTest")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def host(qapp):
    w = QWidget()
    yield w
    w.deleteLater()


@pytest.fixture()
def text_item(qapp):
    return TextAnnotationItem(QPointF(10, 10), "Percer 12")


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=842, height=595)
    doc.save(str(path))
    doc.close()
    return path


def _buttons(toolbar: EditToolbar) -> list[str]:
    return [
        toolbar._lay.itemAt(i).widget().text()
        for i in range(toolbar._lay.count())
        if hasattr(toolbar._lay.itemAt(i).widget(), "text")
    ]


# ----------------------------------------------------------------------
# widget in isolation
# ----------------------------------------------------------------------
def test_context_builds_text_controls(host, text_item) -> None:
    bar = EditToolbar(host)
    bar.set_context(text_item)
    labels = _buttons(bar)
    for expected in ("B", "I", "Align", "Border", "Symbol", "Tolerance"):
        assert expected in labels, f"{expected} missing from {labels}"
    assert bar.item() is text_item


def test_context_none_hides_and_forgets_item(host, text_item) -> None:
    bar = EditToolbar(host)
    bar.set_context(text_item)
    bar.set_context(None)
    assert bar.item() is None
    assert bar.isHidden()


def test_unsupported_item_kind_hides(host) -> None:
    bar = EditToolbar(host)
    bar.set_context(object())
    assert bar.item() is None
    assert bar.isHidden()


def test_font_size_steps_along_the_ladder(host, text_item) -> None:
    text_item.set_font_size(12)
    bar = EditToolbar(host)
    bar.set_context(text_item)
    seen: list[int] = []
    bar.fontSizeChanged.connect(seen.append)
    bar._step_font_size(1)
    bar._step_font_size(1)
    bar._step_font_size(-1)
    idx = FONT_SIZE_LADDER.index(12)
    assert seen == [
        FONT_SIZE_LADDER[idx + 1],
        FONT_SIZE_LADDER[idx + 2],
        FONT_SIZE_LADDER[idx + 1],
    ]


def test_font_size_clamps_at_ladder_ends(host, text_item) -> None:
    text_item.set_font_size(FONT_SIZE_LADDER[-1])
    bar = EditToolbar(host)
    bar.set_context(text_item)
    seen: list[int] = []
    bar.fontSizeChanged.connect(seen.append)
    bar._step_font_size(1)
    assert seen == []  # already at the top, no spurious emission


def test_bold_and_italic_emit(host, text_item) -> None:
    bar = EditToolbar(host)
    bar.set_context(text_item)
    bolds: list[bool] = []
    italics: list[bool] = []
    bar.boldToggled.connect(bolds.append)
    bar.italicToggled.connect(italics.append)
    for i in range(bar._lay.count()):
        w = bar._lay.itemAt(i).widget()
        if getattr(w, "text", None) and w.text() == "B":
            w.toggle()
        if getattr(w, "text", None) and w.text() == "I":
            w.toggle()
    assert bolds == [True]
    assert italics == [True]


def test_toggles_reflect_current_item_state(host, text_item) -> None:
    text_item.set_bold(True)
    bar = EditToolbar(host)
    bar.set_context(text_item)
    checked = {
        w.text(): w.isChecked()
        for i in range(bar._lay.count())
        if (w := bar._lay.itemAt(i).widget()) is not None
        and getattr(w, "isCheckable", None)
        and w.isCheckable()
    }
    assert checked.get("B") is True
    assert checked.get("I") is False


def _menu_named(bar: EditToolbar, name: str):
    for i in range(bar._lay.count()):
        w = bar._lay.itemAt(i).widget()
        if getattr(w, "text", None) and w.text() == name:
            return w.menu()
    raise AssertionError(f"no {name} menu")


def test_align_menu_emits(host, text_item) -> None:
    bar = EditToolbar(host)
    bar.set_context(text_item)
    picked: list[object] = []
    bar.alignPicked.connect(picked.append)
    _menu_named(bar, "Align").actions()[1].trigger()
    assert picked == [TextAlign.CENTER]


def test_border_menu_emits(host, text_item) -> None:
    bar = EditToolbar(host)
    bar.set_context(text_item)
    picked: list[object] = []
    bar.borderPicked.connect(picked.append)
    menu = _menu_named(bar, "Border")
    menu.actions()[-1].trigger()
    assert picked and isinstance(picked[0], TextBorder)


def test_symbol_menu_emits_the_character(host, text_item) -> None:
    bar = EditToolbar(host)
    bar.set_context(text_item)
    picked: list[str] = []
    bar.symbolPicked.connect(picked.append)
    # actions()[0] is the disabled section header.
    _menu_named(bar, "Symbol").actions()[1].trigger()
    assert picked == ["Ø"]  # diameter


def test_tolerance_form_builds_symmetric() -> None:
    form = _ToleranceForm(ToleranceMode.SYMMETRIC)
    form._edits[0].setText("0.05")
    tol = form.tolerance()
    assert tol == Tolerance(mode=ToleranceMode.SYMMETRIC, value="0.05")
    assert tol.plain() == "±0.05"


def test_tolerance_form_builds_bilateral() -> None:
    form = _ToleranceForm(ToleranceMode.BILATERAL)
    form._edits[0].setText("0.10")
    form._edits[1].setText("0.05")
    tol = form.tolerance()
    assert tol == Tolerance(
        mode=ToleranceMode.BILATERAL, upper="0.10", lower="0.05"
    )
    assert tol.plain() == "+0.10/-0.05"


def test_empty_tolerance_form_does_not_emit() -> None:
    form = _ToleranceForm(ToleranceMode.BILATERAL)
    seen: list[object] = []
    form.accepted.connect(seen.append)
    form._accept()
    assert seen == []


def test_tolerance_picked_propagates_and_resets_form(host, text_item) -> None:
    bar = EditToolbar(host)
    bar.set_context(text_item)
    picked: list[object] = []
    bar.tolerancePicked.connect(picked.append)
    form = bar._tolerance_forms[ToleranceMode.BILATERAL]
    form._edits[0].setText("0.10")
    form._edits[1].setText("0.05")
    form._accept()
    assert picked == [
        Tolerance(mode=ToleranceMode.BILATERAL, upper="0.10", lower="0.05")
    ]
    # Reset, so the next insertion does not silently reuse the values.
    assert form._edits[0].text() == ""


# ----------------------------------------------------------------------
# end-to-end through MainWindow
# ----------------------------------------------------------------------
def _open_with_text(qapp, sample_pdf: Path) -> tuple[MainWindow, object]:
    win = MainWindow()
    win.open_path(sample_pdf)
    win._tool_controller.set_tool(Tool.TEXT)
    win._scene._spawn_text_at(QPointF(100, 100))
    qapp.processEvents()
    return win, win._text_edit_item


def _focus_out(item, reason) -> None:
    """Deliver a focus-out to the inner editor.

    The window is never shown in these tests, so the scene is inactive
    and Qt delivers no focus events of its own -- `clearFocus()` is a
    silent no-op. Posting the event is the only way to exercise the
    handler that decides whether an edit session ends.
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    item._inner.focusOutEvent(QFocusEvent(QEvent.FocusOut, reason))


def test_editing_a_text_raises_the_bar(qapp, sample_pdf: Path) -> None:
    win, item = _open_with_text(qapp, sample_pdf)
    try:
        assert item is not None, "edit session did not register"
        assert win._edit_toolbar.item() is item
        assert not win._edit_toolbar.isHidden()
    finally:
        win._on_close()
        win.close()


def test_finishing_the_edit_hides_the_bar(qapp, sample_pdf: Path) -> None:
    win, item = _open_with_text(qapp, sample_pdf)
    try:
        item.insert_symbol("abc")  # non-empty, so it is not rolled back
        _focus_out(item, Qt.OtherFocusReason)
        qapp.processEvents()
        assert win._text_edit_item is None
        assert win._edit_toolbar.isHidden()
    finally:
        win._on_close()
        win.close()


def test_inserting_a_tolerance_lands_a_run(qapp, sample_pdf: Path) -> None:
    win, item = _open_with_text(qapp, sample_pdf)
    try:
        item.insert_symbol("Ø12")
        win._edit_toolbar.tolerancePicked.emit(
            Tolerance(mode=ToleranceMode.BILATERAL, upper="0.10", lower="0.05")
        )
        qapp.processEvents()
        assert item.has_tolerance_runs()
        assert item.has_stacked_runs()
        assert item.text() == "Ø12+0.10/-0.05"
    finally:
        win._on_close()
        win.close()


def test_inserting_keeps_the_edit_session_alive(qapp, sample_pdf: Path) -> None:
    """The whole point of the focus handling: using the bar must not end
    the edit session, or a still-empty new annotation gets rolled back."""
    win, item = _open_with_text(qapp, sample_pdf)
    try:
        win._edit_toolbar.symbolPicked.emit("Ø")
        qapp.processEvents()
        assert item.is_editing(), "edit session ended when using the bar"
        assert win._text_edit_item is item
        assert not win._edit_toolbar.isHidden()
        assert item.scene() is not None, "annotation was rolled back"
    finally:
        win._on_close()
        win.close()


def test_popup_focus_loss_does_not_end_the_session(
    qapp, sample_pdf: Path
) -> None:
    """A menu popup takes keyboard focus off the view; that must read as
    'a popup is open', not 'the user finished editing'."""
    win, item = _open_with_text(qapp, sample_pdf)
    try:
        _focus_out(item, Qt.PopupFocusReason)
        qapp.processEvents()
        assert item.is_editing()
        assert win._text_edit_item is item
    finally:
        win._on_close()
        win.close()


def test_font_size_from_the_bar_is_undoable(qapp, sample_pdf: Path) -> None:
    win, item = _open_with_text(qapp, sample_pdf)
    try:
        item.insert_symbol("abc")
        before = item.font_size()
        win._edit_toolbar.fontSizeChanged.emit(28)
        qapp.processEvents()
        assert item.font_size() == 28
        stack = win._undo_group.activeStack()
        stack.undo()
        assert item.font_size() == before
    finally:
        win._on_close()
        win.close()
