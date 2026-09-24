"""Reusable controls of the inspector (UI redesign, Lot F).

- `SegmentedControl`: a row of mutually exclusive buttons (outline
  straight / cloud, line style, alignment, units...), friendlier than a
  combo box when there are 2-5 choices, because every option is visible.
- `ColorSwatchRow`: the user's palette as round swatches, a "more
  colors" button (the Office-style picker) and an "edit palette" button.
- `StrokeField`: preset widths as a segmented control plus an exact
  value field (`StrokeSpinBox`) -- presets for speed, typing for
  precision (mock-up review comment).
- `PaletteEditor`: dialog to recolor, rename, reorder, add, remove and
  reset the palette swatches (mock-up review comment).

All of them are dumb widgets: they emit the user's choice; the inspector
and MainWindow decide what it changes.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.services.palette import MAX_SWATCHES, PaletteStore
from annoter.views.color_picker import popup_color_picker
from annoter.views.line_icons import line_icon
from annoter.views.stroke_spin import StrokeSpinBox

# Preset stroke widths offered next to the exact field (px, like the
# field itself; the ladder in stroke_spin keeps the arrows useful).
STROKE_PRESETS: tuple[int, ...] = (1, 2, 3, 5)


# ----------------------------------------------------------------------
# segmented control
# ----------------------------------------------------------------------
class SegmentedControl(QWidget):
    """Exclusive choice among a few options, all visible at once.

    `options` are (value, text, icon-or-None, tooltip). Emits
    `valueChanged(value)` only on a user click, never on `set_value`.
    """

    valueChanged = Signal(object)

    def __init__(
        self,
        options: Iterable[tuple[object, str, QIcon | None, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._values: list[object] = []
        self._buttons: list[QToolButton] = []
        for value, text, icon, tip in options:
            b = QToolButton(self)
            b.setObjectName("SegmentButton")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            if icon is not None and not icon.isNull():
                b.setIcon(icon)
                b.setIconSize(QSize(22, 12) if not text else QSize(16, 16))
            if text:
                b.setText(text)
            b.setToolButtonStyle(
                Qt.ToolButtonTextBesideIcon
                if (icon is not None and text)
                else (Qt.ToolButtonTextOnly if text else Qt.ToolButtonIconOnly)
            )
            b.setToolTip(tip or text)
            b.setAccessibleName(tip or text)
            idx = len(self._values)
            b.clicked.connect(lambda _c=False, i=idx: self._clicked(i))
            self._group.addButton(b, idx)
            self._values.append(value)
            self._buttons.append(b)
            row.addWidget(b)

    def _clicked(self, index: int) -> None:
        self.valueChanged.emit(self._values[index])

    def value(self) -> object:
        i = self._group.checkedId()
        return self._values[i] if 0 <= i < len(self._values) else None

    def set_value(self, value: object) -> None:
        """Check the matching option, or none when no option matches."""
        self._group.setExclusive(False)
        for v, b in zip(self._values, self._buttons, strict=True):
            b.setChecked(v == value)
        self._group.setExclusive(True)

    def button_for(self, value: object) -> QToolButton | None:
        for v, b in zip(self._values, self._buttons, strict=True):
            if v == value:
                return b
        return None

    def values(self) -> list[object]:
        return list(self._values)


# ----------------------------------------------------------------------
# color swatches
# ----------------------------------------------------------------------
class _Swatch(QAbstractButton):
    """Round color dot; a ring marks the current color."""

    SIZE = 26

    def __init__(self, color: QColor, name: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._ring = QColor("#1D1E20")
        self.setCheckable(True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(f"{name} ({self._color.name().upper()})")
        self.setAccessibleName(name)

    def color(self) -> QColor:
        return QColor(self._color)

    def set_ring_color(self, color: QColor) -> None:
        self._ring = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self.isChecked():
            p.setPen(QPen(self._ring, 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r)
        inner = r.adjusted(4, 4, -4, -4)
        # Outline in the ring color so a dark swatch stays visible on
        # the dark theme (and a light one on the light theme).
        edge = QColor(self._ring)
        edge.setAlpha(90)
        p.setPen(QPen(edge, 1.0))
        p.setBrush(self._color)
        p.drawEllipse(inner)
        if self.underMouse() and not self.isChecked():
            p.setPen(QPen(QColor(0, 0, 0, 60), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r)
        p.end()

    def sizeHint(self) -> QSize:
        return QSize(self.SIZE, self.SIZE)


class ColorSwatchRow(QWidget):
    """Palette swatches + more colors + edit palette."""

    colorPicked = Signal(QColor)
    editPaletteRequested = Signal()

    def __init__(
        self, palette: PaletteStore, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SwatchRow")
        self._palette = palette
        self._current = QColor()
        self._ring = QColor("#1D1E20")
        self._icon = QColor("#3A3B3E")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(2)
        self._grid.setVerticalSpacing(2)
        self._swatches: list[_Swatch] = []
        self._more = self._icon_button("plus", "More colors")
        self._more.clicked.connect(self._open_more)
        self._edit = self._icon_button("pencil", "Edit your palette")
        self._edit.clicked.connect(self.editPaletteRequested)
        palette.changed.connect(self._rebuild)
        self._rebuild()

    def _icon_button(self, glyph: str, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("SwatchTool")
        b.setFixedSize(_Swatch.SIZE, _Swatch.SIZE)
        b.setIconSize(QSize(14, 14))
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.setProperty("glyph", glyph)
        return b

    def _rebuild(self) -> None:
        for s in self._swatches:
            self._grid.removeWidget(s)
            s.hide()
            s.deleteLater()
        self._swatches = []
        cells: list[QWidget] = []
        for name, hex_ in self._palette.swatches():
            sw = _Swatch(QColor(hex_), name, self)
            sw.set_ring_color(self._ring)
            sw.clicked.connect(lambda _c=False, s=sw: self._pick(s.color()))
            self._swatches.append(sw)
            cells.append(sw)
        cells += [self._more, self._edit]
        per_row = 8
        for i, w in enumerate(cells):
            self._grid.addWidget(w, i // per_row, i % per_row)
        self._grid.setColumnStretch(per_row, 1)
        self.set_current(self._current)

    def _pick(self, color: QColor) -> None:
        self.set_current(color)
        self.colorPicked.emit(QColor(color))

    def _open_more(self) -> None:
        popup_color_picker(
            self,
            self._current if self._current.isValid() else None,
            self._pick,
            global_pos=self._more.mapToGlobal(self._more.rect().bottomLeft()),
        )

    # ------------------------------------------------------------------
    def swatches(self) -> list[_Swatch]:
        return list(self._swatches)

    def current(self) -> QColor:
        return QColor(self._current)

    def set_current(self, color: QColor) -> None:
        self._current = QColor(color)
        for sw in self._swatches:
            sw.setChecked(
                self._current.isValid()
                and sw.color().rgb() == self._current.rgb()
            )

    def set_colors(self, icon: QColor, ring: QColor) -> None:
        self._icon, self._ring = QColor(icon), QColor(ring)
        for b in (self._more, self._edit):
            b.setIcon(line_icon(b.property("glyph"), self._icon))
        for sw in self._swatches:
            sw.set_ring_color(self._ring)


# ----------------------------------------------------------------------
# stroke width
# ----------------------------------------------------------------------
class StrokeField(QWidget):
    """Preset widths + exact value. `spin` is the exact field; presets
    emit `presetPicked(width)` (and show it in the field)."""

    presetPicked = Signal(float)

    def __init__(
        self, minimum: int = 0, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StrokeField")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.presets = SegmentedControl(
            [(w, str(w), None, f"{w} px") for w in STROKE_PRESETS], self
        )
        self.presets.valueChanged.connect(self._on_preset)
        row.addWidget(self.presets, 1)
        self.spin = StrokeSpinBox(minimum=minimum)
        self.spin.setObjectName("StrokeExact")
        self.spin.setToolTip(
            "Type any width (0 = no outline). "
            "Up / Down arrows step through the usual widths."
        )
        self.spin.setAccessibleName("Exact stroke width")
        if minimum == 0:
            self.spin.setSpecialValueText("None")
        self.spin.setFixedWidth(76)
        self.spin.valueChanged.connect(self._sync_presets)
        row.addWidget(self.spin)

    def _on_preset(self, width: object) -> None:
        self.spin.blockSignals(True)
        self.spin.setValue(int(width))
        self.spin.blockSignals(False)
        self.presetPicked.emit(float(width))

    def _sync_presets(self, value: int) -> None:
        self.presets.set_value(value if value in STROKE_PRESETS else None)

    def set_width(self, width: float) -> None:
        v = int(round(width))
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self._sync_presets(v)


# ----------------------------------------------------------------------
# palette editor
# ----------------------------------------------------------------------
class _SwatchEditRow(QWidget):
    def __init__(self, name: str, hex_: str, editor: PaletteEditor) -> None:
        super().__init__(editor)
        self._editor = editor
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.color_button = QPushButton(self)
        self.color_button.setObjectName("PaletteColorButton")
        self.color_button.setFixedSize(30, 26)
        self.color_button.setToolTip("Pick a color")
        self.color_button.clicked.connect(self._pick)
        row.addWidget(self.color_button)
        self.name_edit = QLineEdit(name, self)
        self.name_edit.setPlaceholderText("Name")
        self.name_edit.setAccessibleName("Color name")
        row.addWidget(self.name_edit, 1)
        self.hex_edit = QLineEdit(hex_.lstrip("#"), self)
        self.hex_edit.setInputMask(">HHHHHH")
        self.hex_edit.setFixedWidth(80)
        self.hex_edit.setAccessibleName("Hex value")
        self.hex_edit.textChanged.connect(lambda _t: self._paint())
        row.addWidget(QLabel("#", self))
        row.addWidget(self.hex_edit)
        for glyph, tip, step in (
            ("arrow-up", "Move up", -1),
            ("arrow-down", "Move down", 1),
        ):
            b = QToolButton(self)
            b.setToolTip(tip)
            b.setIcon(line_icon(glyph, editor.icon_color()))
            b.clicked.connect(lambda _c=False, s=step: editor.move_row(self, s))
            row.addWidget(b)
        rm = QToolButton(self)
        rm.setToolTip("Remove this color")
        rm.setIcon(line_icon("trash", editor.icon_color()))
        rm.clicked.connect(lambda: editor.remove_row(self))
        row.addWidget(rm)
        self._paint()

    def color(self) -> QColor:
        return QColor("#" + self.hex_edit.text())

    def swatch(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.color().name().upper()

    def _pick(self) -> None:
        c = QColorDialog.getColor(self.color(), self, "Pick a color")
        if c.isValid():
            self.hex_edit.setText(c.name().upper().lstrip("#"))

    def _paint(self) -> None:
        c = self.color()
        if c.isValid():
            self.color_button.setStyleSheet(
                f"QPushButton#PaletteColorButton {{ background: {c.name()};"
                " border: 1px solid rgba(0,0,0,0.25); border-radius: 6px; }"
            )


class PaletteEditor(QDialog):
    """Edit the palette; `accept()` writes it to the store."""

    def __init__(
        self,
        palette: PaletteStore,
        parent: QWidget | None = None,
        icon_color: QColor | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PaletteEditor")
        self.setWindowTitle("Your colors")
        self._palette = palette
        self._icon_color = QColor(icon_color or QColor("#3A3B3E"))
        self._rows: list[_SwatchEditRow] = []

        col = QVBoxLayout(self)
        intro = QLabel(
            "Rename, retype or pick a color. The order here is the order "
            "of the swatches. Kept for your next sessions.",
            self,
        )
        intro.setWordWrap(True)
        intro.setObjectName("PaletteIntro")
        col.addWidget(intro)
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(4)
        col.addLayout(self._rows_box)
        self.add_button = QPushButton("Add a color", self)
        self.add_button.setIcon(line_icon("plus", self._icon_color))
        self.add_button.clicked.connect(lambda: self.add_row("", "#808080"))
        col.addWidget(self.add_button, 0, Qt.AlignLeft)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        self.reset_button = buttons.addButton(
            "Reset to defaults", QDialogButtonBox.ResetRole
        )
        self.reset_button.clicked.connect(self._reset_rows)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)

        for name, hex_ in palette.swatches():
            self.add_row(name, hex_)
        self.setMinimumWidth(420)

    # ------------------------------------------------------------------
    def icon_color(self) -> QColor:
        return QColor(self._icon_color)

    def rows(self) -> list[_SwatchEditRow]:
        return list(self._rows)

    def add_row(self, name: str, hex_: str) -> _SwatchEditRow | None:
        if len(self._rows) >= MAX_SWATCHES:
            return None
        row = _SwatchEditRow(name, hex_, self)
        self._rows.append(row)
        self._rows_box.addWidget(row)
        self._sync_add()
        return row

    def remove_row(self, row: _SwatchEditRow) -> None:
        if len(self._rows) <= 1 or row not in self._rows:
            return  # keep at least one color
        self._rows.remove(row)
        self._rows_box.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._sync_add()

    def move_row(self, row: _SwatchEditRow, step: int) -> None:
        i = self._rows.index(row)
        j = i + step
        if not 0 <= j < len(self._rows):
            return
        self._rows[i], self._rows[j] = self._rows[j], self._rows[i]
        self._rows_box.removeWidget(row)
        self._rows_box.insertWidget(j, row)

    def _reset_rows(self) -> None:
        from annoter.services.palette import default_swatches

        for row in list(self._rows):
            self._rows_box.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._rows = []
        for name, hex_ in default_swatches():
            self.add_row(name, hex_)

    def _sync_add(self) -> None:
        self.add_button.setEnabled(len(self._rows) < MAX_SWATCHES)
        self.add_button.setText(
            "Add a color"
            if len(self._rows) < MAX_SWATCHES
            else f"Add a color (limit of {MAX_SWATCHES} reached)"
        )

    def result_swatches(self) -> list[tuple[str, str]]:
        return [r.swatch() for r in self._rows if r.color().isValid()]

    def accept(self) -> None:
        self._palette.set_swatches(self.result_swatches())
        super().accept()
