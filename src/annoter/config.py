"""Application-wide constants: rendering DPI, zoom limits, palette, etc.

All UI-facing strings here must be in English.
"""

from __future__ import annotations

# Rendering
BASE_RENDER_DPI = 150
HIGH_RENDER_DPI = 300
# Hysteretic high-DPI re-render: switch to HIGH_RENDER_DPI when the zoom
# factor rises above THRESHOLD, drop back to BASE_RENDER_DPI below EXIT.
# The gap prevents flapping when the user hovers around the threshold.
HIGH_DPI_ZOOM_THRESHOLD = 2.0
HIGH_DPI_ZOOM_EXIT = 1.5

# Hi-res viewport overlay. Beyond what the full-page pixmap can show
# sharply, the visible clip is re-rasterized at exact screen resolution
# after a short idle delay and laid over the page (memory stays bounded
# by the viewport size, not the page size).
HIRES_OVERLAY_MARGIN = 0.25  # extra border around the viewport (fraction)
HIRES_MAX_PIXELS = 24_000_000  # safety cap for one overlay pixmap
HIRES_DEBOUNCE_MS = 150

# Zoom
ZOOM_MIN = 0.1
ZOOM_MAX = 16.0
# Ctrl+wheel and Ctrl+/- move by one notch of 2^(1/3) and land on the
# notch grid (powers of 2^(1/3), 100 % included): the native display
# engine renders and prefetches exactly those levels (PLAN.md, D4).
ZOOM_NOTCHES_PER_OCTAVE = 3
ZOOM_STEP = 2.0 ** (1.0 / ZOOM_NOTCHES_PER_OCTAVE)
# Every zoom factor is a power of 2^(1/768) (0.09 % steps), the engine's
# level grid, so tiles are drawn 1:1 on screen.
ZOOM_LEVELS_PER_OCTAVE = 768

# Cache
PIXMAP_CACHE_PAGES = 3  # Current + previous + next.

# Undo
UNDO_STACK_LIMIT = 200

# Recent files
MAX_RECENT_FILES = 10

# Default 5-color palette + custom slot.
DEFAULT_PALETTE = (
    "#E53935",  # red
    "#1E88E5",  # blue
    "#43A047",  # green
    "#FDD835",  # yellow
    "#212121",  # black
)

# Stroke widths offered in the tool palette.
STROKE_WIDTHS = (1.0, 2.0, 3.5)
