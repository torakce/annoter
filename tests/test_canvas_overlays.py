"""UI redesign, Lot D: tool hint chip, page / zoom pill and toast over
the canvas; the status bar is gone."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QStatusBar  # noqa: E402

from annoter.controllers.tools import LineKind, Tool  # noqa: E402
from annoter.views.canvas_overlays import (  # noqa: E402
    TOOL_HINTS,
    ZOOM_PRESETS,
    hint_for,
)
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.tool_rail import TOOL_LABELS  # noqa: E402


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


@pytest.fixture
def win(qapp, three_page_pdf: Path):
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.open_path(three_page_pdf)
    qapp.processEvents()
    yield w
    w.act_show_hints.setChecked(True)  # restore the persisted default
    w.close()


# ----------------------------------------------------------------------
# hints
# ----------------------------------------------------------------------
def test_every_rail_tool_has_a_hint(qapp) -> None:
    for tool, _label in TOOL_LABELS:
        hint = hint_for(tool, LineKind.ARROW, "APPROVED")
        assert hint is not None, tool
        assert hint[0] and hint[1]


def test_line_and_stamp_hints_follow_the_variant(qapp) -> None:
    assert hint_for(Tool.ARROW, LineKind.LINE)[0] == "Line"
    assert hint_for(Tool.ARROW, LineKind.DOUBLE)[0] == "Double arrow"
    assert "REJECTED" in hint_for(Tool.STAMP, LineKind.ARROW, "REJECTED")[1]
    assert Tool.FORMAT_PAINTER in TOOL_HINTS


def test_chip_follows_the_active_tool(win) -> None:
    chip = win._tool_hint
    assert chip.isVisible()
    assert chip.name_label.text() == "Select"
    win._tool_controller.set_tool(Tool.ELLIPSE)
    assert chip.name_label.text() == "Ellipse"
    win._tool_controller.set_tool(Tool.ARROW)
    win._tool_controller.set_line_kind(LineKind.LINE)
    assert chip.name_label.text() == "Line"
    win._tool_controller.set_tool(Tool.STAMP)
    win._tool_controller.set_stamp_preset("REJECTED", QColor("#C62828"))
    assert "REJECTED" in chip.hint_label.text()


def test_chip_can_be_turned_off_from_the_view_menu(win) -> None:
    chip = win._tool_hint
    view_menu = next(
        m
        for m in win._main_menu.findChildren(QMenu)
        if m.title().replace("&", "") == "View"
    )
    assert win.act_show_hints in view_menu.actions()
    win.act_show_hints.setChecked(False)
    assert not chip.isVisible()
    win._tool_controller.set_tool(Tool.RECTANGLE)
    assert not chip.isVisible()  # stays off across tool changes
    win.act_show_hints.setChecked(True)
    assert chip.isVisible()


def test_chip_sits_top_center_of_the_canvas(win, qapp) -> None:
    chip = win._tool_hint
    vp = win._view.viewport()
    center = chip.geometry().center().x()
    assert abs(center - vp.width() // 2) <= 2
    assert chip.y() < 40


# ----------------------------------------------------------------------
# page / zoom pill
# ----------------------------------------------------------------------
def test_no_status_bar_anymore(win) -> None:
    assert win.findChild(QStatusBar) is None


def test_pill_tracks_page_and_navigates(win) -> None:
    pill = win._nav_pill
    assert pill.page_edit.text() == "1"
    assert pill.page_total.text() == "of 3"
    assert not pill.prev_button.isEnabled() or win._page_index == 0
    pill.next_button.click()
    assert win._page_index == 1
    assert pill.page_edit.text() == "2"
    pill.prev_button.click()
    assert win._page_index == 0
    # Out-of-range input is ignored.
    pill.page_edit.setText("9")
    pill.page_edit.returnPressed.emit()
    assert win._page_index == 0


def test_pill_zoom_buttons_and_presets(win) -> None:
    pill = win._nav_pill
    before = win._view.zoom()
    pill.zoom_in_button.click()
    assert win._view.zoom() > before
    pill.zoom_out_button.click()
    assert win._view.zoom() == pytest.approx(before)
    pill.zoomRequested.emit(2.0)
    assert win._view.zoom() == pytest.approx(2.0)
    assert pill.zoom_button.text() == "200 %"
    pill.actual_button.click()
    assert win._view.zoom() == pytest.approx(1.0)
    labels = [a.text() for a in pill.zoom_button.menu().actions()]
    for _f, label in ZOOM_PRESETS:
        assert label in labels
    assert win.act_zoom_fit in pill.zoom_button.menu().actions()


def test_pill_buttons_mirror_their_actions(win) -> None:
    pill = win._nav_pill
    assert pill.fit_button.text() == "Fit"
    assert pill.actual_button.text() == "1:1"
    assert "Ctrl+0" in pill.fit_button.toolTip()
    win.act_rotate_cw.setEnabled(False)
    assert not pill.rotate_button.isEnabled()
    win.act_rotate_cw.setEnabled(True)
    assert pill.rotate_button.isEnabled()


def test_pill_sits_bottom_center(win, qapp) -> None:
    pill = win._nav_pill
    vp = win._view.viewport()
    assert pill.isVisible()
    assert abs(pill.geometry().center().x() - vp.width() // 2) <= 2
    assert vp.height() - pill.geometry().bottom() < 30


# ----------------------------------------------------------------------
# toast (former status-bar messages)
# ----------------------------------------------------------------------
def test_messages_go_to_the_toast(win, qapp) -> None:
    win._show_message("Exported 3 file(s)", 2000)
    assert win._toast.isVisible()
    assert win._toast.text() == "Exported 3 file(s)"
    # Above the pill, not over it.
    assert win._toast.geometry().bottom() < win._nav_pill.geometry().top()


def test_format_painter_without_selection_explains_itself(win) -> None:
    win.act_format_painter.setChecked(True)
    assert not win.act_format_painter.isChecked()
    assert "Select exactly one annotation" in win._toast.text()


def test_overlays_do_not_steal_canvas_clicks(win) -> None:
    assert win._tool_hint.testAttribute(Qt.WA_TransparentForMouseEvents)
    assert win._toast.testAttribute(Qt.WA_TransparentForMouseEvents)
