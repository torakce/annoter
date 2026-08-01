"""DimensionAnnotationItem: nominal value with an optional tolerance.

Layout (computed via QFontMetricsF), left to right:
    [ prefix? ] [ nominal value ] [ tolerance block? ]
Unlike the GD&T feature control frame, a dimension is not boxed -- real
drawings show it as plain text next to a dimension line. The tolerance
block is either a single "+/-value" cell at the nominal's font size
(SYMMETRIC), or two independently-sized lines stacked above/below the
nominal's vertical center at a reduced font size (BILATERAL) -- each
line keeps its own natural width rather than being stretched to match
the other, consistent with the GD&T composite-row convention.

On save this item is rasterized into an appearance stream; a JSON blob
stored in `/Contents` allows the next open to rebuild the editable item
-- see `model.dimension.DimensionState.to_dict`.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsSceneMouseEvent

from annoter.model.dimension import DimensionState, ToleranceMode
from annoter.model.styles import HandleRole
from annoter.views.items.base import AnnotationItem


_DIM_MIN_FONT_POINTS = 6
_DIM_MAX_FONT_POINTS = 96

_DEFAULT_FONT_FAMILY = "Helvetica"
_DEFAULT_FONT_POINT_SIZE = 12
_CELL_PADDING_X = 3.0
_TOL_FONT_RATIO = 0.6  # bilateral tolerance lines vs. the nominal font
_TOL_LINE_GAP = 1.0  # vertical gap between the stacked +/- lines


class DimensionAnnotationItem(AnnotationItem):
    """Nominal value + optional prefix/tolerance, drawn as plain text."""

    KIND = "dimension"

    def __init__(
        self,
        state: DimensionState | None = None,
        pos: QPointF | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._state: DimensionState = state if state is not None else DimensionState()
        # MainWindow sets this on every Dimension item it creates so a
        # double-click opens the in-place editor.
        self._edit_callback = None  # type: ignore[var-annotated]
        self._color = QColor("#212121")
        self._font_size: int = _DEFAULT_FONT_POINT_SIZE
        self._font: QFont = QFont(_DEFAULT_FONT_FAMILY, self._font_size)
        # Draw list rebuilt by `_compute_layout`: (rect, text, font, align).
        self._text_draws: list[tuple[QRectF, str, QFont, object]] = []
        self._total_size: QRectF = QRectF()
        self._compute_layout()
        if pos is not None:
            self.setPos(pos)

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def state(self) -> DimensionState:
        return self._state

    def set_state(self, state: DimensionState) -> None:
        self.prepareGeometryChange()
        self._state = state
        self._compute_layout()
        self.update()

    def apply_dimension_state(self, state: DimensionState) -> None:
        """Hook used by `ChangeDimensionCommand`."""
        self.set_state(state)

    def scale_geometry(self, s: float) -> None:
        super().scale_geometry(s)
        self.set_font_size(max(4, round(self._font_size * s)))

    def font_size(self) -> int:
        return self._font_size

    def set_font_size(self, size: int) -> None:
        s = max(_DIM_MIN_FONT_POINTS, int(size))
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
        h = fm.height()
        state = self._state

        texts: list[tuple[QRectF, str, QFont, object]] = []
        x = 0.0

        prefix = state.prefix_display()
        nominal = state.nominal if state.nominal else ""
        main_w = fm.horizontalAdvance(prefix + nominal)
        main_w = max(main_w, 4.0)

        mode = state.tolerance_mode
        tol_w = 0.0
        tol_font = QFont(self._font)
        tol_fm = fm
        bilateral_h = h
        if mode is ToleranceMode.BILATERAL:
            tol_size = max(
                _DIM_MIN_FONT_POINTS, round(self._font_size * _TOL_FONT_RATIO)
            )
            tol_font = QFont(_DEFAULT_FONT_FAMILY, tol_size)
            tol_fm = QFontMetricsF(tol_font)
            lines = state.tolerance_lines()
            tol_w = max((tol_fm.horizontalAdvance(t) for t in lines), default=0.0)
            bilateral_h = 2 * tol_fm.height() + _TOL_LINE_GAP
        elif mode is ToleranceMode.SYMMETRIC:
            lines = state.tolerance_lines()
            tol_w = max((fm.horizontalAdvance(t) for t in lines), default=0.0)

        row_h = max(h, bilateral_h)

        # Prefix + nominal, vertically centered against the row.
        main_y = (row_h - h) / 2.0
        if prefix or nominal:
            texts.append(
                (
                    QRectF(x, main_y, main_w, h),
                    prefix + nominal,
                    self._font,
                    Qt.AlignLeft | Qt.AlignVCenter,
                )
            )
        x += main_w

        gap = _CELL_PADDING_X if tol_w > 0 else 0.0
        x += gap

        if mode is ToleranceMode.SYMMETRIC and tol_w > 0:
            line = state.tolerance_lines()[0]
            texts.append(
                (
                    QRectF(x, main_y, tol_w, h),
                    line,
                    self._font,
                    Qt.AlignLeft | Qt.AlignVCenter,
                )
            )
            x += tol_w
        elif mode is ToleranceMode.BILATERAL and tol_w > 0:
            lines = state.tolerance_lines()
            block_y = (row_h - bilateral_h) / 2.0
            line_h = tol_fm.height()
            for i, line in enumerate(lines):
                ly = block_y + i * (line_h + _TOL_LINE_GAP)
                texts.append(
                    (
                        QRectF(x, ly, tol_w, line_h),
                        line,
                        tol_font,
                        Qt.AlignLeft | Qt.AlignVCenter,
                    )
                )
            x += tol_w

        self._text_draws = texts
        self._total_size = QRectF(0.0, 0.0, max(x, 4.0), row_h)

    def boundingRect(self) -> QRectF:
        m = self._stroke / 2.0 + 1.0 + self.handles_extent()
        return self._total_size.adjusted(-m, -m, m, m)

    def content_rect(self) -> QRectF:
        return QRectF(self._total_size)

    # ------------------------------------------------------------------
    # resize handles (corners only -- aspect ratio is fixed, like GD&T)
    # ------------------------------------------------------------------
    def handle_positions(self) -> dict[HandleRole, QPointF]:
        r = self._total_size
        return {
            HandleRole.TOP_LEFT: QPointF(r.left(), r.top()),
            HandleRole.TOP_RIGHT: QPointF(r.right(), r.top()),
            HandleRole.BOTTOM_LEFT: QPointF(r.left(), r.bottom()),
            HandleRole.BOTTOM_RIGHT: QPointF(r.right(), r.bottom()),
        }

    def apply_resize(self, role: HandleRole, local_pos: QPointF) -> None:
        r = self._total_size
        cur_w = max(r.width(), 1.0)
        cur_h = max(r.height(), 1.0)
        if role in (HandleRole.TOP_LEFT, HandleRole.BOTTOM_LEFT):
            new_w = cur_w - local_pos.x()
        else:
            new_w = local_pos.x()
        if role in (HandleRole.TOP_LEFT, HandleRole.TOP_RIGHT):
            new_h = cur_h - local_pos.y()
        else:
            new_h = local_pos.y()

        if new_w < 8.0 and new_h < 8.0:
            return

        ratio_w = new_w / cur_w
        ratio_h = new_h / cur_h
        scale = (
            ratio_h if abs(ratio_h - 1.0) > abs(ratio_w - 1.0) else ratio_w
        )
        scale = max(scale, 0.05)
        new_font = max(
            _DIM_MIN_FONT_POINTS,
            min(_DIM_MAX_FONT_POINTS, int(round(self._font_size * scale))),
        )
        if new_font == self._font_size:
            return
        anchor_local = self._anchor_local(role, r)
        anchor_scene = self.mapToScene(anchor_local)
        self.set_font_size(new_font)
        new_r = self._total_size
        new_anchor_local = self._anchor_local(role, new_r)
        delta_scene = anchor_scene - self.mapToScene(new_anchor_local)
        self.setPos(self.pos() + delta_scene)

    @staticmethod
    def _anchor_local(role: HandleRole, r: QRectF) -> QPointF:
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
        painter.setPen(QPen(self._color))
        for rect, text, font, align in self._text_draws:
            painter.setFont(font)
            painter.drawText(rect, align, text)
        self._draw_selection_marker(painter, self.boundingRect())

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

    def clone(self) -> "DimensionAnnotationItem":
        from copy import deepcopy

        c = DimensionAnnotationItem(deepcopy(self._state), QPointF(self.pos()))
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
        text = self._state.display().strip()
        return f"Dimension {text}" if text else "Dimension"
