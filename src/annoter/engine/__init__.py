"""Native display engine (from Lire) used for on-screen page display.

`load_engine()` will locate the compiled library (`bin/` next to this
package, the PyInstaller bundle, or `ANNOTER_ENGINE_PATH`), check its API
version and return a ready engine, or None when it is unavailable -- the
caller then falls back to the PyMuPDF display path. Setting
`ANNOTER_RENDERER=pymupdf` forces that fallback.

Scaffold only (step 1 of PLAN.md): written at step 3.
"""
