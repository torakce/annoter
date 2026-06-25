"""GdtAnnotationItem: composite feature control frame.

Layout (computed via QFontMetricsF):
    [ symbol cell | tolerance cell | datum 1 | datum 2 | datum 3 ]
where every visible cell shares the same height. The symbol cell is
square; text cells size themselves to fit their content with padding.

On save (M4) this item is rasterized into a Stamp annotation; a JSON
blob stored in `/Contents` allows the next open to rebuild the editable
item -- see `model.gdt.GdtState.to_dict`.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsSceneMouseEvent

from annoter.model.gdt import Characteristic, GdtState
from annoter.model.styles import HandleRole
from annoter.views.items.base import AnnotationItem
from annoter.views.items.gdt_symbols import symbol_path


_GDT_MIN_FONT_POINTS = 6
_GDT_MAX_FONT_POINTS = 96


_DEFAULT_FONT_FAMILY = "Helvetica"
_DEFAULT_FONT_POINT_SIZE = 12
_CELL_PADDING_X = 6.0  # horizontal padding inside text cells
_CELL_PADDING_Y = 4.0  # vertical padding above/below the text


class GdtAnnotationItem(AnnotationItem):
    """Feature control frame rendered as a row of bordered cells."""

    KIND = "gdt"

    def __init__(
        self,
        state: GdtState | None = None,
        pos: QPointF | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._state: GdtState = state if state is not None else GdtState()
        # MainWindow sets this on every GD&T item it creates so a
        # double-click opens the in-place editor. Left None for items
        # shown in standalone previews.
        self._edit_callback = None  # type: ignore[var-annotated]
        self._color = QColor("#212121")  # GD&T frames default to black
        self._font_size: int = _DEFAULT_FONT_POINT_SIZE
        self._font: QFont = QFont(
            _DEFAULT_FONT_FAMILY, self._font_size
        )
        # Draw lists rebuilt by `_compute_layout`.
        self._border_rects: list[QRectF] = []
        self._symbol_draws: list[tuple[QRectF, Characteristic]] = []
        self._text_draws: list[tuple[QRectF, str, object]] = []
        self._total_size: QRectF = QRectF()
        self._compute_layout()
        if pos is not None:
            self.setPos(pos)

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def state(self) -> GdtState:
        return self._state

    def set_state(self, state: GdtState) -> None:
        self.prepareGeometryChange()
        self._state = state
        self._compute_layout()
        self.update()

    def apply_gdt_state(self, state: GdtState) -> None:
        """Hook used by `ChangeGdtCommand`."""
        self.set_state(state)

    def font_size(self) -> int:
        return self._font_size

    def set_font_size(self, size: int) -> None:
        s = max(6, int(size))
        if s == self._font_size:
            return
        self._font_size = s
        self._font = QFont(_DEFAULT_FONT_FAMILY, s)
        self.prepareGeometryChange()
        self._compute_layout()
        self.update()

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    def _compute_layout(self) -> None:
        fm = QFontMetricsF(self._font)
        pad_x, pad_y = _CELL_PADDING_X, _CELL_PADDING_Y
        h = fm.height() + 2 * pad_y  # row height
        text_h = fm.height()
        gap = pad_y  # vertical gap between the frame and upper/lower text

        def cell_w(text: str) -> float:
            return max(h, fm.horizontalAdvance(text) + 2 * pad_x)

        rows = self._state.all_rows()
        n_rows = len(rows)

        # Per-row cells: tolerance + datums. (width, text)
        row_cells: list[list[tuple[float, str]]] = []
        for row in rows:
            cells: list[tuple[float, str]] = [
                (cell_w(row.tolerance_display()), row.tolerance_display())
            ]
            for d_text in row.datum_displays():
                cells.append((cell_w(d_text), d_text))
            row_cells.append(cells)

        content_widths = [sum(w for w, _ in cells) for cells in row_cells]
        max_content = max(content_widths) if content_widths else h
        # Extend each row's last cell so the frame stays rectangular.
        for cells, cw in zip(row_cells, content_widths):
            if cells and cw < max_content:
                w, text = cells[-1]
                cells[-1] = (w + (max_content - cw), text)

        symbol_w = h
        frame_w = symbol_w + max_content
        frame_h = n_rows * h

        upper = self._state.upper_text.strip()
        lower = self._state.lower_text.strip()
        frame_top = (text_h + gap) if upper else 0.0

        borders: list[QRectF] = []
        symbols: list[tuple[QRectF, Characteristic]] = []
        texts: list[tuple[QRectF, str, object]] = []

        # Symbol cells: one per run of consecutive rows that share the
        # same characteristic (so different characteristics get their own
        # cell, identical ones are merged into a single spanning cell).
        i = 0
        while i < n_rows:
            characteristic = rows[i].characteristic
            j = i
            while j + 1 < n_rows and rows[j + 1].characteristic == characteristic:
                j += 1
            span = j - i + 1
            sym_rect = QRectF(0.0, frame_top + i * h, symbol_w, span * h)
            borders.append(sym_rect)
            symbols.append((sym_rect, characteristic))
            i = j + 1

        for i, cells in enumerate(row_cells):
            x = symbol_w
            y = frame_top + i * h
            for w, text in cells:
                r = QRectF(x, y, w, h)
                borders.append(r)
                if text:
                    texts.append((r, text, Qt.AlignCenter))
                x += w

        total_w = frame_w

        # Optional auxiliary frame appended to the right, vertically
        # centered against the main frame.
        aux_active = (
            self._state.aux_symbol is not None
            or bool(self._state.aux_text.strip())
        )
        if aux_active:
            ax = frame_w + h * 0.4
            ay = frame_top + (frame_h - h) / 2.0
            if self._state.aux_symbol is not None:
                ar = QRectF(ax, ay, h, h)
                borders.append(ar)
                symbols.append((ar, self._state.aux_symbol))
                ax += h
            aux_txt = self._state.aux_text.strip()
            if aux_txt or self._state.aux_symbol is None:
                aw = cell_w(aux_txt)
                ar = QRectF(ax, ay, aw, h)
                borders.append(ar)
                if aux_txt:
                    texts.append((ar, aux_txt, Qt.AlignCenter))
                ax += aw
            total_w = max(total_w, ax)

        # Upper / lower text, left-aligned with the frame's left edge.
        if upper:
            w = max(total_w, fm.horizontalAdvance(upper))
            texts.append(
                (QRectF(0.0, 0.0, w, text_h), upper,
                 Qt.AlignLeft | Qt.AlignVCenter)
            )
            total_w = max(total_w, w)
        bottom = frame_top + frame_h
        if lower:
            ly = bottom + gap
            w = max(total_w, fm.horizontalAdvance(lower))
            texts.append(
                (QRectF(0.0, ly, w, text_h), lower,
                 Qt.AlignLeft | Qt.AlignVCenter)
            )
            total_w = max(total_w, w)
            bottom = ly + text_h

        self._border_rects = borders
        self._symbol_draws = symbols
        self._text_draws = texts
        self._total_size = QRectF(0.0, 0.0, total_w, bottom)

    def boundingRect(self) -> QRectF:
        m = self._stroke / 2.0 + 1.0 + self.handles_extent()
        return self._total_size.adjusted(-m, -m, m, m)

    def content_rect(self) -> QRectF:
        return QRectF(self._total_size)

    # ------------------------------------------------------------------
    # resize handles (corners only -- aspect ratio is fixed)
    # ------------------------------------------------------------------
    def handle_positions(self) -> dict[HandleRole, QPointF]:
        r = self._total_size
        return {
            HandleRole.TOP_LEFT: QPointF(r.left(), r.top()),
            HandleRole.TOP_RIGHT: QPointF(r.right(), r.top()),
            HandleRole.BOTTOM_LEFT: QPointF(r.left(), r.bottom()),
            HandleRole.BOTTOM_RIGHT: QPointF(r.right(), r.bottom()),
        }

    def apply_resize(
        self, role: HandleRole, local_pos: QPointF
    ) -> None:
        r = self._total_size
        cur_w = max(r.width(), 1.0)
        cur_h = max(r.height(), 1.0)
        # Width that the cursor implies for the anchored corner.
        if role in (HandleRole.TOP_LEFT, HandleRole.BOTTOM_LEFT):
            new_w = cur_w - local_pos.x()
        else:
            new_w = local_pos.x()
        if role in (HandleRole.TOP_LEFT, HandleRole.TOP_RIGHT):
            new_h = cur_h - local_pos.y()
        else:
            new_h = local_pos.y()

        if new_w < 8.0 and new_h < 8.0:
            return  # cursor crossed the anchor; ignore this step

        # Width and height are coupled (font drives layout), but the user
        # may drag along a single axis. Pick the ratio whose change is
        # largest so vertical-only or horizontal-only drags still rescale
        # the frame instead of being ignored by a max() over an unchanged
        # axis.
        ratio_w = new_w / cur_w
        ratio_h = new_h / cur_h
        scale = (
            ratio_h
            if abs(ratio_h - 1.0) > abs(ratio_w - 1.0)
            else ratio_w
        )
        scale = max(scale, 0.05)
        new_font = max(
            _GDT_MIN_FONT_POINTS,
            min(
                _GDT_MAX_FONT_POINTS,
                int(round(self._font_size * scale)),
            ),
        )
        if new_font == self._font_size:
            return
        # Anchor the opposite corner: capture its scene position first,
        # apply the font change (which updates _total_size), then move
        # self.pos so the anchor lands at the same scene coordinate.
        anchor_local = self._anchor_local(role, r)
        anchor_scene = self.mapToScene(anchor_local)
        self.set_font_size(new_font)
        new_r = self._total_size
        new_anchor_local = self._anchor_local(role, new_r)
        delta_scene = anchor_scene - self.mapToScene(new_anchor_local)
        self.setPos(self.pos() + delta_scene)

    @staticmethod
    def _anchor_local(role: HandleRole, r: QRectF) -> QPointF:
        # The fixed corner during a TL/TR/BL/BR drag is the diagonal
        # opposite.
        if role is HandleRole.TOP_LEFT:
            return QPointF(r.right(), r.bottom())
        if role is HandleRole.TOP_RIGHT:
            return QPointF(r.left(), r.bottom())
        if role is HandleRole.BOTTOM_LEFT:
            return QPointF(r.right(), r.top())
        return QPointF(r.left(), r.top())

    def geom_snapshot(self) -> object:
        return (QPointF(self.pos()), int(self._font_size))

    def apply_geom(self, snapshot: object) -> None:
        if (
            not isinstance(snapshot, tuple)
            or len(snapshot) != 2
            or not isinstance(snapshot[0], QPointF)
        ):
            return
        pos, font_size = snapshot
        self.setPos(pos)
        if int(font_size) != self._font_size:
            self.set_font_size(int(font_size))

    # ------------------------------------------------------------------
    # paint
    # ------------------------------------------------------------------
    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        pen = QPen(self._color, self._stroke)
        pen.setJoinStyle(Qt.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        for rect in self._border_rects:
            painter.drawRect(rect)
        for rect, characteristic in self._symbol_draws:
            self._paint_symbol(painter, rect, characteristic)

        painter.setPen(QPen(self._color))
        painter.setFont(self._font)
        for rect, text, align in self._text_draws:
            painter.drawText(rect, align, text)

        self._draw_selection_marker(painter, self.boundingRect())

    def _paint_symbol(
        self, painter, rect: QRectF, characteristic: Characteristic
    ) -> None:
        """Stroke the unit-box symbol path into a square centered in `rect`.

        Centering in a square (rather than filling the cell) keeps the
        glyph undistorted even when the symbol cell spans several rows of
        a composite frame."""
        path: QPainterPath = symbol_path(characteristic)
        side = min(rect.width(), rect.height())
        inset = side * 0.15
        cx, cy = rect.center().x(), rect.center().y()
        target = QRectF(
            cx - side / 2 + inset,
            cy - side / 2 + inset,
            side - 2 * inset,
            side - 2 * inset,
        )
        painter.save()
        # Map unit-box [0, 1] into the target square.
        painter.translate(target.left(), target.top())
        painter.scale(target.width(), target.height())
        # Use a cosmetic pen so the stroke width stays in device pixels.
        sym_pen = QPen(self._color)
        sym_pen.setCosmetic(True)
        sym_pen.setWidthF(self._stroke)
        sym_pen.setCapStyle(Qt.RoundCap)
        sym_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(sym_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.restore()

    # ------------------------------------------------------------------
    # interaction
    # ------------------------------------------------------------------
    def set_edit_callback(self, callback) -> None:
        self._edit_callback = callback

    def mouseDoubleClickEvent(
        self, event: QGraphicsSceneMouseEvent
    ) -> None:
        if self._edit_callback is not None:
            self._edit_callback(self)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def clone(self) -> "GdtAnnotationItem":
        from copy import deepcopy

        c = GdtAnnotationItem(deepcopy(self._state), QPointF(self.pos()))
        c.set_color(self.color())
        c.set_stroke(self.stroke())
        c.set_dash_style(self.dash_style())
        c.set_font_size(self._font_size)
        c.set_edit_callback(self._edit_callback)
        return c

    # ------------------------------------------------------------------
    # display
    # ------------------------------------------------------------------
    def label(self) -> str:
        from annoter.model.gdt import CHARACTERISTIC_META

        _, name = CHARACTERISTIC_META[self._state.characteristic]
        return f"GD&T {name}"
