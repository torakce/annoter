"""UI redesign, Lot C: vertical tool rail, variant flyout, line kinds
and stamp presets chosen before placement."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDockWidget,
    QGraphicsSceneMouseEvent,
    QInputDialog,
)

from annoter.controllers.tools import (  # noqa: E402
    LineKind,
    Tool,
    ToolController,
)
from annoter.model.styles import EndStyle  # noqa: E402
from annoter.views.items.lines import ArrowItem  # noqa: E402
from annoter.views.items.stamp import StampItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402
from annoter.views.tool_rail import TOOL_LABELS, ToolRail  # noqa: E402


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
    tc = ToolController()
    stack = QUndoStack()
    sc.set_tool_controller(tc)
    sc.set_undo_stack(stack)
    yield sc, tc, stack
    sc.clear_page()


def _ev(etype, pos: QPointF) -> QGraphicsSceneMouseEvent:
    ev = QGraphicsSceneMouseEvent(etype)
    ev.setScenePos(pos)
    ev.setButton(Qt.LeftButton)
    ev.setButtons(Qt.LeftButton)
    ev.setModifiers(Qt.NoModifier)
    ev.setScreenPos(QPoint(int(pos.x()), int(pos.y())))
    return ev


def _drag(sc: PdfScene, a: QPointF, b: QPointF) -> None:
    sc.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, a))
    sc.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove, b))
    sc.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, b))


def _children(sc: PdfScene, cls) -> list:
    return [c for c in sc.page_item().childItems() if isinstance(c, cls)]


# ----------------------------------------------------------------------
# controller
# ----------------------------------------------------------------------
def test_controller_line_kind_and_stamp_preset_signals(qapp) -> None:
    tc = ToolController()
    assert tc.line_kind() is LineKind.ARROW
    kinds: list[LineKind] = []
    tc.lineKindChanged.connect(kinds.append)
    tc.set_line_kind(LineKind.LINE)
    tc.set_line_kind(LineKind.LINE)  # no-op, no second signal
    assert kinds == [LineKind.LINE]

    text, color = tc.stamp_preset()
    assert text == "APPROVED" and color == QColor("#2E7D32")
    seen: list[str] = []
    tc.stampPresetChanged.connect(lambda t, _c: seen.append(t))
    tc.set_stamp_preset("REJECTED", QColor("#C62828"))
    assert seen == ["REJECTED"]
    assert tc.stamp_preset()[1] == QColor("#C62828")


# ----------------------------------------------------------------------
# rail
# ----------------------------------------------------------------------
def test_rail_has_one_button_per_tool_in_order(qapp) -> None:
    rail = ToolRail(ToolController())
    assert rail.tools() == [t for t, _label in TOOL_LABELS]
    assert Tool.GDT in rail.tools() and Tool.DIMENSION in rail.tools()
    for tool, label in TOOL_LABELS:
        btn = rail.button(tool)
        assert btn.toolTip() == label
        assert btn.accessibleName() == label
        assert not btn.icon().isNull()


def test_rail_and_controller_stay_in_sync(qapp) -> None:
    tc = ToolController()
    rail = ToolRail(tc)
    rail.button(Tool.ELLIPSE).click()
    assert tc.tool() is Tool.ELLIPSE
    tc.set_tool(Tool.TEXT)
    assert rail.button(Tool.TEXT).isChecked()
    assert not rail.button(Tool.ELLIPSE).isChecked()
    # Action modes have no button: nothing looks active.
    tc.set_tool(Tool.FORMAT_PAINTER)
    assert not any(rail.button(t).isChecked() for t in rail.tools())


def test_line_button_icon_follows_the_line_kind(qapp) -> None:
    tc = ToolController()
    rail = ToolRail(tc)
    assert rail._glyphs[Tool.ARROW] == "arrow"
    before = rail.button(Tool.ARROW).icon().cacheKey()
    tc.set_line_kind(LineKind.LINE)
    assert rail._glyphs[Tool.ARROW] == "line"
    assert rail.button(Tool.ARROW).icon().cacheKey() != before


def test_tools_panel_dock_is_gone(qapp) -> None:
    win = MainWindow()
    try:
        names = [d.objectName() for d in win.findChildren(QDockWidget)]
        assert "ToolPaletteDock" not in names
        assert win.toolBarArea(win._tool_rail) == Qt.LeftToolBarArea
    finally:
        win.close()


def test_rail_is_disabled_without_a_document(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        assert not win._tool_rail.isEnabled()
        win.open_path(sample_pdf)
        assert win._tool_rail.isEnabled()
        win._on_close()
        assert not win._tool_rail.isEnabled()
    finally:
        win.close()


# ----------------------------------------------------------------------
# flyout
# ----------------------------------------------------------------------
def test_flyout_shows_only_for_variant_tools(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.show()
        win.open_path(sample_pdf)
        fly = win._tool_flyout
        tc = win._tool_controller
        tc.set_tool(Tool.ARROW)
        assert fly.isVisible() and fly.tool() is Tool.ARROW
        tc.set_tool(Tool.RECTANGLE)
        assert not fly.isVisible()
        tc.set_tool(Tool.STAMP)
        assert fly.isVisible() and fly.tool() is Tool.STAMP
        tc.set_tool(Tool.SELECT)
        assert not fly.isVisible()
    finally:
        win.close()


def test_flyout_choice_reaches_the_controller(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        win.show()
        win.open_path(sample_pdf)
        fly = win._tool_flyout
        tc = win._tool_controller
        tc.set_tool(Tool.ARROW)
        fly._line_buttons[LineKind.DOUBLE].click()
        assert tc.line_kind() is LineKind.DOUBLE
        assert fly._line_buttons[LineKind.DOUBLE].isChecked()
        assert not fly._line_buttons[LineKind.ARROW].isChecked()

        tc.set_tool(Tool.STAMP)
        fly._stamp_buttons[1].click()  # REJECTED
        assert tc.stamp_preset()[0] == "REJECTED"
        assert fly._stamp_buttons[1].isChecked()
    finally:
        win.close()


def test_custom_stamp_asks_for_text(
    qapp, sample_pdf: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: ("  CHECKED BY QA ", True)),
    )
    win = MainWindow()
    try:
        win.show()
        win.open_path(sample_pdf)
        win._tool_controller.set_tool(Tool.STAMP)
        win._tool_flyout._custom_button.click()
        assert win._tool_controller.stamp_preset()[0] == "CHECKED BY QA"
        assert win._tool_flyout._custom_button.isChecked()
        assert "CHECKED BY QA" in (
            win._tool_flyout._custom_button.title_label.text()
        )
    finally:
        win.close()


# ----------------------------------------------------------------------
# drawing with the chosen variant
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind, start, end",
    [
        (LineKind.LINE, EndStyle.NONE, EndStyle.NONE),
        (LineKind.ARROW, EndStyle.NONE, EndStyle.OPEN_ARROW),
        (LineKind.DOUBLE, EndStyle.OPEN_ARROW, EndStyle.OPEN_ARROW),
    ],
)
def test_line_tool_draws_the_chosen_kind(scene, kind, start, end) -> None:
    sc, tc, stack = scene
    tc.set_line_kind(kind)
    tc.set_tool(Tool.ARROW)
    _drag(sc, QPointF(50, 50), QPointF(200, 120))
    arrows = _children(sc, ArrowItem)
    assert len(arrows) == 1
    assert arrows[0].start_end() is start
    assert arrows[0].end_end() is end
    assert stack.count() == 1


def test_stamp_tool_places_the_chosen_preset(scene) -> None:
    sc, tc, stack = scene
    tc.set_stamp_preset("BON POUR EXÉCUTION", QColor("#1565C0"))
    tc.set_tool(Tool.STAMP)
    p = QPointF(120, 90)
    sc.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p))
    sc.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease, p))
    stamps = _children(sc, StampItem)
    assert len(stamps) == 1
    assert stamps[0].text() == "BON POUR EXÉCUTION"
    assert stamps[0].color() == QColor("#1565C0")
    assert stack.count() == 1
