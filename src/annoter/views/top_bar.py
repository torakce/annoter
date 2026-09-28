"""TopBar: the slim window header of the redesigned UI (Lot B).

Replaces the QMenuBar and the file / undo half of the old toolbar:

    [menu] [logo] file-name [Unsaved changes]   [ Search ... Ctrl K ]
                                  [undo] [redo] | [theme] [Save]

It is installed with `QMainWindow.setMenuWidget()`. The widget stays
dumb, like the canvas overlays: MainWindow hands it the existing QActions
(undo, redo, save, theme toggle) and the main QMenu, and it only mirrors
their state. No behavior lives here, so every command keeps working from
its shortcut and from the menu exactly as before.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from annoter.views.line_icons import line_icon

TOP_BAR_HEIGHT = 52
_ICON = QSize(20, 20)
# Brand mark colors (same as resources/icons/app.ico); not UI chrome, so
# they do not come from the theme tokens.
_MARK_BG = QColor("#33415C")
_MARK_BAR = QColor("#E0563F")


def _brand_mark(size: int = 28, dpr: float = 2.0) -> QPixmap:
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(_MARK_BG)
    p.drawRoundedRect(QRectF(0, 0, px, px), px * 0.25, px * 0.25)
    p.setBrush(_MARK_BAR)
    p.drawRoundedRect(
        QRectF(px * 0.22, px * 0.80, px * 0.56, px * 0.07), 1.5, 1.5
    )
    font = QFont()
    font.setPixelSize(int(px * 0.52))
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(0, -px * 0.04, px, px), Qt.AlignCenter, "A")
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _divider() -> QFrame:
    d = QFrame()
    d.setObjectName("TopBarDivider")
    d.setFixedSize(1, 22)
    return d


class _TitleLabel(QLabel):
    """The document name: a click opens the document's properties."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton and self.text():
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class TopBar(QFrame):
    """Window header. Emits `searchRequested` from the command field and
    `titleClicked` from the document name."""

    searchRequested = Signal()
    titleClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(TOP_BAR_HEIGHT)
        self._icon_color = QColor("#3A3B3E")
        self._on_accent = QColor("#FFFFFF")
        self._save_action: QAction | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(12)

        # ---- left: menu, brand, document name, unsaved chip ----------
        left = QHBoxLayout()
        left.setSpacing(8)
        self.menu_button = QToolButton(self)
        self.menu_button.setObjectName("TopBarMenuButton")
        self.menu_button.setIconSize(_ICON)
        self.menu_button.setToolTip("Main menu")
        self.menu_button.setAccessibleName("Main menu")
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        left.addWidget(self.menu_button)

        mark = QLabel(self)
        mark.setPixmap(_brand_mark())
        mark.setToolTip("Annoter")
        left.addWidget(mark)

        self.title_label = _TitleLabel(self)
        self.title_label.setObjectName("TopBarTitle")
        self.title_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.title_label.clicked.connect(self.titleClicked)
        left.addSpacing(4)
        left.addWidget(self.title_label)

        self.unsaved_chip = QFrame(self)
        self.unsaved_chip.setObjectName("UnsavedChip")
        chip = QHBoxLayout(self.unsaved_chip)
        chip.setContentsMargins(8, 2, 10, 2)
        chip.setSpacing(6)
        dot = QFrame(self.unsaved_chip)
        dot.setObjectName("UnsavedDot")
        dot.setFixedSize(7, 7)
        chip.addWidget(dot)
        chip_text = QLabel("Unsaved changes", self.unsaved_chip)
        chip_text.setObjectName("UnsavedText")
        chip.addWidget(chip_text)
        self.unsaved_chip.setFixedHeight(22)
        self.unsaved_chip.hide()
        left.addWidget(self.unsaved_chip, 0, Qt.AlignVCenter)
        left.addStretch(1)

        # ---- center: command search -----------------------------------
        self.search_button = QPushButton(self)
        self.search_button.setObjectName("CommandSearch")
        self.search_button.setFixedSize(320, 34)
        self.search_button.setCursor(Qt.PointingHandCursor)
        self.search_button.setToolTip("Search tools and commands (Ctrl+K)")
        self.search_button.setAccessibleName("Search tools and commands")
        inner = QHBoxLayout(self.search_button)
        inner.setContentsMargins(10, 0, 8, 0)
        inner.setSpacing(8)
        self._search_icon = QLabel(self.search_button)
        self._search_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        inner.addWidget(self._search_icon)
        hint = QLabel("Search tools and commands", self.search_button)
        hint.setObjectName("CommandSearchHint")
        hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        inner.addWidget(hint, 1)
        kbd = QLabel("Ctrl K", self.search_button)
        kbd.setObjectName("Kbd")
        kbd.setAttribute(Qt.WA_TransparentForMouseEvents)
        kbd.setFixedHeight(20)
        inner.addWidget(kbd, 0, Qt.AlignVCenter)
        self.search_button.clicked.connect(self.searchRequested)

        # ---- right: undo / redo | theme, Save --------------------
        right = QHBoxLayout()
        right.setSpacing(4)
        right.addStretch(1)
        self.undo_button = self._icon_button()
        self.redo_button = self._icon_button()
        right.addWidget(self.undo_button)
        right.addWidget(self.redo_button)
        right.addSpacing(4)
        right.addWidget(_divider())
        right.addSpacing(4)
        self.theme_button = self._icon_button()
        right.addWidget(self.theme_button)
        self.save_button = QPushButton("Save", self)
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setIconSize(QSize(16, 16))
        right.addSpacing(4)
        right.addWidget(self.save_button)

        left_box = QWidget(self)
        left_box.setLayout(left)
        left_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right_box = QWidget(self)
        right_box.setLayout(right)
        right_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        for box in (left_box, right_box):
            box.setObjectName("TopBarSection")
            box.layout().setContentsMargins(0, 0, 0, 0)
        row.addWidget(left_box, 1)
        row.addWidget(self.search_button, 0, Qt.AlignVCenter)
        row.addWidget(right_box, 1)

        self._refresh_icons()

    # ------------------------------------------------------------------
    # wiring (called once by MainWindow)
    # ------------------------------------------------------------------
    def _icon_button(self) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("TopBarIconButton")
        b.setIconSize(_ICON)
        b.setToolButtonStyle(Qt.ToolButtonIconOnly)
        return b

    def set_menu(self, menu: QMenu) -> None:
        self.menu_button.setMenu(menu)

    def bind(
        self,
        *,
        undo: QAction,
        redo: QAction,
        save: QAction,
        toggle_theme: QAction,
    ) -> None:
        """Mirror existing actions: buttons follow their enabled state,
        icon and tooltip, and trigger them when clicked."""
        self.undo_button.setDefaultAction(undo)
        self.redo_button.setDefaultAction(redo)
        self.theme_button.setDefaultAction(toggle_theme)
        self._save_action = save
        self.save_button.clicked.connect(save.trigger)
        save.changed.connect(self._sync_save)
        self._sync_save()

    def _sync_save(self) -> None:
        act = self._save_action
        if act is None:
            return
        self.save_button.setEnabled(act.isEnabled())
        seq = act.shortcut().toString()
        self.save_button.setToolTip(f"Save ({seq})" if seq else "Save")

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def set_document_name(self, name: str | None, tooltip: str = "") -> None:
        self.title_label.setText(name or "")
        tip = f"{tooltip}\nClick for the document's properties" if name else ""
        self.title_label.setToolTip(tip.strip())

    def set_unsaved(self, unsaved: bool) -> None:
        self.unsaved_chip.setVisible(bool(unsaved))

    def is_unsaved_shown(self) -> bool:
        return not self.unsaved_chip.isHidden()

    def set_icon_color(
        self, color: QColor, on_accent: QColor | None = None
    ) -> None:
        """Glyphs are pre-rasterized: repaint them on theme change.
        `on_accent` colors the glyph on the primary (Save) button."""
        self._icon_color = QColor(color)
        if on_accent is not None:
            self._on_accent = QColor(on_accent)
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        c = self._icon_color
        self.menu_button.setIcon(line_icon("menu", c))
        self._search_icon.setPixmap(line_icon("search", c).pixmap(16, 16))
        self.save_button.setIcon(line_icon("save", self._on_accent))
