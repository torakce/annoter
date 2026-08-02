"""Inline tolerance runs embedded in a text annotation's document.

A tolerance is not a character: a bilateral one is a two-line block
(upper / lower offsets) stacked at a reduced size against the
surrounding text. It is embedded as an **inline image** carrying custom
format properties, which buys the editing behaviour we need for free:
the caret steps over it as one unit, Backspace removes it whole, it
wraps with the surrounding text, and the properties travel with the
fragment so the run stays re-editable and serializable.

Why not `QTextObjectInterface`, the API actually designed for this:
`QAbstractTextDocumentLayout.registerHandler` silently fails under
PySide6 6.11 -- `handlerForObject` returns None right after
registering, with every inheritance order, on both a bare
`QTextDocument` and a live `QGraphicsTextItem` document, so the
handler's `intrinsicSize`/`drawObject` are never called and the object
lays out as a zero-size glyph. The image route is the working
equivalent; the only cost is that the block is raster, so it is
rendered supersampled and re-rendered whenever the font, colour or
zoom changes.
"""

from __future__ import annotations

from itertools import count

from PySide6.QtCore import QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextImageFormat,
)

from annoter.model.tolerance import Tolerance, ToleranceMode


_PROP_MODE = QTextFormat.UserProperty + 1
_PROP_VALUE = QTextFormat.UserProperty + 2
_PROP_UPPER = QTextFormat.UserProperty + 3
_PROP_LOWER = QTextFormat.UserProperty + 4

# The stacked lines of a bilateral tolerance render smaller than the
# nominal text, the way they do on a real drawing.
_STACK_FONT_RATIO = 0.62
_STACK_MIN_POINTS = 4.0
_STACK_LINE_GAP = 1.0
_SIDE_PADDING = 1.5

# Raster supersampling. The block stays crisp up to roughly this zoom
# before it softens; `refresh_tolerances` can re-render higher.
DEFAULT_SUPERSAMPLE = 4.0

_RESOURCE_SCHEME = "annoter-tolerance"
_resource_ids = count()


def _stack_font(base: QFont) -> QFont:
    size = max(_STACK_MIN_POINTS, base.pointSizeF() * _STACK_FONT_RATIO)
    font = QFont(base)
    font.setPointSizeF(size)
    return font


def tolerance_size(tol: Tolerance, base_font: QFont) -> QSizeF:
    """Logical size the run occupies in the text flow."""
    lines = tol.lines()
    if not lines:
        return QSizeF(0.0, 0.0)
    if tol.mode is ToleranceMode.SYMMETRIC:
        fm = QFontMetricsF(base_font)
        return QSizeF(
            fm.horizontalAdvance(lines[0]) + 2 * _SIDE_PADDING, fm.height()
        )
    fm = QFontMetricsF(_stack_font(base_font))
    width = max(fm.horizontalAdvance(t) for t in lines)
    height = len(lines) * fm.height() + (len(lines) - 1) * _STACK_LINE_GAP
    return QSizeF(width + 2 * _SIDE_PADDING, height)


def paint_tolerance(
    painter: QPainter,
    rect: QRectF,
    tol: Tolerance,
    base_font: QFont,
    color: QColor,
) -> None:
    """Draw `tol` inside `rect`. Shared by the inline image renderer and
    any standalone renderer, so both stay pixel-identical."""
    lines = tol.lines()
    if not lines:
        return
    painter.save()
    painter.setPen(color)
    if tol.mode is ToleranceMode.SYMMETRIC:
        painter.setFont(base_font)
        painter.drawText(
            rect.adjusted(_SIDE_PADDING, 0, -_SIDE_PADDING, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            lines[0],
        )
        painter.restore()
        return

    font = _stack_font(base_font)
    painter.setFont(font)
    line_h = QFontMetricsF(font).height()
    # The two offsets are left-aligned against each other, the way a
    # drawing stacks them under one another.
    y = rect.top()
    for text in lines:
        painter.drawText(
            QRectF(rect.left() + _SIDE_PADDING, y, rect.width(), line_h),
            Qt.AlignLeft | Qt.AlignVCenter,
            text,
        )
        y += line_h + _STACK_LINE_GAP
    painter.restore()


def tolerance_image(
    tol: Tolerance,
    base_font: QFont,
    color: QColor,
    supersample: float = DEFAULT_SUPERSAMPLE,
) -> QImage:
    """Render `tol` to a transparent supersampled image."""
    size = tolerance_size(tol, base_font)
    w = max(1, int(round(size.width() * supersample)))
    h = max(1, int(round(size.height() * supersample)))
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.scale(supersample, supersample)
    paint_tolerance(
        painter, QRectF(0, 0, size.width(), size.height()), tol, base_font, color
    )
    painter.end()
    img.setDevicePixelRatio(supersample)
    return img


def tolerance_from_format(fmt: QTextFormat) -> Tolerance | None:
    """Read a tolerance back out of a fragment format, None if absent.

    The image test is not redundant with the property test: a cursor
    keeps the char format of what it just inserted, so text typed
    straight after a run would otherwise inherit its properties and be
    misread as a second tolerance.
    """
    if not fmt.isImageFormat():
        return None
    raw_mode = fmt.property(_PROP_MODE)
    if not raw_mode:
        return None
    try:
        mode = ToleranceMode(raw_mode)
    except ValueError:
        return None
    return Tolerance(
        mode=mode,
        value=str(fmt.property(_PROP_VALUE) or ""),
        upper=str(fmt.property(_PROP_UPPER) or ""),
        lower=str(fmt.property(_PROP_LOWER) or ""),
    )


def _apply_tolerance_props(fmt: QTextImageFormat, tol: Tolerance) -> None:
    fmt.setProperty(_PROP_MODE, tol.mode.value)
    fmt.setProperty(_PROP_VALUE, tol.value)
    fmt.setProperty(_PROP_UPPER, tol.upper)
    fmt.setProperty(_PROP_LOWER, tol.lower)


def make_tolerance_format(
    document: QTextDocument,
    tol: Tolerance,
    base_font: QFont,
    color: QColor,
    supersample: float = DEFAULT_SUPERSAMPLE,
    name: str | None = None,
) -> QTextImageFormat:
    """Register the rendered block on `document` and describe it.

    Pass `name` to overwrite an existing run's image in place (used by
    `refresh_tolerances`); omit it to mint a fresh resource.
    """
    if name is None:
        name = f"{_RESOURCE_SCHEME}:{next(_resource_ids)}"
    image = tolerance_image(tol, base_font, color, supersample)
    document.addResource(
        QTextDocument.ResourceType.ImageResource, QUrl(name), image
    )
    size = tolerance_size(tol, base_font)
    fmt = QTextImageFormat()
    fmt.setName(name)
    fmt.setWidth(size.width())
    fmt.setHeight(size.height())
    _apply_tolerance_props(fmt, tol)
    return fmt


def insert_tolerance(
    cursor: QTextCursor,
    tol: Tolerance,
    base_font: QFont,
    color: QColor,
    supersample: float = DEFAULT_SUPERSAMPLE,
) -> None:
    """Insert `tol` at the cursor, replacing any selection."""
    document = cursor.document()
    fmt = make_tolerance_format(
        document, tol, base_font, color, supersample
    )
    cursor.insertImage(fmt)
    # Reset the typing format: the cursor keeps whatever it last
    # inserted, so text typed right after the run would come out as
    # another image fragment carrying the tolerance properties.
    plain = QTextCharFormat()
    plain.setFont(base_font)
    plain.setForeground(color)
    cursor.setCharFormat(plain)


def iter_fragments(document: QTextDocument):  # noqa: ANN201
    """Yield every fragment of `document` in order.

    Qt exposes fragments only through a per-block iterator; walking it
    is the single supported way to see inline runs (each is its own
    fragment, since its char format differs from the text around it).
    """
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                yield block, fragment
            it += 1
        block = block.next()


def refresh_tolerances(
    document: QTextDocument,
    base_font: QFont,
    color: QColor,
    supersample: float = DEFAULT_SUPERSAMPLE,
) -> bool:
    """Re-render every tolerance run after a font / colour / zoom change.

    Returns True when at least one run was re-rendered, so the caller
    knows whether geometry actually moved.
    """
    touched = False
    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    for _block, fragment in list(iter_fragments(document)):
        fmt = fragment.charFormat()
        tol = tolerance_from_format(fmt)
        if tol is None:
            continue
        name = fmt.toImageFormat().name()
        new_fmt = make_tolerance_format(
            document, tol, base_font, color, supersample, name=name
        )
        cursor.setPosition(fragment.position())
        cursor.setPosition(
            fragment.position() + fragment.length(), QTextCursor.KeepAnchor
        )
        cursor.setCharFormat(new_fmt)
        touched = True
    cursor.endEditBlock()
    if touched:
        # Swapping a resource behind an unchanged URL does not by itself
        # invalidate the laid-out image.
        document.markContentsDirty(0, document.characterCount())
    return touched
