"""AnnotationContextMenu: the right-click menu on the canvas (Lot J).

Laid out like the Windows 11 context menu: the frequent commands are
icon buttons in rows at the top, one click away without reading a list;
the rarer ones stay below as ordinary entries, each with its icon.
MainWindow picks the rows for what is selected:

    (top)       Cut  Copy  Paste  Duplicate  Delete
    Order       To back  Backward  Forward  To front
    Start, End  the extremity shapes of a line, one row per end
    Shape       straight / cloud outline, fill
    Path        open / closed
    Radius      the corner radius of the clicked bend (a spin box), and
                the same radius on every bend
    Align       align and distribute several annotations

A button closes the menu like any entry, except the ones flagged
`keep_open` (Backward / Forward): those leave it up so a pile can be
walked through click after click, and every row re-reads its enabled /
checked state after each of them.

The widget stays dumb: it runs the callbacks MainWindow gives it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QWidget,
    QWidgetAction,
)

Flag = Union[bool, Callable[[], bool]]

BUTTON_SIZE = QSize(28, 28)
ICON_SIZE = QSize(18, 18)
CAPTION_WIDTH = 66
GAP = 8


@dataclass
class IconCommand:
    """One icon button of a row."""

    key: str  # stable id (tests, lookups)
    icon: QIcon
    tooltip: str
    run: Callable[[], None]
    enabled: Flag = True
    checked: Flag | None = None  # None: not a toggle
    keep_open: bool = False


def _value(flag: Flag) -> bool:
    return bool(flag() if callable(flag) else flag)


class IconRow(QWidget):
    """A row of icon buttons, with an optional caption on the left."""

    def __init__(
        self,
        caption: str = "",
        caption_icon: QIcon | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ContextIconRow")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 1, 4, 1)
        self._lay.setSpacing(2)
        self._caption = caption
        if caption:
            cap = QWidget(self)
            cap.setObjectName("ContextRowCaptionBox")
            cap.setFixedWidth(CAPTION_WIDTH)
            h = QHBoxLayout(cap)
            h.setContentsMargins(2, 0, 4, 0)
            h.setSpacing(4)
            if caption_icon is not None:
                glyph = QLabel(cap)
                glyph.setPixmap(caption_icon.pixmap(QSize(16, 16)))
                h.addWidget(glyph)
            text = QLabel(caption, cap)
            text.setObjectName("ContextRowCaption")
            h.addWidget(text)
            h.addStretch(1)
            self._lay.addWidget(cap)

    def caption(self) -> str:
        return self._caption

    def add_button(self, cmd: IconCommand) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("ContextIconButton")
        btn.setIcon(cmd.icon)
        btn.setIconSize(ICON_SIZE)
        btn.setFixedSize(BUTTON_SIZE)
        btn.setToolTip(cmd.tooltip)
        btn.setAccessibleName(cmd.tooltip)
        btn.setAutoRaise(True)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCheckable(cmd.checked is not None)
        self._lay.addWidget(btn)
        return btn

    def add_widget(self, widget: QWidget) -> None:
        """A plain widget in the row (a spin box before the buttons)."""
        widget.setParent(self)
        self._lay.addWidget(widget)

    def add_gap(self) -> None:
        self._lay.addSpacing(GAP)

    def finish(self) -> None:
        self._lay.addStretch(1)


class MenuSpinBox(QDoubleSpinBox):
    """A spin box living in a menu row: Enter accepts (the menu then
    closes), Escape cancels. Plain focus changes do nothing -- a menu
    takes the focus back as soon as the pointer leaves the row."""

    accepted = Signal()
    cancelled = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.interpretText()
            self.accepted.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AnnotationContextMenu(QMenu):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AnnotationContextMenu")
        self.setToolTipsVisible(True)
        self._commands: dict[str, tuple[IconCommand, QToolButton]] = {}
        self._rows: list[IconRow] = []

    # ------------------------------------------------------------------
    # building
    # ------------------------------------------------------------------
    def add_icon_row(
        self,
        commands: list[IconCommand | None],
        caption: str = "",
        caption_icon: QIcon | None = None,
        leading: list[QWidget] | None = None,
    ) -> IconRow:
        """Append a row; `None` in `commands` leaves a small gap.
        `leading` widgets come first, after the caption."""
        row = IconRow(caption, caption_icon, self)
        for widget in leading or ():
            row.add_widget(widget)
        for cmd in commands:
            if cmd is None:
                row.add_gap()
                continue
            btn = row.add_button(cmd)
            btn.clicked.connect(
                lambda _c=False, k=cmd.key: self._on_clicked(k)
            )
            self._commands[cmd.key] = (cmd, btn)
        row.finish()
        action = QWidgetAction(self)
        action.setDefaultWidget(row)
        self.addAction(action)
        self._rows.append(row)
        self.refresh()
        return row

    def add_entry(
        self,
        icon: QIcon | None,
        text: str,
        run: Callable[[], None],
        hint: str = "",
    ) -> QAction:
        """An ordinary entry. `hint` (a shortcut or gesture) is shown in
        the right-hand column, like a real shortcut, without registering
        one."""
        label = f"{text}\t{hint}" if hint else text
        action = QAction(label, self)
        if icon is not None:
            action.setIcon(icon)
        action.triggered.connect(lambda _c=False: run())
        self.addAction(action)
        return action

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Re-read every button's enabled / checked state."""
        for cmd, btn in self._commands.values():
            btn.setEnabled(_value(cmd.enabled))
            if cmd.checked is not None:
                btn.setChecked(_value(cmd.checked))

    def _on_clicked(self, key: str) -> None:
        cmd, _btn = self._commands[key]
        if cmd.keep_open:
            cmd.run()
            self.refresh()
            return
        self.close()
        cmd.run()

    # ------------------------------------------------------------------
    # introspection (tests, command palette parity)
    # ------------------------------------------------------------------
    def keys(self) -> list[str]:
        return list(self._commands)

    def button(self, key: str) -> QToolButton:
        return self._commands[key][1]

    def trigger(self, key: str) -> None:
        self.button(key).click()

    def row_captions(self) -> list[str]:
        return [row.caption() for row in self._rows]

    def entry(self, text: str) -> QAction:
        """The ordinary entry labelled `text` (KeyError if absent)."""
        for a in self.actions():
            if isinstance(a, QWidgetAction):
                continue
            if a.text().split("\t")[0] == text:
                return a
        raise KeyError(text)

    def entry_texts(self) -> list[str]:
        return [
            a.text().split("\t")[0]
            for a in self.actions()
            if not isinstance(a, QWidgetAction) and not a.isSeparator()
        ]
