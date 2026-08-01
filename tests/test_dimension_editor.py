"""Tests for the in-place Dimension editor (DimensionInlineEditor)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from annoter.model.dimension import (  # noqa: E402
    DimensionPrefix,
    DimensionState,
    ToleranceMode,
)
from annoter.views.dimension_editor import DimensionInlineEditor  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def host(qapp):
    w = QWidget()
    yield w
    w.deleteLater()


def _sample_state(mode: ToleranceMode = ToleranceMode.NONE) -> DimensionState:
    if mode is ToleranceMode.NONE:
        return DimensionState(
            prefix=DimensionPrefix.DIAMETER,
            nominal="45.00",
            tolerance_mode=ToleranceMode.NONE,
        )
    if mode is ToleranceMode.SYMMETRIC:
        return DimensionState(
            prefix=DimensionPrefix.NONE,
            nominal="30.00",
            tolerance_mode=ToleranceMode.SYMMETRIC,
            tol_value="0.05",
        )
    return DimensionState(
        prefix=DimensionPrefix.RADIUS,
        nominal="12.50",
        tolerance_mode=ToleranceMode.BILATERAL,
        tol_upper="0.10",
        tol_lower="0.05",
    )


# ----------------------------------------------------------------------
# initial round-trip
# ----------------------------------------------------------------------
def test_initial_state_roundtrip_none(host) -> None:
    state = _sample_state(ToleranceMode.NONE)
    editor = DimensionInlineEditor(state, host)
    assert editor.current_state() == state


def test_initial_state_roundtrip_symmetric(host) -> None:
    state = _sample_state(ToleranceMode.SYMMETRIC)
    editor = DimensionInlineEditor(state, host)
    assert editor.current_state() == state


def test_initial_state_roundtrip_bilateral(host) -> None:
    state = _sample_state(ToleranceMode.BILATERAL)
    editor = DimensionInlineEditor(state, host)
    assert editor.current_state() == state


# ----------------------------------------------------------------------
# field edits
# ----------------------------------------------------------------------
def test_nominal_edit_reflected_in_state(host) -> None:
    editor = DimensionInlineEditor(DimensionState(), host)
    editor._nominal_edit.setText("99.9")
    assert editor.current_state().nominal == "99.9"


def test_mode_switch_shows_hides_fields(host) -> None:
    host.show()
    editor = DimensionInlineEditor(DimensionState(), host)
    editor.show()

    # NONE: no tolerance fields visible.
    assert not editor._tol_value_edit.isVisible()
    assert not editor._tol_upper_edit.isVisible()
    assert not editor._tol_lower_edit.isVisible()

    editor._set_mode(ToleranceMode.SYMMETRIC)
    assert editor.current_state().tolerance_mode is ToleranceMode.SYMMETRIC
    assert editor._tol_value_edit.isVisible()
    assert not editor._tol_upper_edit.isVisible()
    assert not editor._tol_lower_edit.isVisible()

    editor._set_mode(ToleranceMode.BILATERAL)
    assert editor.current_state().tolerance_mode is ToleranceMode.BILATERAL
    assert not editor._tol_value_edit.isVisible()
    assert editor._tol_upper_edit.isVisible()
    assert editor._tol_lower_edit.isVisible()

    editor._set_mode(ToleranceMode.NONE)
    assert editor.current_state().tolerance_mode is ToleranceMode.NONE
    assert not editor._tol_value_edit.isVisible()
    assert not editor._tol_upper_edit.isVisible()
    assert not editor._tol_lower_edit.isVisible()


def test_symmetric_tol_value_edit(host) -> None:
    editor = DimensionInlineEditor(_sample_state(ToleranceMode.SYMMETRIC), host)
    editor._tol_value_edit.setText("0.20")
    assert editor.current_state().tol_value == "0.20"


def test_bilateral_tol_edits(host) -> None:
    editor = DimensionInlineEditor(_sample_state(ToleranceMode.BILATERAL), host)
    editor._tol_upper_edit.setText("0.30")
    editor._tol_lower_edit.setText("0.15")
    out = editor.current_state()
    assert out.tol_upper == "0.30"
    assert out.tol_lower == "0.15"


def test_prefix_menu_updates_state(host) -> None:
    editor = DimensionInlineEditor(DimensionState(), host)
    for prefix in DimensionPrefix:
        editor._set_prefix(prefix)
        assert editor.current_state().prefix is prefix
    # The button label falls back to the em dash when the prefix is
    # empty (plus the drop-down arrow).
    editor._set_prefix(DimensionPrefix.NONE)
    assert editor._prefix_btn.text().startswith("—")


# ----------------------------------------------------------------------
# stateEdited signal
# ----------------------------------------------------------------------
def test_state_edited_emitted_live(host) -> None:
    editor = DimensionInlineEditor(DimensionState(), host)
    slot = Mock()
    editor.stateEdited.connect(slot)
    editor._nominal_edit.setText("10.0")
    assert slot.called
    last_state = slot.call_args[0][0]
    assert last_state.nominal == "10.0"


# ----------------------------------------------------------------------
# commit / cancel
# ----------------------------------------------------------------------
def test_enter_commits_once(host) -> None:
    editor = DimensionInlineEditor(DimensionState(), host)
    commits: list[int] = []
    editor.committed.connect(lambda: commits.append(1))
    editor._nominal_edit.returnPressed.emit()
    # second Enter is a no-op
    editor._nominal_edit.returnPressed.emit()
    assert len(commits) == 1


def test_escape_cancels(host) -> None:
    editor = DimensionInlineEditor(DimensionState(), host)
    cancels: list[int] = []
    commits: list[int] = []
    editor.cancelled.connect(lambda: cancels.append(1))
    editor.committed.connect(lambda: commits.append(1))
    event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    editor.keyPressEvent(event)
    assert cancels == [1]
    assert commits == []


def test_cancel_after_commit_is_noop(host) -> None:
    editor = DimensionInlineEditor(DimensionState(), host)
    fired: list[str] = []
    editor.committed.connect(lambda: fired.append("commit"))
    editor.cancelled.connect(lambda: fired.append("cancel"))
    editor._commit()
    editor._cancel()
    assert fired == ["commit"]


def test_commit_after_cancel_is_noop(host) -> None:
    editor = DimensionInlineEditor(DimensionState(), host)
    fired: list[str] = []
    editor.committed.connect(lambda: fired.append("commit"))
    editor.cancelled.connect(lambda: fired.append("cancel"))
    editor._cancel()
    editor._commit()
    assert fired == ["cancel"]


def test_clicking_outside_does_not_commit(qapp, host) -> None:
    host.show()
    editor = DimensionInlineEditor(DimensionState(), host)
    editor.show()
    commits: list[int] = []
    editor.committed.connect(lambda: commits.append(1))
    outside = QWidget(host)
    outside.setFocusPolicy(Qt.StrongFocus)
    outside.show()
    outside.setFocus()
    qapp.processEvents()
    assert commits == []
