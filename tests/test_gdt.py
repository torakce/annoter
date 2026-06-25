"""Tests for the GD&T model, item, and ChangeGdtCommand round-trip."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.controllers.commands import ChangeGdtCommand  # noqa: E402
from annoter.model.gdt import (  # noqa: E402
    CHARACTERISTIC_META,
    Characteristic,
    DatumRef,
    Family,
    GdtRow,
    GdtState,
    by_family,
    enclosed,
)
from annoter.views.items.gdt import GdtAnnotationItem  # noqa: E402
from annoter.views.items.gdt_symbols import symbol_path  # noqa: E402
from annoter.views.pdf_scene import PdfScene  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _composite_state() -> GdtState:
    return GdtState(
        characteristic=Characteristic.POSITION,
        tolerance_prefix="Ø",
        tolerance_value="2",
        tolerance_modifier="P",
        datum_primary=DatumRef(["A"]),
        datum_secondary=DatumRef(["B", "B"]),
        datum_tertiary=DatumRef(["C"], modifier="M"),
        additional_rows=[
            GdtRow(
                characteristic=Characteristic.POSITION,
                tolerance_prefix="Ø",
                tolerance_value="0.5CZ",
            )
        ],
        upper_text="2x",
        lower_text="VALID FOR BOTH PARTS",
        aux_symbol=Characteristic.PARALLELISM,
        aux_text="A-B",
    )


def test_composite_state_roundtrip() -> None:
    state = _composite_state()
    assert GdtState.from_dict(state.to_dict()) == state
    assert len(state.all_rows()) == 2


def test_symbol_cell_merges_only_when_same(qapp) -> None:
    base = dict(tolerance_value="0.1")
    # Two rows, same characteristic -> a single merged symbol cell.
    same = GdtAnnotationItem(
        GdtState(
            characteristic=Characteristic.POSITION,
            tolerance_value="0.2",
            additional_rows=[
                GdtRow(characteristic=Characteristic.POSITION, **base)
            ],
        ),
        QPointF(0, 0),
    )
    assert len(same._symbol_draws) == 1
    # Two rows, different characteristics -> two symbol cells.
    diff = GdtAnnotationItem(
        GdtState(
            characteristic=Characteristic.POSITION,
            tolerance_value="0.2",
            additional_rows=[
                GdtRow(characteristic=Characteristic.PARALLELISM, **base)
            ],
        ),
        QPointF(0, 0),
    )
    assert len(diff._symbol_draws) == 2


def test_backward_compat_single_row_dict() -> None:
    """An FCF persisted before the composite rework (no rows/texts/aux)
    must reload as a one-row frame with empty extras."""
    old = {
        "characteristic": "position",
        "tolerance_prefix": "Ø",
        "tolerance_value": "0.1",
        "tolerance_modifier": "M",
        "datum_primary": {"letters": ["A"], "modifier": None},
        "datum_secondary": {"letters": [], "modifier": None},
        "datum_tertiary": {"letters": [], "modifier": None},
    }
    state = GdtState.from_dict(old)
    assert state.additional_rows == []
    assert state.upper_text == "" and state.lower_text == ""
    assert state.aux_symbol is None
    assert len(state.all_rows()) == 1
    assert state.tolerance_value == "0.1"


def test_composite_item_layout_grows(qapp) -> None:
    single = GdtAnnotationItem(
        GdtState(characteristic=Characteristic.POSITION, tolerance_value="2"),
        QPointF(0, 0),
    )
    composite = GdtAnnotationItem(_composite_state(), QPointF(0, 0))
    # Extra rows make it taller; upper/lower text and aux make it wider.
    assert composite.content_rect().height() > single.content_rect().height()
    assert composite.content_rect().width() > single.content_rect().width()
    # One merged main symbol cell (both rows share POSITION) + the
    # auxiliary symbol = 2 symbol draws.
    assert len(composite._symbol_draws) == 2


# ----------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------


def test_all_14_characteristics_have_metadata() -> None:
    assert len(CHARACTERISTIC_META) == 14
    families = {fam for fam, _ in CHARACTERISTIC_META.values()}
    assert families == set(Family)


def test_by_family_groups_correctly() -> None:
    groups = by_family()
    assert len(groups[Family.FORM]) == 4
    assert len(groups[Family.PROFILE]) == 2
    assert len(groups[Family.ORIENTATION]) == 3
    assert len(groups[Family.LOCATION]) == 3
    assert len(groups[Family.RUNOUT]) == 2


def test_enclosed_modifiers() -> None:
    # The five modifiers we expose should map to the Unicode enclosed forms.
    for letter in ("M", "L", "P", "E", "F"):
        glyph = enclosed(letter)
        assert len(glyph) == 1
        assert glyph != letter


def test_datumref_display() -> None:
    assert DatumRef([], None).display() == ""
    assert DatumRef(["A"]).display() == "A"
    assert DatumRef(["A", "B"]).display() == "A-B"
    d = DatumRef(["A"], modifier="M")
    out = d.display()
    assert out.startswith("A")
    assert out[-1] != "A"  # trailing modifier glyph


def test_gdtstate_tolerance_display() -> None:
    s = GdtState(
        characteristic=Characteristic.POSITION,
        tolerance_prefix="Ø",
        tolerance_value="0.1",
        tolerance_modifier="M",
    )
    text = s.tolerance_display()
    assert text.startswith("Ø")
    assert "0.1" in text


def test_gdtstate_tolerance_display_all_prefixes() -> None:
    for prefix in ("Ø", "R", "SØ", "SR"):
        s = GdtState(tolerance_prefix=prefix, tolerance_value="0.2")
        assert s.tolerance_display().startswith(prefix)


def test_gdtstate_from_dict_legacy_diameter_prefix() -> None:
    # PDFs saved before the prefix rework stored a boolean.
    s = GdtState.from_dict(
        {"characteristic": "position", "diameter_prefix": True}
    )
    assert s.tolerance_prefix == "Ø"
    s2 = GdtState.from_dict(
        {"characteristic": "position", "diameter_prefix": False}
    )
    assert s2.tolerance_prefix == ""


def test_gdtstate_datum_displays_drops_trailing_empty() -> None:
    s = GdtState(
        datum_primary=DatumRef(["A"]),
        datum_secondary=DatumRef([]),
        datum_tertiary=DatumRef([]),
    )
    assert s.datum_displays() == ["A"]


def test_gdtstate_roundtrip_dict() -> None:
    s = GdtState(
        characteristic=Characteristic.CYLINDRICITY,
        tolerance_prefix="SR",
        tolerance_value="0.02",
        tolerance_modifier=None,
        datum_primary=DatumRef(["A", "B"], modifier="M"),
    )
    s2 = GdtState.from_dict(s.to_dict())
    assert s2 == s


# ----------------------------------------------------------------------
# Symbol paths
# ----------------------------------------------------------------------


def test_every_characteristic_has_a_symbol_path(qapp) -> None:
    for c in Characteristic:
        path = symbol_path(c)
        assert not path.isEmpty(), c


# ----------------------------------------------------------------------
# Item
# ----------------------------------------------------------------------


def test_gdt_item_layout_grows_with_datums(qapp) -> None:
    base = GdtAnnotationItem(GdtState())
    base_w = base.boundingRect().width()

    expanded = GdtAnnotationItem(
        GdtState(
            datum_primary=DatumRef(["A"]),
            datum_secondary=DatumRef(["B", "C"], modifier="M"),
        )
    )
    assert expanded.boundingRect().width() > base_w


def test_gdt_item_setstate_updates_geometry(qapp) -> None:
    item = GdtAnnotationItem(GdtState())
    initial = item.boundingRect().width()
    item.set_state(
        GdtState(tolerance_value="0.0001 something_long_xxxxxxxx")
    )
    assert item.boundingRect().width() > initial


# ----------------------------------------------------------------------
# Command
# ----------------------------------------------------------------------


def test_change_gdt_command_roundtrip(qapp) -> None:
    scene = PdfScene()
    pm = QPixmap(200, 200)
    pm.fill()
    scene.set_page_pixmap(pm)
    page = scene.page_item()

    old = GdtState(characteristic=Characteristic.FLATNESS)
    new = GdtState(
        characteristic=Characteristic.PERPENDICULARITY,
        tolerance_value="0.05",
        datum_primary=DatumRef(["A"]),
    )
    item = GdtAnnotationItem(old, QPointF(10, 10))
    item.setParentItem(page)

    cmd = ChangeGdtCommand(item, old, new)
    cmd.redo()
    assert item.state().characteristic is Characteristic.PERPENDICULARITY
    assert item.state().tolerance_value == "0.05"
    cmd.undo()
    assert item.state().characteristic is Characteristic.FLATNESS
    assert item.state().tolerance_value == ""
