"""Tests for the GD&T frame builder (GdtFrameBuilder, UI redesign Lot I)
and its plain-language read-back (model.gdt_readback)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from annoter.controllers.tools import Tool  # noqa: E402
from annoter.model.gdt import (  # noqa: E402
    Characteristic,
    DatumRef,
    GdtRow,
    GdtState,
)
from annoter.model.gdt_readback import (  # noqa: E402
    describe_row,
    needs_datum,
    row_warning,
    takes_datums,
)
from annoter.services.theme import Theme  # noqa: E402
from annoter.services.tokens import DARK, LIGHT  # noqa: E402
from annoter.views.gdt_editor import (  # noqa: E402
    GdtFrameBuilder,
    render_frame_preview,
)
from annoter.views.items.gdt import GdtAnnotationItem  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def host(qapp):
    w = QWidget()
    w.resize(900, 1000)
    yield w
    w.deleteLater()


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page(width=842, height=595)
    doc.save(str(path))
    doc.close()
    return path


def _sample_state() -> GdtState:
    return GdtState(
        characteristic=Characteristic.POSITION,
        tolerance_prefix="SR",
        tolerance_value="0.1",
        tolerance_modifier="M",
        datum_primary=DatumRef(["A"]),
        datum_secondary=DatumRef(["B", "C"], modifier="L"),
        datum_tertiary=DatumRef([]),
        additional_rows=[
            GdtRow(
                characteristic=Characteristic.PARALLELISM,
                tolerance_value="0.02",
                datum_primary=DatumRef(["A"]),
            )
        ],
        upper_runs=[{"t": "4X"}],
        lower_runs=[{"t": "BOTH SIDES"}],
        aux_symbol=Characteristic.SYMMETRY,
        aux_text="A-B",
    )


FRAME_COLOR = QColor("#E53935")


def _key(builder: GdtFrameBuilder, key: Qt.Key) -> None:
    builder.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier))


# ----------------------------------------------------------------------
# read-back (model)
# ----------------------------------------------------------------------
def test_readback_sentences() -> None:
    row = GdtRow(
        characteristic=Characteristic.POSITION,
        tolerance_prefix="Ø",
        tolerance_value="0.1",
        tolerance_modifier="M",
        datum_primary=DatumRef(["A"]),
        datum_secondary=DatumRef(["B"], modifier="M"),
        datum_tertiary=DatumRef(["C"]),
    )
    assert describe_row(row) == (
        "Position within a Ø0.1 mm cylindrical zone at maximum "
        "material (MMC), relative to datums A, B at MMC, C."
    )
    flat = GdtRow(
        characteristic=Characteristic.FLATNESS,
        tolerance_value="0.05",
        datum_primary=DatumRef(["A"]),  # ignored: form takes none
    )
    assert describe_row(flat) == "Flatness within a 0.05 mm wide zone."
    common = GdtRow(
        characteristic=Characteristic.CIRCULAR_RUNOUT,
        tolerance_value="0.1",
        datum_primary=DatumRef(["A", "B"]),
    )
    assert describe_row(common).endswith("relative to datum A-B.")
    sphere = GdtRow(
        characteristic=Characteristic.POSITION,
        tolerance_prefix="SØ",
        tolerance_value="0.3",
        tolerance_modifier="P",
    )
    assert "SØ0.3 mm spherical zone, projected" in describe_row(sphere)
    assert describe_row(GdtRow()) == (
        "Perpendicularity: type the tolerance value."
    )


def test_datum_rules() -> None:
    assert not takes_datums(Characteristic.CYLINDRICITY)
    assert takes_datums(Characteristic.PROFILE_SURFACE)
    assert needs_datum(Characteristic.PARALLELISM)
    assert needs_datum(Characteristic.TOTAL_RUNOUT)
    assert needs_datum(Characteristic.SYMMETRY)
    assert not needs_datum(Characteristic.POSITION)
    assert not needs_datum(Characteristic.PROFILE_LINE)
    lonely = GdtRow(
        characteristic=Characteristic.ANGULARITY, tolerance_value="0.1"
    )
    assert row_warning(lonely) == (
        "Angularity needs at least one datum reference."
    )
    lonely.datum_primary = DatumRef(["A"])
    assert row_warning(lonely) is None


# ----------------------------------------------------------------------
# builder: state in and out
# ----------------------------------------------------------------------
def test_initial_state_roundtrip(host) -> None:
    """Everything the former editor expressed survives: composite rows
    with their own symbols, prefixes, modifiers, common datums, notes
    and the auxiliary frame."""
    state = _sample_state()
    builder = GdtFrameBuilder(state, host)
    assert builder.current_state() == state
    assert builder.row_count() == 2


def test_fields_update_the_state(host) -> None:
    builder = GdtFrameBuilder(GdtState(), host)
    builder._symbol_buttons[Characteristic.POSITION].click()
    builder._zone._buttons[1].click()  # the diameter prefix
    builder._value_edit.setText("0.25")
    builder._modifier._buttons[2].click()  # LMC
    builder._datum_edits[0].setText("a-b")
    builder._datum_edits[1].setText("c")
    out = builder.current_state()
    assert out.characteristic is Characteristic.POSITION
    assert out.tolerance_prefix == "Ø"
    assert out.tolerance_value == "0.25"
    assert out.tolerance_modifier == "L"
    assert out.datum_primary.letters == ["A", "B"]
    assert out.datum_secondary.letters == ["C"]
    assert builder._char_name.text() == "Position"


def test_datum_modifier_menu(host) -> None:
    builder = GdtFrameBuilder(
        GdtState(
            characteristic=Characteristic.POSITION,
            datum_primary=DatumRef(["A"]),
        ),
        host,
    )
    menu = builder._datum_mod_btns[0].menu()
    act = next(a for a in menu.actions() if a.text().startswith("Ⓜ"))
    act.trigger()
    assert builder.current_state().datum_primary.modifier == "M"
    assert builder._datum_mod_btns[0].text().startswith("Ⓜ")
    menu.actions()[0].trigger()  # "No modifier"
    assert builder.current_state().datum_primary.modifier is None


def test_state_is_emitted_live(host) -> None:
    builder = GdtFrameBuilder(GdtState(), host)
    seen: list[GdtState] = []
    builder.stateEdited.connect(seen.append)
    builder._value_edit.setText("0.5")
    assert seen and seen[-1].tolerance_value == "0.5"


def test_symbol_grid_lists_the_14_characteristics(host) -> None:
    builder = GdtFrameBuilder(
        GdtState(characteristic=Characteristic.TOTAL_RUNOUT), host
    )
    assert set(builder._symbol_buttons) == set(Characteristic)
    checked = [c for c, b in builder._symbol_buttons.items() if b.isChecked()]
    assert checked == [Characteristic.TOTAL_RUNOUT]
    tips = {b.toolTip() for b in builder._symbol_buttons.values()}
    assert "Total runout" in tips and "Profile of a surface" in tips


def test_form_tolerances_skip_the_datum_step(host) -> None:
    builder = GdtFrameBuilder(
        GdtState(characteristic=Characteristic.POSITION), host
    )
    builder._datum_edits[0].setText("A")
    builder._datum_edits[1].setText("B")
    assert not builder._datum_box.isHidden()
    assert builder._form_info.isHidden()
    builder._symbol_buttons[Characteristic.FLATNESS].click()
    assert builder._datum_box.isHidden()
    assert not builder._form_info.isHidden()
    out = builder.current_state()
    assert out.datum_primary.is_empty() and out.datum_secondary.is_empty()
    # Switching back restores what was typed.
    builder._symbol_buttons[Characteristic.POSITION].click()
    assert builder.current_state().datum_primary.letters == ["A"]
    assert builder.current_state().datum_secondary.letters == ["B"]


def test_readback_and_warning_follow_the_fields(host) -> None:
    builder = GdtFrameBuilder(GdtState(), host)
    builder._symbol_buttons[Characteristic.PERPENDICULARITY].click()
    builder._value_edit.setText("0.02")
    assert builder.readback_text() == (
        "Perpendicularity within a 0.02 mm wide zone."
    )
    assert "needs at least one datum" in builder.warning_text()
    builder._datum_edits[0].setText("A")
    assert builder.warning_text() == ""
    assert builder.readback_text().endswith("relative to datum A.")


def test_preview_is_the_real_frame(qapp) -> None:
    short = render_frame_preview(
        GdtState(tolerance_value="0.1"), FRAME_COLOR
    )
    long = render_frame_preview(
        GdtState(
            tolerance_value="0.1",
            datum_primary=DatumRef(["A"]),
            datum_secondary=DatumRef(["B"]),
        ),
        FRAME_COLOR,
    )
    assert not short.isNull()
    assert long.width() > short.width()
    # Capped so a long composite frame never outgrows the builder.
    huge = render_frame_preview(
        GdtState(
            tolerance_value="0.123456789 0.123456789 0.123456789",
            datum_primary=DatumRef(["A", "B"]),
            datum_secondary=DatumRef(["C", "D"]),
            datum_tertiary=DatumRef(["E", "F"]),
        ),
        FRAME_COLOR,
        max_width=300,
    )
    assert huge.width() <= 301


# ----------------------------------------------------------------------
# builder: composite rows
# ----------------------------------------------------------------------
def test_composite_rows_add_select_remove(host) -> None:
    builder = GdtFrameBuilder(
        GdtState(
            characteristic=Characteristic.POSITION,
            tolerance_value="0.2",
            datum_primary=DatumRef(["A"]),
        ),
        host,
    )
    assert builder._row_strip.isHidden()
    builder._add_row_btn.click()
    assert builder.row_count() == 2
    assert builder.current_row_index() == 1
    assert not builder._row_strip.isHidden()
    # A new row repeats the characteristic and the primary datum.
    assert builder._char_name.text() == "Position"
    assert builder._datum_edits[0].text() == "A"
    builder._value_edit.setText("0.05")
    builder._row_chips[0].click()
    assert builder.current_row_index() == 0
    assert builder._value_edit.text() == "0.2"
    rows = builder.current_state().all_rows()
    assert [r.tolerance_value for r in rows] == ["0.2", "0.05"]
    assert "Row 2:" in builder.readback_text()
    builder._remove_row_btn.click()
    assert builder.row_count() == 1
    assert builder.current_state().tolerance_value == "0.05"
    assert builder._row_strip.isHidden()
    builder._remove_current_row()  # no-op on the last row
    assert builder.row_count() == 1


def test_row_chips_are_rebuilt_not_stacked(qapp, host) -> None:
    host.show()
    builder = GdtFrameBuilder(GdtState(), host)
    builder.show()
    builder._add_row()
    qapp.processEvents()
    old = list(builder._row_chips)
    assert not any(c.isHidden() for c in old)
    builder._add_row()
    builder._add_row()
    assert [c.text() for c in builder._row_chips] == [
        "Row 1", "Row 2", "Row 3", "Row 4",
    ]
    # Replaced chips are hidden at once (not left painted until
    # deleteLater runs, which drew "Row 1" twice).
    assert all(c.isHidden() for c in old)
    host.hide()


# ----------------------------------------------------------------------
# builder: commit / cancel / notes
# ----------------------------------------------------------------------
def test_enter_commits_once(host) -> None:
    builder = GdtFrameBuilder(GdtState(), host)
    fired: list[str] = []
    builder.committed.connect(lambda: fired.append("commit"))
    builder._value_edit.returnPressed.emit()
    _key(builder, Qt.Key_Return)
    builder._primary.click()
    assert fired == ["commit"]


@pytest.mark.parametrize("how", ["escape", "close", "cancel"])
def test_cancel_paths(host, how: str) -> None:
    builder = GdtFrameBuilder(GdtState(), host)
    fired: list[str] = []
    builder.cancelled.connect(lambda: fired.append("cancel"))
    builder.committed.connect(lambda: fired.append("commit"))
    if how == "escape":
        _key(builder, Qt.Key_Escape)
    elif how == "close":
        builder._close_btn.click()
    else:
        next(
            b for b in builder.findChildren(type(builder._primary))
            if b.text() == "Cancel"
        ).click()
    assert fired == ["cancel"]


def test_clicking_outside_does_not_commit(qapp, host) -> None:
    host.show()
    builder = GdtFrameBuilder(GdtState(), host)
    builder.show()
    commits: list[int] = []
    builder.committed.connect(lambda: commits.append(1))
    outside = QWidget(host)
    outside.setFocusPolicy(Qt.StrongFocus)
    outside.show()
    outside.setFocus()
    qapp.processEvents()
    assert commits == []
    host.hide()


def test_note_buttons_request_in_place_editing(host) -> None:
    builder = GdtFrameBuilder(GdtState(upper_runs=[{"t": "4X"}]), host)
    asked: list[str] = []
    builder.noteEditRequested.connect(asked.append)
    assert builder._note_btns["upper"].text() == "Note above: 4X"
    assert builder._note_btns["lower"].text() == "+ Note below"
    for btn in builder._note_btns.values():
        btn.click()
    assert asked == ["upper", "lower"]


def test_new_and_edit_wording(host) -> None:
    new = GdtFrameBuilder(GdtState(), host, is_new=True)
    edit = GdtFrameBuilder(GdtState(), host, is_new=False)
    assert new._primary.text() == "Place frame"
    assert edit._primary.text() == "Apply"


def test_body_scrolls_when_the_viewport_is_short(qapp, host) -> None:
    host.show()
    builder = GdtFrameBuilder(GdtState(), host)
    builder.show()
    builder.set_max_height(320)
    qapp.processEvents()
    assert builder.height() <= 320
    assert builder._scroll.verticalScrollBar().maximum() > 0
    host.hide()


def test_colors_follow_the_theme(host) -> None:
    builder = GdtFrameBuilder(GdtState(), host, tokens=LIGHT)
    btn = builder._symbol_buttons[Characteristic.FLATNESS]
    light = btn.icon().pixmap(22, 22).toImage()
    builder.set_colors(DARK)
    dark = btn.icon().pixmap(22, 22).toImage()
    assert light != dark


# ----------------------------------------------------------------------
# main window wiring
# ----------------------------------------------------------------------
def _window(sample_pdf: Path):
    from annoter.views.main_window import MainWindow

    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    win.open_path(sample_pdf)
    win._tool_controller.set_tool(Tool.GDT)
    return win


def test_placing_a_frame_through_the_builder(qapp, sample_pdf: Path) -> None:
    win = _window(sample_pdf)
    try:
        win._on_gdt_placement(QPointF(200, 200))
        builder = win._gdt_editor
        assert isinstance(builder, GdtFrameBuilder)
        assert builder._primary.text() == "Place frame"
        builder._symbol_buttons[Characteristic.POSITION].click()
        builder._value_edit.setText("0.1")
        builder._datum_edits[0].setText("A")
        item = win._gdt_edit_item
        assert item.state().tolerance_value == "0.1"  # live on the page
        builder._primary.click()
        assert win._gdt_editor is None
        items = [
            i for i in win._scene.items() if isinstance(i, GdtAnnotationItem)
        ]
        assert len(items) == 1
        assert items[0].state().characteristic is Characteristic.POSITION
        win.act_undo.trigger()
        assert not [
            i for i in win._scene.items() if isinstance(i, GdtAnnotationItem)
        ]
    finally:
        win._on_close()
        win.close()


def test_untouched_new_frame_is_dropped(qapp, sample_pdf: Path) -> None:
    win = _window(sample_pdf)
    try:
        win._on_gdt_placement(QPointF(200, 200))
        win._gdt_editor._primary.click()
        assert not [
            i for i in win._scene.items() if isinstance(i, GdtAnnotationItem)
        ]
    finally:
        win._on_close()
        win.close()


def test_new_frames_start_from_the_last_characteristic(
    qapp, sample_pdf: Path
) -> None:
    win = _window(sample_pdf)
    try:
        win._on_gdt_placement(QPointF(200, 200))
        b = win._gdt_editor
        b._symbol_buttons[Characteristic.POSITION].click()
        b._value_edit.setText("0.1")
        b._primary.click()
        win._on_gdt_placement(QPointF(400, 300))
        b = win._gdt_editor
        assert b._symbol_buttons[Characteristic.POSITION].isChecked()
        # Still "untouched": closing it right away leaves nothing behind.
        b._primary.click()
        frames = [
            i for i in win._scene.items() if isinstance(i, GdtAnnotationItem)
        ]
        assert len(frames) == 1
    finally:
        win._on_close()
        win.close()


def test_double_click_edits_in_the_builder(qapp, sample_pdf: Path) -> None:
    win = _window(sample_pdf)
    try:
        win._on_gdt_placement(QPointF(200, 200))
        win._gdt_editor._value_edit.setText("0.1")
        win._gdt_editor._primary.click()
        frame = next(
            i for i in win._scene.items() if isinstance(i, GdtAnnotationItem)
        )
        win._open_gdt_editor(frame)
        b = win._gdt_editor
        assert b._primary.text() == "Apply"
        assert b._value_edit.text() == "0.1"
        b._value_edit.setText("0.3")
        b._primary.click()
        assert frame.state().tolerance_value == "0.3"
        win.act_undo.trigger()
        assert frame.state().tolerance_value == "0.1"
    finally:
        win._on_close()
        win.close()


def test_builder_sits_beside_the_frame(qapp, sample_pdf: Path) -> None:
    win = _window(sample_pdf)
    try:
        page = win._scene.page_item()
        win._view.set_zoom(0.5)
        qapp.processEvents()
        corner = page.mapToScene(QPointF(20, 20))
        win._on_gdt_placement(corner)
        qapp.processEvents()
        b = win._gdt_editor
        item = win._gdt_edit_item
        vp = win._view.viewport()
        frame = win._view.mapFromScene(
            item.mapToScene(item.content_rect()).boundingRect()
        ).boundingRect()
        geo = b.geometry()
        assert vp.rect().contains(geo)
        if vp.width() >= frame.right() + 16 + geo.width() + 8:
            assert not geo.intersects(frame)
    finally:
        win._on_close()
        win.close()


def test_theme_switch_recolors_an_open_builder(
    qapp, sample_pdf: Path
) -> None:
    win = _window(sample_pdf)
    try:
        win._on_gdt_placement(QPointF(200, 200))
        win._set_theme(Theme.DARK)
        assert win._gdt_editor._tokens is DARK
        win._set_theme(Theme.LIGHT)
        assert win._gdt_editor._tokens is LIGHT
    finally:
        qapp.setStyleSheet("")
        win._on_close()
        win.close()
