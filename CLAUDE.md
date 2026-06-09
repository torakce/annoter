# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This is a **greenfield project**. Only `PROMPT_RESTART.md` (the full product brief, in French) exists at the time of writing. No source tree, no `pyproject.toml`, no tests yet. **Read `PROMPT_RESTART.md` end to end before doing anything** — it is the single source of truth for product scope, architecture, and milestones.

## Product in one paragraph

**Annoter** is a Windows-first (portable to Linux/macOS) standalone PDF annotation tool for **mechanical engineering drawings** (A0/A1 plans, files >100 MB). Single-user, local, no install, no admin rights. Annotations are persisted **directly inside the PDF using standard PDF annotation types** (no sidecar file) so they open in Acrobat/Foxit. Ships as a PyInstaller `--onefile` `.exe` and a `--onedir` portable zip.

## Stack (mandated — do not substitute)

- Python 3.12 (CPython embedded for packaging)
- PySide6 (Qt 6, LGPL)
- PyMuPDF (`fitz`) — both rendering and annotation I/O
- Pillow (image utilities)
- PyInstaller — both `--onefile` and `--onedir`
- pytest

`pyproject.toml` uses **src-layout** (`src/annoter/...`). Dev install: `pip install -e .`.

## Architecture (MVC adapted to Qt)

The full target tree is in `PROMPT_RESTART.md` §4. Load-bearing invariants future Claude instances must respect:

- **Annotations are children of their page's `QGraphicsPixmapItem`.** Their coordinates are page-local; moving/rotating a page moves its annotations automatically.
- **Zoom convention:** `_apply_zoom(factor)` where `factor=1.0` means 1 screen pixel per PDF point (real size). The actual `QGraphicsView` scale is `factor * 72 / render_dpi`.
- **Re-render at higher DPI** uses a hysteretic threshold. Until M4, the threshold is set very high because re-rendering would orphan child annotations. M4 introduces a re-parenting step.
- **Every scene mutation goes through a `QUndoCommand`** (`Add`, `Delete`, `Move`, `ChangeColor`, `ChangeStroke`, `ChangeGdt`). Widgets must never mutate the scene directly.
- **`ToolController(QObject)` is the single source of truth** for current tool/color/stroke. Views subscribe to its signals — no direct view-to-view coupling.
- **GD&T feature control frames** are composite items laid out from `QFontMetricsF` (symbol cell + tolerance cell + datum cells), with modifiers Ⓜ/Ⓛ/Ⓟ/Ⓔ as Unicode enclosed characters.

### PDF annotation mapping (M4)

When saving, items are converted to native `fitz.Annot` types: Rectangle→`Square`, Ellipse→`Circle`, Line→`Line`, Arrow→`Line` with `endStyle`, Freehand→`Ink`, Text→`FreeText`, GD&T→`Stamp` (rasterized) **with a JSON blob in `Contents`** so the next open can rebuild the editable item.

## Milestones — strict order

Work M1 → M5 sequentially. **Do not start a milestone until the previous one is validated by the user.**

1. **M1** — PDF viewer socle: open/close, page navigation, zoom (Ctrl+wheel, Ctrl+/-/0/1), pan (space + middle-click), drag&drop, recent files (`QSettings`, max 10), status bar. No annotations yet.
2. **M2** — Classic annotations: tools (rect, ellipse, line, arrow, text, freehand), `ToolController`, `QUndoStack` (limit 200), tool palette + annotation list docks, all the Add/Delete/Move/ChangeColor/ChangeStroke commands, Ctrl+Z/Y/Del/Ctrl+A. Empty text after edit auto-rolls-back the add.
3. **M3** — GD&T: 14 ISO 1101 symbols as `QPainterPath`, `GdtAnnotationItem`, `GdtDialog` (with live preview), `GdtPalette` dock grouped by family (Form / Profile / Orientation / Location / Runout).
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

Stamps ("APPROVED" / "REJECTED" / "BON POUR EXÉCUTION") — deferred to v2. No collaboration, no multi-user, no OCR, no scale calibration / measurement.
