"""Welcome screen / home page (Discussion #1, item 12)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtCore import QMimeData, QPoint, QRectF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402
from annoter.services.theme import Theme  # noqa: E402
from annoter.services.tokens import DARK, LIGHT  # noqa: E402
from annoter.views.welcome_screen import (  # noqa: E402
    CARD_MIN_W,
    GRID_GAP,
    HANDY_SHORTCUTS,
    MAX_COLUMNS,
    WelcomeScreen,
    _RecentGrid,
    annotations_caption,
    describe_mtime,
    format_size,
    pages_caption,
    read_recent_info,
)


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


def test_welcome_shown_without_document(qapp) -> None:
    win = MainWindow()
    try:
        assert win._central.currentWidget() is win._welcome
    finally:
        win.close()


def test_open_switches_to_view_and_close_back(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win.open_path(sample_pdf)
        assert win._central.currentWidget() is win._view
        win._on_close()
        assert win._central.currentWidget() is win._welcome
    finally:
        win.close()


def _drain(ws: WelcomeScreen) -> None:
    while ws._pending:
        ws._render_next()


def test_recent_list_populates_and_thumbnails_render(
    qapp, sample_pdf: Path
) -> None:
    ws = WelcomeScreen()
    ws.set_recent([str(sample_pdf)])
    assert len(ws.cards()) == 1
    _drain(ws)
    card = ws.cards()[0]
    assert card.thumbnail() is not None
    assert card.info is not None and card.info.problem is None


def test_missing_file_gets_placeholder_not_crash(qapp, tmp_path: Path) -> None:
    ws = WelcomeScreen()
    ws.set_recent([str(tmp_path / "gone.pdf")])
    _drain(ws)  # must not raise
    assert len(ws.cards()) == 1
    card = ws.cards()[0]
    assert card.is_missing()
    assert card.meta_text() == "File not found"
    assert card.thumbnail() is not None  # the dashed placeholder
    assert card.badge_text() == ""


def test_click_recent_opens_document(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win._recent.add(sample_pdf)
        win._welcome.set_recent(win._recent.list())
        card = win._welcome.cards()[0]
        QTest.mouseClick(card, Qt.LeftButton)
        assert win._doc is not None
        assert win._doc.path.name == sample_pdf.name
    finally:
        win.close()


def test_remove_recent_signal_updates_list(qapp, sample_pdf: Path) -> None:
    win = MainWindow()
    try:
        win._recent.add(sample_pdf)
        assert str(sample_pdf.resolve()) in win._recent.list()
        win._welcome.removePathRequested.emit(str(sample_pdf.resolve()))
        assert str(sample_pdf.resolve()) not in win._recent.list()
    finally:
        win.close()


def test_blank_document_opens_untitled_a4(qapp) -> None:
    win = MainWindow()
    try:
        recent_before = win._recent.list()
        win._new_blank_document()
        assert win._doc is not None
        assert win._is_untitled is True
        assert win._doc.page_count == 1
        w, h = win._doc.page_size_pt(0)
        assert (round(w), round(h)) == (595, 842)
        # The scratch file never enters the recent list.
        assert win._recent.list() == recent_before
        assert win._central.currentWidget() is win._view
    finally:
        win._on_close()
        win.close()


def test_blank_document_is_annotatable_and_saveable(
    qapp, tmp_path: Path
) -> None:
    win = MainWindow()
    try:
        win._new_blank_document()
        win._scene.push_add(RectangleItem(QRectF(10, 10, 80, 40)))
        assert win._has_unsaved_changes() is True

        target = tmp_path / "was_blank.pdf"
        assert win._save_to(target) is True
        win._reopen_after_save(target)
        assert win._is_untitled is False
        assert win._doc.path.name == "was_blank.pdf"

        doc = fitz.open(str(target))
        try:
            assert len(list(doc[0].annots() or [])) == 1
        finally:
            doc.close()
    finally:
        win._on_close()
        win.close()


# ----------------------------------------------------------------------
# UI redesign, Lot G: start page
# ----------------------------------------------------------------------
def _pdf(
    path: Path, pages: int = 1, annots: dict[int, int] | None = None
) -> Path:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=842, height=595)
        for k in range((annots or {}).get(i, 0)):
            page.add_rect_annot(fitz.Rect(10 + 30 * k, 10, 30 + 30 * k, 30))
    doc.save(str(path))
    doc.close()
    return path


def test_formatting_helpers() -> None:
    from datetime import datetime

    now = datetime(2026, 9, 24, 15, 0)
    assert describe_mtime(datetime(2026, 9, 24, 9, 5).timestamp(), now) == (
        "Today, 09:05"
    )
    assert describe_mtime(datetime(2026, 9, 23, 23, 0).timestamp(), now) == (
        "Yesterday"
    )
    assert describe_mtime(datetime(2026, 9, 3).timestamp(), now) == "3 Sep"
    assert describe_mtime(datetime(2025, 1, 12).timestamp(), now) == (
        "12 Jan 2025"
    )
    assert format_size(10) == "1 KB"
    assert format_size(6 * 1024) == "6 KB"
    assert format_size(int(2.14 * 1024 * 1024)) == "2.1 MB"
    assert format_size(118 * 1024 * 1024) == "118 MB"
    assert format_size(3 * 1024**3) == "3.0 GB"
    assert pages_caption(1) == "1 page"
    assert pages_caption(12) == "12 pages"
    assert annotations_caption(1) == "1 annotation"
    assert annotations_caption(9) == "9 annotations"


def test_read_recent_info_counts_pages_and_annotations(
    qapp, tmp_path: Path
) -> None:
    path = _pdf(tmp_path / "a.pdf", pages=3, annots={0: 2, 2: 1})
    doc = fitz.open(str(path))
    doc[1].insert_link(
        {"kind": fitz.LINK_URI, "from": fitz.Rect(0, 0, 9, 9), "uri": "x:y"}
    )
    doc.saveIncr()
    doc.close()
    info = read_recent_info(str(path))
    assert info.problem is None
    assert info.pages == 3
    assert info.annotations == 3  # links are not annotations to the user
    assert info.pixmap is not None and not info.pixmap.isNull()


def test_read_recent_info_reports_problems(qapp, tmp_path: Path) -> None:
    assert read_recent_info(str(tmp_path / "nope.pdf")).problem == (
        "File not found"
    )
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"this is not a pdf")
    assert read_recent_info(str(junk)).problem == "Can't read this file"
    locked = tmp_path / "locked.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(
        str(locked),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    doc.close()
    assert read_recent_info(str(locked)).problem == "Password protected"


def test_card_shows_meta_line_and_annotation_badge(
    qapp, tmp_path: Path
) -> None:
    marked = _pdf(tmp_path / "marked.pdf", pages=4, annots={0: 9})
    clean = _pdf(tmp_path / "clean.pdf", pages=1)
    ws = WelcomeScreen()
    ws.set_recent([str(marked), str(clean)])
    first, second = ws.cards()
    assert first.name_text() == "marked.pdf"
    assert first.folder_text() == str(tmp_path)
    assert "pages" not in first.meta_text()  # not read yet
    _drain(ws)
    assert first.meta_text().startswith("Today, ")
    assert "4 pages" in first.meta_text()
    assert "KB" in first.meta_text()
    assert first.badge_text() == "9 annotations"
    assert "1 page" in second.meta_text()
    assert second.badge_text() == ""
    assert first.toolTip() == str(marked)


def test_unchanged_files_are_not_read_twice(qapp, tmp_path: Path) -> None:
    path = _pdf(tmp_path / "a.pdf", annots={0: 1})
    ws = WelcomeScreen()
    ws.set_recent([str(path)])
    _drain(ws)
    ws.set_recent([str(path)])
    assert ws._pending == []  # served from the cache
    assert ws.cards()[0].badge_text() == "1 annotation"
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 60))
    ws.set_recent([str(path)])
    assert len(ws._pending) == 1  # modified since: read again


def test_reading_waits_until_the_page_is_shown(
    qapp, sample_pdf: Path
) -> None:
    ws = WelcomeScreen()
    ws.set_recent([str(sample_pdf)])
    assert not ws._timer.isActive()
    ws.show()
    assert ws._timer.isActive()
    ws.hide()
    assert not ws._timer.isActive()
    assert len(ws._pending) == 1


def test_grid_columns_follow_the_width(qapp, tmp_path: Path) -> None:
    assert _RecentGrid.columns_for(CARD_MIN_W) == 1
    assert _RecentGrid.columns_for(2 * CARD_MIN_W + GRID_GAP) == 2
    assert _RecentGrid.columns_for(10_000) == MAX_COLUMNS
    paths = [str(_pdf(tmp_path / f"f{i}.pdf")) for i in range(5)]
    ws = WelcomeScreen()
    ws.set_recent(paths)
    ws.resize(1440, 900)
    ws.show()
    qapp.processEvents()
    grid = ws._grid
    assert grid.columns() == MAX_COLUMNS
    # Fifth card wraps under the first.
    first, fifth = ws.cards()[0], ws.cards()[4]
    assert fifth.x() == first.x() and fifth.y() > first.y()
    ws.resize(700, 900)
    qapp.processEvents()
    assert grid.columns() == _RecentGrid.columns_for(grid.width())
    assert grid.columns() < MAX_COLUMNS
    # Nothing is pushed wider than the window.
    assert ws._scroll.widget().width() <= ws._scroll.viewport().width()
    ws.hide()


def test_shortcuts_row_wraps_instead_of_widening(qapp) -> None:
    ws = WelcomeScreen()
    ws.resize(1440, 900)
    ws.show()
    qapp.processEvents()
    assert ws._shortcuts_flow.line_count() == 1
    ws.resize(560, 900)
    qapp.processEvents()
    assert ws._shortcuts_flow.line_count() > 1
    assert ws._scroll.widget().width() <= ws._scroll.viewport().width()
    ws.hide()


def test_recent_section_hidden_when_empty(qapp, sample_pdf: Path) -> None:
    ws = WelcomeScreen()
    assert ws._recent_section.isHidden()
    ws.set_recent([str(sample_pdf)])
    assert not ws._recent_section.isHidden()
    assert ws._recent_count.text() == "1"
    ws.set_recent([])
    assert ws._recent_section.isHidden()


def test_clear_list_forgets_every_recent_file(
    qapp, sample_pdf: Path, tmp_path: Path
) -> None:
    win = MainWindow()
    try:
        win._recent.add(sample_pdf)
        win._recent.add(_pdf(tmp_path / "b.pdf"))
        assert len(win._welcome.cards()) == 2
        QTest.mouseClick(win._welcome._btn_clear, Qt.LeftButton)
        assert win._recent.list() == []
        assert win._welcome.cards() == []
        assert win._welcome._recent_section.isHidden()
        assert sample_pdf.exists()  # the files themselves stay
    finally:
        win.close()


def test_card_context_menu(qapp, sample_pdf: Path) -> None:
    ws = WelcomeScreen()
    ws.set_recent([str(sample_pdf)])
    card = ws.cards()[0]
    menu = ws._build_card_menu(card)
    assert [a.text() for a in menu.actions()] == [
        "Open",
        "Show in Folder",
        "",
        "Remove from List",
    ]
    opened: list[str] = []
    removed: list[str] = []
    ws.openPathRequested.connect(opened.append)
    ws.removePathRequested.connect(removed.append)
    menu.actions()[0].trigger()
    menu.actions()[3].trigger()
    assert opened == [str(sample_pdf)]
    assert removed == [str(sample_pdf)]


def test_start_buttons_emit(qapp) -> None:
    ws = WelcomeScreen()
    fired: list[str] = []
    ws.openRequested.connect(lambda: fired.append("open"))
    ws.blankRequested.connect(lambda: fired.append("blank"))
    QTest.mouseClick(ws._btn_open, Qt.LeftButton)
    QTest.mouseClick(ws._btn_blank, Qt.LeftButton)
    assert fired == ["open", "blank"]
    # The primary button is sized from its label layout, not collapsed.
    assert ws._btn_open.sizeHint().width() > ws._open_label.sizeHint().width()


def test_handy_shortcuts_match_the_real_actions(qapp) -> None:
    win = MainWindow()
    try:
        assert win._welcome.shortcut_keys() == [k for k, _ in HANDY_SHORTCUTS]
        by_keys = {
            "Ctrl 0": win.act_zoom_fit,
            "Ctrl K": win.act_command_palette,
        }
        for keys, act in by_keys.items():
            assert keys in win._welcome.shortcut_keys()
            assert act.shortcut().toString() == keys.replace(" ", "+")
    finally:
        win.close()


def test_start_page_hides_panels_and_restores_them(
    qapp, sample_pdf: Path
) -> None:
    win = MainWindow()
    win.show()  # dock toggles only track visibility on a shown window
    try:
        panels = (win._tool_rail, win._sidebar, win._properties_dock)
        assert win.is_welcome_mode()
        assert all(p.isHidden() for p in panels)
        assert not any(p.toggleViewAction().isEnabled() for p in panels)
        win.open_path(sample_pdf)
        assert not win.is_welcome_mode()
        assert not any(p.isHidden() for p in panels)
        assert all(p.toggleViewAction().isEnabled() for p in panels)
        # A panel the user closed stays closed across the start page.
        win._sidebar.toggleViewAction().trigger()
        assert win._sidebar.isHidden()
        win._on_close()
        assert all(p.isHidden() for p in panels)
        win.open_path(sample_pdf)
        assert win._sidebar.isHidden()
        assert not win._properties_dock.isHidden()
        assert not win._tool_rail.isHidden()
    finally:
        win.close()


def test_layout_saved_from_the_start_page_keeps_the_panels(
    qapp, sample_pdf: Path
) -> None:
    first = MainWindow()
    first.show()
    first.open_path(sample_pdf)
    first._sidebar.toggleViewAction().trigger()  # user closes the sidebar
    first._on_close()  # back on the start page: every panel hidden
    first._save_settings()
    first.hide()
    first.deleteLater()

    # Closed from the start page without ever opening a document.
    second = MainWindow()
    assert second.is_welcome_mode()
    second._save_settings()
    second.deleteLater()

    third = MainWindow()
    try:
        third.open_path(sample_pdf)
        assert third._sidebar.isHidden()
        assert not third._properties_dock.isHidden()
        assert not third._tool_rail.isHidden()
    finally:
        third.close()


def _mime(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def test_dragging_a_file_lights_up_the_drop_zone(
    qapp, sample_pdf: Path
) -> None:
    from PySide6.QtGui import QDragLeaveEvent

    win = MainWindow()
    try:
        ws = win._welcome
        mime = _mime(sample_pdf)
        enter = QDragEnterEvent(
            QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
        )
        win.dragEnterEvent(enter)
        assert enter.isAccepted()
        assert ws.is_drag_active()
        assert ws._drop_zone.property("dragActive") is True
        win.dragLeaveEvent(QDragLeaveEvent())
        assert not ws.is_drag_active()

        win.dragEnterEvent(
            QDragEnterEvent(
                QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        drop = QDropEvent(
            QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
        )
        win.dropEvent(drop)
        assert not ws.is_drag_active()
        assert win._doc is not None
        assert win._central.currentWidget() is win._view
    finally:
        win.close()


def test_other_files_do_not_light_up_the_drop_zone(
    qapp, tmp_path: Path
) -> None:
    other = tmp_path / "notes.txt"
    other.write_text("x")
    mime = _mime(other)  # the event does not own its mime data
    win = MainWindow()
    try:
        enter = QDragEnterEvent(
            QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton,
            Qt.NoModifier,
        )
        win.dragEnterEvent(enter)
        assert not enter.isAccepted()
        assert not win._welcome.is_drag_active()
    finally:
        win.close()


def test_theme_recolors_the_start_page(qapp) -> None:
    ws = WelcomeScreen()
    ws.set_colors(LIGHT)
    light = ws._drop_icon.pixmap().toImage()
    ws.set_colors(DARK)
    dark = ws._drop_icon.pixmap().toImage()
    assert light != dark

    win = MainWindow()
    try:
        win._set_theme(Theme.DARK)
        assert win._welcome._tokens is DARK
        win._set_theme(Theme.LIGHT)
        assert win._welcome._tokens is LIGHT
    finally:
        qapp.setStyleSheet("")
        win.close()


def test_cards_are_keyboard_reachable_buttons(qapp, sample_pdf: Path) -> None:
    ws = WelcomeScreen()
    ws.set_recent([str(sample_pdf)])
    card = ws.cards()[0]
    assert isinstance(card, QPushButton)
    assert card.focusPolicy() & Qt.TabFocus
    assert card.accessibleName() == sample_pdf.name
