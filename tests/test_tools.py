"""Tests for ToolController: state changes and signal emissions."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from annoter.controllers.tools import Tool, ToolController  # noqa: E402


@pytest.fixture(scope="module")
def qcore():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def test_default_state(qcore) -> None:
    tc = ToolController()
    assert tc.tool() is Tool.SELECT
    assert tc.color().isValid()
    assert tc.stroke() > 0


def test_set_tool_emits_signal(qcore) -> None:
    tc = ToolController()
    seen: list[Tool] = []
    tc.toolChanged.connect(seen.append)
    tc.set_tool(Tool.RECTANGLE)
    tc.set_tool(Tool.RECTANGLE)  # no re-emit on idempotent set
    tc.set_tool(Tool.LINE)
    assert seen == [Tool.RECTANGLE, Tool.LINE]
    assert tc.tool() is Tool.LINE


def test_set_color_emits_signal(qcore) -> None:
    tc = ToolController()
    seen: list[QColor] = []
    tc.colorChanged.connect(seen.append)
    tc.set_color(QColor("#112233"))
    tc.set_color(QColor("#112233"))  # idempotent
    tc.set_color(QColor("#445566"))
    assert len(seen) == 2
    assert seen[0].name() == "#112233"
    assert seen[1].name() == "#445566"
    assert tc.color().name() == "#445566"


def test_set_stroke_emits_signal(qcore) -> None:
    tc = ToolController()
    seen: list[float] = []
    tc.strokeChanged.connect(seen.append)
    tc.set_stroke(3.5)
    tc.set_stroke(3.5)
    tc.set_stroke(1.0)
    assert seen == [3.5, 1.0]
    assert tc.stroke() == 1.0
