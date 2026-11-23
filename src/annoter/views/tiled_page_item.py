"""TiledPageItem: the page shown as tiles from the native engine.

Replaces the page's `QGraphicsPixmapItem` when the engine is active and
is reused across pages the same way. Its `boundingRect()` is exactly the
logical rect the 150-DPI pixmap had (rotated page box, rounded like
`get_pixmap`), so annotations stay its children in page-local
coordinates and the zoom convention, undo history and save path are
unchanged. `paint()` draws the white page, then -- only where the target
level is incomplete -- the low-resolution preview and the tiles of other
levels stretched, then the sharp tiles aligned on device pixels.

Scaffold only (step 1 of PLAN.md): written at step 4.
"""
