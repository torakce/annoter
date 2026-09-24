"""Design tokens: the single source of UI colors, radii and fonts.

Every themed surface reads from here -- the QSS template
(`resources/themes/app.qss`, rendered by `services.theme`), the QPalette
built for the application, and the code-drawn icons. A view never
hard-codes a UI color; it asks `tokens_for(theme)` (or uses the QSS /
palette roles derived from it).

Annotation colors (red cloud, blue GD&T frame, ...) are *document* data,
not UI chrome, and are deliberately not part of this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class Theme(Enum):
    LIGHT = "light"
    DARK = "dark"


# UI font stacks. "IBM Plex Sans/Mono" are bundled in resources/fonts/
# (OFL) and registered at startup by `services.fonts`; the fallbacks keep
# the UI readable if registration failed (e.g. a stripped build).
UI_FONT_STACK = '"IBM Plex Sans", "Segoe UI", "Helvetica Neue", sans-serif'
MONO_FONT_STACK = '"IBM Plex Mono", Consolas, "Courier New", monospace'


@dataclass(frozen=True)
class Tokens:
    """One theme's palette. Field names are the `@name` placeholders of
    the QSS template, so adding a field makes it usable there."""

    # surfaces, back to front
    canvas: str  # behind the PDF page
    app: str  # window chrome: top bar, tool rail, status bar
    panel: str  # docks / side panels
    field: str  # inputs, list backgrounds
    pill: str  # floating bars, menus, popovers
    # lines
    line: str  # hairline separators, panel borders
    line_strong: str  # input and button borders
    # text
    text: str
    text_muted: str  # secondary labels, section headers
    text_disabled: str
    # interaction states
    hover: str
    pressed: str
    soft: str  # selected / checked background
    soft_text: str  # text on `soft`
    accent: str  # focus rings, links, selection outlines
    accent_bg: str  # primary button fill (white text on it)
    accent_hover: str  # primary button fill, hovered
    on_accent: str
    warning: str  # "unsaved changes" dot
    danger: str  # destructive actions (Delete)
    # glyphs
    icon: str  # code-drawn icon color (pre-rasterized, one color)
    scrollbar: str
    scrollbar_hover: str
    tooltip_bg: str
    tooltip_text: str
    # the drawing sheet: white in both themes (previews of page content)
    paper: str = "#FFFFFF"
    # shape
    radius_sm: str = "6px"
    radius: str = "8px"
    radius_lg: str = "10px"
    # type
    font_ui: str = UI_FONT_STACK
    font_mono: str = MONO_FONT_STACK

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


LIGHT = Tokens(
    canvas="#E3E1DA",
    app="#F3F2EE",
    panel="#FBFAF7",
    field="#FFFFFF",
    pill="#FFFFFF",
    line="#DCDAD3",
    line_strong="#C9C7BF",
    text="#1D1E20",
    text_muted="#5E5D57",
    text_disabled="#A3A29B",
    hover="#EAE8E2",
    pressed="#E0DED7",
    soft="#E2E9FB",
    soft_text="#2447B0",
    accent="#2F5BD3",
    accent_bg="#2F5BD3",
    accent_hover="#274DB8",
    on_accent="#FFFFFF",
    warning="#E08A1E",
    danger="#B83224",
    icon="#3A3B3E",
    scrollbar="#C4C2BA",
    scrollbar_hover="#A8A69E",
    tooltip_bg="#1D1E20",
    tooltip_text="#F3F2EE",
)

DARK = Tokens(
    canvas="#101113",
    app="#18191C",
    panel="#1F2024",
    field="#16171A",
    pill="#26282D",
    line="#2E3035",
    line_strong="#41444B",
    text="#ECEBE7",
    text_muted="#A6A59F",
    text_disabled="#6B6C70",
    hover="#2A2C31",
    pressed="#33363C",
    soft="#26335C",
    soft_text="#BFD0FF",
    accent="#7C9CF5",
    accent_bg="#3F68D8",
    accent_hover="#4C74E4",
    on_accent="#FFFFFF",
    warning="#F0A548",
    danger="#FF8F80",
    icon="#D2D1CC",
    scrollbar="#44474E",
    scrollbar_hover="#5A5E66",
    tooltip_bg="#ECEBE7",
    tooltip_text="#1D1E20",
)


def tokens_for(theme: Theme) -> Tokens:
    return DARK if theme is Theme.DARK else LIGHT


# ----------------------------------------------------------------------
# contrast helpers (WCAG 2.x) -- used by the tests to keep the palette
# legible when someone tweaks a value.
# ----------------------------------------------------------------------
def _channel(c: int) -> float:
    s = c / 255.0
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)
