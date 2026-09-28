"""UI redesign, Lot B: top bar, main menu and command palette."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QAction, QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QMenuBar  # noqa: E402

from annoter.controllers.tools import Tool  # noqa: E402
from annoter.services.theme import Theme  # noqa: E402
from annoter.views.command_palette import (  # noqa: E402
    CommandEntry,
    CommandPalette,
    entries_from_menu,
)
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.line_icons import glyph_names, line_icon  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.views.top_bar import TopBar  # noqa: E402


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


def _menu_titles(menu: QMenu) -> list[str]:
    # Not QAction.menu(): in PySide6 it invalidates the window's own
    # wrappers of menus built with addMenu(title) (see command_palette).
    subs = {id(m.menuAction()): m for m in menu.findChildren(QMenu)}
    return [
        a.text().replace("&", "")
        for a in menu.actions()
        if id(a) in subs
    ]


# ----------------------------------------------------------------------
# top bar replaces the menu bar
# ----------------------------------------------------------------------
def test_top_bar_is_the_menu_widget(qapp) -> None:
    win = MainWindow()
    try:
        assert isinstance(win.menuWidget(), TopBar)
        assert win.menuWidget() is win._top_bar
        assert win.findChild(QMenuBar) is None
    finally:
        win.close()


def test_main_menu_keeps_every_former_menu(qapp) -> None:
    win = MainWindow()
    try:
        assert win._top_bar.menu_button.menu() is win._main_menu
        titles = _menu_titles(win._main_menu)
        for title in ("Open Recent", "Edit", "View", "Page"):
            assert title in titles
        root = set(win._main_menu.actions())
        for act in (
            win.act_open,
            win.act_save,
            win.act_save_as,
            win.act_export_images,
            win.act_insert_pdf,
            win.act_resize_doc,
            win.act_close,
            win.act_quit,
        ):
            assert act in root
    finally:
        win.close()


def test_every_shortcut_action_is_on_the_window(qapp) -> None:
    """Menu-only actions must be on the window or their shortcut dies
    once the menu bar is gone."""
    win = MainWindow()
    try:
        on_window = set(win.actions())
        for name, act in vars(win).items():
            if not name.startswith("act_") or not isinstance(act, QAction):
                continue
            if not act.shortcut().isEmpty():
                assert act in on_window, name
    finally:
        win.close()


def test_no_quick_access_toolbar_left(qapp) -> None:
    """Open / Save / Undo / Redo went to the top bar (Lot B), zoom to the
    canvas pill (Lot D), quick styles and Format Painter to the inspector
    (Lot F): the old Quick Access toolbar is gone."""
    from PySide6.QtWidgets import QToolBar

    win = MainWindow()
    try:
        assert not hasattr(win, "_toolbar")
        names = [tb.objectName() for tb in win.findChildren(QToolBar)]
        assert "MainToolBar" not in names
        # Format Painter is still reachable: Edit menu + inspector.
        edit_actions = [
            a
            for m in win._main_menu.findChildren(QMenu)
            if m.title().replace("&", "") == "Edit"
            for a in m.actions()
        ]
        assert win.act_format_painter in edit_actions
    finally:
        win.close()


def test_title_and_unsaved_chip_follow_the_document(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        bar = win._top_bar
        assert bar.title_label.text() == ""
        assert not bar.is_unsaved_shown()
        win.open_path(sample_pdf)
        assert bar.title_label.text() == "sample.pdf"
        assert bar.title_label.toolTip().startswith(str(sample_pdf))
        assert not bar.is_unsaved_shown()
        win._scene.push_add(RectangleItem(QRectF(10, 10, 40, 40)))
        assert bar.is_unsaved_shown()
        win.act_undo.trigger()
        assert not bar.is_unsaved_shown()
        win._on_close()
        assert bar.title_label.text() == ""
        assert not bar.is_unsaved_shown()
    finally:
        win.close()


def test_save_button_follows_the_save_action(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    try:
        bar = win._top_bar
        assert not bar.save_button.isEnabled()
        win.open_path(sample_pdf)
        assert bar.save_button.isEnabled()
        fired: list[bool] = []
        # Swap the real save (it may prompt) for a spy on the action.
        win.act_save.triggered.disconnect(win._on_save)
        win.act_save.triggered.connect(lambda *_: fired.append(True))
        # Clicking goes through the action: no second code path.
        QTest.mouseClick(bar.save_button, Qt.LeftButton)
        assert fired == [True]
    finally:
        win.close()


def test_undo_redo_buttons_mirror_the_actions(qapp) -> None:
    win = MainWindow()
    try:
        bar = win._top_bar
        assert bar.undo_button.defaultAction() is win.act_undo
        assert bar.redo_button.defaultAction() is win.act_redo
        assert bar.theme_button.defaultAction() is win.act_toggle_theme
    finally:
        win.close()


def test_theme_toggle_flips_the_theme(qapp) -> None:
    win = MainWindow()
    try:
        win._set_theme(Theme.LIGHT)
        win.act_toggle_theme.trigger()
        assert win._theme is Theme.DARK
        assert win.act_theme_dark.isChecked()
        assert "light" in win.act_toggle_theme.toolTip().lower()
        win.act_toggle_theme.trigger()
        assert win._theme is Theme.LIGHT
    finally:
        win._set_theme(Theme.LIGHT)
        qapp.setStyleSheet("")
        win.close()


# ----------------------------------------------------------------------
# command palette
# ----------------------------------------------------------------------
def test_menu_walk_groups_entries_by_top_level_menu(qapp) -> None:
    win = MainWindow()
    try:
        entries = entries_from_menu(win._main_menu)
        by_label = {e.label: e for e in entries}
        assert by_label["Open..."].group == "File"
        assert by_label["Rotate Right 90°"].group == "View"
        # Nested submenu (Edit > Align) keeps the top-level group.
        assert by_label["Align Left"].group == "Edit"
        assert by_label["Rotate Right 90°"].shortcut == "Ctrl+R"
        # Each action appears once even if reachable twice.
        labels = [e.label for e in entries]
        assert len(labels) == len(set(labels))
    finally:
        win.close()


def test_palette_filters_ranks_and_hides_disabled(qapp) -> None:
    ran: list[str] = []
    entries = [
        CommandEntry("Reset Rotation", "View", lambda: ran.append("reset")),
        CommandEntry("Rotate Right 90°", "View", lambda: ran.append("r")),
        CommandEntry("Save", "File", lambda: ran.append("s"), enabled=False),
        CommandEntry("Grayscale Page", "View", lambda: ran.append("g")),
    ]
    pal = CommandPalette()
    pal.set_entries(entries)
    assert "Save" not in pal.visible_labels()
    pal._input.setText("rot")
    labels = pal.visible_labels()
    assert labels[0] == "Rotate Right 90°"  # prefix match ranks first
    assert "Reset Rotation" in labels
    assert "Grayscale Page" not in labels
    pal._input.setText("view gray")  # every term, label or group
    assert pal.visible_labels() == ["Grayscale Page"]
    pal._input.setText("zzz")
    assert pal.visible_labels() == []
    assert not pal._empty.isHidden()


def test_palette_runs_the_current_entry_with_enter(qapp) -> None:
    ran: list[str] = []
    pal = CommandPalette()
    pal.set_entries(
        [
            CommandEntry("Alpha", "X", lambda: ran.append("a")),
            CommandEntry("Beta", "X", lambda: ran.append("b")),
        ]
    )
    pal.show()
    QTest.keyClick(pal._input, Qt.Key_Down)
    QTest.keyClick(pal._input, Qt.Key_Return)
    for _ in range(5):
        qapp.processEvents()
    assert ran == ["b"]
    assert pal.isHidden()


def test_palette_lists_tools_and_runs_them(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        entries = {e.label: e for e in win._command_entries()}
        assert entries["Rectangle / cloud tool"].group == "Tools"
        entries["Rectangle / cloud tool"].run()
        assert win._tool_controller.tool() is Tool.RECTANGLE
    finally:
        win.close()


def test_ctrl_k_opens_the_palette(qapp) -> None:
    win = MainWindow()
    try:
        assert win.act_command_palette.shortcut().toString() == "Ctrl+K"
        win.act_command_palette.trigger()
        assert win._command_palette is not None
        assert win._command_palette.isVisible()
        win._command_palette.hide()
        win._top_bar.searchRequested.emit()
        assert win._command_palette.isVisible()
        win._command_palette.hide()
    finally:
        win.close()


# ----------------------------------------------------------------------
# icons
# ----------------------------------------------------------------------
def test_every_line_glyph_renders(qapp) -> None:
    for name in glyph_names():
        icon = line_icon(name, QColor("#202020"))
        img = icon.pixmap(24, 24).toImage()
        assert not icon.isNull(), name
        painted = any(
            img.pixelColor(x, y).alpha() > 0
            for x in range(24)
            for y in range(24)
        )
        assert painted, name


def test_unknown_glyph_raises(qapp) -> None:
    with pytest.raises(KeyError):
        line_icon("no-such-glyph", QColor("#000000"))
