# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**M1 through M5 are implemented** (pending final user validation); the full source tree, test suite and `build.py` exist. `PROMPT_RESTART.md` is the original product brief (in French); **`PLAN.md` is the live source of truth** for architectural decisions, milestone deviations and the remaining-issues backlog — read it first. A "Post-v1 hardening" section in `PLAN.md` tracks work landed after M5.

## Product in one paragraph

**Annoter** is a Windows-first (portable to Linux/macOS) standalone PDF annotation tool for **mechanical engineering drawings** (A0/A1 plans, files >100 MB). Single-user, local, no install, no admin rights. Annotations are persisted **directly inside the PDF using standard PDF annotation types** (no sidecar file) so they open in Acrobat/Foxit. Ships as a PyInstaller `--onefile` `.exe` and a `--onedir` portable zip.

## Stack (mandated — do not substitute, except the display exception below)

- Python 3.12 (CPython embedded for packaging)
- PySide6 (Qt 6, LGPL)
- PyMuPDF (`fitz`) — annotation I/O, saving, page operations, start-page thumbnails, image export, and the fallback page display (page view and page-list thumbnails when the engine is unavailable)
- **Display exception (approved by the user, 2026-11-09)**: on-screen page display may go through a native engine taken from the user's viewer Lire (C, MuPDF 1.28.6 with Lire's patches, statically linked in one library exporting only `ann_*`, loaded with ctypes). The exception covers on-screen display only (the page view and the page-list thumbnails); PyMuPDF keeps everything else and remains the fallback (`ANNOTER_RENDERER=pymupdf`, library missing, document refused). Design, decisions and measurements: PLAN.md, "Native display engine from Lire" (branch `perf/lire-engine`).
- Pillow (image utilities)
- PyInstaller — both `--onefile` and `--onedir`
- pytest

`pyproject.toml` uses **src-layout** (`src/annoter/...`). Dev install: `pip install -e .`.

## Architecture (MVC adapted to Qt)

The full target tree is in `PROMPT_RESTART.md` §4. Load-bearing invariants future Claude instances must respect:

- **Annotations are children of their page item** -- a `QGraphicsPixmapItem` on the PyMuPDF display path, a `TiledPageItem` with the native engine (same logical rect, see `page_box_150()`). Their coordinates are page-local; moving/rotating a page moves its annotations automatically.
- **Zoom convention:** `_apply_zoom(factor)` where `factor=1.0` means 1 screen pixel per PDF point (real size). The actual `QGraphicsView` scale is `factor * 72 / render_dpi`.
- **Zoom grid (native engine, 2026-11):** every zoom factor is a power of 2^(1/768) and wheel notches land on powers of 2^(1/3); keep it so (see PLAN.md, D4) -- the engine renders exactly those levels.
- **Native engine rules (`native/`, `annoter.engine`, `EngineDisplay`):** every `ann_*` call comes from the UI thread and the engine's threads never call back -- Qt polls with a `QTimer` that runs only while `ann_busy()`; the library exports nothing but `ann_*` (checked by `native/build_engine.py`); the engine never keeps the PDF file open (Save replaces it, then the engine reloads; page operations reload from `tobytes()`); every annotation Annoter turns into a Qt item is hidden from the engine by xref (`managed_annot_xrefs`), so nothing is drawn twice. A change to `annoter_engine.h` bumps `ANN_API_VERSION` and `native.API_VERSION` together.
- **Re-render at higher DPI** (PyMuPDF display path only) is hysteretic (`HIGH_DPI_ZOOM_THRESHOLD` up, `HIGH_DPI_ZOOM_EXIT` down) and implemented via the pixmap's `devicePixelRatio`: the supersampled pixmap keeps the same *logical* scene size, so child annotations, undo history and the save path are untouched. Never swap the page item or rescale item geometry for DPI purposes.
- **Every scene mutation goes through a `QUndoCommand`** (`Add`, `Delete`, `Move`, `ChangeColor`, `ChangeStroke`, `ChangeGdt`). Widgets must never mutate the scene directly.
- **`ToolController(QObject)` is the single source of truth** for current tool/color/stroke. Views subscribe to its signals — no direct view-to-view coupling.
- **GD&T feature control frames** are composite items laid out from `QFontMetricsF` (symbol cell + tolerance cell + datum cells), with modifiers Ⓜ/Ⓛ/Ⓟ/Ⓔ as Unicode enclosed characters.

### PDF annotation mapping (M4)

When saving, items are converted to native `fitz.Annot` types: Rectangle→`Square`, Ellipse→`Circle`, Line→`Line`, Arrow→`Line` with `endStyle`, Freehand→`Ink`, Text→`FreeText`, GD&T→`Square` **with a JSON blob in `Contents`** (rebuilds the editable item on reopen) **plus a rasterized appearance stream** so Acrobat/Foxit display the actual frame. Owned annots also persist exact geometry (`rect_pt`/`pos_pt`, in points) in the `/Subject` JSON because MuPDF pads `/Rect` by the border width — always prefer that payload when reading.

## Milestones — strict order

Work M1 → M5 sequentially. **Do not start a milestone until the previous one is validated by the user.**

1. **M1** — PDF viewer socle: open/close, page navigation, zoom (Ctrl+wheel, Ctrl+/-/0/1), pan (space + middle-click), drag&drop, recent files (`QSettings`, max 10), status bar. No annotations yet.
2. **M2** — Classic annotations: tools (rect, ellipse, line, arrow, text, freehand), `ToolController`, `QUndoStack` (limit 200), tool palette + annotation list docks, all the Add/Delete/Move/ChangeColor/ChangeStroke commands, Ctrl+Z/Y/Del/Ctrl+A. Empty text after edit auto-rolls-back the add.
3. **M3** — GD&T: 14 ISO 1101 symbols as `QPainterPath`, `GdtAnnotationItem`, `GdtDialog` (with live preview), `GdtPalette` dock grouped by family (Form / Profile / Orientation / Location / Runout). *Post-v1: dialog and palette were replaced by the in-place `GdtInlineEditor`, itself replaced by the `GdtFrameBuilder` in the UI redesign, Lot I (see PLAN.md).*
4. **M4** — Persistence (Save/Save As → native PDF annotations + reopen reconstructs items), light/dark theme switch (QSS), DPI re-render without orphaning annotations, persistent prefs (window size, dock state, theme).
5. **M5** — Packaging: `build.py` runs PyInstaller twice (onefile then onedir), bundles `.qm`, fitz, fonts. Verify on a clean Windows VM from a USB stick with no admin rights.

End each milestone with a short summary, list of deviations from the plan, and an explicit "Je peux passer à M{n+1} ?".

## Commands (once the project is bootstrapped)

These do not work yet — they are the expected commands once `pyproject.toml` exists:

```bash
pip install -e .[dev]            # dev install
python -m annoter                # run app
pytest                           # all tests
pytest tests/test_tools.py       # single file
pytest tests/test_commands.py::TestAddCommand::test_roundtrip  # single test
python build.py                  # M5: builds both onefile .exe and onedir zip
python build.py --onefile --zip-onedir --smoke   # what the release CI runs
git tag -a vX.Y.Z -m "Annoter vX.Y.Z" && git push origin master vX.Y.Z  # CI builds + publishes the GitHub release (see PLAN.md "Releases on GitHub")
```

## Testing conventions

- **`test_smoke.py`** is **headless** — PyMuPDF only, no Qt import. It verifies open / page count / dimensions / DPI.
- All Qt tests start with `pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)` and run under `QT_QPA_PLATFORM=offscreen` (CI uses `xvfb-run` on Linux).
- Cover `ToolController`, every `QUndoCommand` (including round-trip), and end-to-end main-window wiring.

## Hard rules from the brief

- **UI language: English only.** Code, comments, docstrings, UI strings: **English**. Conversation with the user (commit replies, milestone reviews): **French**.
- **No emojis anywhere** — not in code, docstrings, UI strings, or commit messages.
- **No `README.md` proliferation.** Keep a `PLAN.md` up to date with architectural decisions instead.
- Shell commands must use absolute paths and work in both Windows PowerShell and Linux/bash.
- Before writing logic, **scaffold the file tree first** (empty files or docstring-only) so the user can review the architecture.

## Out of scope for v1

Stamps ("APPROVED" / "REJECTED" / "BON POUR EXÉCUTION") were deferred from v1 but have since been implemented post-v1 (see PLAN.md). No collaboration, no multi-user, no OCR, no scale calibration / measurement.
