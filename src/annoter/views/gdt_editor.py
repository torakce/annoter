"""GdtInlineEditor: floating in-place editor for a feature control frame.

A vertical panel shown over the page near the item being edited,
inspired by CATIA's Geometrical Tolerance dialog but kept in-place so
the scene item stays its own live preview. It supports composite
(multi-row) frames where **each row has its own characteristic symbol**
(the item merges the symbol cell only across consecutive rows that share
one), plus upper/lower texts and an optional auxiliary frame:

    Top text [______________]
    line 1: [sym v][Ø v][value][mod v]  [A][m v][B][m v][C][m v]  [-]
    line 2: [sym v][Ø v][value][mod v]  [A][m v][B][m v][C][m v]  [-]
    [+ line]
    Aux [sym v][text]
    Bottom text [______________]
                                              [OK] [Cancel]

Lifecycle contract (driven by MainWindow):
    - `stateEdited(GdtState)` on every change -- apply to the item.
    - `committed()` on Enter, the confirm button, or when focus leaves
      the editor. Caller pushes the undo command and closes the editor.
    - `cancelled()` on Escape or the cancel button. Caller rolls back.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.model.gdt import (
    ALLOWED_DATUM_MODIFIERS,
    ALLOWED_TOLERANCE_MODIFIERS,
    CHARACTERISTIC_META,
    Characteristic,
    DatumRef,
    Family,
    GdtRow,
    GdtState,
    MODIFIER_NAMES,
    TOLERANCE_PREFIX_NAMES,
    TOLERANCE_PREFIXES,
    by_family,
    enclosed,
)
from annoter.views.icons import action_icon, gdt_symbol_icon


_NONE_LABEL = "—"  # em dash, shown when a slot has no value
_ARROW = "▾"  # explicit drop-down affordance appended to menu buttons
_SYMBOL_ICON_SIZE = 20
_MENU_ICON_SIZE = 20
_ACTION_ICON_SIZE = 16

_ACCENT = "#1E88E5"
_FIELD_BORDER = "#9aa0a6"

_PANEL_QSS = f"""
#GdtInlineEditor {{
    border: 2px solid {_ACCENT};
    border-radius: 6px;
}}
#GdtInlineEditor QLineEdit {{
    border: 1px solid {_FIELD_BORDER};
    border-radius: 3px;
    padding: 1px 3px;
}}
#GdtInlineEditor QToolButton {{
    border: 1px solid {_FIELD_BORDER};
    border-radius: 3px;
    padding: 1px 4px;
}}
#GdtInlineEditor QToolButton:hover {{
    border: 1px solid {_ACCENT};
}}
#GdtInlineEditor QToolButton:disabled {{
    color: #b0b0b0;
    border-color: #d8d8d8;
}}
#GdtInlineEditor QToolButton::menu-indicator {{
    image: none;
    width: 0;
}}
"""


def _parse_datum_text(text: str) -> list[str]:
    """Split user input like 'A-B' into ['A', 'B']."""
    return [tok.strip().upper() for tok in text.split("-") if tok.strip()]


def _fill_characteristic_menu(
    menu: QMenu, icon_color: QColor, setter, on_hide, *, allow_none: bool
) -> None:
    menu.aboutToHide.connect(on_hide)
    if allow_none:
        act = menu.addAction(f"{_NONE_LABEL}  None")
        act.triggered.connect(lambda: setter(None))
    families = by_family()
    for i, fam in enumerate(Family):
        if i or allow_none:
            menu.addSeparator()
        header = menu.addAction(fam.value)
        header.setEnabled(False)  # textless section header under the QSS
        for c in families[fam]:
            _, name = CHARACTERISTIC_META[c]
            act = menu.addAction(
                gdt_symbol_icon(c, _MENU_ICON_SIZE, icon_color), name
            )
            act.triggered.connect(lambda _c=False, ch=c: setter(ch))


def _sync_symbol_button(
    btn: QToolButton,
    characteristic: Characteristic | None,
    icon_color: QColor,
) -> None:
    btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    if characteristic is None:
        btn.setIcon(QIcon())
        btn.setText(f"{_NONE_LABEL} {_ARROW}")
        btn.setToolTip("Auxiliary symbol (none)")
    else:
        btn.setIcon(
            gdt_symbol_icon(characteristic, _SYMBOL_ICON_SIZE, icon_color)
        )
        btn.setText(_ARROW)
        _, name = CHARACTERISTIC_META[characteristic]
        btn.setToolTip(f"Characteristic: {name}")


class _RowEditor(QWidget):
    """One tolerance row: symbol, prefix, value, modifier, three datums."""

    changed = Signal()
    commitRequested = Signal()
    removeRequested = Signal(object)  # self

    def __init__(
        self, row: GdtRow, icon_color: QColor, on_menu_hide, parent=None
    ) -> None:
        super().__init__(parent)
        self._icon_color = icon_color
        self._on_menu_hide = on_menu_hide
        self._characteristic: Characteristic = row.characteristic
        self._prefix: str = row.tolerance_prefix
        self._tol_modifier: str | None = row.tolerance_modifier
        datums = (row.datum_primary, row.datum_secondary, row.datum_tertiary)
        self._datum_modifiers: list[str | None] = [d.modifier for d in datums]

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        self._symbol_btn = QToolButton(self)
        self._symbol_btn.setPopupMode(QToolButton.InstantPopup)
        self._symbol_btn.setFocusPolicy(Qt.ClickFocus)
        self._symbol_btn.setIconSize(
            QSize(_SYMBOL_ICON_SIZE, _SYMBOL_ICON_SIZE)
        )
        menu = QMenu(self._symbol_btn)
        _fill_characteristic_menu(
            menu, icon_color, self._set_characteristic, on_menu_hide,
            allow_none=False,
        )
        self._symbol_btn.setMenu(menu)
        _sync_symbol_button(self._symbol_btn, self._characteristic, icon_color)
        lay.addWidget(self._symbol_btn)

        self._prefix_btn = self._prefix_button(row.tolerance_prefix)
        lay.addWidget(self._prefix_btn)

        self._value_edit = QLineEdit(row.tolerance_value, self)
        self._value_edit.setPlaceholderText("0.05")
        self._value_edit.setFixedWidth(58)
        self._value_edit.setToolTip("Tolerance value")
        self._value_edit.textChanged.connect(self.changed)
        self._value_edit.returnPressed.connect(self.commitRequested)
        lay.addWidget(self._value_edit)

        self._tol_mod_btn = self._modifier_button(
            ALLOWED_TOLERANCE_MODIFIERS,
            row.tolerance_modifier,
            self._set_tol_modifier,
        )
        lay.addWidget(self._tol_mod_btn)

        lay.addWidget(self._v_separator())

        self._datum_edits: list[QLineEdit] = []
        self._datum_mod_btns: list[QToolButton] = []
        for i, (datum, placeholder) in enumerate(
            zip(datums, ("A", "B", "C"))
        ):
            edit = QLineEdit("-".join(datum.letters), self)
            edit.setPlaceholderText(placeholder)
            edit.setFixedWidth(32)
            edit.setAlignment(Qt.AlignCenter)
            edit.setToolTip("Datum letter(s); join with '-' (e.g. A-B)")
            edit.textChanged.connect(self.changed)
            edit.returnPressed.connect(self.commitRequested)
            lay.addWidget(edit)
            self._datum_edits.append(edit)
            btn = self._modifier_button(
                ALLOWED_DATUM_MODIFIERS,
                datum.modifier,
                lambda v, idx=i: self._set_datum_modifier(idx, v),
            )
            lay.addWidget(btn)
            self._datum_mod_btns.append(btn)

        lay.addSpacing(4)
        self._remove_btn = QToolButton(self)
        self._remove_btn.setFocusPolicy(Qt.ClickFocus)
        self._remove_btn.setText("✕")
        self._remove_btn.setToolTip("Remove this line")
        self._remove_btn.clicked.connect(
            lambda: self.removeRequested.emit(self)
        )
        lay.addWidget(self._remove_btn)

    # ------------------------------------------------------------------
    def _v_separator(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    def _prefix_button(self, current: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setFocusPolicy(Qt.ClickFocus)
        btn.setToolTip("Tolerance zone prefix")
        menu = QMenu(btn)
        menu.aboutToHide.connect(self._on_menu_hide)
        act = menu.addAction(f"{_NONE_LABEL}  No prefix")
        act.triggered.connect(lambda: self._set_prefix(""))
        for prefix in TOLERANCE_PREFIXES:
            act = menu.addAction(f"{prefix}  {TOLERANCE_PREFIX_NAMES[prefix]}")
            act.triggered.connect(lambda _c=False, pf=prefix: self._set_prefix(pf))
        btn.setMenu(menu)
        btn.setText(self._prefix_label(current))
        return btn

    def _modifier_button(self, allowed, current, setter) -> QToolButton:
        btn = QToolButton(self)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setFocusPolicy(Qt.ClickFocus)
        btn.setToolTip("Modifier")
        menu = QMenu(btn)
        menu.aboutToHide.connect(self._on_menu_hide)
        act = menu.addAction(f"{_NONE_LABEL}  No modifier")
        act.triggered.connect(lambda: setter(None))
        for letter in allowed:
            act = menu.addAction(f"{enclosed(letter)}  {MODIFIER_NAMES[letter]}")
            act.triggered.connect(lambda _c=False, lt=letter: setter(lt))
        btn.setMenu(menu)
        btn.setText(self._mod_label(current))
        return btn

    @staticmethod
    def _prefix_label(value: str) -> str:
        return f"{value if value else _NONE_LABEL} {_ARROW}"

    @staticmethod
    def _mod_label(value: str | None) -> str:
        return f"{enclosed(value) if value else _NONE_LABEL} {_ARROW}"

    def _set_characteristic(self, c: Characteristic) -> None:
        self._characteristic = c
        _sync_symbol_button(self._symbol_btn, c, self._icon_color)
        self.changed.emit()

    def _set_prefix(self, value: str) -> None:
        self._prefix = value
        self._prefix_btn.setText(self._prefix_label(value))
        self.changed.emit()

    def _set_tol_modifier(self, value: str | None) -> None:
        self._tol_modifier = value
        self._tol_mod_btn.setText(self._mod_label(value))
        self.changed.emit()

    def _set_datum_modifier(self, index: int, value: str | None) -> None:
        self._datum_modifiers[index] = value
        self._datum_mod_btns[index].setText(self._mod_label(value))
        self.changed.emit()

    def set_remove_enabled(self, enabled: bool) -> None:
        self._remove_btn.setEnabled(enabled)

    def first_field(self) -> QWidget:
        return self._value_edit

    def row_state(self) -> GdtRow:
        return GdtRow(
            characteristic=self._characteristic,
            tolerance_prefix=self._prefix,
            tolerance_value=self._value_edit.text(),
            tolerance_modifier=self._tol_modifier,
            datum_primary=DatumRef(
                _parse_datum_text(self._datum_edits[0].text()),
                self._datum_modifiers[0],
            ),
            datum_secondary=DatumRef(
                _parse_datum_text(self._datum_edits[1].text()),
                self._datum_modifiers[1],
            ),
            datum_tertiary=DatumRef(
                _parse_datum_text(self._datum_edits[2].text()),
                self._datum_modifiers[2],
            ),
        )


class GdtInlineEditor(QFrame):
    """Floating multi-row FCF editor. Parent it to the view's viewport."""

    stateEdited = Signal(object)  # GdtState, on every live change
    committed = Signal()
    cancelled = Signal()

    def __init__(
        self,
        initial: GdtState,
        parent: QWidget,
        *,
        icon_color: QColor | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("GdtInlineEditor")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        self.setStyleSheet(_PANEL_QSS)
        self._icon_color = (
            QColor(icon_color) if icon_color is not None
            else QColor("#212121")
        )
        self._aux_symbol: Characteristic | None = initial.aux_symbol
        self._finished = False
        self._watching_focus = False
        self._row_editors: list[_RowEditor] = []
        # Suppress live emissions until every widget exists (rows are
        # added before the lower/aux fields during construction).
        self._ready = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        # Upper text.
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self._field_label("Top"))
        self._upper_edit = QLineEdit(initial.upper_text, self)
        self._upper_edit.setPlaceholderText("upper text (e.g. 2x)")
        self._upper_edit.textChanged.connect(self._emit_state)
        self._upper_edit.returnPressed.connect(self._commit)
        top.addWidget(self._upper_edit)
        outer.addLayout(top)

        # Tolerance rows (each with its own symbol).
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(4)
        outer.addLayout(self._rows_box)
        for row in initial.all_rows():
            self._add_row_editor(row)

        add_btn = QToolButton(self)
        add_btn.setFocusPolicy(Qt.ClickFocus)
        add_btn.setText("+ line")
        add_btn.setToolTip("Add a composite tolerance line")
        add_btn.clicked.connect(lambda: self._add_row_editor(GdtRow()))
        outer.addWidget(add_btn, 0, Qt.AlignLeft)

        outer.addWidget(self._h_separator())

        # Auxiliary frame.
        aux = QHBoxLayout()
        aux.setSpacing(6)
        aux.addWidget(self._field_label("Aux"))
        self._aux_btn = QToolButton(self)
        self._aux_btn.setPopupMode(QToolButton.InstantPopup)
        self._aux_btn.setFocusPolicy(Qt.ClickFocus)
        self._aux_btn.setIconSize(QSize(_SYMBOL_ICON_SIZE, _SYMBOL_ICON_SIZE))
        aux_menu = QMenu(self._aux_btn)
        _fill_characteristic_menu(
            aux_menu, self._icon_color, self._set_aux_symbol,
            self._refocus_after_menu, allow_none=True,
        )
        self._aux_btn.setMenu(aux_menu)
        _sync_symbol_button(self._aux_btn, self._aux_symbol, self._icon_color)
        aux.addWidget(self._aux_btn)
        self._aux_edit = QLineEdit(initial.aux_text, self)
        self._aux_edit.setPlaceholderText("aux text (e.g. A-B)")
        self._aux_edit.setFixedWidth(96)
        self._aux_edit.textChanged.connect(self._emit_state)
        self._aux_edit.returnPressed.connect(self._commit)
        aux.addWidget(self._aux_edit)
        aux.addStretch(1)
        outer.addLayout(aux)

        # Lower text.
        low = QHBoxLayout()
        low.setSpacing(6)
        low.addWidget(self._field_label("Bottom"))
        self._lower_edit = QLineEdit(initial.lower_text, self)
        self._lower_edit.setPlaceholderText("lower text")
        self._lower_edit.textChanged.connect(self._emit_state)
        self._lower_edit.returnPressed.connect(self._commit)
        low.addWidget(self._lower_edit)
        outer.addLayout(low)

        outer.addWidget(self._h_separator())

        # Confirm / cancel.
        btns = QHBoxLayout()
        btns.addStretch(1)
        confirm = QToolButton(self)
        confirm.setFocusPolicy(Qt.ClickFocus)
        confirm.setIcon(action_icon("confirm", color=QColor("#2e7d32")))
        confirm.setIconSize(QSize(_ACTION_ICON_SIZE, _ACTION_ICON_SIZE))
        confirm.setToolTip("Apply (Enter)")
        confirm.clicked.connect(self._commit)
        btns.addWidget(confirm)
        cancel = QToolButton(self)
        cancel.setFocusPolicy(Qt.ClickFocus)
        cancel.setIcon(action_icon("cancel", color=QColor("#c62828")))
        cancel.setIconSize(QSize(_ACTION_ICON_SIZE, _ACTION_ICON_SIZE))
        cancel.setToolTip("Discard (Esc)")
        cancel.clicked.connect(self._cancel)
        btns.addWidget(cancel)
        outer.addLayout(btns)

        self._update_remove_buttons()
        self._ready = True

    # ------------------------------------------------------------------
    # small builders
    # ------------------------------------------------------------------
    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setFixedWidth(46)
        return lbl

    def _h_separator(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    # ------------------------------------------------------------------
    # rows
    # ------------------------------------------------------------------
    def _add_row_editor(self, row: GdtRow) -> None:
        editor = _RowEditor(row, self._icon_color, self._refocus_after_menu, self)
        editor.changed.connect(self._emit_state)
        editor.commitRequested.connect(self._commit)
        editor.removeRequested.connect(self._remove_row_editor)
        self._row_editors.append(editor)
        self._rows_box.addWidget(editor)
        self._update_remove_buttons()
        self.adjustSize()
        self._emit_state()

    def _remove_row_editor(self, editor: _RowEditor) -> None:
        if len(self._row_editors) <= 1:
            return
        self._row_editors.remove(editor)
        self._rows_box.removeWidget(editor)
        editor.setParent(None)
        editor.deleteLater()
        self._update_remove_buttons()
        self.adjustSize()
        self._emit_state()

    def _update_remove_buttons(self) -> None:
        multi = len(self._row_editors) > 1
        for re in self._row_editors:
            re.set_remove_enabled(multi)

    def _set_aux_symbol(self, c: Characteristic | None) -> None:
        self._aux_symbol = c
        _sync_symbol_button(self._aux_btn, c, self._icon_color)
        self._emit_state()

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def current_state(self) -> GdtState:
        rows = [re.row_state() for re in self._row_editors] or [GdtRow()]
        row0 = rows[0]
        return GdtState(
            characteristic=row0.characteristic,
            tolerance_prefix=row0.tolerance_prefix,
            tolerance_value=row0.tolerance_value,
            tolerance_modifier=row0.tolerance_modifier,
            datum_primary=row0.datum_primary,
            datum_secondary=row0.datum_secondary,
            datum_tertiary=row0.datum_tertiary,
            additional_rows=rows[1:],
            upper_text=self._upper_edit.text(),
            lower_text=self._lower_edit.text(),
            aux_symbol=self._aux_symbol,
            aux_text=self._aux_edit.text(),
        )

    def _emit_state(self, *_args) -> None:
        if not self._ready:
            return
        self.stateEdited.emit(self.current_state())

    # ------------------------------------------------------------------
    # open / commit / cancel
    # ------------------------------------------------------------------
    def open(self) -> None:
        self.adjustSize()
        self.show()
        self.raise_()
        self._focus_first_field()
        app = QApplication.instance()
        if app is not None and not self._watching_focus:
            app.focusChanged.connect(self._on_app_focus_changed)
            self._watching_focus = True

    def _focus_first_field(self) -> None:
        if self._row_editors:
            field = self._row_editors[0].first_field()
            field.setFocus()
            if isinstance(field, QLineEdit):
                field.selectAll()

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

    def _on_app_focus_changed(self, _old, now) -> None:
        if now is None or self._finished:
            return
        if self._is_inside(now):
            return
        QTimer.singleShot(0, self._maybe_commit_on_focus_loss)

    def _maybe_commit_on_focus_loss(self) -> None:
        if self._finished:
            return
        if QApplication.activePopupWidget() is not None:
            return  # one of our menus is open; not a focus loss
        w = QApplication.focusWidget()
        if w is None or self._is_inside(w):
            return
        self._commit()

    def _refocus_after_menu(self) -> None:
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

    def hideEvent(self, event) -> None:  # noqa: ANN001
        self._stop_focus_watch()
        super().hideEvent(event)
