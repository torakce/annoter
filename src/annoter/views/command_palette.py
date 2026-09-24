"""CommandPalette: Ctrl+K search over every command, tool and recent file.

A frameless popup with a search field and a result list. Entries are
plain `CommandEntry` records built by MainWindow from its main menu (so
every menu command is searchable with no extra bookkeeping), the drawing
tools and the recent files. Picking an entry closes the popup, then runs
it.

Only enabled commands are listed: a greyed-out result the user cannot
run is noise in a search box.

UI redesign, Lot H (polish, following the mock-up):

- results are grouped under their menu name ("VIEW", "EDIT", ...),
  groups ordered by their best match; the typed text is shown in bold
  inside each label; shortcuts are drawn as key caps;
- before anything is typed the list opens on "Recently used" (the last
  commands run from the palette) and "Recent files", then every command
  by menu;
- equally good text matches are ranked by how often and how recently
  each was run from the palette (`services.command_usage`), so the
  commands a user reaches for float up;
- recent files are searchable by name and by folder.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from PySide6.QtCore import (
    QEvent,
    QObject,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)
import shiboken6

from annoter.services.command_usage import CommandUsage
from annoter.services.tokens import LIGHT, MONO_FONT_STACK, Tokens
from annoter.views.line_icons import line_pixmap

_ROLE_ENTRY = Qt.UserRole + 1
_ROLE_KIND = Qt.UserRole + 2
_ROLE_SPANS = Qt.UserRole + 3
_ROLE_CONTEXT = Qt.UserRole + 4
_KIND_HEADER = "header"
_KIND_ENTRY = "entry"

PALETTE_WIDTH = 620
ROW_H = 38
HEADER_H = 28
MAX_LIST_H = 360

FILES_GROUP = "Recent files"
RECENT_GROUP = "Recently used"
RECENT_COMMANDS = 5
RECENT_FILES = 5

_WORD_SPLIT = re.compile(r"[^\w°]+")


@dataclass
class CommandEntry:
    label: str
    group: str
    run: Callable[[], None]
    shortcut: str = ""
    icon: QIcon = field(default_factory=QIcon)
    enabled: bool = True
    # Extra searchable text shown on the right when there is no
    # shortcut (a recent file's folder).
    detail: str = ""

    @property
    def key(self) -> str:
        """Stable id for usage tracking."""
        return f"{self.group}/{self.label}"

    def matches(self, terms: list[str]) -> bool:
        hay = f"{self.label} {self.group} {self.detail}".lower()
        return all(t in hay for t in terms)

    def rank(self, query: str) -> tuple[int, str]:
        """Text-match tier (lower is better), then the label."""
        low = self.label.lower()
        if not query:
            return (0, "")
        terms = query.split()
        words = _words(low)
        if low.startswith(query):
            tier = 0
        elif any(w.startswith(query) for w in words):
            tier = 1
        elif all(any(w.startswith(t) for w in words) for t in terms):
            tier = 2
        elif all(t in low for t in terms):
            tier = 3
        else:
            tier = 4  # some term matched the group or the detail only
        return (tier, low)


def _words(text: str) -> list[str]:
    return [w for w in _WORD_SPLIT.split(text.lower()) if w]


def _clean(text: str) -> str:
    """Menu text without its mnemonic ampersands."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def shortcut_keys(text: str) -> list[str]:
    """ "Ctrl+Shift+R" -> ["Ctrl", "Shift", "R"]; "Ctrl++" keeps its plus."""
    parts = text.split("+")
    keys: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part == "" and i + 1 < len(parts) and parts[i + 1] == "":
            keys.append("+")
            i += 2
            continue
        if part:
            keys.append(part.strip())
        i += 1
    return keys


def match_spans(label: str, terms: Iterable[str]) -> list[tuple[int, int]]:
    """Character ranges of `label` to show in bold for the typed terms:
    per term, its first occurrence at a word start, else its first
    occurrence anywhere. Overlaps are merged."""
    low = label.lower()
    spans: list[tuple[int, int]] = []
    for term in terms:
        if not term:
            continue
        at = -1
        for m in re.finditer(re.escape(term), low):
            i = m.start()
            if i == 0 or not (low[i - 1].isalnum()):
                at = i
                break
        if at < 0:
            at = low.find(term)
        if at >= 0:
            spans.append((at, at + len(term)))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def entries_from_menu(
    menu: QMenu,
    group: str = "File",
    skip: Iterable[QAction] = (),
) -> list[CommandEntry]:
    """Walk a QMenu tree. Top-level submenus name the group of their
    entries; deeper submenus keep the group of their top-level parent.
    Actions in `skip` are left out (e.g. the recent-file entries of the
    Open Recent menu, which the palette lists its own way).

    Submenus are found through `QMenu.menuAction()`, never through
    `QAction.menu()`: in PySide6 the latter leaves the Python wrapper
    the application keeps for a menu built with `addMenu(title)` dead
    ("Internal C++ object already deleted") once the walk's own
    references go away -- the first Ctrl+K then broke later updates of
    the Open Recent menu and the theme switch.
    """
    submenus = {
        _ptr(sub.menuAction()): sub
        for sub in menu.findChildren(QMenu)
    }
    seen = {_ptr(a) for a in skip}
    out: list[CommandEntry] = []
    _walk(menu, group, True, submenus, seen, out)
    return out


def _ptr(obj: QObject) -> int:
    return shiboken6.getCppPointer(obj)[0]


def _walk(
    menu: QMenu,
    group: str,
    top: bool,
    submenus: dict[int, QMenu],
    seen: set[int],
    out: list[CommandEntry],
) -> None:
    for act in menu.actions():
        if act.isSeparator() or not act.isVisible():
            continue
        key = _ptr(act)
        sub = submenus.get(key)
        if sub is not None:
            sub_group = _clean(act.text()) if top else group
            _walk(sub, sub_group, False, submenus, seen, out)
            continue
        if key in seen or not act.text():
            continue
        seen.add(key)
        out.append(entry_from_action(act, group))


def entry_from_action(act: QAction, group: str) -> CommandEntry:
    return CommandEntry(
        label=_clean(act.text()),
        group=group,
        run=act.trigger,
        shortcut=act.shortcut().toString(),
        icon=act.icon(),
        enabled=act.isEnabled(),
    )


# ----------------------------------------------------------------------
# painting
# ----------------------------------------------------------------------
class _EntryDelegate(QStyledItemDelegate):
    """Group headers, and entry rows: icon, label with the typed text in
    bold, key caps (or the folder / group) on the right."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tokens: Tokens = LIGHT

    def sizeHint(self, option, index) -> QSize:  # noqa: ANN001
        if index.data(_ROLE_KIND) == _KIND_HEADER:
            return QSize(PALETTE_WIDTH - 24, HEADER_H)
        return QSize(PALETTE_WIDTH - 24, ROW_H)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: ANN001
        t = self.tokens
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if index.data(_ROLE_KIND) == _KIND_HEADER:
            self._paint_header(painter, option, index)
            painter.restore()
            return

        entry: CommandEntry | None = index.data(_ROLE_ENTRY)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        row = QRectF(option.rect).adjusted(2, 1, -2, -1)
        if selected or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(t.soft if selected else t.hover))
            painter.drawRoundedRect(row, 8, 8)

        rect = option.rect.adjusted(12, 0, -12, 0)
        x = rect.left()
        if entry is not None and not entry.icon.isNull():
            entry.icon.paint(
                painter, QRect(x, rect.center().y() - 9, 18, 18)
            )
        x += 30

        # Right side first, so the label knows how much room is left.
        right = rect.right()
        keys = shortcut_keys(entry.shortcut) if entry is not None else []
        if keys:
            right = self._paint_keys(painter, keys, rect) - 12
        else:
            side = index.data(_ROLE_CONTEXT) or ""
            if side:
                font = QFont(option.font)
                painter.setFont(font)
                fm = QFontMetrics(font)
                # A few px of slack: the int advance can round below
                # the real text width and elide a string that fits.
                side_w = min(fm.horizontalAdvance(side) + 4,
                             rect.width() // 2)
                text = fm.elidedText(side, Qt.ElideMiddle, side_w)
                painter.setPen(QColor(t.text_muted))
                side_rect = QRect(rect.right() - side_w, rect.top(),
                                  side_w, rect.height())
                painter.drawText(side_rect, Qt.AlignVCenter | Qt.AlignRight,
                                 text)
                right = side_rect.left() - 16

        label = index.data(Qt.DisplayRole) or ""
        spans = index.data(_ROLE_SPANS) or []
        color = QColor(t.soft_text if selected else t.text)
        self._paint_label(
            painter, option.font, label, spans, color,
            QRect(x, rect.top(), max(0, right - x), rect.height()),
        )
        painter.restore()

    def _paint_header(
        self, painter: QPainter, option, index  # noqa: ANN001
    ) -> None:
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        font.setLetterSpacing(QFont.PercentageSpacing, 108)
        font.setCapitalization(QFont.AllUppercase)
        painter.setFont(font)
        painter.setPen(QColor(self.tokens.text_muted))
        rect = option.rect.adjusted(12, 6, -12, 0)
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter,
                         index.data(Qt.DisplayRole) or "")

    def _paint_label(
        self,
        painter: QPainter,
        base: QFont,
        label: str,
        spans: list[tuple[int, int]],
        color: QColor,
        rect: QRect,
    ) -> None:
        regular = QFont(base)
        bold = QFont(base)
        bold.setBold(True)
        segments: list[tuple[str, bool]] = []
        pos = 0
        for start, end in spans:
            if start > pos:
                segments.append((label[pos:start], False))
            segments.append((label[start:end], True))
            pos = end
        if pos < len(label):
            segments.append((label[pos:], False))
        total = sum(
            QFontMetrics(bold if b else regular).horizontalAdvance(s)
            for s, b in segments
        )
        painter.setPen(color)
        if total > rect.width():
            painter.setFont(regular)
            text = QFontMetrics(regular).elidedText(
                label, Qt.ElideRight, rect.width()
            )
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)
            return
        x = rect.left()
        for text, is_bold in segments:
            font = bold if is_bold else regular
            painter.setFont(font)
            w = QFontMetrics(font).horizontalAdvance(text)
            painter.drawText(
                QRect(x, rect.top(), w + 2, rect.height()),
                Qt.AlignVCenter | Qt.AlignLeft,
                text,
            )
            x += w

    def _paint_keys(
        self, painter: QPainter, keys: list[str], rect: QRect
    ) -> int:
        """Key caps right-aligned in `rect`; returns their left edge."""
        t = self.tokens
        font = QFont()
        font.setFamilies([f.strip().strip('"') for f in
                          MONO_FONT_STACK.split(",")])
        font.setPointSizeF(8.0)
        fm = QFontMetrics(font)
        painter.setFont(font)
        h = 20
        y = rect.center().y() - h // 2
        widths = [fm.horizontalAdvance(k) + 12 for k in keys]
        x = rect.right() - (sum(widths) + 4 * (len(keys) - 1))
        left = x
        for key, w in zip(keys, widths):
            cap = QRectF(x + 0.5, y + 0.5, w - 1, h - 1)
            painter.setPen(QPen(QColor(t.line_strong), 1.0))
            painter.setBrush(QColor(t.field))
            painter.drawRoundedRect(cap, 5, 5)
            painter.setPen(QPen(QColor(t.line_strong), 1.0))
            painter.drawLine(
                int(cap.left() + 3), int(cap.bottom()),
                int(cap.right() - 3), int(cap.bottom()),
            )
            painter.setPen(QColor(t.text_muted))
            painter.drawText(QRect(int(x), y, w, h - 1), Qt.AlignCenter, key)
            x += w + 4
        return int(left)


# ----------------------------------------------------------------------
# the popup
# ----------------------------------------------------------------------
class CommandPalette(QDialog):
    """Popup command search. `open_with(entries)` fills and shows it."""

    def __init__(
        self,
        parent: QWidget | None = None,
        usage: CommandUsage | None = None,
    ) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("CommandPalette")
        self.setFixedWidth(PALETTE_WIDTH)
        # Rounded corners: the window itself is see-through and a card
        # inside it carries the background, border and radius.
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._entries: list[CommandEntry] = []
        self._usage = usage
        self._tokens: Tokens = LIGHT

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(self)
        self._card = card
        card.setObjectName("CommandPaletteCard")
        outer.addWidget(card)
        col = QVBoxLayout(card)
        # 1 px keeps the card border visible around the children.
        col.setContentsMargins(1, 1, 1, 1)
        col.setSpacing(0)

        search = QFrame(card)
        search.setObjectName("CommandPaletteSearch")
        row = QHBoxLayout(search)
        row.setContentsMargins(18, 0, 14, 0)
        row.setSpacing(12)
        self._search_icon = QLabel(search)
        row.addWidget(self._search_icon, 0, Qt.AlignVCenter)
        self._input = QLineEdit(search)
        self._input.setObjectName("CommandPaletteInput")
        self._input.setPlaceholderText("Type a command, a tool or a file name")
        self._input.textChanged.connect(self._refilter)
        self._input.installEventFilter(self)
        row.addWidget(self._input, 1)
        esc = QLabel("Esc", search)
        esc.setObjectName("Kbd")
        row.addWidget(esc, 0, Qt.AlignVCenter)
        search.setFixedHeight(56)
        col.addWidget(search)

        self._list = QListWidget(card)
        self._list.setObjectName("CommandPaletteList")
        self._delegate = _EntryDelegate(self._list)
        self._list.setItemDelegate(self._delegate)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setMouseTracking(True)
        # Keyboard focus stays in the search field (Up / Down / Enter are
        # forwarded by eventFilter); the list only reacts to clicks.
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.itemClicked.connect(self._run_item)
        col.addWidget(self._list)

        self._empty = QLabel("No matching command or file", card)
        self._empty.setObjectName("CommandPaletteEmpty")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setMinimumHeight(56)
        col.addWidget(self._empty)

        footer = QFrame(card)
        footer.setObjectName("CommandPaletteFooter")
        frow = QHBoxLayout(footer)
        frow.setContentsMargins(18, 0, 18, 0)
        frow.setSpacing(6)
        for keys, what in (("↑ ↓", "Navigate"), ("Enter", "Run")):
            for key in keys.split():
                cap = QLabel(key, footer)
                cap.setObjectName("Kbd")
                frow.addWidget(cap, 0, Qt.AlignVCenter)
            text = QLabel(what, footer)
            text.setObjectName("CommandPaletteHint")
            frow.addWidget(text, 0, Qt.AlignVCenter)
            frow.addSpacing(12)
        frow.addStretch(1)
        tagline = QLabel(
            "Every menu command, tool and recent file is here", footer
        )
        tagline.setObjectName("CommandPaletteHint")
        frow.addWidget(tagline, 0, Qt.AlignVCenter)
        footer.setFixedHeight(42)
        col.addWidget(footer)

        self._apply_icons()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def set_colors(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self._delegate.tokens = tokens
        self._apply_icons()
        self._list.viewport().update()

    def set_entries(self, entries: Iterable[CommandEntry]) -> None:
        self._entries = [e for e in entries if e.enabled]
        self._refilter(self._input.text())

    def open_with(
        self, entries: Iterable[CommandEntry], anchor: QWidget | None = None
    ) -> None:
        self._input.clear()
        self.set_entries(entries)
        host = anchor or self.parentWidget()
        if host is not None:
            top_left = host.mapToGlobal(host.rect().topLeft())
            x = top_left.x() + (host.width() - self.width()) // 2
            y = top_left.y() + 60
            self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

    def visible_labels(self) -> list[str]:
        """Entry labels in display order (group headers left out)."""
        return [
            item.text()
            for item in self._items()
            if item.data(_ROLE_KIND) == _KIND_ENTRY
        ]

    def visible_groups(self) -> list[str]:
        """Group headers in display order."""
        return [
            item.text()
            for item in self._items()
            if item.data(_ROLE_KIND) == _KIND_HEADER
        ]

    def current_label(self) -> str:
        item = self._list.currentItem()
        return item.text() if item is not None else ""

    def run_current(self) -> None:
        self._run_item(self._list.currentItem())

    # ------------------------------------------------------------------
    # filtering and ranking
    # ------------------------------------------------------------------
    def _frecency(self, entry: CommandEntry) -> float:
        return self._usage.score(entry.key) if self._usage else 0.0

    def _sections(
        self, query: str
    ) -> list[tuple[str, list[CommandEntry], bool]]:
        """(title, entries, show_group_on_the_right) blocks to display."""
        if not query:
            return self._browse_sections()
        terms = query.split()
        order = {id(e): i for i, e in enumerate(self._entries)}
        hits = [e for e in self._entries if e.matches(terms)]
        # Match quality, then the user's habits, then menu order (the
        # menus already put related commands in a sensible sequence).
        hits.sort(
            key=lambda e: (e.rank(query)[0], -self._frecency(e),
                           order[id(e)])
        )
        groups: dict[str, list[CommandEntry]] = {}
        for e in hits:
            groups.setdefault(e.group, []).append(e)
        return [(g, members, False) for g, members in groups.items()]

    def _browse_sections(self) -> list[tuple[str, list[CommandEntry], bool]]:
        commands = [e for e in self._entries if e.group != FILES_GROUP]
        files = [e for e in self._entries if e.group == FILES_GROUP]
        recent: list[CommandEntry] = []
        if self._usage is not None:
            by_key = {e.key: e for e in commands}
            for key in self._usage.recent():
                if key in by_key:
                    recent.append(by_key[key])
                if len(recent) >= RECENT_COMMANDS:
                    break
        sections: list[tuple[str, list[CommandEntry], bool]] = []
        if recent:
            sections.append((RECENT_GROUP, recent, True))
        if files:
            sections.append((FILES_GROUP, files[:RECENT_FILES], False))
        shown = {id(e) for e in recent}
        groups: dict[str, list[CommandEntry]] = {}
        for e in commands:
            if id(e) not in shown:
                groups.setdefault(e.group, []).append(e)
        sections.extend((g, m, False) for g, m in groups.items())
        return sections

    def _refilter(self, text: str) -> None:
        query = text.strip().lower()
        terms = query.split()
        self._list.clear()
        first_entry = -1
        height = 0
        for title, members, show_group in self._sections(query):
            header = QListWidgetItem(title)
            header.setData(_ROLE_KIND, _KIND_HEADER)
            header.setFlags(Qt.NoItemFlags)
            self._list.addItem(header)
            height += HEADER_H
            for e in members:
                item = QListWidgetItem(e.label)
                item.setData(_ROLE_KIND, _KIND_ENTRY)
                item.setData(_ROLE_ENTRY, e)
                item.setData(_ROLE_SPANS, match_spans(e.label, terms))
                side = e.group if show_group else e.detail
                item.setData(_ROLE_CONTEXT, side)
                tip = f"{e.group}: {e.label}"
                if e.detail:
                    tip += f"\n{e.detail}"
                item.setToolTip(tip)
                self._list.addItem(item)
                if first_entry < 0:
                    first_entry = self._list.count() - 1
                height += ROW_H
        has_hits = first_entry >= 0
        if has_hits:
            self._list.setCurrentRow(first_entry)
            self._list.scrollToTop()
        self._list.setFixedHeight(min(height, MAX_LIST_H) + 12)
        self._list.setVisible(has_hits)
        self._empty.setVisible(not has_hits)
        # The card's size hint is cached by the outer layout until the
        # next layout pass; refresh it so the popup shrinks right away.
        self._card.layout().invalidate()
        self._card.updateGeometry()
        self.adjustSize()

    # ------------------------------------------------------------------
    # running and keyboard
    # ------------------------------------------------------------------
    def _items(self) -> list[QListWidgetItem]:
        return [self._list.item(i) for i in range(self._list.count())]

    def _run_item(self, item: QListWidgetItem | None) -> None:
        if item is None or item.data(_ROLE_KIND) != _KIND_ENTRY:
            return
        entry: CommandEntry = item.data(_ROLE_ENTRY)
        if self._usage is not None:
            self._usage.record(entry.key)
        self.hide()
        # Run after the popup is gone, so dialogs the command opens get
        # focus and a command that reopens the palette does not fight it.
        QTimer.singleShot(0, entry.run)

    def _step(self, step: int) -> None:
        rows = [
            i
            for i in range(self._list.count())
            if self._list.item(i).data(_ROLE_KIND) == _KIND_ENTRY
        ]
        if not rows:
            return
        current = self._list.currentRow()
        at = rows.index(current) if current in rows else -1
        if abs(step) == 1:
            nxt = rows[(at + step) % len(rows)]  # arrows wrap around
        else:
            nxt = rows[max(0, min(len(rows) - 1, at + step))]  # pages stop
        self._list.setCurrentRow(nxt)
        # Keep the group header of the first entry in view.
        if nxt == rows[0]:
            self._list.scrollToTop()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._step(1 if key == Qt.Key_Down else -1)
                return True
            if key in (Qt.Key_PageDown, Qt.Key_PageUp):
                page = max(1, MAX_LIST_H // ROW_H - 1)
                self._step(page if key == Qt.Key_PageDown else -page)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.run_current()
                return True
        return super().eventFilter(obj, event)

    def _apply_icons(self) -> None:
        pm = line_pixmap("search", QColor(self._tokens.text_muted), 36)
        pm.setDevicePixelRatio(2.0)
        self._search_icon.setPixmap(pm)
