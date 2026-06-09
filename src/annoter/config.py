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

# Zoom
ZOOM_MIN = 0.1
ZOOM_MAX = 16.0
ZOOM_STEP = 1.25  # Multiplicative step for Ctrl+wheel and Ctrl+/-.

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
