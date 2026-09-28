"""GdtFrameBuilder: the feature control frame builder (UI redesign, Lot I).

Replaces the compact `GdtInlineEditor` for both placing a new frame and
editing one (double-click), following the mock-up:

    Feature control frame                                        [x]
    Pick a characteristic, then fill in the tolerance...
    +--------------------------------------------------------------+
    |            [ (+) | Ø 0.1 (M) | A | B | C ]                   |
    |  Position within a Ø0.1 mm cylindrical zone at MMC, ...     |
    +--------------------------------------------------------------+
    [Row 1] [Row 2]                                     Remove row
    1 - CHARACTERISTIC   Position
      Form  [-][/][o][/o/]     Profile [^][^]
      Orientation [//][_|_][<] Location [+][(o)][=]
      Runout [/][//]
    2 - TOLERANCE
      Zone      [Width|Ø|SØ|R|SR]  [0.1     ] mm
      Modifier  [None|Ⓜ MMC|Ⓛ LMC|Ⓟ|Ⓔ]
    3 - DATUMS
      Reference [A][-v] [B][-v] [C][-v]      (skipped for form)
    Notes     + Note above   + Note below
    + Add composite row                 [Cancel] [Place frame]

Everything the former editor could express is kept: composite frames
(each row its own characteristic, picked with the row chips), the four
zone prefixes, tolerance and datum modifiers, common datums ("A-B"),
the rich notes above / below (handed over to the in-place text editor,
as before) and the auxiliary frame (passed through untouched).

It stays a viewport child next to the frame, so the scene item remains
the full-size live preview; the card at the top is a larger copy with a
plain-language read-back (`model.gdt_readback`) and ISO 1101 warnings.
Form tolerances skip the datum step; datums typed for another
characteristic are kept in the row and come back if the user switches
back, but a form row never carries them into the frame.

Lifecycle contract (unchanged, shared with the note editor):
`committed` on Enter or the primary button, `cancelled` on Escape, the
close button or Cancel; clicking elsewhere does not close it --
MainWindow commits it on save, page switch or another frame.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGraphicsScene,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
    runs_to_plain,
)
from annoter.model.gdt_readback import (
    describe_row,
    name_of,
    row_warning,
    takes_datums,
)
from annoter.services.tokens import LIGHT, Tokens
from annoter.views.icons import gdt_symbol_icon
from annoter.views.inspector_widgets import SegmentedControl
from annoter.views.line_icons import line_icon

BUILDER_WIDTH = 560
SYMBOL_BUTTON = 36
PREVIEW_SCALE = 1.6
PREVIEW_MAX_W = BUILDER_WIDTH - 90
_NONE_LABEL = "—"  # em dash: a slot with no value
_ARROW = "▾"  # drop-down affordance on menu buttons

# Families laid out two per line, in ISO order.
_FAMILY_GRID: tuple[tuple[Family, ...], ...] = (
    (Family.FORM, Family.PROFILE),
    (Family.ORIENTATION, Family.LOCATION),
    (Family.RUNOUT,),
)

_ZONE_OPTIONS = [("", "Width", None, "No prefix: a zone of the given width")]
_ZONE_OPTIONS += [
    (p, p, None, TOLERANCE_PREFIX_NAMES[p])
    for p in ("Ø", "SØ", "R", "SR")
    if p in TOLERANCE_PREFIXES
]

_MODIFIER_LABELS = {"M": "MMC", "L": "LMC"}
_MODIFIER_OPTIONS = [(None, "None", None, "No modifier")] + [
    (
        m,
        f"{enclosed(m)} {_MODIFIER_LABELS[m]}" if m in _MODIFIER_LABELS
        else enclosed(m),
        None,
        MODIFIER_NAMES[m],
    )
    for m in ALLOWED_TOLERANCE_MODIFIERS
]


def _parse_datum_text(text: str) -> list[str]:
    """Split user input like 'a-b' into ['A', 'B']."""
    return [tok.strip().upper() for tok in text.split("-") if tok.strip()]


def _copy_row(row: GdtRow) -> GdtRow:
    return GdtRow.from_dict(row.to_dict())


def frame_rows(state: GdtState) -> list[GdtRow]:
    """The state's rows as the frame will draw them: form rows lose any
    datum a previous characteristic left behind."""
    out: list[GdtRow] = []
    for row in state.all_rows():
        if takes_datums(row.characteristic):
            out.append(row)
        else:
            out.append(
                replace(
                    row,
                    datum_primary=DatumRef(),
                    datum_secondary=DatumRef(),
                    datum_tertiary=DatumRef(),
                )
            )
    return out


def state_from_rows(rows: list[GdtRow], base: GdtState) -> GdtState:
    """`base` (notes, auxiliary frame) with `rows` as its frame rows."""
    row0 = rows[0] if rows else GdtRow()
    return replace(
        base,
        characteristic=row0.characteristic,
        tolerance_prefix=row0.tolerance_prefix,
        tolerance_value=row0.tolerance_value,
        tolerance_modifier=row0.tolerance_modifier,
        datum_primary=row0.datum_primary,
        datum_secondary=row0.datum_secondary,
        datum_tertiary=row0.datum_tertiary,
        additional_rows=list(rows[1:]),
    )


def render_frame_preview(
    state: GdtState,
    color: QColor,
    *,
    scale: float = PREVIEW_SCALE,
    max_width: float = PREVIEW_MAX_W,
    dpr: float = 1.0,
) -> QPixmap:
    """The frame drawn by the real `GdtAnnotationItem`, enlarged, on a
    transparent pixmap (notes left out: they are edited on the page)."""
    from annoter.views.items.gdt import GdtAnnotationItem

    scene = QGraphicsScene()
    item = GdtAnnotationItem(
        replace(state, upper_runs=[], lower_runs=[]), QPointF(0, 0)
    )
    item.set_color(color)
    scene.addItem(item)
    src = item.mapToScene(item.content_rect()).boundingRect()
    src = src.adjusted(-2, -2, 2, 2)
    s = min(scale, max_width / max(src.width(), 1.0))
    w = max(1, round(src.width() * s))
    h = max(1, round(src.height() * s))
    pm = QPixmap(max(1, round(w * dpr)), max(1, round(h * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    scene.render(painter, QRectF(0, 0, w, h), src)
    painter.end()
    scene.removeItem(item)
    return pm


class _FitScrollArea(QScrollArea):
    """Scroll area that asks for its content's full height, so the
    builder only scrolls when the viewport is too short for it."""

    def sizeHint(self) -> QSize:
        w = self.widget()
        if w is None:
            return super().sizeHint()
        hint = w.sizeHint()
        f = 2 * self.frameWidth()
        return QSize(hint.width() + f, hint.height() + f)


class GdtFrameBuilder(QFrame):
    """Floating FCF builder. Parent it to the view's viewport."""

    stateEdited = Signal(object)  # GdtState, on every live change
    committed = Signal()
    cancelled = Signal()
    # The user asked to write the note above / below the frame (payload:
    # "upper" or "lower"). The caller commits this panel and hands over
    # to the in-place text editor, so there is only ever one editing
    # surface at a time.
    noteEditRequested = Signal(str)

    def __init__(
        self,
        initial: GdtState,
        parent: QWidget,
        *,
        tokens: Tokens = LIGHT,
        frame_color: QColor | None = None,
        is_new: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("GdtFrameBuilder")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(BUILDER_WIDTH)
        self._tokens = tokens
        self._frame_color = (
            QColor(frame_color) if frame_color is not None
            else QColor(tokens.text)
        )
        self._is_new = is_new
        self._base = replace(
            initial, additional_rows=list(initial.additional_rows)
        )
        self._rows: list[GdtRow] = [_copy_row(r) for r in initial.all_rows()]
        self._current = 0
        self._finished = False
        self._loading = False
        self._ready = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        self._scroll = _FitScrollArea(self)
        self._scroll.setObjectName("GdtBuilderScroll")
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("GdtBuilderBody")
        self._scroll.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(18, 0, 18, 14)
        col.setSpacing(0)
        col.addWidget(self._build_preview(body))
        col.addSpacing(10)
        col.addWidget(self._build_row_strip(body))
        col.addWidget(self._build_characteristics(body))
        col.addWidget(self._separator(body))
        col.addWidget(self._build_tolerance(body))
        col.addWidget(self._separator(body))
        col.addWidget(self._build_datums(body))
        col.addWidget(self._separator(body))
        col.addWidget(self._build_notes(body))
        outer.addWidget(self._scroll, 1)
        outer.addWidget(self._build_footer())

        self._load_row(0)
        self._refresh_rows_strip()
        self._ready = True
        self._refresh_preview()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_header(self) -> QWidget:
        head = QFrame(self)
        head.setObjectName("GdtBuilderHeader")
        row = QHBoxLayout(head)
        row.setContentsMargins(18, 14, 12, 10)
        row.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel(
            "Feature control frame" if self._is_new
            else "Edit feature control frame",
            head,
        )
        title.setObjectName("GdtBuilderTitle")
        texts.addWidget(title)
        sub = QLabel(
            "Pick a characteristic, then fill in the tolerance. "
            "The frame updates as you go.",
            head,
        )
        sub.setObjectName("GdtBuilderSubtitle")
        sub.setWordWrap(True)
        texts.addWidget(sub)
        row.addLayout(texts, 1)
        self._close_btn = QToolButton(head)
        self._close_btn.setObjectName("GdtBuilderClose")
        self._close_btn.setFocusPolicy(Qt.NoFocus)
        self._close_btn.setToolTip("Discard (Esc)")
        self._close_btn.setIconSize(QSize(16, 16))
        self._close_btn.clicked.connect(self._cancel)
        row.addWidget(self._close_btn, 0, Qt.AlignTop)
        return head

    def _build_preview(self, parent: QWidget) -> QWidget:
        card = QFrame(parent)
        card.setObjectName("GdtPreviewCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(8)
        # The frame sits on a white "paper" chip in both themes (a black
        # frame would vanish on the dark card); the card itself and the
        # read-back follow the theme.
        self._preview = QLabel(card)
        self._preview.setObjectName("GdtPreview")
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        v.addWidget(self._preview, 0, Qt.AlignHCenter)
        self._readback = QLabel(card)
        self._readback.setObjectName("GdtReadBack")
        self._readback.setAlignment(Qt.AlignCenter)
        self._readback.setWordWrap(True)
        v.addWidget(self._readback)
        self._warning = QLabel(card)
        self._warning.setObjectName("GdtWarning")
        self._warning.setAlignment(Qt.AlignCenter)
        self._warning.setWordWrap(True)
        v.addWidget(self._warning)
        return card

    def _build_row_strip(self, parent: QWidget) -> QWidget:
        strip = QWidget(parent)
        strip.setObjectName("GdtRowStrip")
        self._row_strip = strip
        self._row_strip_layout = QHBoxLayout(strip)
        self._row_strip_layout.setContentsMargins(0, 0, 0, 10)
        self._row_strip_layout.setSpacing(6)
        self._row_group = QButtonGroup(strip)
        self._row_group.setExclusive(True)
        self._row_chips: list[QToolButton] = []
        self._remove_row_btn = QPushButton("Remove row", strip)
        self._remove_row_btn.setObjectName("LinkButton")
        self._remove_row_btn.setFocusPolicy(Qt.NoFocus)
        self._remove_row_btn.setCursor(Qt.PointingHandCursor)
        self._remove_row_btn.setToolTip("Remove the selected row")
        self._remove_row_btn.clicked.connect(self._remove_current_row)
        return strip

    def _step_header(
        self, parent: QWidget, text: str
    ) -> tuple[QWidget, QLabel]:
        box = QWidget(parent)
        box.setObjectName("GdtStep")
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 10, 0, 6)
        h.setSpacing(10)
        title = QLabel(text, box)
        title.setObjectName("GdtStepTitle")
        h.addWidget(title, 0, Qt.AlignBaseline)
        value = QLabel("", box)
        value.setObjectName("GdtStepValue")
        h.addWidget(value, 0, Qt.AlignBaseline)
        h.addStretch(1)
        return box, value

    def _build_characteristics(self, parent: QWidget) -> QWidget:
        box = QWidget(parent)
        box.setObjectName("GdtStep")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 10)
        v.setSpacing(0)
        head, self._char_name = self._step_header(box, "1 · CHARACTERISTIC")
        v.addWidget(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        self._symbol_group = QButtonGroup(box)
        self._symbol_group.setExclusive(True)
        self._symbol_buttons: dict[Characteristic, QToolButton] = {}
        families = by_family()
        for r, fams in enumerate(_FAMILY_GRID):
            for c, fam in enumerate(fams):
                cell = QVBoxLayout()
                cell.setSpacing(4)
                label = QLabel(fam.value, box)
                label.setObjectName("GdtFamilyLabel")
                cell.addWidget(label)
                buttons = QHBoxLayout()
                buttons.setSpacing(6)
                for ch in families[fam]:
                    btn = QToolButton(box)
                    btn.setObjectName("GdtSymbolButton")
                    btn.setCheckable(True)
                    btn.setFocusPolicy(Qt.NoFocus)
                    btn.setFixedSize(SYMBOL_BUTTON, SYMBOL_BUTTON)
                    btn.setIconSize(QSize(22, 22))
                    name = CHARACTERISTIC_META[ch][1]
                    btn.setToolTip(name)
                    btn.setAccessibleName(name)
                    btn.clicked.connect(
                        lambda _c=False, x=ch: self._set_characteristic(x)
                    )
                    self._symbol_group.addButton(btn)
                    self._symbol_buttons[ch] = btn
                    buttons.addWidget(btn)
                buttons.addStretch(1)
                cell.addLayout(buttons)
                grid.addLayout(cell, r, c)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        self._apply_symbol_icons()
        return box

    def _field_label(self, parent: QWidget, text: str) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("GdtFieldLabel")
        label.setFixedWidth(84)
        return label

    def _build_tolerance(self, parent: QWidget) -> QWidget:
        box = QWidget(parent)
        box.setObjectName("GdtStep")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 10)
        v.setSpacing(8)
        head, _ = self._step_header(box, "2 · TOLERANCE")
        v.addWidget(head)

        zone = QHBoxLayout()
        zone.setSpacing(10)
        zone.addWidget(self._field_label(box, "Zone"))
        self._zone = SegmentedControl(_ZONE_OPTIONS, box)
        self._zone.valueChanged.connect(self._on_zone)
        zone.addWidget(self._zone)
        self._value_edit = QLineEdit(box)
        self._value_edit.setObjectName("GdtValueField")
        self._value_edit.setPlaceholderText("0.05")
        self._value_edit.setFixedWidth(96)
        self._value_edit.setToolTip("Tolerance value")
        self._value_edit.setAccessibleName("Tolerance value")
        self._value_edit.textChanged.connect(self._on_value)
        self._value_edit.returnPressed.connect(self._commit)
        zone.addWidget(self._value_edit)
        unit = QLabel("mm", box)
        unit.setObjectName("GdtUnit")
        zone.addWidget(unit)
        zone.addStretch(1)
        v.addLayout(zone)

        mod = QHBoxLayout()
        mod.setSpacing(10)
        mod.addWidget(self._field_label(box, "Modifier"))
        self._modifier = SegmentedControl(_MODIFIER_OPTIONS, box)
        self._modifier.valueChanged.connect(self._on_modifier)
        mod.addWidget(self._modifier)
        mod.addStretch(1)
        v.addLayout(mod)
        return box

    def _build_datums(self, parent: QWidget) -> QWidget:
        box = QWidget(parent)
        box.setObjectName("GdtStep")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 10)
        v.setSpacing(6)
        head, _ = self._step_header(box, "3 · DATUMS")
        v.addWidget(head)

        self._datum_box = QWidget(box)
        self._datum_box.setObjectName("GdtStep")
        dv = QVBoxLayout(self._datum_box)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self._field_label(self._datum_box, "Reference"))
        self._datum_edits: list[QLineEdit] = []
        self._datum_mod_btns: list[QToolButton] = []
        tips = ("Primary datum", "Secondary datum", "Tertiary datum")
        for i, (placeholder, tip) in enumerate(zip("ABC", tips)):
            if i:
                row.addSpacing(8)
            edit = QLineEdit(self._datum_box)
            edit.setObjectName("GdtDatumField")
            edit.setPlaceholderText(placeholder)
            edit.setFixedWidth(52)
            edit.setAlignment(Qt.AlignCenter)
            edit.setToolTip(f"{tip}: letter(s); join with '-' (A-B)")
            edit.setAccessibleName(tip)
            edit.textChanged.connect(lambda _t, idx=i: self._on_datum(idx))
            edit.returnPressed.connect(self._commit)
            row.addWidget(edit)
            btn = self._datum_modifier_button(i)
            row.addWidget(btn)
            self._datum_edits.append(edit)
            self._datum_mod_btns.append(btn)
        row.addStretch(1)
        dv.addLayout(row)
        help_text = QLabel(
            "Primary, secondary, tertiary. Leave empty to drop a cell.",
            self._datum_box,
        )
        help_text.setObjectName("GdtHelp")
        help_text.setWordWrap(True)
        dv.addWidget(help_text)
        v.addWidget(self._datum_box)

        self._form_info = QLabel(
            "Form tolerances never reference a datum, so this step is "
            "skipped.",
            box,
        )
        self._form_info.setObjectName("GdtInfoBox")
        self._form_info.setWordWrap(True)
        v.addWidget(self._form_info)
        return box

    def _datum_modifier_button(self, index: int) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("GdtDatumModifier")
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setToolTip("Datum modifier")
        menu = QMenu(btn)
        menu.aboutToHide.connect(self._refocus_after_menu)
        act = menu.addAction(f"{_NONE_LABEL}  No modifier")
        act.triggered.connect(lambda: self._on_datum_modifier(index, None))
        for letter in ALLOWED_DATUM_MODIFIERS:
            act = menu.addAction(
                f"{enclosed(letter)}  {MODIFIER_NAMES[letter]}"
            )
            act.triggered.connect(
                lambda _c=False, lt=letter: self._on_datum_modifier(index, lt)
            )
        btn.setMenu(menu)
        btn.setText(self._mod_label(None))
        return btn

    def _build_notes(self, parent: QWidget) -> QWidget:
        box = QWidget(parent)
        box.setObjectName("GdtStep")
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 10, 0, 0)
        h.setSpacing(10)
        h.addWidget(self._field_label(box, "Notes"))
        self._note_btns: dict[str, QPushButton] = {}
        for role, title in (("upper", "above"), ("lower", "below")):
            btn = QPushButton(box)
            btn.setObjectName("GdtNoteButton")
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(
                f"Write the note {title} the frame, on the page, with "
                "symbols and tolerances"
            )
            runs = (
                self._base.upper_runs if role == "upper"
                else self._base.lower_runs
            )
            preview = runs_to_plain(runs).replace("\n", " ").strip()
            if len(preview) > 18:
                preview = preview[:17] + "…"
            btn.setText(
                f"Note {title}: {preview}" if preview
                else f"+ Note {title}"
            )
            btn.clicked.connect(
                lambda _c=False, r=role: self.noteEditRequested.emit(r)
            )
            self._note_btns[role] = btn
            h.addWidget(btn)
        h.addStretch(1)
        return box

    def _build_footer(self) -> QWidget:
        foot = QFrame(self)
        foot.setObjectName("GdtBuilderFooter")
        h = QHBoxLayout(foot)
        h.setContentsMargins(18, 12, 18, 12)
        h.setSpacing(10)
        self._add_row_btn = QPushButton("+ Add composite row", foot)
        self._add_row_btn.setObjectName("LinkButton")
        self._add_row_btn.setFocusPolicy(Qt.NoFocus)
        self._add_row_btn.setCursor(Qt.PointingHandCursor)
        self._add_row_btn.setToolTip(
            "Add a line to the frame (composite tolerance)"
        )
        self._add_row_btn.clicked.connect(self._add_row)
        h.addWidget(self._add_row_btn)
        h.addStretch(1)
        cancel = QPushButton("Cancel", foot)
        cancel.setObjectName("GdtCancelButton")
        cancel.setFocusPolicy(Qt.NoFocus)
        cancel.setToolTip("Discard (Esc)")
        cancel.clicked.connect(self._cancel)
        h.addWidget(cancel)
        self._primary = QPushButton(
            "Place frame" if self._is_new else "Apply", foot
        )
        self._primary.setObjectName("PrimaryButton")
        self._primary.setFocusPolicy(Qt.NoFocus)
        self._primary.setToolTip(
            ("Place the frame" if self._is_new else "Apply the changes")
            + " (Enter)"
        )
        self._primary.clicked.connect(self._commit)
        h.addWidget(self._primary)
        return foot

    def _separator(self, parent: QWidget) -> QFrame:
        line = QFrame(parent)
        line.setObjectName("GdtSeparator")
        line.setFixedHeight(1)
        return line

    # ------------------------------------------------------------------
    # colors
    # ------------------------------------------------------------------
    def _apply_symbol_icons(self) -> None:
        t = self._tokens
        for ch, btn in self._symbol_buttons.items():
            icon = QIcon()
            icon.addPixmap(
                gdt_symbol_icon(ch, 48, QColor(t.icon)).pixmap(48, 48),
                QIcon.Normal,
                QIcon.Off,
            )
            icon.addPixmap(
                gdt_symbol_icon(ch, 48, QColor(t.soft_text)).pixmap(48, 48),
                QIcon.Normal,
                QIcon.On,
            )
            btn.setIcon(icon)
        self._close_btn.setIcon(line_icon("close-doc", QColor(t.text_muted)))

    def set_colors(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self._apply_symbol_icons()

    # ------------------------------------------------------------------
    # rows
    # ------------------------------------------------------------------
    def _row(self) -> GdtRow:
        return self._rows[self._current]

    def _load_row(self, index: int) -> None:
        """Show row `index` in the fields."""
        self._current = max(0, min(index, len(self._rows) - 1))
        row = self._row()
        self._loading = True
        try:
            for ch, btn in self._symbol_buttons.items():
                btn.setChecked(ch is row.characteristic)
            self._char_name.setText(name_of(row.characteristic))
            self._zone.set_value(row.tolerance_prefix)
            self._value_edit.setText(row.tolerance_value)
            self._modifier.set_value(row.tolerance_modifier)
            refs = (row.datum_primary, row.datum_secondary, row.datum_tertiary)
            for edit, btn, ref in zip(
                self._datum_edits, self._datum_mod_btns, refs
            ):
                edit.setText("-".join(ref.letters))
                btn.setText(self._mod_label(ref.modifier))
            self._sync_datum_step()
        finally:
            self._loading = False

    def _refresh_rows_strip(self) -> None:
        lay = self._row_strip_layout
        for chip in self._row_chips:
            self._row_group.removeButton(chip)
            lay.removeWidget(chip)
            chip.hide()  # deleteLater alone leaves it painted until then
            chip.deleteLater()
        self._row_chips = []
        lay.removeWidget(self._remove_row_btn)
        while lay.count():
            lay.takeAt(0)
        for i in range(len(self._rows)):
            chip = QToolButton(self._row_strip)
            chip.setObjectName("GdtRowChip")
            chip.setCheckable(True)
            chip.setFocusPolicy(Qt.NoFocus)
            chip.setText(f"Row {i + 1}")
            chip.setToolTip(f"Edit row {i + 1} of the frame")
            chip.setChecked(i == self._current)
            chip.clicked.connect(lambda _c=False, idx=i: self._select_row(idx))
            self._row_group.addButton(chip)
            self._row_chips.append(chip)
            lay.addWidget(chip)
        lay.addStretch(1)
        lay.addWidget(self._remove_row_btn)
        self._row_strip.setVisible(len(self._rows) > 1)

    def _select_row(self, index: int) -> None:
        self._load_row(index)
        for i, chip in enumerate(self._row_chips):
            chip.setChecked(i == self._current)
        self._focus_first_field()

    def _add_row(self) -> None:
        # A new composite row starts from the current characteristic
        # (composite frames usually repeat it) and the same datums.
        cur = self._row()
        self._rows.append(
            GdtRow(
                characteristic=cur.characteristic,
                datum_primary=DatumRef(list(cur.datum_primary.letters)),
            )
        )
        self._current = len(self._rows) - 1
        self._refresh_rows_strip()
        self._load_row(self._current)
        self._emit_state()
        self._focus_first_field()

    def _remove_current_row(self) -> None:
        if len(self._rows) <= 1:
            return
        del self._rows[self._current]
        self._current = min(self._current, len(self._rows) - 1)
        self._refresh_rows_strip()
        self._load_row(self._current)
        self._emit_state()

    def row_count(self) -> int:
        return len(self._rows)

    def current_row_index(self) -> int:
        return self._current

    # ------------------------------------------------------------------
    # field handlers
    # ------------------------------------------------------------------
    def _set_characteristic(self, ch: Characteristic) -> None:
        self._row().characteristic = ch
        for c, btn in self._symbol_buttons.items():
            btn.setChecked(c is ch)
        self._char_name.setText(name_of(ch))
        self._sync_datum_step()
        self._emit_state()

    def _on_zone(self, value: object) -> None:
        if self._loading:
            return
        self._row().tolerance_prefix = str(value or "")
        self._emit_state()

    def _on_value(self, text: str) -> None:
        if self._loading:
            return
        self._row().tolerance_value = text
        self._emit_state()

    def _on_modifier(self, value: object) -> None:
        if self._loading:
            return
        self._row().tolerance_modifier = value if value else None
        self._emit_state()

    def _datum_ref(self, index: int) -> DatumRef:
        row = self._row()
        return (row.datum_primary, row.datum_secondary, row.datum_tertiary)[
            index
        ]

    def _on_datum(self, index: int) -> None:
        if self._loading:
            return
        ref = self._datum_ref(index)
        ref.letters = _parse_datum_text(self._datum_edits[index].text())
        self._emit_state()

    def _on_datum_modifier(self, index: int, value: str | None) -> None:
        self._datum_ref(index).modifier = value
        self._datum_mod_btns[index].setText(self._mod_label(value))
        self._emit_state()

    @staticmethod
    def _mod_label(value: str | None) -> str:
        return f"{enclosed(value) if value else _NONE_LABEL} {_ARROW}"

    def _sync_datum_step(self) -> None:
        form = not takes_datums(self._row().characteristic)
        self._datum_box.setVisible(not form)
        self._form_info.setVisible(form)

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def current_state(self) -> GdtState:
        rows = [_copy_row(r) for r in self._rows] or [GdtRow()]
        state = state_from_rows(rows, self._base)
        return state_from_rows(frame_rows(state), self._base)

    def _emit_state(self, *_args) -> None:
        if not self._ready or self._loading:
            return
        self._refresh_preview()
        self.stateEdited.emit(self.current_state())

    def _refresh_preview(self) -> None:
        state = self.current_state()
        dpr = max(1.0, self.devicePixelRatioF())
        self._preview.setPixmap(
            render_frame_preview(state, self._frame_color, dpr=dpr)
        )
        rows = state.all_rows()
        lines = []
        for i, row in enumerate(rows):
            text = describe_row(row)
            lines.append(f"Row {i + 1}: {text}" if len(rows) > 1 else text)
        self._readback.setText("\n".join(lines))
        warnings = [w for w in (row_warning(r) for r in rows) if w]
        self._warning.setText("\n".join(warnings))
        self._warning.setVisible(bool(warnings))

    def readback_text(self) -> str:
        return self._readback.text()

    def warning_text(self) -> str:
        return self._warning.text() if not self._warning.isHidden() else ""

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    def set_max_height(self, height: int) -> None:
        """Cap the panel (the body scrolls beyond it)."""
        self.setMaximumHeight(max(160, height))
        self.adjustSize()

    # ------------------------------------------------------------------
    # open / commit / cancel  (explicit only -- no commit on focus loss)
    # ------------------------------------------------------------------
    def open(self) -> None:
        self.adjustSize()
        self.show()
        self.raise_()
        self._focus_first_field()

    def _focus_first_field(self) -> None:
        self._value_edit.setFocus()
        self._value_edit.selectAll()

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

    def _is_inside(self, widget) -> bool:  # noqa: ANN001
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
