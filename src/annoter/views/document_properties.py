"""DocumentPropertiesDialog: file, pages and metadata of the open PDF.

Read-only, every value selectable so it can be copied. Three blocks:

    General    file name, folder, size on disk, last saved, PDF version,
               pages, page size, annotations
    Pages      one row per run of pages sharing a size: pages, paper
               format (A3 landscape, Letter portrait, Custom), size in
               mm, rotation
    Metadata   title, author, subject, keywords, the application that
               created it, the PDF producer, created / modified dates

Fed a `services.doc_info.DocumentInfo` plus the annotation count
(annotations live in the scene, not in the fitz.Document, until saved).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from annoter.services.doc_info import DocumentInfo, file_date_text
from annoter.views.welcome_screen import annotations_caption, format_size

EMPTY = "—"  # em dash for an empty field


def _value(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text if text else EMPTY, parent)
    label.setObjectName("DocPropValue" if text else "DocPropEmpty")
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setWordWrap(True)
    return label


class _Block(QFrame):
    def __init__(self, title: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("DocPropBlock")
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)
        head = QLabel(title.upper(), self)
        head.setObjectName("InspectorSectionTitle")
        col.addWidget(head)
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(16)
        self.form.setVerticalSpacing(6)
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        col.addLayout(self.form)
        self.col = col

    def add_row(self, label: str, value: str) -> QLabel:
        name = QLabel(label, self)
        name.setObjectName("InspectorLabel")
        field = _value(value, self)
        self.form.addRow(name, field)
        return field


class DocumentPropertiesDialog(QDialog):
    def __init__(
        self,
        info: DocumentInfo,
        annotation_count: int,
        parent: QWidget | None = None,
        *,
        saved: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DocumentProperties")
        self.setWindowTitle(f"Document Properties - {info.file_name}")
        self.setMinimumWidth(520)
        self._info = info
        self._values: dict[str, str] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget(scroll)
        body.setObjectName("DocPropBody")
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---- general ---------------------------------------------------
        general = _Block("General", body)
        uniform = info.uniform_size()
        if uniform is not None:
            size = f"{uniform.format_text()}, {uniform.size_text()}"
        else:
            size = "Mixed (see Pages)"
        rows = [
            ("File", info.file_name),
            ("Folder", info.folder),
            (
                "Size on disk",
                format_size(info.file_size) if info.file_size else "",
            ),
            ("Last saved", file_date_text(info.file_modified)),
            ("PDF version", info.pdf_version.replace("PDF ", "")),
            ("Pages", str(info.page_count)),
            ("Page size", size),
            (
                "Annotations",
                annotations_caption(annotation_count)
                + ("" if saved else " (some not saved yet)"),
            ),
        ]
        for label, value in rows:
            general.add_row(label, value)
            self._values[label] = value
        col.addWidget(general)

        # ---- pages -----------------------------------------------------
        pages = _Block("Pages", body)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self._page_rows: list[tuple[str, str, str, str]] = []
        for r, g in enumerate(info.groups):
            cells = (
                g.pages_text(),
                g.format_text(),
                g.size_text(),
                f"{g.rotation}° rotation" if g.rotation else "",
            )
            self._page_rows.append(cells)
            for c, text in enumerate(cells):
                if text:
                    grid.addWidget(_value(text, pages), r, c)
        grid.setColumnStretch(3, 1)
        pages.col.addLayout(grid)
        col.addWidget(pages)

        # ---- metadata --------------------------------------------------
        meta = _Block("Metadata", body)
        for label, value in info.metadata_rows():
            meta.add_row(label, value)
            self._values[label] = value
        col.addWidget(meta)
        col.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.folder_button = buttons.addButton(
            "Show in Folder", QDialogButtonBox.ActionRole
        )
        self.folder_button.setEnabled(Path(info.path).parent.is_dir())
        self.folder_button.setAutoDefault(False)
        self.folder_button.clicked.connect(self._show_in_folder)
        buttons.button(QDialogButtonBox.Close).setDefault(True)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.resize(560, 640)

    # ------------------------------------------------------------------
    def value(self, label: str) -> str:
        """Text of a General / Metadata row (tests)."""
        return self._values[label]

    def page_rows(self) -> list[tuple[str, str, str, str]]:
        return list(self._page_rows)

    def _show_in_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._info.folder))
