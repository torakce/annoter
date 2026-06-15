"""GdtInlineEditor: floating in-place editor for a feature control frame.

Replaces the modal GdtDialog. The editor is a small strip shaped like
the printed FCF itself -- symbol cell, tolerance cell, three datum
cells -- shown directly over the page near the item being edited. Every
keystroke is pushed out through `stateEdited` so the caller can update
the real `GdtAnnotationItem` live; the scene item is its own preview.

The characteristic symbol is picked from a drop-down menu (grouped by
ISO 1101 family) instead of a side palette.

Lifecycle contract (driven by MainWindow):
    - `stateEdited(GdtState)` on every change -- apply to the item.
    - `committed()` on Enter, on the confirm button, or when focus
      leaves the editor (click elsewhere). Caller pushes the undo
      command and closes the editor.
    - `cancelled()` on Escape or the cancel button. Caller rolls back.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QToolButton,
    QWidget,
)

from annoter.model.gdt import (
    ALLOWED_DATUM_MODIFIERS,
    ALLOWED_TOLERANCE_MODIFIERS,
    CHARACTERISTIC_META,
    Characteristic,
    DatumRef,
    Family,
    GdtState,
    MODIFIER_NAMES,
    TOLERANCE_PREFIX_NAMES,
    TOLERANCE_PREFIXES,
    by_family,
    enclosed,
)
from annoter.views.icons import action_icon, gdt_symbol_icon


_NO_MODIFIER_LABEL = "—"  # em dash
_SYMBOL_ICON_SIZE = 22
_MENU_ICON_SIZE = 20
_ACTION_ICON_SIZE = 16


def _parse_datum_text(text: str) -> list[str]:
    """Split user input like 'A-B' into ['A', 'B']."""
    return [tok.strip().upper() for tok in text.split("-") if tok.strip()]


class GdtInlineEditor(QFrame):
    """Floating FCF-shaped editor. Parent it to the view's viewport."""

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
        self._icon_color = (
            QColor(icon_color) if icon_color is not None
            else QColor("#212121")
        )
        self._characteristic: Characteristic = initial.characteristic
        self._tol_prefix: str = initial.tolerance_prefix
        self._tol_modifier: str | None = initial.tolerance_modifier
        self._datum_modifiers: list[str | None] = [
            initial.datum_primary.modifier,
            initial.datum_secondary.modifier,
            initial.datum_tertiary.modifier,
        ]
        # True once committed/cancelled has fired; blocks the duplicate
        # emission the focus watcher would otherwise produce on hide.
        self._finished = False
        self._watching_focus = False

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(2)

        self._symbol_btn = self._build_symbol_button()
        row.addWidget(self._symbol_btn)
        row.addWidget(self._v_separator())

        self._prefix_btn = self._build_prefix_button(
            initial.tolerance_prefix
        )
        row.addWidget(self._prefix_btn)

        self._tol_edit = QLineEdit(initial.tolerance_value, self)
        self._tol_edit.setPlaceholderText("0.05")
        self._tol_edit.setFixedWidth(64)
        self._tol_edit.setToolTip("Tolerance value")
        self._tol_edit.textChanged.connect(self._emit_state)
        self._tol_edit.returnPressed.connect(self._commit)
        row.addWidget(self._tol_edit)

        self._tol_mod_btn = self._build_modifier_button(
            ALLOWED_TOLERANCE_MODIFIERS,
            initial.tolerance_modifier,
            self._set_tol_modifier,
        )
        row.addWidget(self._tol_mod_btn)

        self._datum_edits: list[QLineEdit] = []
        self._datum_mod_btns: list[QToolButton] = []
        initial_datums = (
            initial.datum_primary,
            initial.datum_secondary,
            initial.datum_tertiary,
        )
        for i, (datum, placeholder) in enumerate(
            zip(initial_datums, ("A", "B", "C"))
        ):
            row.addWidget(self._v_separator())
            edit = QLineEdit("-".join(datum.letters), self)
            edit.setPlaceholderText(placeholder)
            edit.setFixedWidth(36)
            edit.setAlignment(Qt.AlignCenter)
            edit.setToolTip(
                "Datum letter(s); join with '-' for a composite "
                "datum (e.g. A-B)"
            )
            edit.textChanged.connect(self._emit_state)
            edit.returnPressed.connect(self._commit)
            row.addWidget(edit)
            self._datum_edits.append(edit)

            btn = self._build_modifier_button(
                ALLOWED_DATUM_MODIFIERS,
                datum.modifier,
                lambda v, idx=i: self._set_datum_modifier(idx, v),
            )
            row.addWidget(btn)
            self._datum_mod_btns.append(btn)

        row.addSpacing(4)
        row.addWidget(self._v_separator())

        confirm = QToolButton(self)
        confirm.setFocusPolicy(Qt.ClickFocus)
        confirm.setIcon(
            action_icon("confirm", color=QColor("#2e7d32"))
        )
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

    # ------------------------------------------------------------------
    # builders
    # ------------------------------------------------------------------
    def _v_separator(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    def _build_symbol_button(self) -> QToolButton:
        btn = QToolButton(self)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setFocusPolicy(Qt.ClickFocus)
        btn.setIconSize(QSize(_SYMBOL_ICON_SIZE, _SYMBOL_ICON_SIZE))
        btn.setToolTip("Geometric characteristic")

        menu = QMenu(btn)
        menu.aboutToHide.connect(self._refocus_after_menu)
        families = by_family()
        for i, fam in enumerate(Family):
            if i:
                menu.addSeparator()
            # Disabled action as a family header: QMenu::addSection
            # renders without text under the app's QSS styles.
            header = menu.addAction(fam.value)
            header.setEnabled(False)
            for c in families[fam]:
                _, name = CHARACTERISTIC_META[c]
                act = menu.addAction(
                    gdt_symbol_icon(c, _MENU_ICON_SIZE, self._icon_color),
                    name,
                )
                act.triggered.connect(
                    lambda _checked=False, ch=c: (
                        self._set_characteristic(ch)
                    )
                )
        btn.setMenu(menu)
        self._sync_symbol_button(btn)
        return btn

    def _build_prefix_button(self, current: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setFocusPolicy(Qt.ClickFocus)
        btn.setToolTip("Tolerance zone prefix")
        font = QFont(btn.font())
        font.setPointSize(font.pointSize() + 1)
        btn.setFont(font)
        menu = QMenu(btn)
        menu.aboutToHide.connect(self._refocus_after_menu)
        act = menu.addAction(f"{_NO_MODIFIER_LABEL}  No prefix")
        act.triggered.connect(
            lambda _checked=False: self._set_prefix("")
        )
        for prefix in TOLERANCE_PREFIXES:
            act = menu.addAction(
                f"{prefix}  {TOLERANCE_PREFIX_NAMES[prefix]}"
            )
            act.triggered.connect(
                lambda _checked=False, pf=prefix: self._set_prefix(pf)
            )
        btn.setMenu(menu)
        btn.setText(current if current else _NO_MODIFIER_LABEL)
        return btn

    def _build_modifier_button(
        self,
        allowed: tuple[str, ...],
        current: str | None,
        setter,
    ) -> QToolButton:
        btn = QToolButton(self)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setFocusPolicy(Qt.ClickFocus)
        btn.setToolTip("Modifier")
        menu = QMenu(btn)
        menu.aboutToHide.connect(self._refocus_after_menu)
        act = menu.addAction(f"{_NO_MODIFIER_LABEL}  No modifier")
        act.triggered.connect(
            lambda _checked=False: setter(None)
        )
        for letter in allowed:
            act = menu.addAction(
                f"{enclosed(letter)}  {MODIFIER_NAMES[letter]}"
            )
            act.triggered.connect(
                lambda _checked=False, lt=letter: setter(lt)
            )
        btn.setMenu(menu)
        btn.setText(
            enclosed(current) if current else _NO_MODIFIER_LABEL
        )
        return btn

    # ------------------------------------------------------------------
    # field setters (menu callbacks)
    # ------------------------------------------------------------------
    def _set_characteristic(self, c: Characteristic) -> None:
        self._characteristic = c
        self._sync_symbol_button(self._symbol_btn)
        self._emit_state()

    def _sync_symbol_button(self, btn: QToolButton) -> None:
        btn.setIcon(
            gdt_symbol_icon(
                self._characteristic, _SYMBOL_ICON_SIZE, self._icon_color
            )
        )
        _, name = CHARACTERISTIC_META[self._characteristic]
        btn.setToolTip(f"Geometric characteristic: {name}")

    def _set_prefix(self, value: str) -> None:
        self._tol_prefix = value
        self._prefix_btn.setText(value if value else _NO_MODIFIER_LABEL)
        self._emit_state()

    def _set_tol_modifier(self, value: str | None) -> None:
        self._tol_modifier = value
        self._tol_mod_btn.setText(
            enclosed(value) if value else _NO_MODIFIER_LABEL
        )
        self._emit_state()

    def _set_datum_modifier(self, index: int, value: str | None) -> None:
        self._datum_modifiers[index] = value
        self._datum_mod_btns[index].setText(
            enclosed(value) if value else _NO_MODIFIER_LABEL
        )
        self._emit_state()

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def current_state(self) -> GdtState:
        datums = [
            DatumRef(
                letters=_parse_datum_text(edit.text()),
                modifier=self._datum_modifiers[i],
            )
            for i, edit in enumerate(self._datum_edits)
        ]
        return GdtState(
            characteristic=self._characteristic,
            tolerance_prefix=self._tol_prefix,
            tolerance_value=self._tol_edit.text(),
            tolerance_modifier=self._tol_modifier,
            datum_primary=datums[0],
            datum_secondary=datums[1],
            datum_tertiary=datums[2],
        )

    def _emit_state(self, *_args) -> None:
        self.stateEdited.emit(self.current_state())

    # ------------------------------------------------------------------
    # open / commit / cancel
    # ------------------------------------------------------------------
    def open(self) -> None:
        """Show, focus the tolerance field, start watching focus."""
        self.adjustSize()
        self.show()
        self.raise_()
        self._tol_edit.setFocus()
        self._tol_edit.selectAll()
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

    def _is_inside(self, widget) -> bool:
        # Walk parentWidget(): unlike isAncestorOf it crosses window
        # boundaries, so popup menus parented to their buttons count
        # as "inside".
        w = widget
        while w is not None:
            if w is self:
                return True
            w = w.parentWidget()
        return False

    def _on_app_focus_changed(self, _old, now) -> None:
        # `now is None` means the app lost activation (Alt-Tab) -- not
        # a commit gesture.
        if now is None or self._finished:
            return
        if self._is_inside(now):
            return
        # Do not commit synchronously: opening one of the editor's
        # popup menus bounces the focus to the view (the tool buttons
        # never take focus themselves), which would destroy the editor
        # before the menu action fires. Decide on the next tick, when
        # the popup -- if any -- is active.
        QTimer.singleShot(0, self._maybe_commit_on_focus_loss)

    def _maybe_commit_on_focus_loss(self) -> None:
        if self._finished:
            return
        if QApplication.activePopupWidget() is not None:
            return  # one of our menus is open; not a focus loss
        w = QApplication.focusWidget()
        if w is None or self._is_inside(w):
            # None: the app lost activation, same stance as Alt-Tab.
            return
        self._commit()

    def _refocus_after_menu(self) -> None:
        # When a popup menu closes, the focus stays wherever it bounced
        # to (the view). Pull it back so typing keeps working and the
        # focus watcher does not read it as a commit gesture.
        QTimer.singleShot(0, self._take_focus_back)

    def _take_focus_back(self) -> None:
        if self._finished:
            return
        w = QApplication.focusWidget()
        if w is not None and self._is_inside(w):
            return
        self._tol_edit.setFocus()

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
