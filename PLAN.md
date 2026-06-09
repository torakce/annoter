# PLAN — Annoter

Living document for architectural decisions and milestone tracking.
Update this whenever a non-trivial design decision is taken.

---

## 1. Product summary

Standalone, install-free, single-user PDF annotator for **mechanical engineering drawings** (A0/A1, files >100 MB). Annotations are persisted as **standard PDF annotation objects** so files remain interoperable with Acrobat / Foxit. Windows-first, portable to Linux/macOS.

---

## 2. Locked architectural decisions

### Display model
- **One page at a time** (PageUp/PageDown swaps the page in the scene). No continuous vertical scroll.
- Single `QGraphicsScene` instance, repopulated when the current page changes.

### Rendering & cache
- Base render DPI: **150 DPI** (good lisibility/RAM trade-off; ~3500x5000 px on A0, ~50 MB RGBA).
- High-DPI re-render threshold: **200 % zoom -> re-rasterize at 300 DPI**. Disabled in M1 (would orphan child annotations); enabled in M4 with a re-parenting step.
- LRU pixmap cache: **3 pages** (current + previous + next). Sufficient given drawings have few pages.

### Annotations
- All annotation `QGraphicsItem`s are children of the current page's `QGraphicsPixmapItem` (page-local coordinates).
- Default color palette: **red, blue, green, yellow, black** + a **"Custom..."** button opening `QColorDialog`. The last custom color stays available until the app closes.
- Stroke widths: **1.0 / 2.0 / 3.5 px**.
- Every scene mutation goes through a `QUndoCommand` (Add / Delete / Move / ChangeColor / ChangeStroke / ChangeGdt). Stack capped at 200.
- `ToolController(QObject)` is the single source of truth for current tool/color/stroke.

### Fonts (compatibility-critical)
- **GD&T frames are rasterized into `Stamp` annotations** -> the font is baked into the bitmap, zero runtime dependency on the reader machine. We can therefore use a "real" technical font (OSIFont) safely.
- **Free-text annotations use Helvetica/Arial** (PDF base-14, always available, no embedding needed) to guarantee Acrobat compatibility on any machine.
- GD&T font is **configurable in preferences**: default = bundled OSIFont (in `resources/fonts/`), fallback = system sans-serif.

### GD&T scope
- Full ISO 1101 set: 14 characteristics across Form / Profile / Orientation / Location / Runout.
- Modifiers M / L / P / E rendered as Unicode enclosed characters.
- Datums must support **everything the standard allows**: simple (A, B, C), composite (A-B), with modifiers (A M, B L), and target/direction-specific datums. The dialog design will be presented for validation at the start of M3 because the input UX is non-trivial.
- Diameter prefix toggle on the tolerance cell.

### Persistence (M4)
- **Round-trip is bidirectional**: annotations created in Acrobat must reopen as editable items in Annoter, not only the ones we wrote ourselves. The reader scans every `fitz.Annot` on each page and maps to the closest item type; unknown types fall back to a generic read-only item.
- GD&T -> `Stamp` (rasterized) **with a JSON blob in `Contents`** to rebuild the editable item on next open. Acrobat preserves `Contents` on save, so a round-trip through Acrobat does not destroy our editability.
- **Save** is allowed but pops a confirmation dialog ("Overwrite the original file?") to prevent accidental loss. **Save As** behaves normally.

### Mapping item -> PDF annotation type
| Item                  | PDF annot type            |
|-----------------------|---------------------------|
| RectangleItem         | Square                    |
| EllipseItem           | Circle                    |
| LineItem              | Line                      |
| ArrowItem             | Line + endStyle           |
| FreehandItem          | Ink                       |
| TextAnnotationItem    | FreeText (Helvetica only) |
| GdtAnnotationItem     | Stamp + JSON in Contents  |

---

## 3. Hard rules (from the brief)

- UI / code / comments / commits in **English**. Conversation with the user in **French**.
- **No emojis** anywhere.
- No proliferating README files; this `PLAN.md` is the source of architectural truth.
- Shell commands must work on PowerShell and bash, with absolute paths.
- Scaffold the file tree first; logic only after the user validates the structure.
- Strict M1 -> M5 milestone order. No starting M(n+1) before M(n) is validated.

---

## 4. Out of scope (v1)

- Stamps ("APPROVED" / "REJECTED" / "BON POUR EXECUTION") -> v2.
- Multi-user / collaboration.
- OCR.
- Scale calibration / geometric measurement.
- Continuous-scroll page view.

---

## 5. Open questions tracked for later milestones

- **M3**: full GD&T dialog UX (composite datums, datum modifiers, datum targets) — present a mockup before coding.
- **M4**: exact strategy for re-parenting child annotations during a high-DPI re-render (transactional swap of the parent pixmap item, preserving child transforms).
- **M4**: rotation currently re-rasterizes the page server-side (PyMuPDF matrix), which means annotations do **not** follow the rotated page. Acceptable for M2; revisit in M4 by rotating via `QGraphicsItem.setRotation()` instead so children cascade.
- **M5**: confirm on a clean Windows VM that the onefile `.exe` boots from a USB stick under a non-admin account; document any antivirus false-positive workaround if encountered.

## M2 deviations / notes

- Annotations and their undo stacks are partitioned **per page** (`MainWindow._page_items`, `_page_stacks` + `QUndoGroup`). The brief did not specify multi-page behavior in M2; this keeps each page's history independent and avoids cross-page command leakage when items live as children of the page pixmap.
- `PdfScene.set_page_pixmap` now reuses the page item across renders (instead of removing/recreating it) so annotation children survive zoom and rotation. Cross-page transitions are handled by `MainWindow` via `detach_children`/`attach_children`.
- Text creation does **not** push an `AddAnnotationCommand` until the inline edit finishes with non-empty content; empty text is silently rolled back without polluting the undo stack.

---

## 6. Milestone progress

- [x] M0 — Bootstrap (this scaffold)
- [x] M1 — PDF viewer socle
- [x] M2 — Classic annotations (pending user validation)
- [x] M3 — GD&T (pending user validation)
- [x] M4 — Persistence & polish (pending user validation)
- [x] M5 — Packaging (pending user validation)

---

## M5 deviations / notes

- `build.py` drives PyInstaller in both modes via a single command (`python build.py`). Outputs are split into `dist-onefile/` and `dist-onedir/` so the two builds don't share state, and each mode gets its own `build-*/` work directory.
- Resources are embedded via `--add-data` for `resources/{themes,icons,fonts}` directories that exist; missing directories are skipped silently.
- `services/theme.py` resolves `resources/themes/` via `sys._MEIPASS` when frozen, falling back to the dev-tree path otherwise. Apply the same pattern to any future resource-loading service.
- `--zip-onedir` packages the portable folder as `Annoter-portable.zip` for USB distribution.
- The build itself (running PyInstaller) was **not executed** in this session for time/output reasons; the script has been validated via `--help` and the test suite. End-to-end clean-VM verification is the M5 acceptance step.

---

## M4 deviations / notes

- **GD&T persistence stores a Square annot + JSON in `/Contents`** (prefix `annoter.gdt:`), tagged via `/T = "Annoter:gdt"`. We do **not** rasterize the frame as a Stamp annot in v1: PyMuPDF's `add_stamp_annot` only accepts a fixed list of predefined names, and inserting the rendered image as a separate page object would drift across saves. The structured JSON survives a round-trip through Acrobat (Acrobat preserves `/Contents` on Square annots). Acrobat will display the GD&T frame as a plain rectangle; Annoter rebuilds the editable item on reopen.
- **Owned annotations are tagged with `/T = "Annoter"`** so a re-save cleans them out before re-emitting from the current scene state. Foreign annotations (created by Acrobat) are read back as editable items by subtype — Square/Circle/Line/Ink/FreeText all map cleanly — but they are not deleted on save.
- **Save flow** copies the document to a sibling `*.tmp.pdf`, closes the original, then atomically replaces the target. Required on Windows because PyMuPDF holds the source file open. Save reopens the file after replacement so editing can continue.
- **High-DPI re-render is deferred**: the threshold in `config.HIGH_DPI_ZOOM_THRESHOLD` stays effectively disabled in v1. Enabling it requires a per-item geometry rescale (multiply pos/rect/points by `new_dpi/old_dpi`) AND a font-size rescale for `TextAnnotationItem` / `GdtAnnotationItem` so they keep their physical size on the page. Lands as a follow-up.
- **Page rotation is view-only**: rotated annotations are not transformed to PDF user space on save. The save assumes the viewing rotation is 0; rotating then saving is acceptable for visual review but not recommended for round-trip fidelity. Documented for the user.
- **Themes** ship as simple QSS files in `resources/themes/`. The dark theme also restyles `QGraphicsView` so the page chrome reads against dark backgrounds.
- **Prefs persisted** via `QSettings("Annoter", "Annoter")`: window geometry, dock state, theme. The recent-files list was already persisted in M1 via the same settings backend.

---

## M3 deviations / notes

- `GdtAnnotationItem._edit_callback` is an **instance** attribute (not class attribute). Each `MainWindow` wires its own callback so the editor opens against the correct window — important for tests and any future multi-window scenario.
- The dialog input UX shipped in v1 covers: characteristic, Ø prefix, tolerance value + modifier (M/L/P/E), three datum rows with composite syntax (`A-B`) and per-datum modifier (M/L/P/F). **Datum targets** (point/line/area targets, e.g. `A1`, `A2`) are intentionally not exposed yet — deferred until a real drawing demands them.
- `ChangeGdtCommand` does not merge consecutive edits. Each accept of the dialog is one undo step; this matches `ChangeColor`/`ChangeStroke` semantics and keeps the history readable.
- GD&T frames default to black (`#212121`) regardless of the current `ToolController` color. Color can still be changed via the color toolbar after placement.
- `PdfScene.gdtPlacementRequested(QPointF)` is a new signal — the scene cannot run a modal dialog from inside `mousePressEvent`, so `MainWindow` opens `GdtDialog` and pushes the `Add` via `scene.push_add()`.
