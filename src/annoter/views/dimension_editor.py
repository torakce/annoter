"""DimensionInlineEditor: floating in-place editor for a Dimension annotation.

A single-row floating panel shown over the page near the item being
edited, following the exact same interaction contract as
`GdtInlineEditor` (see `annoter.views.gdt_editor`): the scene item is
its own live preview, and the editor only closes when the user
explicitly commits or cancels.

    [prefix v] [nominal____] [tol mode v] [+/-tol fields...]  [OK] [Cancel]

    - `committed()` on Enter, the confirm button. Caller pushes the
      undo command and closes the editor.
    - `cancelled()` on Escape or the cancel button. Caller rolls back.

Clicking elsewhere does NOT close it; MainWindow still commits it on
document save, page switch, or when another frame is opened.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.model.dimension import DimensionPrefix, DimensionState, PREFIX_NAMES, ToleranceMode
from annoter.views.icons import action_icon


_NONE_LABEL = "—"  # em dash, shown when a slot has no value
_ARROW = "▾"  # explicit drop-down affordance appended to menu buttons
_ACTION_ICON_SIZE = 16

_ACCENT = "#1E88E5"
_FIELD_BORDER = "#9aa0a6"

_PANEL_QSS = f"""
#DimensionInlineEditor {{
    border: 2px solid {_ACCENT};
    border-radius: 6px;
}}
#DimensionInlineEditor QLineEdit {{
    border: 1px solid {_FIELD_BORDER};
    border-radius: 3px;
    padding: 1px 3px;
}}
#DimensionInlineEditor QToolButton {{
    border: 1px solid {_FIELD_BORDER};
    border-radius: 3px;
    padding: 1px 4px;
}}
#DimensionInlineEditor QToolButton:hover {{
    border: 1px solid {_ACCENT};
}}
#DimensionInlineEditor QToolButton::menu-indicator {{
    image: none;
    width: 0;
}}
"""

_MODE_LABELS: dict[ToleranceMode, str] = {
    ToleranceMode.NONE: "No tolerance",
    ToleranceMode.SYMMETRIC: "± symmetric",
    ToleranceMode.BILATERAL: "+/- bilateral",
}

_MODE_SHORT: dict[ToleranceMode, str] = {
    ToleranceMode.NONE: _NONE_LABEL,
    ToleranceMode.SYMMETRIC: "±",
    ToleranceMode.BILATERAL: "+/-",
}


class DimensionInlineEditor(QFrame):
    """Floating single-row dimension editor. Parent it to the view's viewport."""

    stateEdited = Signal(object)  # DimensionState, on every live change
    committed = Signal()
    cancelled = Signal()

    def __init__(
        self,
        initial: DimensionState,
        parent: QWidget,
        *,
        icon_color: QColor | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DimensionInlineEditor")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        self.setStyleSheet(_PANEL_QSS)
        self._icon_color = (
            QColor(icon_color) if icon_color is not None else QColor("#212121")
        )
        self._prefix: DimensionPrefix = initial.prefix
        self._tolerance_mode: ToleranceMode = initial.tolerance_mode
        self._finished = False
        # Suppress live emissions until every widget exists.
        self._ready = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)
        # Grow/shrink the panel as tolerance fields are shown/hidden.
        outer.setSizeConstraint(QLayout.SetFixedSize)

        row = QHBoxLayout()
        row.setSpacing(3)

        self._prefix_btn = self._build_prefix_button()
        row.addWidget(self._prefix_btn)

        self._nominal_edit = QLineEdit(initial.nominal, self)
        self._nominal_edit.setPlaceholderText("45.00")
        self._nominal_edit.setFixedWidth(70)
        self._nominal_edit.setToolTip("Nominal value")
        self._nominal_edit.textChanged.connect(self._emit_state)
        self._nominal_edit.returnPressed.connect(self._commit)
        row.addWidget(self._nominal_edit)

        row.addWidget(self._v_separator())

        self._mode_btn = self._build_mode_button()
        row.addWidget(self._mode_btn)

        self._tol_value_edit = QLineEdit(initial.tol_value, self)
        self._tol_value_edit.setPlaceholderText("0.05")
        self._tol_value_edit.setFixedWidth(50)
        self._tol_value_edit.setToolTip("Tolerance (±)")
        self._tol_value_edit.textChanged.connect(self._emit_state)
        self._tol_value_edit.returnPressed.connect(self._commit)
        row.addWidget(self._tol_value_edit)

        self._upper_label = QLabel("+", self)
        row.addWidget(self._upper_label)
        self._tol_upper_edit = QLineEdit(initial.tol_upper, self)
        self._tol_upper_edit.setPlaceholderText("0.10")
        self._tol_upper_edit.setFixedWidth(50)
        self._tol_upper_edit.setToolTip("Upper tolerance offset")
        self._tol_upper_edit.textChanged.connect(self._emit_state)
        self._tol_upper_edit.returnPressed.connect(self._commit)
        row.addWidget(self._tol_upper_edit)

        self._lower_label = QLabel("-", self)
        row.addWidget(self._lower_label)
        self._tol_lower_edit = QLineEdit(initial.tol_lower, self)
        self._tol_lower_edit.setPlaceholderText("0.05")
        self._tol_lower_edit.setFixedWidth(50)
        self._tol_lower_edit.setToolTip("Lower tolerance offset")
        self._tol_lower_edit.textChanged.connect(self._emit_state)
        self._tol_lower_edit.returnPressed.connect(self._commit)
        row.addWidget(self._tol_lower_edit)

        row.addSpacing(6)
        confirm = QToolButton(self)
        confirm.setFocusPolicy(Qt.ClickFocus)
        confirm.setIcon(action_icon("confirm", color=QColor("#2e7d32")))
        confirm.setIconSize(QSize(_ACTION_ICON_SIZE, _ACTION_ICON_SIZE))
        confirm.setToolTip("Apply (Enter)")
        confirm.clicked.connect(self._commit)
        row.addWidget(confirm)
        cancel = QToolButton(self)
        cancel.setFocusPolicy(Qt.ClickFocus)
        cancel.setIcon(action_icon("cancel", color=QColor("#c62828")))
        cancel.setIconSize(QSize(_ACTION_ICON_SIZE, _ACTION_ICON_SIZE))
        cancel.setToolTip("Discard (Esc)")
        cancel.clicked.connect(self._cancel)
        row.addWidget(cancel)

        outer.addLayout(row)

        self._sync_tolerance_visibility()
        self._ready = True

    # ------------------------------------------------------------------
    # small builders
    # ------------------------------------------------------------------
    def _v_separator(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    def _build_prefix_button(self) -> QToolButton:
        btn = QToolButton(self)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setFocusPolicy(Qt.ClickFocus)
        btn.setToolTip("Prefix")
        menu = QMenu(btn)
        menu.aboutToHide.connect(self._refocus_after_menu)
        for prefix in DimensionPrefix:
            display = prefix.value if prefix.value else _NONE_LABEL
            act = menu.addAction(f"{display}  {PREFIX_NAMES[prefix]}")
            act.triggered.connect(lambda _c=False, pf=prefix: self._set_prefix(pf))
        btn.setMenu(menu)
        btn.setText(self._prefix_label(self._prefix))
        return btn

    def _build_mode_button(self) -> QToolButton:
        btn = QToolButton(self)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setFocusPolicy(Qt.ClickFocus)
        btn.setToolTip("Tolerance mode")
        menu = QMenu(btn)
        menu.aboutToHide.connect(self._refocus_after_menu)
        for mode in ToleranceMode:
            act = menu.addAction(_MODE_LABELS[mode])
            act.triggered.connect(lambda _c=False, m=mode: self._set_mode(m))
        btn.setMenu(menu)
        btn.setText(self._mode_label(self._tolerance_mode))
        return btn

    @staticmethod
    def _prefix_label(value: DimensionPrefix) -> str:
        display = value.value if value.value else _NONE_LABEL
        return f"{display} {_ARROW}"

    @staticmethod
    def _mode_label(value: ToleranceMode) -> str:
        return f"{_MODE_SHORT[value]} {_ARROW}"

    # ------------------------------------------------------------------
    # setters
    # ------------------------------------------------------------------
    def _set_prefix(self, value: DimensionPrefix) -> None:
        self._prefix = value
        self._prefix_btn.setText(self._prefix_label(value))
        self._emit_state()

    def _set_mode(self, value: ToleranceMode) -> None:
        self._tolerance_mode = value
        self._mode_btn.setText(self._mode_label(value))
        self._sync_tolerance_visibility()
        self._emit_state()

    def _sync_tolerance_visibility(self) -> None:
        mode = self._tolerance_mode
        self._tol_value_edit.setVisible(mode is ToleranceMode.SYMMETRIC)
        bilateral = mode is ToleranceMode.BILATERAL
        self._upper_label.setVisible(bilateral)
        self._tol_upper_edit.setVisible(bilateral)
        self._lower_label.setVisible(bilateral)
        self._tol_lower_edit.setVisible(bilateral)

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def current_state(self) -> DimensionState:
        return DimensionState(
            prefix=self._prefix,
            nominal=self._nominal_edit.text(),
            tolerance_mode=self._tolerance_mode,
            tol_value=self._tol_value_edit.text(),
            tol_upper=self._tol_upper_edit.text(),
            tol_lower=self._tol_lower_edit.text(),
        )

    def _emit_state(self, *_args) -> None:
        if not self._ready:
            return
        self.stateEdited.emit(self.current_state())

    # ------------------------------------------------------------------
    # open / commit / cancel  (explicit only -- no commit on focus loss)
    # ------------------------------------------------------------------
    def open(self) -> None:
        self.adjustSize()
        self.show()
        self.raise_()
        self._focus_first_field()

    def _focus_first_field(self) -> None:
        self._nominal_edit.setFocus()
        self._nominal_edit.selectAll()

    def _commit(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.committed.emit()

    def _cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.cancelled.emit()

    def _is_inside(self, widget) -> bool:
        # Walk parentWidget(): unlike isAncestorOf it crosses window
        # boundaries, so popup menus parented to their buttons count as
        # "inside".
        w = widget
        while w is not None:
            if w is self:
                return True
            w = w.parentWidget()
        return False

    def _refocus_after_menu(self) -> None:
        # When a popup menu closes the focus stays on the (focus-less)
        # button; pull it back into a field so Enter keeps committing.
        QTimer.singleShot(0, self._take_focus_back)

    def _take_focus_back(self) -> None:
        if self._finished:
            return
        w = QApplication.focusWidget()
        if w is not None and self._is_inside(w):
            return
        self._focus_first_field()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if event.key() == Qt.Key_Escape:
            self._cancel()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._commit()
            event.accept()
            return
        super().keyPressEvent(event)
