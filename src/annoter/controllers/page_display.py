"""EngineDisplay: drives the native engine for the open document.

Owns the engine document, builds the view request from `PdfView`
(zoom, scroll, viewport, devicePixelRatio, rotation), asks for the
previous / next page as prefetch, polls with a `QTimer` that runs only
while the engine is busy, hands finished tiles to the `TiledPageItem`,
forgets evicted ones, reloads after Save and after page operations, and
passes the grayscale flag and the hidden annotations (option A of D3:
every annotation Annoter draws as a Qt item).

Scaffold only (step 1 of PLAN.md): written at step 4.
"""
