"""AnnotationTree: every annotation of the document, grouped by page.

UI redesign, Lot E. Replaces the right-side `AnnotationListDock`, which
listed only the current page as bare type names ("Rectangle", "Text").
This is the "Annotations" tab of the left `DocumentSidebar`:

- one group per page ("Page 3 . 5"), the current page expanded;
- each row names the annotation by its content when it has some (the
  text of a note, the stamp label, the GD&T characteristic) with its
  type underneath, and a glyph in the annotation's own color;
- a filter field and type chips (All / Shapes / Lines / Text / GD&T /
  Stamps);
- clicking a row selects the annotation; a row on another page first
  jumps to that page.

Safety: the tree never calls into an annotation outside `set_annotations`
(which receives live items). Selection sync compares object identities
only, so a stale row can never touch a deleted C++ item -- the old list
did (`sync_selection_from_scene` during `_on_close`), which crashed.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QGridLayout,
    QLabel,
    QLineEdit,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from annoter.model.styles import EndStyle
from annoter.views.items.base import AnnotationItem
from annoter.views.line_icons import line_pixmap

_ROLE_ID = Qt.UserRole + 1  # id() of the annotation (child rows)
_ROLE_PAGE = Qt.UserRole + 2  # page index (all rows)
_ROLE_SUB = Qt.UserRole + 3  # muted second line (child rows)

KIND_NAMES: dict[str, str] = {
    "rect": "Rectangle",
    "ellipse": "Ellipse",
    "cloud": "Revision cloud",
    "polygon": "Polygon",
    "line": "Line",
    "arrow": "Arrow",
    "polyline": "Polyline",
    "ink": "Freehand",
    "text": "Text",
    "callout": "Callout",
    "note": "Sticky note",
    "stamp": "Stamp",
    "gdt": "GD&T frame",
    "dimension": "Dimension",
}

KIND_GLYPHS: dict[str, str] = {
    "rect": "rectangle",
    "ellipse": "ellipse",
    "cloud": "cloud",
    "polygon": "polygon",
    "line": "line",
    "arrow": "arrow",
    "polyline": "polyline",
    "ink": "freehand",
    "text": "text",
    "callout": "text",
    "note": "note",
    "stamp": "stamp",
    "gdt": "gdt",
    "dimension": "dimension",
}

# Filter chips: (label, kinds). "All" has no kinds.
CATEGORIES: list[tuple[str, frozenset[str]]] = [
    ("All", frozenset()),
    ("Shapes", frozenset({"rect", "ellipse", "cloud", "polygon"})),
    ("Lines", frozenset({"line", "arrow", "polyline", "ink"})),
    ("Text", frozenset({"text", "callout", "note"})),
    ("GD&T", frozenset({"gdt", "dimension"})),
    ("Stamps", frozenset({"stamp"})),
]

_TITLE_MAX = 40


def describe(item: AnnotationItem) -> tuple[str, str, str]:
    """(title, subtitle, glyph) for a row.

    The title is what the user wrote or chose when there is such a thing
    (note text, stamp label, GD&T characteristic), else
    the type name; the subtitle is then the type name.
    """
    kind = item.KIND or ""
    name = KIND_NAMES.get(kind, kind.capitalize() or "Annotation")
    glyph = KIND_GLYPHS.get(kind, "rectangle")
    if kind == "arrow":
        ends = (item.start_end(), item.end_end())
        if ends == (EndStyle.NONE, EndStyle.NONE):
            name, glyph = "Line", "line"
        elif EndStyle.NONE not in ends:
            name, glyph = "Double arrow", "double-arrow"
    content = ""
    if kind == "gdt":
        content = item.label().removeprefix("GD&T ").strip()
    else:
        getter = getattr(item, "text", None)
        if callable(getter):
            try:
                content = str(getter() or "")
            except Exception:
                content = ""
    content = content.strip().splitlines()[0] if content.strip() else ""
    if len(content) > _TITLE_MAX:
        content = content[: _TITLE_MAX - 1].rstrip() + "…"
    if content:
        return content, name, glyph
    return name, "", glyph


class _RowDelegate(QStyledItemDelegate):
    """Two-line child rows (title + muted type); page headers bold."""

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        base = super().sizeHint(option, index)
        is_header = index.data(_ROLE_ID) is None
        return QSize(base.width(), 30 if is_header else 40)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title = opt.text
        opt.text = ""
        # Draw the glyph ourselves (next to the two text lines), so the
        # style only paints the row background.
        deco = index.data(Qt.DecorationRole)
        opt.icon = QIcon()
        opt.features &= ~QStyleOptionViewItem.HasDecoration
        style = opt.widget.style() if opt.widget else None
        if style is not None:
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        pal = option.palette
        r: QRect = option.rect.adjusted(6, 0, -8, 0)
        painter.save()
        is_header = index.data(_ROLE_ID) is None
        font = QFont(option.font)
        if is_header:
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(pal.placeholderText().color())
            painter.drawText(r, Qt.AlignVCenter | Qt.AlignLeft, title)
            painter.restore()
            return
        glyph_rect = QRect(r.left(), r.center().y() - 8, 16, 16)
        if isinstance(deco, QPixmap) and not deco.isNull():
            painter.drawPixmap(glyph_rect, deco)
        elif isinstance(deco, QIcon) and not deco.isNull():
            deco.paint(painter, glyph_rect)
        text_r = r.adjusted(26, 3, 0, -3)
        sub = index.data(_ROLE_SUB) or ""
        painter.setPen(pal.text().color())
        painter.setFont(font)
        if sub:
            top = QRect(text_r.left(), text_r.top(), text_r.width(), text_r.height() // 2)
            bottom = QRect(
                text_r.left(),
                text_r.top() + text_r.height() // 2,
                text_r.width(),
                text_r.height() // 2,
            )
            painter.drawText(
                top,
                Qt.AlignVCenter | Qt.AlignLeft,
                painter.fontMetrics().elidedText(title, Qt.ElideRight, top.width()),
            )
            small = QFont(font)
            small.setPointSizeF(max(6.0, font.pointSizeF() - 1.0))
            painter.setFont(small)
            painter.setPen(pal.placeholderText().color())
            painter.drawText(bottom, Qt.AlignVCenter | Qt.AlignLeft, sub)
        else:
            painter.drawText(text_r, Qt.AlignVCenter | Qt.AlignLeft, title)
        painter.restore()


class AnnotationTree(QWidget):
    """Filterable tree of annotations, grouped by page."""

    selectionRequested = Signal(list)  # annotations on the current page
    jumpRequested = Signal(int, object)  # (page, annotation) elsewhere
    pageRequested = Signal(int)  # a page header was clicked
    deleteRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AnnotationTreePanel")
        self._pages: dict[int, list[AnnotationItem]] = {}
        self._current_page = 0
        self._by_id: dict[int, AnnotationItem] = {}
        self._rows: dict[int, QTreeWidgetItem] = {}
        self._syncing = False
        self._category = 0

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        self._filter = QLineEdit(self)
        self._filter.setObjectName("SidebarFilter")
        self._filter.setPlaceholderText("Filter annotations")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(lambda _t: self._rebuild())
        col.addWidget(self._filter)

        chips = QGridLayout()
        chips.setSpacing(4)
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        self._chips: list[QToolButton] = []
        for i, (label, _kinds) in enumerate(CATEGORIES):
            chip = QToolButton(self)
            chip.setObjectName("FilterChip")
            chip.setText(label.replace("&", "&&"))  # no mnemonic
            chip.setCheckable(True)
            chip.setChecked(i == 0)
            chip.setFocusPolicy(Qt.NoFocus)
            chip.setToolButtonStyle(Qt.ToolButtonTextOnly)
            chip.clicked.connect(lambda _c=False, n=i: self.set_category(n))
            self._chip_group.addButton(chip)
            self._chips.append(chip)
            chips.addWidget(chip, i // 3, i % 3)
        col.addLayout(chips)

        self._tree = QTreeWidget(self)
        self._tree.setObjectName("AnnotationTree")
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(10)
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(False)
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.setItemDelegate(_RowDelegate(self._tree))
        self._tree.setIconSize(QSize(16, 16))
        self._tree.itemSelectionChanged.connect(self._on_tree_selection)
        self._tree.itemClicked.connect(self._on_item_clicked)
        col.addWidget(self._tree, 1)

        self._empty = QLabel("", self)
        self._empty.setObjectName("SidebarEmpty")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        col.addWidget(self._empty)
        self._rebuild()

    # ------------------------------------------------------------------
    # content
    # ------------------------------------------------------------------
    def set_annotations(
        self, pages: dict[int, list[AnnotationItem]], current_page: int
    ) -> None:
        """Replace the content. `pages` must hold live items only."""
        self._pages = {p: list(v) for p, v in pages.items() if v}
        self._current_page = int(current_page)
        self._rebuild()

    def clear_all(self) -> None:
        """Forget every item (call before the scene deletes them)."""
        self._pages = {}
        self._rebuild()

    def total(self) -> int:
        return sum(len(v) for v in self._pages.values())

    def set_category(self, index: int) -> None:
        self._category = max(0, min(len(CATEGORIES) - 1, int(index)))
        self._chips[self._category].setChecked(True)
        self._rebuild()

    def set_filter_text(self, text: str) -> None:
        self._filter.setText(text)

    def visible_titles(self) -> list[str]:
        out: list[str] = []
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            for j in range(top.childCount()):
                out.append(top.child(j).text(0))
        return out

    def page_headers(self) -> list[str]:
        return [
            self._tree.topLevelItem(i).text(0)
            for i in range(self._tree.topLevelItemCount())
        ]

    def _matches(self, title: str, sub: str, kind: str, terms: list[str]) -> bool:
        kinds = CATEGORIES[self._category][1]
        if kinds and kind not in kinds:
            return False
        hay = f"{title} {sub}".lower()
        return all(t in hay for t in terms)

    def _rebuild(self) -> None:
        self._syncing = True
        try:
            self._tree.clear()
            self._by_id = {}
            self._rows = {}
            terms = self._filter.text().strip().lower().split()
            shown = 0
            for page in sorted(self._pages):
                rows: list[tuple[AnnotationItem, str, str, str]] = []
                for it in self._pages[page]:
                    title, sub, glyph = describe(it)
                    if self._matches(title, sub, it.KIND or "", terms):
                        rows.append((it, title, sub, glyph))
                if not rows:
                    continue
                header = QTreeWidgetItem(
                    [f"Page {page + 1}  ·  {len(rows)}"]
                )
                header.setData(0, _ROLE_PAGE, page)
                header.setFlags(Qt.ItemIsEnabled)
                self._tree.addTopLevelItem(header)
                for it, title, sub, glyph in rows:
                    row = QTreeWidgetItem([title])
                    row.setData(0, _ROLE_ID, id(it))
                    row.setData(0, _ROLE_PAGE, page)
                    row.setData(0, _ROLE_SUB, sub)
                    row.setToolTip(0, f"{title} ({sub})" if sub else title)
                    pm = line_pixmap(glyph, QColor(it.color()), 32)
                    pm.setDevicePixelRatio(2.0)
                    row.setIcon(0, pm)
                    header.addChild(row)
                    self._by_id[id(it)] = it
                    self._rows[id(it)] = row
                    shown += 1
                header.setExpanded(bool(terms) or page == self._current_page)
            total = self.total()
            if total == 0:
                self._empty.setText(
                    "No annotations yet.\nPick a tool on the left and "
                    "draw on the page."
                )
            elif shown == 0:
                self._empty.setText("No annotation matches the filter.")
            self._empty.setVisible(shown == 0)
            self._tree.setVisible(shown > 0)
        finally:
            self._syncing = False

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------
    def sync_selection(self, selected: Iterable[object]) -> None:
        """Mirror the scene selection. Identity only: no item is called."""
        ids = {id(it) for it in selected}
        self._syncing = True
        try:
            for key, row in self._rows.items():
                row.setSelected(key in ids)
        finally:
            self._syncing = False

    def _on_item_clicked(self, row: QTreeWidgetItem, _col: int) -> None:
        if row.data(0, _ROLE_ID) is None:
            page = row.data(0, _ROLE_PAGE)
            if page is not None and page != self._current_page:
                self.pageRequested.emit(int(page))
            row.setExpanded(not row.isExpanded())

    def _on_tree_selection(self) -> None:
        if self._syncing:
            return
        rows = [r for r in self._tree.selectedItems() if r.data(0, _ROLE_ID)]
        if not rows:
            self.selectionRequested.emit([])
            return
        here = [
            self._by_id[r.data(0, _ROLE_ID)]
            for r in rows
            if r.data(0, _ROLE_PAGE) == self._current_page
            and r.data(0, _ROLE_ID) in self._by_id
        ]
        if here:
            self.selectionRequested.emit(here)
            return
        # Only rows on another page: jump there with the last clicked.
        target = self._tree.currentItem() or rows[-1]
        key = target.data(0, _ROLE_ID)
        if key in self._by_id:
            self.jumpRequested.emit(
                int(target.data(0, _ROLE_PAGE)), self._by_id[key]
            )

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and not (
            self._filter.hasFocus()
        ):
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
