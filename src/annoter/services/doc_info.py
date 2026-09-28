"""What there is to know about the open document (2026-09-28).

Collected once from the fitz.Document and the file on disk, for the
Document Properties dialog and the Inspector's "Document" section:
file, PDF version, page sizes (grouped, with their paper format),
metadata (title, author, dates...). Pure data plus the formatting that
is domain knowledge (paper formats, PDF dates); the widgets lay it out.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import fitz

MM_PER_PT = 25.4 / 72.0

# Portrait width x height in millimetres. Checked in this order.
PAPER_FORMATS: tuple[tuple[str, float, float], ...] = (
    ("A0", 841.0, 1189.0),
    ("A1", 594.0, 841.0),
    ("A2", 420.0, 594.0),
    ("A3", 297.0, 420.0),
    ("A4", 210.0, 297.0),
    ("A5", 148.0, 210.0),
    ("Letter", 215.9, 279.4),
    ("Legal", 215.9, 355.6),
    ("Tabloid (ANSI B)", 279.4, 431.8),
    ("ANSI C", 431.8, 558.8),
    ("ANSI D", 558.8, 863.6),
    ("ANSI E", 863.6, 1117.6),
)
_FORMAT_TOLERANCE_MM = 2.0

METADATA_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("author", "Author"),
    ("subject", "Subject"),
    ("keywords", "Keywords"),
    ("creator", "Created with"),
    ("producer", "PDF producer"),
    ("creationDate", "Created"),
    ("modDate", "Modified"),
)

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def pt_to_mm(value: float) -> float:
    return value * MM_PER_PT


def paper_format(width_pt: float, height_pt: float) -> str:
    """"A3 landscape", "Letter portrait", or "Custom"."""
    w, h = pt_to_mm(width_pt), pt_to_mm(height_pt)
    short, long_ = sorted((w, h))
    for name, fw, fh in PAPER_FORMATS:
        if (
            abs(short - fw) <= _FORMAT_TOLERANCE_MM
            and abs(long_ - fh) <= _FORMAT_TOLERANCE_MM
        ):
            if abs(w - h) < 1e-6:
                return name
            return f"{name} {'landscape' if w > h else 'portrait'}"
    return "Custom"


def size_mm_text(width_pt: float, height_pt: float) -> str:
    return f"{pt_to_mm(width_pt):.0f} x {pt_to_mm(height_pt):.0f} mm"


def pdf_date_text(raw: str) -> str:
    """A PDF date ("D:20240315143000+01'00'") as "15 Mar 2024, 14:30";
    anything unreadable comes back as it is ("" stays "")."""
    m = re.match(r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?", raw or "")
    if not m:
        return raw or ""
    year = int(m.group(1))
    month = int(m.group(2) or 1)
    day = int(m.group(3) or 1)
    if not 1 <= month <= 12:
        return raw
    text = f"{day} {_MONTHS[month - 1]} {year}"
    if m.group(4) is not None:
        text += f", {int(m.group(4)):02d}:{int(m.group(5) or 0):02d}"
    return text


@dataclass(frozen=True)
class PageGroup:
    """Consecutive pages sharing one size and rotation (1-based)."""

    first: int
    last: int
    width_pt: float
    height_pt: float
    rotation: int = 0

    def pages_text(self) -> str:
        if self.first == self.last:
            return f"Page {self.first}"
        return f"Pages {self.first}-{self.last}"

    def format_text(self) -> str:
        return paper_format(self.width_pt, self.height_pt)

    def size_text(self) -> str:
        return size_mm_text(self.width_pt, self.height_pt)


@dataclass
class DocumentInfo:
    path: Path
    file_size: int | None
    file_modified: float | None
    pdf_version: str
    page_count: int
    groups: list[PageGroup] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def file_name(self) -> str:
        return self.path.name

    @property
    def folder(self) -> str:
        return str(self.path.parent)

    def uniform_size(self) -> PageGroup | None:
        """The one page size when every page shares it, else None."""
        if not self.groups:
            return None
        g = self.groups[0]
        same = all(
            abs(o.width_pt - g.width_pt) < 0.5
            and abs(o.height_pt - g.height_pt) < 0.5
            for o in self.groups
        )
        return g if same else None

    def metadata_rows(self) -> list[tuple[str, str]]:
        """(label, value) for every metadata field, dates made readable;
        empty values stay empty (the view shows a dash)."""
        rows = []
        for key, label in METADATA_FIELDS:
            value = (self.metadata.get(key) or "").strip()
            if key in ("creationDate", "modDate"):
                value = pdf_date_text(value)
            rows.append((label, value))
        return rows


def page_groups(doc: fitz.Document) -> list[PageGroup]:
    groups: list[PageGroup] = []
    for i in range(doc.page_count):
        page = doc[i]
        w, h = page.rect.width, page.rect.height
        rot = int(page.rotation or 0)
        last = groups[-1] if groups else None
        if (
            last is not None
            and last.last == i
            and abs(last.width_pt - w) < 0.5
            and abs(last.height_pt - h) < 0.5
            and last.rotation == rot
        ):
            groups[-1] = PageGroup(last.first, i + 1, last.width_pt,
                                   last.height_pt, rot)
        else:
            groups.append(PageGroup(i + 1, i + 1, w, h, rot))
    return groups


def collect(doc: fitz.Document, path: Path | str) -> DocumentInfo:
    path = Path(path)
    try:
        st = os.stat(path)
        size, mtime = st.st_size, st.st_mtime
    except OSError:  # a scratch document never saved, a removed file
        size, mtime = None, None
    meta = dict(doc.metadata or {})
    return DocumentInfo(
        path=path,
        file_size=size,
        file_modified=mtime,
        pdf_version=str(meta.get("format") or ""),
        page_count=doc.page_count,
        groups=page_groups(doc),
        metadata={k: str(v or "") for k, v in meta.items()},
    )


def file_date_text(ts: float | None) -> str:
    if ts is None:
        return ""
    d = datetime.fromtimestamp(ts)
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}, {d:%H:%M}"
