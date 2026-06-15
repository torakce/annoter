"""NoteEditor: floating popup for editing a sticky note's text.

A small frame with a multi-line text field shown over the page next to
the note icon, mirroring Acrobat's note popup. Follows the same
lifecycle contract as `GdtInlineEditor`:

    - `committed()` on Ctrl+Enter, the confirm button, or when focus
      leaves the editor. The caller pushes the undo command.
    - `cancelled()` on Escape or the cancel button. The caller rolls back.

Plain Enter inserts a newline (notes are multi-line), so commit is bound
to Ctrl+Enter instead.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.views.icons import action_icon


_ACTION_ICON_SIZE = 16


class NoteEditor(QFrame):
    """Floating note-text editor. Parent it to the view's viewport."""

    committed = Signal()
    cancelled = Signal()

    def __init__(self, initial_text: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("NoteEditor")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        self._finished = False
        self._watching_focus = False

        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 6)
        col.setSpacing(4)

        self._edit = QPlainTextEdit(initial_text, self)
        self._edit.setPlaceholderText("Type a note...")
        self._edit.setFixedSize(220, 96)
        col.addWidget(self._edit)

        buttons = QHBoxLayout()
        buttons.setSpacing(2)
        buttons.addStretch(1)
        confirm = QToolButton(self)
        confirm.setFocusPolicy(Qt.ClickFocus)
        confirm.setIcon(action_icon("confirm", color=QColor("#2e7d32")))
        confirm.setIconSize(QSize(_ACTION_ICON_SIZE, _ACTION_ICON_SIZE))
        confirm.setToolTip("Apply (Ctrl+Enter)")
        confirm.clicked.connect(self._commit)
        buttons.addWidget(confirm)
        cancel = QToolButton(self)
        cancel.setFocusPolicy(Qt.ClickFocus)
        cancel.setIcon(action_icon("cancel", color=QColor("#c62828")))
        cancel.setIconSize(QSize(_ACTION_ICON_SIZE, _ACTION_ICON_SIZE))
        cancel.setToolTip("Discard (Esc)")
        cancel.clicked.connect(self._cancel)
        buttons.addWidget(cancel)
        col.addLayout(buttons)

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def current_text(self) -> str:
        return self._edit.toPlainText()

    # ------------------------------------------------------------------
    # open / commit / cancel
    # ------------------------------------------------------------------
    def open(self) -> None:
        self.adjustSize()
        self.show()
        self.raise_()
        self._edit.setFocus()
        self._edit.selectAll()
        app = QApplication.instance()
        if app is not None and not self._watching_focus:
            app.focusChanged.connect(self._on_app_focus_changed)
            self._watching_focus = True

    def _commit(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._stop_focus_watch()
        self.committed.emit()

    def _cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._stop_focus_watch()
        self.cancelled.emit()

    def _stop_focus_watch(self) -> None:
        if not self._watching_focus:
            return
        self._watching_focus = False
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.disconnect(self._on_app_focus_changed)

    def _is_inside(self, widget) -> bool:  # noqa: ANN001
        w = widget
        while w is not None:
            if w is self:
                return True
            w = w.parentWidget()
        return False

    def _on_app_focus_changed(self, _old, now) -> None:  # noqa: ANN001
        if now is None or self._finished:
            return
        if self._is_inside(now):
            return
        QTimer.singleShot(0, self._maybe_commit_on_focus_loss)

    def _maybe_commit_on_focus_loss(self) -> None:
        if self._finished:
            return
        if QApplication.activePopupWidget() is not None:
            return
        w = QApplication.focusWidget()
        if w is None or self._is_inside(w):
            return
        self._commit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self._cancel()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (
            event.modifiers() & Qt.ControlModifier
        ):
            self._commit()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: ANN001
        self._stop_focus_watch()
        super().hideEvent(event)
