"""Tests for the Dimension model, item, and ChangeDimensionCommand round-trip."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.commands import ChangeDimensionCommand  # noqa: E402
from annoter.model.dimension import (  # noqa: E402
    DimensionPrefix,
    DimensionState,
    ToleranceMode,
)
from annoter.views.items.dimension import DimensionAnnotationItem  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ----------------------------------------------------------------------
# Model: DimensionState.to_dict / from_dict round-trip
# ----------------------------------------------------------------------


def test_state_roundtrip_none_mode() -> None:
    state = DimensionState(prefix=DimensionPrefix.DIAMETER, nominal="45.00")
    assert DimensionState.from_dict(state.to_dict()) == state


def test_state_roundtrip_symmetric_mode() -> None:
    state = DimensionState(
        prefix=DimensionPrefix.RADIUS,
        nominal="10.00",
        tolerance_mode=ToleranceMode.SYMMETRIC,
        tol_value="0.05",
    )
    assert DimensionState.from_dict(state.to_dict()) == state


def test_state_roundtrip_bilateral_mode() -> None:
    state = DimensionState(
        prefix=DimensionPrefix.SPHERICAL_DIAMETER,
        nominal="20.00",
        tolerance_mode=ToleranceMode.BILATERAL,
        tol_upper="0.10",
        tol_lower="0.05",
    )
    assert DimensionState.from_dict(state.to_dict()) == state


@pytest.mark.parametrize("prefix", list(DimensionPrefix))
def test_state_roundtrip_every_prefix(prefix: DimensionPrefix) -> None:
    state = DimensionState(prefix=prefix, nominal="7.5")
    assert DimensionState.from_dict(state.to_dict()) == state


def test_state_to_dict_omits_empty_optional_fields() -> None:
    state = DimensionState(nominal="5.0")
    data = state.to_dict()
    assert "prefix" not in data
    assert "tolerance_mode" not in data
    assert "tol_value" not in data
    assert "tol_upper" not in data
    assert "tol_lower" not in data


def test_state_is_empty() -> None:
    assert DimensionState().is_empty()
    assert not DimensionState(nominal="1").is_empty()
    assert DimensionState(nominal="   ").is_empty()


# ----------------------------------------------------------------------
# Item: layout for each tolerance mode
# ----------------------------------------------------------------------


def test_item_layout_none_mode(qapp) -> None:
    item = DimensionAnnotationItem(
        DimensionState(prefix=DimensionPrefix.DIAMETER, nominal="45.00"),
        QPointF(0, 0),
    )
    # Bare nominal value -> a single text draw (prefix + nominal).
    assert len(item._text_draws) == 1
    text = item._text_draws[0][1]
    assert text == "Ø45.00"
    rect = item.content_rect()
    assert rect.width() > 0
    assert rect.height() > 0


def test_item_layout_symmetric_mode(qapp) -> None:
    item = DimensionAnnotationItem(
        DimensionState(
            nominal="10.00",
            tolerance_mode=ToleranceMode.SYMMETRIC,
            tol_value="0.05",
        ),
        QPointF(0, 0),
    )
    # Nominal cell + a single "+/-value" cell.
    assert len(item._text_draws) == 2
    texts = [t for _, t, _, _ in item._text_draws]
    assert texts[0] == "10.00"
    assert texts[1] == "±0.05"
    # Wider than the bare-nominal layout since the tolerance cell adds
    # to the total width.
    bare = DimensionAnnotationItem(DimensionState(nominal="10.00"), QPointF(0, 0))
    assert item.content_rect().width() > bare.content_rect().width()


def test_item_layout_bilateral_mode(qapp) -> None:
    item = DimensionAnnotationItem(
        DimensionState(
            nominal="20.00",
            tolerance_mode=ToleranceMode.BILATERAL,
            tol_upper="0.10",
            tol_lower="0.05",
        ),
        QPointF(0, 0),
    )
    # Nominal cell + two stacked +/- lines.
    assert len(item._text_draws) == 3
    texts = [t for _, t, _, _ in item._text_draws]
    assert texts[0] == "20.00"
    assert texts[1] == "+0.10"
    assert texts[2] == "-0.05"
    # The two tolerance lines are vertically stacked (upper above lower).
    upper_rect = item._text_draws[1][0]
    lower_rect = item._text_draws[2][0]
    assert upper_rect.top() < lower_rect.top()
    # Bilateral tolerance lines use a smaller font than the nominal.
    nominal_font = item._text_draws[0][2]
    tol_font = item._text_draws[1][2]
    assert tol_font.pointSize() < nominal_font.pointSize()


def test_item_layout_bilateral_taller_than_symmetric(qapp) -> None:
    """Two stacked lines need more vertical room than a single one."""
    symmetric = DimensionAnnotationItem(
        DimensionState(
            nominal="20.00",
            tolerance_mode=ToleranceMode.SYMMETRIC,
            tol_value="0.05",
        ),
        QPointF(0, 0),
    )
    bilateral = DimensionAnnotationItem(
        DimensionState(
            nominal="20.00",
            tolerance_mode=ToleranceMode.BILATERAL,
            tol_upper="0.10",
            tol_lower="0.05",
        ),
        QPointF(0, 0),
    )
    assert bilateral.content_rect().height() >= symmetric.content_rect().height()


def test_item_set_state_updates_layout(qapp) -> None:
    item = DimensionAnnotationItem(DimensionState(nominal="1"), QPointF(0, 0))
    initial_width = item.content_rect().width()
    item.set_state(
        DimensionState(
            nominal="1",
            tolerance_mode=ToleranceMode.SYMMETRIC,
            tol_value="0.001",
        )
    )
    assert item.content_rect().width() > initial_width


# ----------------------------------------------------------------------
# Command
# ----------------------------------------------------------------------


def test_change_dimension_command_roundtrip(qapp) -> None:
    scene = PdfScene()
    pm = QPixmap(200, 200)
    pm.fill()
    scene.set_page_pixmap(pm)
    page = scene.page_item()

    old = DimensionState(nominal="10.00")
    new = DimensionState(
        prefix=DimensionPrefix.DIAMETER,
        nominal="20.00",
        tolerance_mode=ToleranceMode.SYMMETRIC,
        tol_value="0.05",
    )
    item = DimensionAnnotationItem(old, QPointF(10, 10))
    item.setParentItem(page)

    cmd = ChangeDimensionCommand(item, old, new)
    cmd.redo()
    assert item.state().nominal == "20.00"
    assert item.state().tolerance_mode is ToleranceMode.SYMMETRIC
    cmd.undo()
    assert item.state().nominal == "10.00"
    assert item.state().tolerance_mode is ToleranceMode.NONE
