"""Headless smoke tests: PyMuPDF only, no Qt import.

Verifies that we can open a PDF, count its pages, and read page
dimensions / DPI.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from annoter.model.document import PdfDocument


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=842, height=595)  # A4 landscape
        page.insert_text((72, 72), f"Page {i + 1}")
    doc.save(str(path))
    doc.close()
    return path


def test_open_pdf_page_count(sample_pdf: Path) -> None:
    pdf = PdfDocument(sample_pdf)
    try:
        assert pdf.page_count == 3
    finally:
        pdf.close()


def test_page_size_in_points(sample_pdf: Path) -> None:
    pdf = PdfDocument(sample_pdf)
    try:
        w, h = pdf.page_size_pt(0)
        assert w == pytest.approx(842)
        assert h == pytest.approx(595)
    finally:
        pdf.close()


def test_open_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        PdfDocument(tmp_path / "does_not_exist.pdf")


def test_close_is_idempotent(sample_pdf: Path) -> None:
    pdf = PdfDocument(sample_pdf)
    pdf.close()
    pdf.close()  # must not raise
