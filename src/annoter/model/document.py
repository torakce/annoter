"""PdfDocument: thin wrapper around `fitz.Document`.

Owns the open file, exposes page metadata, and serves as the boundary
between PyMuPDF and the rest of the application.
"""

from __future__ import annotations

from pathlib import Path

import fitz


class PdfDocument:
    """Wrapper around a fitz.Document.

    Encrypted PDFs are rejected (we do not prompt for passwords in v1).
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"PDF not found: {self.path}")
        self._doc = fitz.open(str(self.path))
        if self._doc.needs_pass:
            self._doc.close()
            raise ValueError("Encrypted PDFs are not supported.")

    def close(self) -> None:
        if self._doc is not None and not self._doc.is_closed:
            self._doc.close()

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def page(self, index: int) -> fitz.Page:
        return self._doc[index]

    def page_size_pt(self, index: int) -> tuple[float, float]:
        rect = self._doc[index].rect
        return rect.width, rect.height

    @property
    def raw(self) -> fitz.Document:
        """Underlying fitz.Document. Use sparingly."""
        return self._doc
