"""UI redesign, Lot H: command palette polish (groups, highlights, key
caps, recent files, ranking by usage)."""

from __future__ import annotations

import gc
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
import shiboken6  # noqa: E402
from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.services.command_usage import (  # noqa: E402
    HALF_LIFE_DAYS,
    MAX_ENTRIES,
    SETTINGS_KEY,
    CommandUsage,
)
from annoter.services.theme import Theme  # noqa: E402
from annoter.services.tokens import DARK, LIGHT  # noqa: E402
from annoter.views.command_palette import (  # noqa: E402
    FILES_GROUP,
    MAX_LIST_H,
    RECENT_GROUP,
    CommandEntry,
    CommandPalette,
    match_spans,
    shortcut_keys,
)
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "usage.ini"), QSettings.IniFormat)


def _pdf(path: Path) -> Path:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


def _noop() -> None:
    pass


def _entries() -> list[CommandEntry]:
    return [
        CommandEntry("Open...", "File", _noop, shortcut="Ctrl+O"),
        CommandEntry("Save", "File", _noop, shortcut="Ctrl+S"),
        CommandEntry("Rotate Right 90\u00b0", "View", _noop, "Ctrl+R"),
        CommandEntry("Rotate Left 90\u00b0", "View", _noop),
        CommandEntry("Reset Rotation", "View", _noop),
        CommandEntry("Grayscale Page", "View", _noop),
        CommandEntry("Rectangle / cloud tool", "Tools", _noop),
        CommandEntry("Page Size...", "Page", _noop),
        CommandEntry(
            "FLANGE_revB.pdf",
            FILES_GROUP,
            _noop,
            detail="C:\\Plans\\Hydraulics",
        ),
    ]


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def test_shortcut_keys() -> None:
    assert shortcut_keys("Ctrl+Shift+R") == ["Ctrl", "Shift", "R"]
    assert shortcut_keys("Ctrl+R") == ["Ctrl", "R"]
    assert shortcut_keys("Ctrl++") == ["Ctrl", "+"]
    assert shortcut_keys("Ctrl+Shift+]") == ["Ctrl", "Shift", "]"]
    assert shortcut_keys("Del") == ["Del"]
    assert shortcut_keys("") == []


def test_match_spans_prefer_word_starts() -> None:
    assert match_spans("Reset Rotation", ["rot"]) == [(6, 9)]
    assert match_spans("Rotate Right 90\u00b0", ["rot", "ri"]) == [
        (0, 3),
        (7, 9),
    ]
    # Inside a word only when no word starts with the term.
    assert match_spans("Grayscale Page", ["scale"]) == [(4, 9)]
    assert match_spans("Save", ["zzz"]) == []


# ----------------------------------------------------------------------
# usage store
# ----------------------------------------------------------------------
def test_usage_counts_orders_and_persists(qapp, settings) -> None:
    usage = CommandUsage(settings)
    usage.record("View/Rotate", now=1000.0)
    usage.record("File/Save", now=2000.0)
    usage.record("View/Rotate", now=3000.0)
    assert usage.count("View/Rotate") == 2
    assert usage.last_used("File/Save") == 2000.0
    assert usage.recent() == ["View/Rotate", "File/Save"]
    again = CommandUsage(settings)
    assert again.count("View/Rotate") == 2
    assert again.recent(1) == ["View/Rotate"]


def test_usage_score_decays_with_age(qapp) -> None:
    usage = CommandUsage()
    usage.record("a", now=0.0)
    fresh = usage.score("a", now=0.0)
    later = usage.score("a", now=HALF_LIFE_DAYS * 86400.0)
    assert fresh == pytest.approx(1.0)
    assert later == pytest.approx(0.5)
    assert usage.score("never") == 0.0


def test_usage_ignores_corrupt_settings_and_caps_size(
    qapp, settings
) -> None:
    settings.setValue(SETTINGS_KEY, "{not json")
    assert CommandUsage(settings).recent() == []
    settings.setValue(
        SETTINGS_KEY, json.dumps({"ok": [2, 5.0], "bad": "x", "neg": [0, 1]})
    )
    assert CommandUsage(settings).recent() == ["ok"]
    usage = CommandUsage(settings)
    for i in range(MAX_ENTRIES + 10):
        usage.record(f"k{i}", now=float(i))
    assert len(usage.recent()) == MAX_ENTRIES
    assert "k0" not in usage.recent()  # the oldest went first


# ----------------------------------------------------------------------
# palette: search results
# ----------------------------------------------------------------------
def test_results_are_grouped_best_group_first(qapp) -> None:
    pal = CommandPalette()
    pal.set_entries(_entries())
    pal._input.setText("rot")
    assert pal.visible_groups() == ["View"]
    assert pal.visible_labels() == [
        "Rotate Right 90\u00b0",  # prefix match, then menu order
        "Rotate Left 90\u00b0",
        "Reset Rotation",  # word match
    ]
    pal._input.setText("pa")
    # "Page Size..." (prefix) puts the Page group, last in the menus,
    # before View ("Grayscale Page", word match).
    assert pal.visible_groups() == ["Page", "View"]
    assert pal.current_label() == "Page Size..."


def test_usage_breaks_ties_but_not_match_quality(qapp) -> None:
    usage = CommandUsage()
    pal = CommandPalette(usage=usage)
    entries = _entries()
    pal.set_entries(entries)
    left = next(e for e in entries if e.label.startswith("Rotate Left"))
    reset = next(e for e in entries if e.label == "Reset Rotation")
    for _ in range(5):
        usage.record(left.key)
        usage.record(reset.key)
    pal._input.setText("rot")
    labels = pal.visible_labels()
    assert labels[0] == "Rotate Left 90\u00b0"  # habit wins among equals
    assert labels.index("Rotate Right 90\u00b0") < labels.index(
        "Reset Rotation"
    )  # a word match never beats a prefix match


def test_files_are_searchable_by_folder(qapp) -> None:
    pal = CommandPalette()
    pal.set_entries(_entries())
    pal._input.setText("hydraulics")
    assert pal.visible_groups() == [FILES_GROUP]
    assert pal.visible_labels() == ["FLANGE_revB.pdf"]


def test_no_match_shows_the_empty_state(qapp) -> None:
    pal = CommandPalette()
    pal.set_entries(_entries())
    pal._input.setText("zzz")
    assert pal.visible_labels() == []
    assert not pal._empty.isHidden()
    assert pal._list.isHidden()


# ----------------------------------------------------------------------
# palette: browsing before typing
# ----------------------------------------------------------------------
def test_browse_view_opens_on_recent_commands_and_files(qapp) -> None:
    usage = CommandUsage()
    entries = _entries()
    save = next(e for e in entries if e.label == "Save")
    gray = next(e for e in entries if e.label == "Grayscale Page")
    usage.record(save.key, now=1.0)
    usage.record(gray.key, now=2.0)
    usage.record("Gone/Command no longer there", now=3.0)
    pal = CommandPalette(usage=usage)
    pal.set_entries(entries)
    groups = pal.visible_groups()
    assert groups[:2] == [RECENT_GROUP, FILES_GROUP]
    labels = pal.visible_labels()
    assert labels[:3] == ["Grayscale Page", "Save", "FLANGE_revB.pdf"]
    # Shown once: not repeated under their own menu.
    assert labels.count("Save") == 1
    assert labels.count("Grayscale Page") == 1
    assert pal.current_label() == "Grayscale Page"


def test_browse_view_without_history(qapp) -> None:
    pal = CommandPalette(usage=CommandUsage())
    pal.set_entries(_entries())
    assert pal.visible_groups() == [
        FILES_GROUP, "File", "View", "Tools", "Page",
    ]


# ----------------------------------------------------------------------
# palette: keyboard, mouse, running
# ----------------------------------------------------------------------
def test_keyboard_skips_group_headers(qapp) -> None:
    pal = CommandPalette()
    pal.set_entries(_entries())
    pal._input.setText("r")
    labels = pal.visible_labels()
    seen = [pal.current_label()]
    for _ in range(len(labels) - 1):
        QTest.keyClick(pal._input, Qt.Key_Down)
        seen.append(pal.current_label())
    assert seen == labels  # every entry, no header in between
    QTest.keyClick(pal._input, Qt.Key_Down)
    assert pal.current_label() == labels[0]  # arrows wrap
    QTest.keyClick(pal._input, Qt.Key_Up)
    assert pal.current_label() == labels[-1]
    QTest.keyClick(pal._input, Qt.Key_PageDown)
    assert pal.current_label() == labels[-1]  # pages stop at the end


def test_running_records_usage(qapp) -> None:
    ran: list[str] = []
    usage = CommandUsage()
    entry = CommandEntry("Alpha", "X", lambda: ran.append("a"))
    pal = CommandPalette(usage=usage)
    pal.set_entries([entry])
    pal.show()
    pal.run_current()
    for _ in range(5):
        qapp.processEvents()
    assert ran == ["a"]
    assert usage.count(entry.key) == 1
    assert pal.isHidden()


def test_clicking_a_group_header_does_nothing(qapp) -> None:
    ran: list[str] = []
    pal = CommandPalette()
    pal.set_entries([CommandEntry("Alpha", "X", lambda: ran.append("a"))])
    pal.show()
    header = pal._list.item(0)
    assert header.text() == "X"
    assert not (header.flags() & Qt.ItemIsSelectable)
    pal._run_item(header)
    for _ in range(5):
        qapp.processEvents()
    assert ran == []
    assert pal.isVisible()
    pal.hide()


def test_list_height_is_bounded(qapp) -> None:
    pal = CommandPalette()
    pal.set_entries(
        [CommandEntry(f"Command {i}", f"G{i % 4}", _noop) for i in range(60)]
    )
    assert pal._list.height() <= MAX_LIST_H + 12
    pal._input.setText("command 7")
    small = pal._list.height()
    assert small < MAX_LIST_H


def test_theme_colors_reach_the_rows(qapp) -> None:
    pal = CommandPalette()
    pal.set_colors(DARK)
    assert pal._delegate.tokens is DARK
    pal.set_colors(LIGHT)
    assert pal._delegate.tokens is LIGHT


# ----------------------------------------------------------------------
# main window wiring
# ----------------------------------------------------------------------
def test_window_palette_lists_recent_files(qapp, tmp_path: Path) -> None:
    a = _pdf(tmp_path / "a.pdf")
    (tmp_path / "plans").mkdir()
    b = _pdf(tmp_path / "plans" / "b.pdf")
    gone = _pdf(tmp_path / "gone.pdf")
    win = MainWindow()
    try:
        for p in (gone, b, a):
            win._recent.add(p)
        gone.unlink()
        win.open_path(a)  # the open document is not offered again
        files = [e for e in win._command_entries() if e.group == FILES_GROUP]
        assert [e.label for e in files] == ["b.pdf"]
        assert files[0].detail == str((tmp_path / "plans").resolve())
        files[0].run()
        assert win._doc.path.name == "b.pdf"
    finally:
        win.close()


def test_open_recent_paths_are_not_duplicated(qapp, tmp_path: Path) -> None:
    a = _pdf(tmp_path / "a.pdf")
    win = MainWindow()
    try:
        win._recent.add(a)
        entries = win._command_entries()
        labels = [e.label for e in entries]
        assert str(a.resolve()) not in labels  # the menu's path entry
        assert "a.pdf" in labels  # the palette's own file entry
        clear = next(e for e in entries if e.label == "Clear Recent Files")
        assert clear.group == "File"
    finally:
        win.close()


def test_palette_keeps_the_window_menus_alive(qapp, tmp_path: Path) -> None:
    """Regression (Lot B): walking the menu with QAction.menu() killed
    the window's Python wrapper of Open Recent, so the first Ctrl+K
    broke the next recent-list refresh and the theme switch."""
    win = MainWindow()
    try:
        win.act_command_palette.trigger()
        win._command_palette.hide()
        gc.collect()
        assert shiboken6.isValid(win._menu_recent)
        win._recent.add(_pdf(tmp_path / "a.pdf"))
        win._refresh_recent_menu()  # raised RuntimeError before the fix
        win._set_theme(Theme.DARK)
        win._set_theme(Theme.LIGHT)
        assert any(
            "a.pdf" in act.text() for act in win._menu_recent.actions()
        )
    finally:
        qapp.setStyleSheet("")
        win.close()


def test_window_palette_follows_the_theme(qapp) -> None:
    win = MainWindow()
    try:
        win._set_theme(Theme.DARK)
        win.act_command_palette.trigger()
        assert win._command_palette._delegate.tokens is DARK
        win._command_palette.hide()
        win._set_theme(Theme.LIGHT)
        assert win._command_palette._delegate.tokens is LIGHT
    finally:
        qapp.setStyleSheet("")
        win.close()


def test_window_palette_records_what_runs(qapp) -> None:
    win = MainWindow()
    try:
        win.act_command_palette.trigger()
        pal = win._command_palette
        pal._input.setText("grayscale")
        key = f"View/{pal.current_label()}"
        pal.run_current()
        for _ in range(5):
            qapp.processEvents()
        assert win._command_usage.count(key) == 1
        win.act_command_palette.trigger()
        assert pal.visible_groups()[0] == RECENT_GROUP
        pal.hide()
    finally:
        win.close()
