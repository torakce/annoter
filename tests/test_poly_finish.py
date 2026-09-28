"""Ending a polyline / polygon (2026-09-28): a visible Finish bar,
right-click, Esc, Backspace for the last point, double-click."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import fitz
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.tools import Tool  # noqa: E402
from annoter.views.items.poly import PolylineItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def win(qapp, tmp_path: Path):
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=842, height=595)
    doc.save(str(path))
    doc.close()
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.open_path(path)
    w._view.set_zoom(1.0)
    w._view.centerOn(w._scene.page_item().mapToScene(QPointF(300, 200)))
    yield w
    w._on_close()
    w.close()


def _vp(win, x: float, y: float) -> QPoint:
    scene_pt = win._scene.page_item().mapToScene(QPointF(x, y))
    return win._view.mapFromScene(scene_pt)


def _click(win, x: float, y: float) -> None:
    QTest.mouseClick(win._view.viewport(), Qt.LeftButton, Qt.NoModifier, _vp(win, x, y))


def _polylines(win) -> list[PolylineItem]:
    page = win._scene.page_item()
    return [c for c in page.childItems() if isinstance(c, PolylineItem)]


def test_finish_bar_follows_the_draft(win) -> None:
    bar = win._draft_bar
    win._tool_controller.set_tool(Tool.POLYLINE)
    assert bar.isHidden()
    _click(win, 200, 150)
    assert not bar.isHidden()
    assert bar.count_label.text() == "1 point"
    assert not bar.finish_button.isEnabled()  # a polyline needs 2
    _click(win, 300, 180)
    _click(win, 380, 120)
    assert bar.count_label.text() == "3 points"
    assert bar.finish_button.isEnabled()
    bar.remove_button.click()
    assert bar.count_label.text() == "2 points"
    bar.finish_button.click()
    assert bar.isHidden()
    lines = _polylines(win)
    assert len(lines) == 1 and len(lines[0].points()) == 2


def test_cancel_button_drops_the_shape(win) -> None:
    win._tool_controller.set_tool(Tool.POLYLINE)
    _click(win, 200, 150)
    _click(win, 300, 180)
    win._draft_bar.cancel_button.click()
    assert win._draft_bar.isHidden()
    assert _polylines(win) == []


def test_right_click_finishes_without_a_menu(win) -> None:
    win._tool_controller.set_tool(Tool.POLYLINE)
    _click(win, 200, 150)
    _click(win, 300, 180)
    _click(win, 380, 120)
    win._show_context_menu(QPoint(0, 0), QPointF(380, 120))
    assert not win._scene.poly_draft_active()
    assert len(_polylines(win)[0].points()) == 3


def test_backspace_takes_back_the_last_point(win) -> None:
    win._tool_controller.set_tool(Tool.POLYLINE)
    _click(win, 200, 150)
    _click(win, 300, 180)
    win.act_delete.trigger()  # Delete / Backspace
    assert win._scene.poly_point_count() == 1
    assert win._scene.poly_draft_active()


def test_double_click_finishes_on_the_clicked_point(win) -> None:
    win._tool_controller.set_tool(Tool.POLYLINE)
    _click(win, 200, 150)
    _click(win, 300, 180)
    vp = win._view.viewport()
    at = _vp(win, 380, 120)
    # A real double-click: press / release, then the double-click event.
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(vp, Qt.LeftButton, Qt.NoModifier, at)
    assert not win._scene.poly_draft_active()
    lines = _polylines(win)
    assert len(lines) == 1
    assert len(lines[0].points()) == 3  # no doubled last point
