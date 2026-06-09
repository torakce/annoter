"""Modal dialog to compose a GD&T feature control frame.

Inputs: characteristic, tolerance value, diameter prefix (checkbox),
tolerance modifier (M/L/P/E), and three datum cells supporting
composite datums (typed as `A-B`) plus datum modifiers (M/L/P/F).
A live preview of the resulting feature control frame is rendered
in real time as the user edits.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
    GdtState,
    by_family,
)
from annoter.views.items.gdt import GdtAnnotationItem


_NO_MODIFIER_LABEL = "—"


def _parse_datum_text(text: str) -> list[str]:
    """Split user input like 'A-B' into ['A', 'B']."""
    return [tok.strip().upper() for tok in text.split("-") if tok.strip()]


class _PreviewView(QGraphicsView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.TextAntialiasing
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setMinimumHeight(60)
        self.setStyleSheet("background: white;")
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: GdtAnnotationItem | None = None

    def set_state(self, state: GdtState) -> None:
        if self._item is None:
            self._item = GdtAnnotationItem(state)
            self._scene.addItem(self._item)
        else:
            self._item.set_state(state)
        self._scene.setSceneRect(self._item.boundingRect())
        self.fitInView(self._item.boundingRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        if self._item is not None:
            self.fitInView(self._item.boundingRect(), Qt.KeepAspectRatio)


class GdtDialog(QDialog):
    """Modal editor for a GdtState. `result_state()` returns the value
    on accept, or `None` on cancel."""

    def __init__(
        self,
        initial: GdtState | None = None,
        parent: QWidget | None = None,
        *,
        characteristic_locked: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit GD&T")
        self._state = initial if initial is not None else GdtState()
        self._result: GdtState | None = None

        outer = QVBoxLayout(self)

        # Characteristic ------------------------------------------------
        char_box = QGroupBox("Characteristic")
        char_layout = QHBoxLayout(char_box)
        self._char_combo = QComboBox()
        for fam in Family:
            for c in by_family()[fam]:
                _, name = CHARACTERISTIC_META[c]
                self._char_combo.addItem(f"{fam.value}: {name}", c)
        self._set_combo_value(self._char_combo, self._state.characteristic)
        if characteristic_locked:
            self._char_combo.setEnabled(False)
        char_layout.addWidget(self._char_combo)
        outer.addWidget(char_box)

        # Tolerance -----------------------------------------------------
        tol_box = QGroupBox("Tolerance")
        tol_form = QFormLayout(tol_box)
        self._diameter_cb = QCheckBox("Ø  (diameter prefix)")
        self._diameter_cb.setChecked(self._state.diameter_prefix)
        self._tol_value_edit = QLineEdit(self._state.tolerance_value)
        self._tol_value_edit.setPlaceholderText("e.g. 0.05")
        self._tol_modifier_combo = self._build_modifier_combo(
            ALLOWED_TOLERANCE_MODIFIERS, self._state.tolerance_modifier
        )
        tol_form.addRow("", self._diameter_cb)
        tol_form.addRow("Value:", self._tol_value_edit)
        tol_form.addRow("Modifier:", self._tol_modifier_combo)
        outer.addWidget(tol_box)

        # Datums --------------------------------------------------------
        datum_box = QGroupBox("Datums")
        datum_form = QFormLayout(datum_box)
        (
            self._d1_edit,
            self._d1_mod,
        ) = self._build_datum_row(self._state.datum_primary)
        (
            self._d2_edit,
            self._d2_mod,
        ) = self._build_datum_row(self._state.datum_secondary)
        (
            self._d3_edit,
            self._d3_mod,
        ) = self._build_datum_row(self._state.datum_tertiary)

        datum_form.addRow(
            "Primary:", self._row_widget(self._d1_edit, self._d1_mod)
        )
        datum_form.addRow(
            "Secondary:", self._row_widget(self._d2_edit, self._d2_mod)
        )
        datum_form.addRow(
            "Tertiary:", self._row_widget(self._d3_edit, self._d3_mod)
        )
        hint = QLabel(
            "Composite datum: type letters joined by '-', e.g. 'A-B'."
        )
        hint.setStyleSheet("color: gray;")
        datum_form.addRow(hint)
        outer.addWidget(datum_box)

        # Preview -------------------------------------------------------
        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        self._preview = _PreviewView()
        preview_layout.addWidget(self._preview)
        outer.addWidget(preview_box)

        # Buttons -------------------------------------------------------
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # Wire live updates --------------------------------------------
        self._char_combo.currentIndexChanged.connect(self._refresh_preview)
        self._diameter_cb.toggled.connect(self._refresh_preview)
        self._tol_value_edit.textChanged.connect(self._refresh_preview)
        self._tol_modifier_combo.currentIndexChanged.connect(
            self._refresh_preview
        )
        for ed in (self._d1_edit, self._d2_edit, self._d3_edit):
            ed.textChanged.connect(self._refresh_preview)
        for cb in (self._d1_mod, self._d2_mod, self._d3_mod):
            cb.currentIndexChanged.connect(self._refresh_preview)

        self._refresh_preview()

    # ------------------------------------------------------------------
    # builders
    # ------------------------------------------------------------------
    def _build_modifier_combo(
        self, allowed: tuple[str, ...], current: str | None
    ) -> QComboBox:
        cb = QComboBox()
        cb.addItem(_NO_MODIFIER_LABEL, None)
        for letter in allowed:
            cb.addItem(letter, letter)
        self._set_combo_value(cb, current)
        return cb

    def _build_datum_row(
        self, datum: DatumRef
    ) -> tuple[QLineEdit, QComboBox]:
        edit = QLineEdit("-".join(datum.letters))
        edit.setPlaceholderText("A or A-B")
        edit.setMaximumWidth(100)
        cb = self._build_modifier_combo(
            ALLOWED_DATUM_MODIFIERS, datum.modifier
        )
        return edit, cb

    def _row_widget(self, edit: QLineEdit, cb: QComboBox) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit)
        h.addWidget(cb)
        h.addStretch(1)
        return w

    def _set_combo_value(self, combo: QComboBox, value) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # collect
    # ------------------------------------------------------------------
    def _collect(self) -> GdtState:
        return GdtState(
            characteristic=self._char_combo.currentData(),
            diameter_prefix=self._diameter_cb.isChecked(),
            tolerance_value=self._tol_value_edit.text(),
            tolerance_modifier=self._tol_modifier_combo.currentData(),
            datum_primary=DatumRef(
                letters=_parse_datum_text(self._d1_edit.text()),
                modifier=self._d1_mod.currentData(),
            ),
            datum_secondary=DatumRef(
                letters=_parse_datum_text(self._d2_edit.text()),
                modifier=self._d2_mod.currentData(),
            ),
            datum_tertiary=DatumRef(
                letters=_parse_datum_text(self._d3_edit.text()),
                modifier=self._d3_mod.currentData(),
            ),
        )

    def _refresh_preview(self) -> None:
        self._preview.set_state(self._collect())

    def _on_accept(self) -> None:
        self._result = self._collect()
        self.accept()

    def result_state(self) -> GdtState | None:
        return self._result
