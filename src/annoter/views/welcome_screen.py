"""WelcomeScreen: home page shown when no document is open.

Discussion #1, item 12: instead of an empty gray viewport, the app opens
on a start page offering Open / New blank document and the recent files
with real first-page thumbnails.

UI redesign, Lot G: the page follows the mock-up. A heading says what to
do, a dashed drop zone carries the two start actions (and lights up while
a file is dragged over the window), recent files are cards -- thumbnail,
file name, folder, "modified / pages / size" line and an annotation-count
badge -- in a grid that reflows from four columns down to one, and a
"Handy shortcuts" row teaches the three gestures people miss most.

Thumbnails and page / annotation counts are read lazily, one file per
event-loop tick and only while the page is visible, since each one means
opening the PDF. Results are cached per (path, mtime, size) in memory
and, when MainWindow passes a `ThumbnailCache`, on disk too: a file
that has not changed is shown at once, even right after a restart.
A file that is gone or unreadable keeps a placeholder and says why.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from annoter.services.thumbnail_cache import ThumbnailCache
from annoter.services.tokens import LIGHT, Tokens
from annoter.views.line_icons import line_pixmap

CONTENT_MAX_W = 1120
DROP_ZONE_H = 200
# Below this page width the drop zone drops its decorative icon.
COMPACT_BELOW_W = 720
CARD_MIN_W = 220
MAX_COLUMNS = 4
GRID_GAP = 20
THUMB_WELL_H = 150
# Box the first page is fitted into, in logical pixels.
PAGE_BOX = (200, 124)
# Counting annotations loads every page; past this many pages the badge
# is skipped rather than stalling the start page on a huge document.
ANNOT_COUNT_MAX_PAGES = 300

# Gestures listed in the "Handy shortcuts" row: (keys, what they do).
HANDY_SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Space", "+ drag to pan"),
    ("Ctrl", "+ wheel to zoom"),
    ("Ctrl 0", "fit the page"),
    ("Ctrl K", "find any command"),
)

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_SEP = "  \u00b7  "


# ----------------------------------------------------------------------
# formatting helpers
# ----------------------------------------------------------------------
def describe_mtime(ts: float, now: datetime | None = None) -> str:
    """Short, human date for a file time: "Today, 14:32", "Yesterday",
    "12 Sep", or "12 Sep 2025" for another year."""
    now = now or datetime.now()
    when = datetime.fromtimestamp(ts)
    if when.date() == now.date():
        return f"Today, {when:%H:%M}"
    if when.date() == (now - timedelta(days=1)).date():
        return "Yesterday"
    label = f"{when.day} {_MONTHS[when.month - 1]}"
    if when.year != now.year:
        label += f" {when.year}"
    return label


def format_size(n_bytes: int) -> str:
    """File size the way a file manager shows it (1000-based units
    would disagree with Explorer, so this uses 1024)."""
    kb = n_bytes / 1024
    if kb < 1024:
        return f"{max(1, round(kb))} KB"
    mb = kb / 1024
    if mb < 10:
        return f"{mb:.1f} MB"
    if mb < 1024:
        return f"{round(mb)} MB"
    return f"{mb / 1024:.1f} GB"


def pages_caption(n: int) -> str:
    return "1 page" if n == 1 else f"{n} pages"


def annotations_caption(n: int) -> str:
    return "1 annotation" if n == 1 else f"{n} annotations"


# ----------------------------------------------------------------------
# what is known about one recent file
# ----------------------------------------------------------------------
@dataclass
class RecentInfo:
    """Result of reading a recent file for its card."""

    pixmap: QPixmap | None = None
    pages: int | None = None
    annotations: int | None = None
    # None when the file read fine; otherwise a short reason shown on
    # the card ("File not found", "Password protected", ...).
    problem: str | None = None


def read_recent_info(path: str, dpr: float = 1.0) -> RecentInfo:
    """First-page thumbnail, page count and annotation count of `path`.

    Opens the PDF just long enough to rasterize page 1; missing, locked
    or corrupt files come back with a `problem` instead of raising into
    the event loop.
    """
    import fitz
    from PySide6.QtGui import QImage

    if not os.path.exists(path):
        return RecentInfo(problem="File not found")
    try:
        doc = fitz.open(path)
    except Exception:
        return RecentInfo(problem="Can't read this file")
    try:
        if doc.needs_pass:
            return RecentInfo(problem="Password protected")
        if doc.page_count < 1:
            return RecentInfo(pages=0, problem="No pages")
        page = doc[0]
        bw, bh = PAGE_BOX
        pw = max(page.rect.width, 1.0)
        ph = max(page.rect.height, 1.0)
        zoom = min(bw / pw, bh / ph) * max(dpr, 1.0)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image = QImage(
            pix.samples,
            pix.width,
            pix.height,
            pix.stride,
            QImage.Format_RGB888,
        ).copy()
        pm = QPixmap.fromImage(image)
        pm.setDevicePixelRatio(max(dpr, 1.0))
        annotations: int | None = None
        if doc.page_count <= ANNOT_COUNT_MAX_PAGES:
            skip = {
                fitz.PDF_ANNOT_LINK,
                fitz.PDF_ANNOT_WIDGET,
                fitz.PDF_ANNOT_POPUP,
            }
            annotations = sum(
                1
                for p in doc
                for _xref, kind, *_rest in p.annot_xrefs()
                if kind not in skip
            )
        return RecentInfo(
            pixmap=_framed(pm),
            pages=doc.page_count,
            annotations=annotations,
        )
    except Exception:
        return RecentInfo(problem="Can't read this file")
    finally:
        doc.close()


def _framed(page: QPixmap) -> QPixmap:
    """The page image with a hairline frame, so a white sheet still
    reads as a sheet on a light well."""
    dpr = page.devicePixelRatio() or 1.0
    out = QPixmap(page.size())
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, page)
    w = page.width() / dpr
    h = page.height() / dpr
    p.setPen(QPen(QColor(0, 0, 0, 60), 1.0))
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0))
    p.end()
    return out


def _placeholder_page(tokens: Tokens, dpr: float = 1.0) -> QPixmap:
    """Dashed sheet outline shown for a file that could not be read."""
    w, h = 96, PAGE_BOX[1]
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(tokens.line_strong), 1.4, Qt.DashLine)
    p.setPen(pen)
    p.setBrush(QColor(tokens.panel))
    p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 3, 3)
    glyph = line_pixmap("close-doc", QColor(tokens.text_disabled), 48)
    glyph.setDevicePixelRatio(2.0)
    p.drawPixmap(int((w - 24) / 2), int((h - 24) / 2), glyph)
    p.end()
    return pm


def _glyph(name: str, color: str, size: int, dpr: float) -> QPixmap:
    pm = line_pixmap(name, QColor(color), int(size * max(dpr, 2.0)))
    pm.setDevicePixelRatio(max(dpr, 2.0))
    return pm


# ----------------------------------------------------------------------
# small widgets
# ----------------------------------------------------------------------
class _ElidedLabel(QLabel):
    """Single-line label that elides instead of growing its parent."""

    def __init__(
        self,
        text: str = "",
        mode: Qt.TextElideMode = Qt.ElideRight,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(1)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._elide()

    def _elide(self) -> None:
        width = self.width() if self.width() > 1 else 10_000
        super().setText(
            self.fontMetrics().elidedText(self._full, self._mode, width)
        )


class _FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new line when the row is
    full (Qt's "flow layout" example), so the shortcuts row never forces
    the page wider than the window."""

    def __init__(
        self,
        parent: QWidget | None = None,
        h_spacing: int = 24,
        v_spacing: int = 8,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect: QRect, apply: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x = rect.x()
                y += line_h + self._v
                line_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()

    def line_count(self) -> int:
        """Rows used at the current width (tests)."""
        tops = {
            item.geometry().top()
            for item in self._items
            if not item.isEmpty()
        }
        return len(tops)


class _Box(QWidget):
    """Layout-only container (transparent in the stylesheet)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WelcomeBox")


class _LayoutButton(QPushButton):
    """Push button whose content is a layout of labels (icon, text,
    shortcut); QPushButton's own size hint only knows its text."""

    def sizeHint(self) -> QSize:
        lay = self.layout()
        if lay is None:
            return super().sizeHint()
        hint = lay.sizeHint()
        return QSize(hint.width(), max(hint.height(), self.minimumHeight()))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()


class RecentCard(QPushButton):
    """One recent file: thumbnail well on top, three lines of text."""

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecentCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.path = path
        p = Path(path)
        self.setToolTip(str(path))
        self.setAccessibleName(p.name)

        lay = QVBoxLayout(self)
        # 1 px keeps the card's own border visible around the well.
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)

        self._well = QLabel(self)
        self._well.setObjectName("RecentThumb")
        self._well.setAlignment(Qt.AlignCenter)
        self._well.setFixedHeight(THUMB_WELL_H)
        self._well.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        well_lay = QGridLayout(self._well)
        well_lay.setContentsMargins(8, 8, 8, 8)
        self._badge = QLabel(self._well)
        self._badge.setObjectName("RecentBadge")
        self._badge.hide()
        well_lay.addWidget(self._badge, 0, 0, Qt.AlignRight | Qt.AlignBottom)
        lay.addWidget(self._well)

        text = _Box(self)
        tl = QVBoxLayout(text)
        tl.setContentsMargins(14, 10, 14, 12)
        tl.setSpacing(3)
        self._name = _ElidedLabel(p.name, Qt.ElideMiddle, text)
        self._name.setObjectName("RecentName")
        self._folder = _ElidedLabel(str(p.parent), Qt.ElideMiddle, text)
        self._folder.setObjectName("RecentFolder")
        self._meta = _ElidedLabel("", Qt.ElideRight, text)
        self._meta.setObjectName("RecentMeta")
        self._meta.setProperty("problem", False)
        tl.addWidget(self._name)
        tl.addWidget(self._folder)
        tl.addSpacing(3)
        tl.addWidget(self._meta)
        lay.addWidget(text)

        for w in (self._well, self._badge, text, self._name,
                  self._folder, self._meta):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)

        self._stat: tuple[float, int] | None = None
        try:
            st = os.stat(path)
            self._stat = (st.st_mtime, st.st_size)
        except OSError:
            self._stat = None
        self.info: RecentInfo | None = None
        self._refresh_meta()

    # -- geometry --------------------------------------------------------
    def sizeHint(self) -> QSize:
        return QSize(CARD_MIN_W, self.layout().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        return QSize(CARD_MIN_W // 2, self.layout().minimumSize().height())

    # -- state -----------------------------------------------------------
    def cache_key(self) -> tuple[str, float, int] | None:
        if self._stat is None:
            return None
        return (self.path, self._stat[0], self._stat[1])

    def set_info(self, info: RecentInfo, placeholder: QPixmap) -> None:
        self.info = info
        self._well.setPixmap(
            info.pixmap if info.pixmap is not None else placeholder
        )
        n = info.annotations or 0
        if info.problem is None and n > 0:
            self._badge.setText(annotations_caption(n))
            self._badge.show()
        else:
            self._badge.hide()
        self._refresh_meta()

    def set_placeholder(self, placeholder: QPixmap) -> None:
        """Re-tint the placeholder after a theme change."""
        if self.info is not None and self.info.pixmap is None:
            self._well.setPixmap(placeholder)

    def _refresh_meta(self) -> None:
        parts: list[str] = []
        problem = None
        if self._stat is None:
            problem = "File not found"
        elif self.info is not None and self.info.problem is not None:
            problem = self.info.problem
        if self._stat is not None:
            parts.append(describe_mtime(self._stat[0]))
        if problem is not None:
            parts.append(problem)
        else:
            if self.info is not None and self.info.pages is not None:
                parts.append(pages_caption(self.info.pages))
            if self._stat is not None:
                parts.append(format_size(self._stat[1]))
        self._meta.set_full_text(_SEP.join(parts))
        if bool(self._meta.property("problem")) != (problem is not None):
            self._meta.setProperty("problem", problem is not None)
            self._meta.style().unpolish(self._meta)
            self._meta.style().polish(self._meta)

    # -- introspection (tests, accessibility) -------------------------
    def name_text(self) -> str:
        return self._name.full_text()

    def folder_text(self) -> str:
        return self._folder.full_text()

    def meta_text(self) -> str:
        return self._meta.full_text()

    def badge_text(self) -> str:
        return self._badge.text() if not self._badge.isHidden() else ""

    def thumbnail(self) -> QPixmap | None:
        pm = self._well.pixmap()
        return None if pm is None or pm.isNull() else pm

    def is_missing(self) -> bool:
        return self._stat is None


class _RecentGrid(_Box):
    """Cards in equal columns: as many as fit (up to four), reflowed on
    resize, so the grid stays aligned with the text above it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(GRID_GAP)
        self._grid.setVerticalSpacing(GRID_GAP)
        self._cards: list[RecentCard] = []
        self._columns = 0

    def cards(self) -> list[RecentCard]:
        return list(self._cards)

    def columns(self) -> int:
        return self._columns

    def set_cards(self, cards: list[RecentCard]) -> None:
        for card in self._cards:
            self._grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._cards = list(cards)
        self._columns = 0
        self._relayout()

    @staticmethod
    def columns_for(width: int) -> int:
        fit = (width + GRID_GAP) // (CARD_MIN_W + GRID_GAP)
        return max(1, min(MAX_COLUMNS, fit))

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        cols = self.columns_for(self.width())
        if cols == self._columns and all(
            self._grid.indexOf(c) >= 0 for c in self._cards
        ):
            return
        self._columns = cols
        for card in self._cards:
            self._grid.removeWidget(card)
        for i in range(self._grid.columnCount()):
            self._grid.setColumnStretch(i, 0)
        for i in range(cols):
            self._grid.setColumnStretch(i, 1)
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // cols, i % cols)
            card.show()


# ----------------------------------------------------------------------
# the page
# ----------------------------------------------------------------------
class WelcomeScreen(QWidget):
    """Start page: drop zone with the start actions + recent files."""

    openRequested = Signal()
    blankRequested = Signal()
    openPathRequested = Signal(str)
    removePathRequested = Signal(str)
    clearRecentRequested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        thumbnails: ThumbnailCache | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("WelcomeScreen")
        self._tokens: Tokens = LIGHT
        self._pending: list[RecentCard] = []
        self._cache: dict[tuple[str, float, int], RecentInfo] = {}
        # Survives restarts: unchanged files are not reopened at launch.
        self._disk = thumbnails
        self._drag_active = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("WelcomeScroll")
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll)

        page = QWidget()
        page.setObjectName("WelcomePage")
        self._scroll.setWidget(page)
        row = QHBoxLayout(page)
        row.setContentsMargins(32, 40, 32, 24)
        column = _Box(page)
        column.setMaximumWidth(CONTENT_MAX_W)
        column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addStretch(1)
        row.addWidget(column, 100)
        row.addStretch(1)

        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        title = QLabel("Open a drawing to start annotating", column)
        title.setObjectName("WelcomeTitle")
        title.setWordWrap(True)
        col.addWidget(title)
        col.addSpacing(6)
        subtitle = QLabel(
            "Annotations are saved inside the PDF, so they open in "
            "Acrobat and Foxit too.",
            column,
        )
        subtitle.setObjectName("WelcomeSubtitle")
        subtitle.setWordWrap(True)
        col.addWidget(subtitle)
        col.addSpacing(28)

        col.addWidget(self._build_drop_zone(column))
        col.addSpacing(36)

        self._recent_section = _Box(column)
        rs = QVBoxLayout(self._recent_section)
        rs.setContentsMargins(0, 0, 0, 0)
        rs.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(10)
        self._recent_label = QLabel("Recent files", self._recent_section)
        self._recent_label.setObjectName("WelcomeSection")
        self._recent_count = QLabel("", self._recent_section)
        self._recent_count.setObjectName("WelcomeCount")
        self._btn_clear = QPushButton("Clear list", self._recent_section)
        self._btn_clear.setObjectName("LinkButton")
        self._btn_clear.setCursor(Qt.PointingHandCursor)
        self._btn_clear.setToolTip("Forget every recent file (the files stay)")
        self._btn_clear.clicked.connect(self.clearRecentRequested)
        head.addWidget(self._recent_label, 0, Qt.AlignBaseline)
        head.addWidget(self._recent_count, 0, Qt.AlignBaseline)
        head.addStretch(1)
        head.addWidget(self._btn_clear)
        rs.addLayout(head)
        self._grid = _RecentGrid(self._recent_section)
        rs.addWidget(self._grid)
        col.addWidget(self._recent_section)

        col.addStretch(1)
        col.addSpacing(28)
        col.addWidget(self._build_shortcuts_row(column))

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_next)

        self._apply_icons()
        self.set_recent([])

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_drop_zone(self, parent: QWidget) -> QFrame:
        zone = QFrame(parent)
        zone.setObjectName("DropZone")
        zone.setProperty("dragActive", False)
        zone.setFixedHeight(DROP_ZONE_H)
        self._drop_zone = zone
        h = QHBoxLayout(zone)
        h.setContentsMargins(32, 24, 32, 24)
        h.setSpacing(40)
        h.addStretch(1)

        self._drop_icon = QLabel(zone)
        self._drop_icon.setObjectName("DropIcon")
        self._drop_icon.setFixedSize(88, 88)
        self._drop_icon.setAlignment(Qt.AlignCenter)
        h.addWidget(self._drop_icon, 0, Qt.AlignVCenter)

        texts = _Box(zone)
        v = QVBoxLayout(texts)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        self._drop_title = QLabel("Drop a PDF here", texts)
        self._drop_title.setObjectName("DropTitle")
        v.addWidget(self._drop_title)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self._btn_open = _LayoutButton(texts)
        self._btn_open.setObjectName("WelcomePrimary")
        self._btn_open.setCursor(Qt.PointingHandCursor)
        self._btn_open.setAccessibleName("Open PDF")
        self._btn_open.setToolTip("Open a PDF or an image (Ctrl+O)")
        inner = QHBoxLayout(self._btn_open)
        inner.setContentsMargins(16, 0, 16, 0)
        inner.setSpacing(10)
        self._open_icon = QLabel(self._btn_open)
        self._open_label = QLabel("Open PDF...", self._btn_open)
        self._open_label.setObjectName("WelcomePrimaryText")
        self._open_keys = QLabel("Ctrl O", self._btn_open)
        self._open_keys.setObjectName("WelcomePrimaryKeys")
        for w in (self._open_icon, self._open_label, self._open_keys):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            inner.addWidget(w)
        self._btn_open.setFixedHeight(40)
        self._btn_open.clicked.connect(self.openRequested)
        buttons.addWidget(self._btn_open)

        self._btn_blank = QPushButton("New blank document", texts)
        self._btn_blank.setObjectName("WelcomeSecondary")
        self._btn_blank.setCursor(Qt.PointingHandCursor)
        self._btn_blank.setToolTip("Start from an empty A4 page")
        self._btn_blank.setFixedHeight(40)
        self._btn_blank.setIconSize(QSize(18, 18))
        self._btn_blank.clicked.connect(self.blankRequested)
        buttons.addWidget(self._btn_blank)
        buttons.addStretch(1)
        v.addLayout(buttons)

        hint = QLabel(
            "Images work too. You can also drop a file onto the window "
            "at any time.",
            texts,
        )
        hint.setObjectName("DropHint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        h.addWidget(texts, 0, Qt.AlignVCenter)
        h.addStretch(1)
        return zone

    def _build_shortcuts_row(self, parent: QWidget) -> QWidget:
        row = _Box(parent)
        self._shortcuts_flow = _FlowLayout(row, h_spacing=24, v_spacing=8)
        head = QLabel("Handy shortcuts", row)
        head.setObjectName("WelcomeShortcutsTitle")
        self._shortcuts_flow.addWidget(head)
        self._shortcut_keys: list[str] = []
        for keys, what in HANDY_SHORTCUTS:
            pair = _Box(row)
            item = QHBoxLayout(pair)
            item.setContentsMargins(0, 0, 0, 0)
            item.setSpacing(6)
            kbd = QLabel(keys, pair)
            kbd.setObjectName("Kbd")
            text = QLabel(what, pair)
            text.setObjectName("WelcomeShortcutText")
            item.addWidget(kbd)
            item.addWidget(text)
            self._shortcuts_flow.addWidget(pair)
            self._shortcut_keys.append(keys)
        return row

    # ------------------------------------------------------------------
    # theme
    # ------------------------------------------------------------------
    def set_colors(self, tokens: Tokens) -> None:
        """Re-tint the code-drawn glyphs for the active theme."""
        self._tokens = tokens
        self._apply_icons()
        placeholder = self._placeholder()
        for card in self._grid.cards():
            card.set_placeholder(placeholder)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        # The drop icon is decoration: let it go before the buttons wrap.
        self._drop_icon.setVisible(self.width() >= COMPACT_BELOW_W)

    def _dpr(self) -> float:
        return max(1.0, self.devicePixelRatioF())

    def _apply_icons(self) -> None:
        t = self._tokens
        dpr = self._dpr()
        self._drop_icon.setPixmap(_glyph("file-upload", t.soft_text, 40, dpr))
        self._open_icon.setPixmap(_glyph("open", t.on_accent, 18, dpr))
        blank = _glyph("insert-pages", t.icon, 18, dpr)
        self._btn_blank.setIcon(QIcon(blank))

    def _placeholder(self) -> QPixmap:
        return _placeholder_page(self._tokens, self._dpr())

    # ------------------------------------------------------------------
    # drag feedback (MainWindow owns the drop itself)
    # ------------------------------------------------------------------
    def set_drag_active(self, active: bool) -> None:
        if active == self._drag_active:
            return
        self._drag_active = active
        self._drop_zone.setProperty("dragActive", active)
        self._drop_title.setText(
            "Release to open" if active else "Drop a PDF here"
        )
        # Descendant rules keyed on the property need a re-polish too.
        for w in (self._drop_zone, self._drop_icon):
            w.style().unpolish(w)
            w.style().polish(w)
            w.update()

    def is_drag_active(self) -> bool:
        return self._drag_active

    # ------------------------------------------------------------------
    # recent files
    # ------------------------------------------------------------------
    def set_recent(self, paths: list[str]) -> None:
        self._timer.stop()
        self._pending = []
        placeholder = self._placeholder()
        cards: list[RecentCard] = []
        for path in paths:
            card = RecentCard(str(path))
            card.clicked.connect(
                lambda _=False, p=card.path: self.openPathRequested.emit(p)
            )
            card.customContextMenuRequested.connect(
                lambda pos, c=card: self._on_card_context_menu(c, pos)
            )
            key = card.cache_key()
            if card.is_missing():
                card.set_info(
                    RecentInfo(problem="File not found"), placeholder
                )
            elif key is not None and key in self._cache:
                card.set_info(self._cache[key], placeholder)
            elif key is not None and (hit := self._from_disk(key)):
                self._cache[key] = hit
                card.set_info(hit, placeholder)
            else:
                self._pending.append(card)
            cards.append(card)
        self._grid.set_cards(cards)
        n = len(cards)
        self._recent_section.setVisible(n > 0)
        self._recent_count.setText(str(n) if n else "")
        if self._pending and self.isVisible():
            self._timer.start()

    def cards(self) -> list[RecentCard]:
        return self._grid.cards()

    def recent_paths(self) -> list[str]:
        return [c.path for c in self._grid.cards()]

    def shortcut_keys(self) -> list[str]:
        return list(self._shortcut_keys)

    def _build_card_menu(self, card: RecentCard) -> QMenu:
        menu = QMenu(self)
        act_open = menu.addAction("Open")
        act_open.triggered.connect(
            lambda: self.openPathRequested.emit(card.path)
        )
        act_folder = menu.addAction("Show in Folder")
        act_folder.setEnabled(Path(card.path).parent.is_dir())
        act_folder.triggered.connect(lambda: self._reveal(card.path))
        menu.addSeparator()
        act_remove = menu.addAction("Remove from List")
        act_remove.triggered.connect(
            lambda: self.removePathRequested.emit(card.path)
        )
        return menu

    def _on_card_context_menu(
        self, card: RecentCard, pos  # noqa: ANN001
    ) -> None:
        menu = self._build_card_menu(card)
        menu.exec(card.mapToGlobal(pos))
        menu.deleteLater()

    @staticmethod
    def _reveal(path: str) -> None:
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(Path(path).parent))
        )

    # ------------------------------------------------------------------
    # lazy reading: one file per event-loop tick, only while shown
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if self._pending:
            self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: ANN001
        # No point opening PDFs for a page nobody sees; resumes on show.
        self._timer.stop()
        super().hideEvent(event)

    def _from_disk(self, key: tuple[str, float, int]) -> RecentInfo | None:
        if self._disk is None:
            return None
        hit = self._disk.load(*key)
        if hit is None:
            return None
        return RecentInfo(
            pixmap=hit.pixmap, pages=hit.pages, annotations=hit.annotations
        )

    def _to_disk(self, key: tuple[str, float, int], info: RecentInfo) -> None:
        if self._disk is None or info.pixmap is None or info.pages is None:
            return
        self._disk.store(*key, info.pixmap, info.pages, info.annotations)

    def _render_next(self) -> None:
        if not self._pending:
            return
        card = self._pending.pop(0)
        info = read_recent_info(card.path, self._dpr())
        key = card.cache_key()
        if key is not None and info.problem is None:
            self._cache[key] = info
            self._to_disk(key, info)
        card.set_info(info, self._placeholder())
        if self._pending and self.isVisible():
            self._timer.start()
