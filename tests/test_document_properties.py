"""Document properties: dimensions, metadata, file (2026-09-28)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import fitz
import pytest

from annoter.services import doc_info
from annoter.services.doc_info import PageGroup, paper_format, pdf_date_text


def _mm(v: float) -> float:
    return v * 72.0 / 25.4


@pytest.mark.parametrize(
    "w_mm, h_mm, name",
    [
        (841, 1189, "A0 portrait"),
        (1189, 841, "A0 landscape"),
        (420, 297, "A3 landscape"),
        (210, 297, "A4 portrait"),
        (211, 296, "A4 portrait"),  # within the 2 mm tolerance
        (215.9, 279.4, "Letter portrait"),
        (431.8, 279.4, "Tabloid (ANSI B) landscape"),
        (300, 300, "Custom"),
        (500, 250, "Custom"),
    ],
)
def test_paper_format_names(w_mm, h_mm, name) -> None:
    assert paper_format(_mm(w_mm), _mm(h_mm)) == name


def test_pdf_dates_are_made_readable() -> None:
    assert pdf_date_text("D:20240315143000+01'00'") == "15 Mar 2024, 14:30"
    assert pdf_date_text("D:20250910") == "10 Sep 2025"
    assert pdf_date_text("2023") == "1 Jan 2023"
    assert pdf_date_text("") == ""
    assert pdf_date_text("yesterday") == "yesterday"
    assert pdf_date_text("D:20241399") == "D:20241399"  # month 13


def _doc(tmp_path: Path) -> Path:
    path = tmp_path / "plan.pdf"
    d = fitz.open()
    d.new_page(width=_mm(420), height=_mm(297))
    d.new_page(width=_mm(420), height=_mm(297))
    d.new_page(width=_mm(210), height=_mm(297))
    d.new_page(width=_mm(420), height=_mm(297))
    d[3].set_rotation(90)
    d.set_metadata(
        {
            "title": "Bracket assembly",
            "author": "Design office",
            "creator": "CATIA V5",
            "creationDate": "D:20240315143000+01'00'",
        }
    )
    d.save(str(path))
    d.close()
    return path


def test_collect_groups_pages_and_reads_metadata(tmp_path: Path) -> None:
    path = _doc(tmp_path)
    doc = fitz.open(str(path))
    info = doc_info.collect(doc, path)
    doc.close()
    assert info.page_count == 4
    assert info.file_size == path.stat().st_size
    assert info.pdf_version.startswith("PDF 1.")
    groups = [(g.first, g.last, g.format_text(), g.rotation) for g in info.groups]
    # The rotated A3 shows up turned: 297 x 420 as displayed.
    assert groups == [
        (1, 2, "A3 landscape", 0),
        (3, 3, "A4 portrait", 0),
        (4, 4, "A3 portrait", 90),
    ]
    assert info.uniform_size() is None
    rows = dict(info.metadata_rows())
    assert rows["Title"] == "Bracket assembly"
    assert rows["Created with"] == "CATIA V5"
    assert rows["Created"] == "15 Mar 2024, 14:30"
    assert rows["Subject"] == ""


def test_page_group_texts() -> None:
    g = PageGroup(1, 3, _mm(297), _mm(420))
    assert g.pages_text() == "Pages 1-3"
    assert g.size_text() == "297 x 420 mm"
    assert PageGroup(2, 2, 10, 10).pages_text() == "Page 2"


def test_missing_file_has_no_size(tmp_path: Path) -> None:
    d = fitz.open()
    d.new_page()
    info = doc_info.collect(d, tmp_path / "never-saved.pdf")
    d.close()
    assert info.file_size is None and info.file_modified is None


# ----------------------------------------------------------------------
# widgets
# ----------------------------------------------------------------------
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.views.document_properties import (  # noqa: E402
    EMPTY,
    DocumentPropertiesDialog,
)
from annoter.views.items.shapes import RectangleItem  # noqa: E402
from annoter.views.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_dialog_shows_everything(qapp, tmp_path: Path) -> None:
    path = _doc(tmp_path)
    doc = fitz.open(str(path))
    info = doc_info.collect(doc, path)
    doc.close()
    dlg = DocumentPropertiesDialog(info, 5, saved=False)
    assert dlg.value("File") == "plan.pdf"
    assert dlg.value("Pages") == "4"
    assert dlg.value("Page size") == "Mixed (see Pages)"
    assert dlg.value("Annotations") == "5 annotations (some not saved yet)"
    assert dlg.value("Author") == "Design office"
    assert dlg.page_rows()[1] == ("Page 3", "A4 portrait", "210 x 297 mm", "")
    assert dlg.page_rows()[2][3] == "90° rotation"
    # Empty metadata shows a dash, not a blank.
    from PySide6.QtWidgets import QLabel

    texts = [lbl.text() for lbl in dlg.findChildren(QLabel)]
    assert EMPTY in texts


@pytest.fixture
def win(qapp, tmp_path: Path):
    path = _doc(tmp_path)
    w = MainWindow()
    w.open_path(path)
    yield w
    w._on_close()
    w.close()


def test_main_window_entry_points(win, monkeypatch) -> None:
    labels = [e.label for e in win._command_entries()]
    assert "Document Properties..." in labels
    assert win.act_doc_properties.shortcut().toString() == "Alt+Return"
    win._scene.push_add(RectangleItem(QRectF(10, 10, 40, 40)))
    dlg = win._build_document_properties()
    assert dlg.value("Annotations").startswith("1 annotation")
    assert "not saved" in dlg.value("Annotations")
    opened: list[str] = []
    monkeypatch.setattr(
        DocumentPropertiesDialog,
        "exec",
        lambda self: opened.append(self.value("File")) or 0,
    )
    QTest.mouseClick(win._top_bar.title_label, Qt.LeftButton)  # the name
    win.act_doc_properties.trigger()  # menu, palette, Alt+Enter
    for it in win._scene.selectedItems():
        it.setSelected(False)
    win._properties_dock.field("Document properties").click()  # inspector
    assert opened == ["plan.pdf"] * 3


def test_inspector_document_rows(win, monkeypatch) -> None:
    monkeypatch.setattr(DocumentPropertiesDialog, "exec", lambda self: 0)
    dock = win._properties_dock
    for it in win._scene.selectedItems():
        it.setSelected(False)
    dock.set_items([])
    assert dock.field("File").text() == "plan.pdf"
    assert dock.field("Pages").text() == "4"
    assert dock.field("This page").text() == "A3 landscape, 420 x 297 mm"
    win._show_page(2)
    assert dock.field("This page").text() == "A4 portrait, 210 x 297 mm"
    fired: list[bool] = []
    dock.documentPropertiesRequested.connect(lambda: fired.append(True))
    dock.field("Document properties").click()
    assert fired
    win._on_close()
    with pytest.raises(KeyError):
        dock.field("File")
