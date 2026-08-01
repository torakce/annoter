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
- High-DPI re-render: **hysteretic** -- above 200 % zoom the page is re-rasterized at 300 DPI, below 150 % it drops back to base DPI. The supersampled pixmap carries a `devicePixelRatio` equal to the scale factor, so its *logical* scene size is unchanged: child annotations, undo history and the save path are unaffected. No re-parenting step needed.
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
| DimensionAnnotationItem | Square + JSON in Contents |

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

- **GD&T persistence stores a Square annot + JSON in `/Contents`** (prefix `annoter.gdt:`), tagged via `/T = "Annoter:gdt"`. The structured JSON survives a round-trip through Acrobat (Acrobat preserves `/Contents` on Square annots); Annoter rebuilds the editable item on reopen. Since the post-v1 hardening pass, the annot also carries a **custom appearance stream** (rasterized frame as an image XObject with SMask, wired into `/AP/N` via `xref` surgery) so Acrobat/Foxit display the actual feature control frame instead of an empty rectangle.
- **Owned annotations are tagged with `/T = "Annoter"`** so a re-save cleans them out before re-emitting from the current scene state. Foreign annotations (created by Acrobat) are read back as editable items by subtype — Square/Circle/Line/Ink/FreeText all map cleanly — but they are not deleted on save.
- **Save flow** copies the document to a sibling `*.tmp.pdf`, closes the original, then atomically replaces the target. Required on Windows because PyMuPDF holds the source file open. Save reopens the file after replacement so editing can continue.
- **Page rotation is view-only**: rotated annotations are not transformed to PDF user space on save. The save assumes the viewing rotation is 0; rotating then saving is acceptable for visual review but not recommended for round-trip fidelity. Documented for the user.
- **Themes** ship as simple QSS files in `resources/themes/`. The dark theme also restyles `QGraphicsView` so the page chrome reads against dark backgrounds.
- **Prefs persisted** via `QSettings("Annoter", "Annoter")`: window geometry, dock state, theme. The recent-files list was already persisted in M1 via the same settings backend.

---

## M3 deviations / notes

- `GdtAnnotationItem._edit_callback` is an **instance** attribute (not class attribute). Each `MainWindow` wires its own callback so the editor opens against the correct window — important for tests and any future multi-window scenario.
- The dialog input UX shipped in v1 covers: characteristic, Ø prefix, tolerance value + modifier (M/L/P/E), three datum rows with composite syntax (`A-B`) and per-datum modifier (M/L/P/F). **Datum targets** (point/line/area targets, e.g. `A1`, `A2`) are intentionally not exposed yet — deferred until a real drawing demands them.
- `ChangeGdtCommand` does not merge consecutive edits. Each accept of the dialog is one undo step; this matches `ChangeColor`/`ChangeStroke` semantics and keeps the history readable.
- GD&T frames default to black (`#212121`) regardless of the current `ToolController` color. Color can still be changed via the color toolbar after placement.
- `PdfScene.gdtPlacementRequested(QPointF)` is a new signal — placement needs MainWindow-level UI, so the scene defers. In v1 MainWindow opened the modal `GdtDialog`; since the post-v1 in-place editing pass it spawns a draft item plus the floating `GdtInlineEditor` and pushes the `Add` via `scene.push_add()` on commit.

---

## Post-v1 hardening (2026-06-09)

Changes landed after the M5 milestone, in git history from the initial commit onward:

- **Git history starts here**: the project was not under version control during M1-M5.
- **Ink read fix**: a multi-stroke Ink annotation (e.g. from Acrobat) now yields one `FreehandItem` per stroke instead of a single polyline with spurious connecting segments (`_annot_to_items` returns a list).
- **High-DPI re-render enabled** via pixmap `devicePixelRatio` (see "Rendering & cache" above). `config.HIGH_DPI_ZOOM_EXIT` (1.5) provides the hysteresis low side.
- **GD&T appearance stream**: external viewers now render the actual frame (see M4 notes above).
- **Exact geometry round-trip**: MuPDF pads the stored `/Rect` of Square/Circle annots by the border width, which made items drift on every save/reopen cycle (worst for GD&T, whose writer also used `boundingRect` including the selection margin). Owned annots now persist exact geometry in points inside the `/Subject` JSON (`rect_pt` for rect/ellipse/GD&T, `pos_pt` for text); the reader prefers it over `/Rect`.
- **GdtDialog redesign** (2026-06-10): the characteristic combo box became a grid of symbol icon buttons grouped by family; tolerance and datums are one horizontal strip mirroring the printed frame order (diameter toggle, value, modifier, then the three datum cells under Primary/Secondary/Tertiary headers); modifier combos display the enclosed glyphs with full ISO names as item tooltips (`MODIFIER_NAMES` added to `model/gdt.py`); the live preview renders at 12 pt and clamps its scale to 2x instead of `fitInView`-ing the frame to fill the dialog. The symbol icon painter moved to `views/icons.py` (`gdt_symbol_icon`), shared with `GdtPalette`; `GdtDialog` takes an `icon_color` kwarg so MainWindow passes the theme-appropriate glyph color.
- **Hi-res viewport overlay** (2026-06-10): beyond the 300 DPI full-page ceiling, deep zoom was blurry. After the view settles (`HIRES_DEBOUNCE_MS`), `PageRenderer.render_clip` rasterizes only the visible clip (plus `HIRES_OVERLAY_MARGIN`) at exact screen resolution and `PdfScene.set_hires_overlay` lays it over the page as a child `QGraphicsPixmapItem` with negative Z (above the page raster, below annotations, mouse-transparent). The overlay is roughly viewport-sized whatever the zoom, capped by `HIRES_MAX_PIXELS`, so memory stays bounded on A0 plans; logical geometry is untouched (devicePixelRatio convention), so annotations, undo and the save path are unaffected. Rotation is handled by offsetting the clip by the rotated page bbox origin (the rotated matrix maps the page into negative coordinates). The overlay is dropped on every `set_page_pixmap` and rebuilt on the next debounce tick. The hysteretic 150/300 DPI full-page mechanism is kept as the fallback during pans.
- **Main toolbar** (2026-06-10): Office-style quick-access bar (open/save, undo/redo, the eight drawing tools as checkable actions synced both ways with `ToolController`, zoom controls). Icons are code-drawn in `views/icons.py` (`action_icon`, plus a GD&T glyph in `tool_icon`) and repainted on theme change via `MainWindow._apply_icon_theme`; `ToolPalette.set_icon_color` was added so the dock icons follow too. Tooltips advertise keyboard shortcuts.
- **Theme refresh** (2026-06-10): both QSS files rewritten flat/rounded with the blue accent — borderless hover-tinted toolbar buttons, rounded inputs with focus accent, styled combo drop-downs and popups, slim rounded scrollbars without arrow buttons, accent default push button, plain-`QLabel` color rule in dark.
- **In-place GD&T editing** (2026-06-10): `GdtDialog` and the `GdtPalette` dock are gone (files deleted). Clicking with the GD&T tool now drops a draft `GdtAnnotationItem` on the page (parented directly, no undo entry yet) and opens `views/gdt_editor.py::GdtInlineEditor` — a floating strip parented to the view's viewport, shaped like the printed FCF (symbol cell, diameter toggle, tolerance value + modifier, three datum cells, confirm/cancel buttons). The characteristic is picked from a drop-down `QMenu` grouped by ISO 1101 family (disabled actions serve as headers; `addSection` renders textless under the QSS). Every keystroke emits `stateEdited` and MainWindow applies it via `item.set_state`, so **the scene item is its own live preview**. Commit (Enter, confirm button, or focus leaving the editor) pushes `AddAnnotationCommand` via `scene.push_add` for new frames or `ChangeGdtCommand` for edits; an untouched new frame (`state == GdtState()`) rolls back like an empty text annotation. Escape cancels (removes the draft / restores the old state). The editor is repositioned on zoom/scroll, committed on page switch and save, cancelled on document close. Double-click still edits: the `set_edit_callback` hook now opens the inline editor.
  - **Symbol shapes corrected against the printed chart** (2026-06-10): flatness parallelogram now leans right; cylindricity's oblique lines are tangent to the circle; symmetry's middle bar is longer than the outer two; total runout gained the base line joining the two arrow tails; angularity flattened to ~30 degrees; runout arrowheads are closed outlined triangles with the shaft stopping at the head base; concentricity/profile proportions tuned. All in `views/items/gdt_symbols.py` (unit-box paths), so the item painter, the editor menu icons and the rasterized PDF appearance streams all pick the fixes up.
  - **Tolerance zone prefix** (2026-06-10): the boolean `diameter_prefix` became `GdtState.tolerance_prefix`, a literal string out of `TOLERANCE_PREFIXES` ("Ø", "R", "SØ", "SR", or "" for none). `from_dict` still maps the legacy `diameter_prefix: true` JSON (PDFs annotated before the rework) to "Ø". In the editor, the Ø toggle button became a drop-down menu button like the modifier cells — the toggle was also broken: clicking it bounced the focus to the view with no popup open, so the deferred focus-loss check committed and closed the editor mid-edit. All editor buttons now take `Qt.ClickFocus` so clicks keep the focus inside the editor.
  - **Focus-loss commit is deferred by one tick.** The editor's tool buttons never take focus, so opening one of its dropdown menus bounces the focus to the view; committing synchronously on `focusChanged` destroyed the editor (and the menu) before the menu action could fire — dropdown selections silently did nothing. `_on_app_focus_changed` now schedules `_maybe_commit_on_focus_loss` via `QTimer.singleShot(0)`, which skips the commit when `QApplication.activePopupWidget()` is set or the focus is back inside the editor (walking `parentWidget()`, which unlike `isAncestorOf` crosses window boundaries, so popups parented to their buttons count as inside). Each menu's `aboutToHide` pulls the focus back into the tolerance field so typing keeps working and the watcher stays quiet.

- **Multi-selection** (2026-06-11): three gestures, all in the Select tool. (1) Rubber band: `PdfView` switches to `QGraphicsView.RubberBandDrag` while the Select tool is active (`NoDrag` for drawing tools, switched in `set_tool_cursor_for`); Qt only starts the band when the press lands on no interactive item, so dragging from empty page area selects everything the band touches (`IntersectsItemShape`) while presses on an annotation still move it. (2) Shift+click toggles the clicked annotation in/out of the selection (handled in `PdfScene.mousePressEvent`; Qt reserves this gesture for Ctrl natively). (3) Ctrl+click also toggles -- the Ctrl+drag duplicate gesture still works because cloning is now deferred: the press only records a pending state, and the clone set is created the first time the cursor travels past `QApplication.startDragDistance()` (screen pixels, so zoom-independent); a release before that toggles the selection instead. Group move/nudge/delete/restyle already operated on `selectedItems()`, so they pick up multi-selections unchanged.

- **Revision Cloud tool** (2026-06-15): new `Tool.CLOUD` mirroring Acrobat's cloud markup, the highest-value classic annotation for engineering drawings. `CloudItem` (in `views/items/shapes.py`) subclasses the shared `_ShapeItem`, so it reuses the 8-handle resize, fill and clone machinery; only the outline differs (scalloped border drawn by `build_cloud_path`, a clockwise walk of the rect perimeter sampling each outward semicircle into short segments to stay independent of Qt's y-down arc-angle convention). Clouds carry no inline text label (double-click is swallowed). Drag-drafted exactly like rect/ellipse (added to the three `isinstance` branches in `PdfScene`). Persisted as a native PDF **Polygon** with a cloudy border effect (`set_border(clouds=1)` -> `/BE <</S/C/I 1>>`) so Acrobat/Foxit render the scallops; exact geometry round-trips via `rect_pt` in `/Subject` (MuPDF pads `/Rect` for the cloudy border). `build_cloud_path` is shared with the code-drawn tool icon. The Properties dock shows a fill toggle + fill color (no corner radius / text). Foreign `Polygon` annots are best-effort reconstructed from their bounding box.

- **Polyline & Polygon tools** (2026-06-15): `Tool.POLYLINE` (open path) and `Tool.POLYGON` (closed, fillable), mirroring Acrobat's connected-lines / polygon markup. `PolylineItem` / `PolygonItem` live in `views/items/poly.py` over a shared `_PolyItem` base that stores an ordered vertex list; each vertex gets its own resize handle, **keyed by integer index** -- the base item treats the handle "role" opaquely (it is only stored and passed back to `apply_resize`), so an int works without touching the `HandleRole` enum. New **multi-click drafting** in `PdfScene`: the first press starts a draft (committed vertices + a floating one tracking the cursor via `mouseMoveEvent`), each press appends a vertex, double-click / Enter / (for polygons) clicking near the first vertex finishes, Escape discards. State lives in `_poly_draft` / `_poly_points`; `finish_poly_draft` dedups the trailing floating point, enforces a 2-vertex (polyline) / 3-vertex (polygon) minimum, and pushes the usual `AddAnnotationCommand`. Switching tools mid-draft commits; a page switch (`detach_children`) discards. `PdfView` routes Enter to `finish_poly_draft` (via the public `poly_draft_active`) and adds both tools to the crosshair-cursor set (which also gained the previously-missed `CLOUD`). Persisted natively: `PolylineItem` -> **PolyLine**, `PolygonItem` -> **Polygon** (exact vertices round-trip via `/Vertices`, no `rect_pt` needed). Since clouds also use the Polygon subtype, the reader disambiguates on a `"poly"` discriminator in the `/Subject` JSON (`"cloud"` vs `"polygon"`), falling back for foreign polygons to the cloudy `/BE` border (`_polygon_has_cloud_border`). Polygons expose a fill toggle in the Properties dock (shared `_add_fill_rows`, also used by clouds); polylines show only the common stroke/dash rows. Tests in `tests/test_poly.py` (interaction) and `tests/test_persistence.py` (round-trip + cloud/polygon disambiguation).

- **Callout tool** (2026-06-15): `Tool.CALLOUT` -- a text box with a leader line ending in an open arrow, Acrobat's most-used annotation for pointing at a feature without obscuring it. `CalloutItem` (in `views/items/callout.py`) subclasses `TextAnnotationItem`, so it inherits inline editing, fonts/alignment, the wrap-resize handles and the Properties-dock text rows for free; it adds a draggable `tip` (item-local coords, like the text box at local origin), a leader from the nearest text-box edge (`connection_point`) to the tip, an arrowhead, and a tip handle reusing `HandleRole.P1`. Drafting is **drag-based** (unlike polyline's multi-click): press = arrow tip (the feature), drag to the text-box anchor; `_make_draft_item` / `_update_draft` build it and pin the tip at the press point, then `_finish_draft` defers to `_finish_callout_draft`, which begins inline edit and -- exactly like a plain text annotation -- commits via `AddAnnotationCommand` on a non-empty edit or rolls back on empty (`_on_text_edit_finished`). Persisted as a native **FreeText** plus `/IT /FreeTextCallout` and a `/CL` leader line (y-flipped into PDF user space by `_set_callout_line`); however MuPDF's appearance generator ignores `/CL` (verified: it draws no leader), so the authoritative leader geometry is the `callout_tip_pt` field in the `/Subject` JSON, from which Annoter rebuilds the full callout on reopen. The reader distinguishes a callout from a plain text FreeText by the presence of `callout_tip_pt`. Acrobat (which honors `/CL`) shows the leader; other MuPDF-based viewers show the text box only. Tests in `tests/test_callout.py` (drag + commit/rollback) and `tests/test_persistence.py` (round-trip + callout/text disambiguation).

- **Sticky Note tool** (2026-06-16): `Tool.STICKY_NOTE` -- a small comment-bubble marker with a text note, mirroring Acrobat's sticky note. `StickyNoteItem` (in `views/items/note.py`) is a fixed-size icon (page pixels, scales with zoom) carrying the note body; it has no resize handles (movable only), shows the note as a hover tooltip, and exposes `set_edit_callback` for double-click editing like `GdtAnnotationItem`. Editing uses a **floating popup**, `views/note_editor.py::NoteEditor` (a `QPlainTextEdit` in a `QFrame` parented to the viewport), following the same lifecycle contract as `GdtInlineEditor`: committed on Ctrl+Enter / confirm button / focus-out, cancelled on Escape, an empty new note rolls back like an empty text annotation. MainWindow drives it with the GD&T-parallel methods (`_on_note_placement`, `_open_note_inline`, `_commit_note_editor`, `_cancel_note_editor`, `_position_note_editor`) wired into the same hooks (save, page switch, document close, zoom/scroll reposition, edit-callback re-attach on read/paste); existing-note text edits go through `ChangePropsCommand("text")`. The scene emits `notePlacementRequested(QPointF)` on a click with the tool active. Persisted as a native PDF **Text** annotation (`add_text_annot`, note body in `/Contents`), so it opens as a real sticky note in Acrobat/Foxit; exact position round-trips via `pos_pt`. Tests in `tests/test_main_window_wiring.py` (placement + commit/rollback) and `tests/test_persistence.py` (round-trip).

- **Stamp tool** (2026-06-16): `Tool.STAMP` -- a rubber-stamp marker
  (preset labels APPROVED / REJECTED / BON POUR EXÉCUTION, or custom
  text), the first item out of the post-v1 backlog and previously
  earmarked for v2 in CLAUDE.md. `StampItem` (in `views/items/stamp.py`)
  is a bold uppercase label in a double rounded border tinted with the
  stamp color; the box auto-sizes to the text and font size is an
  editable property (no resize handles, movable only), like the GD&T
  frame. One-click placement in `PdfScene` (no editor) drops a default
  APPROVED stamp and returns to Select; the user re-labels / recolors it
  via the Properties dock, where a preset combo sets text+color in one
  `ChangePropsCommand`. Persisted as a native PDF **Stamp** annotation
  with a rasterized appearance stream so Acrobat/Foxit show the real
  stamp, plus text/size in the `/Subject` JSON for editable
  reconstruction. The GD&T appearance machinery was generalized for this:
  `_gdt_frame_planes` -> `_rasterize_item_planes` and
  `_set_gdt_appearance` -> `_set_rasterized_appearance` (resource name
  `/AnnoterGdt` -> `/AnnoterAP`), now shared by both items. Tests in
  `tests/test_stamp.py` (placement + preset) and
  `tests/test_persistence.py` (round-trip + appearance-visible).

- **Composite (multi-row) GD&T, CATIA-inspired** (2026-06-18): the feature control frame now supports stacked composite tolerance rows under a shared characteristic symbol, upper/lower texts, and an optional auxiliary frame appended to the right -- matching CATIA's Geometrical Tolerance dialog and the multi-row frames on real drawings.
  - **Model** (`model/gdt.py`): new `GdtRow` dataclass (per-row tolerance prefix/value/modifier + three datums). `GdtState` keeps its flat `tolerance_*` / `datum_*` fields as **row 0** (so single-row FCFs persisted before this change still load unchanged) and adds `additional_rows: list[GdtRow]`, `upper_text`, `lower_text`, `aux_symbol: Characteristic | None`, `aux_text`. `all_rows()` returns row 0 + the extras; `to_dict` only writes the new keys when non-default and `from_dict` defaults them, so the JSON is backward compatible both ways.
  - **Item** (`views/items/gdt.py`): `_compute_layout` was rewritten to build flat draw lists (`_border_rects`, `_symbol_draws`, `_text_draws`). The symbol cell spans every row; each row lays out its own tolerance + datum cells (row widths were briefly equalized by stretching the last cell -- reverted 2026-07-02, see below). The symbol is drawn into a centered square (not the cell) so it isn't stretched by a tall spanning cell. Upper/lower texts are left-aligned above/below; the auxiliary `[symbol][text]` frame is centered vertically to the right. `content_rect` covers everything, so the existing rect_pt geometry and the rasterized appearance stream (now generalized as `_set_rasterized_appearance`) pick it all up with no persistence changes.
  - **Editor** (`views/gdt_editor.py`): the single-row strip became a vertical panel -- one `_RowEditor` per tolerance line with its **own symbol picker** + prefix / value / modifier / three datum cells, a "+ line" button, plus "Top" and "Bottom" text fields. Still a floating in-place panel with live preview (`stateEdited` on every change). Public API (`GdtInlineEditor(initial, parent, icon_color=...)`, `current_state()`, `open()`, signals) is unchanged, so MainWindow needs no edits. Per-row fields live in `_RowEditor`.
  - **Editor refinements** (2026-06-18, after user feedback): (1) the editor now closes **only on explicit commit/cancel** (Enter / OK, Escape / Cancel) -- the commit-on-focus-loss watcher was removed, since clicking a neutral area was closing it; MainWindow still commits it on save / page switch / opening another frame. (2) The outer layout uses `QLayout.SetFixedSize` so "+ line" actually grows the panel instead of crushing the new row. (3) Per-row symbol picker (composite frames can mix characteristics; the item merges the symbol cell only across consecutive same-characteristic rows). (4) Readability: every field has a visible border, menu buttons show an explicit "▾" drop-down arrow (native indicator hidden), and each datum is wrapped with its modifier in a bordered `#GdtDatumGroup` with extra spacing between groups. (5) The **"Aux" frame was pulled from the editor UI** (not ready); `GdtState.aux_*` and the item rendering stay, and the editor preserves any existing aux values across an edit so they are not silently dropped.

- **GD&T opaque cell background** (2026-06-29): `GdtAnnotationItem.paint` filled cells with `Qt.NoBrush` (transparent), so page content showed through the frame. Cells now paint an opaque white brush before the border stroke, matching CATIA-style frames legible over drawing content. Since the rasterized PDF appearance stream reuses this same `paint()` call, the exported annotation picks up the white background automatically -- no persistence changes needed.
- **Shift-constrained resize of existing annotations** (2026-06-29): Shift already constrained drafting of *new* shapes (45-degree angle snap for lines/arrows, square footprint for rect/ellipse/cloud) but had no effect when resizing an *existing* selected item via its handles. `PdfScene._constrain_resize` (called from `mouseMoveEvent` when Shift is held during a handle drag) now applies the same rule: a line/arrow endpoint snaps to a 45-degree step from the other (fixed) endpoint -- keeping the segment's alignment constant while lengthening or rotating it -- and a rectangle/ellipse/cloud dragged from a corner keeps a square footprint relative to the opposite corner. Tests in `tests/test_resize_constrain.py`.

- **Creation/edit UX pass, PowerPoint/Canva-inspired** (2026-06-30): nine editing-ergonomics features landed together, sharing a new `controllers/geometry.py` (`item_local_rect` / `item_scene_rect` -- a duck-typed "true geometry rect" ignoring selection/handle padding, used by every feature below -- plus `px_to_pt` / `pt_to_px`, page-pixel <-> PDF-point conversion pinned to `BASE_RENDER_DPI`).
  1. **Align & Distribute** (`controllers/align.py`): `AlignMode` enum + `compute_align_moves(items, mode)`, a pure function returning `(item, old_pos, new_pos)` triples fed into the existing `MoveAnnotationsCommand` -- so it's one undo step, same as a manual drag. Distribute needs >= 3 items (equalizes edge-to-edge gaps, endpoints fixed) and is a no-op below that. Wired into the Edit menu's new "Align" submenu and the selection context menu.
  2. **Fill opacity**: `_ShapeItem` (Rectangle/Ellipse/Cloud) and `PolygonItem` gained `fill_opacity` (0-1, applied as brush alpha in `paint()`), a Properties-dock spinbox, and persistence. PDF has no fill-only alpha for Square/Circle/Polygon annots -- only a whole-annotation `/CA` -- so `_apply_fill_opacity` sets it only when the fill is on and opacity < 1.0, keeping untouched shapes (opacity 1.0, the default) writing byte-identical annotations to before this feature.
  3. **Format Painter** (`Tool.FORMAT_PAINTER`, a click-mode, not a drawing tool -- absent from the palette grid): toggling it with exactly one item selected captures a style dict over a fixed prop list (`_PAINTABLE_PROPS` in `main_window.py`); the scene then emits `formatPaintRequested(item)` for each subsequent click (duck-typed per-target via `hasattr(item, f"set_{name}")`, so e.g. font props never leak onto a rectangle), applied as one `ChangePropsCommand` per click. Sticky until toggled off or Escape; `_on_tool_changed` un-checks the toolbar button if the user switches to any other tool mid-paint.
  4. **Live measurement HUD** (`views/measurement_hud.py`): a small floating label following the cursor while drafting or handle-resizing a rect-like shape (`W x H`) or a line/arrow (`length @ angle`), in PDF points -- explicitly the annotation's own on-page size, not a calibrated real-world measurement (scale calibration stays out of scope). `PdfScene.current_measurement()` inspects `_draft_item`/`_resize_item`; `PdfView` queries it after every `mouseMoveEvent` and hides it on release/`leaveEvent`.
  5. **Precise numeric geometry panel** (Properties dock, single-selection only): X/Y always; Width/Height for shapes, Length/Angle for lines/arrows (same unit and angle convention as the HUD -- y-up, 0=east). Edits push the exact same `MoveAnnotationsCommand`/`ResizeCommand` a mouse drag would, so undo history reads identically either way.
  6. **Smart alignment guides** (PowerPoint/Canva "smart guides"): `AnnotationItem.itemChange` now intercepts `ItemPositionChange` and delegates to `PdfScene.maybe_snap_move` (duck-typed, so the base item class stays ignorant of scene internals). Snapping only engages when `_interactive_drag_active` is True -- set between the plain-click-drag branch of `mousePressEvent` and the matching `mouseReleaseEvent` -- so undo/redo, Align commands, duplicate-drag and every other programmatic `setPos()` in the app are never perturbed. Scoped to a single selected item (snapping each item of a multi-selection independently would pull the group's relative layout apart); compares against every sibling's and the page's own left/center/right and top/middle/bottom, within `_SNAP_THRESHOLD_PX` (6px), and shows a dashed guide line (`QGraphicsLineItem`, Z=1e6) while a snap is active. Synthetic `QTest` mouse-drag simulation proved unreliable under the offscreen platform for exercising Qt's native item-drag machinery, so `tests/test_smart_guides.py` drives the real press/release code path but calls `setPos()` directly to stand in for whatever delta Qt's drag would have produced -- still exercises the real `itemChange` hook, just not through flaky synthetic mouse-move delivery.
  7. **Line/arrow endpoint snapping** (PowerPoint connector-style): while drafting or handle-resizing a `LineItem`/`ArrowItem` endpoint, `PdfScene._nearest_shape_snap_point` checks the 9 "connection points" (4 corners, 4 edge-midpoints, center) of every other annotation's bounding rect within `_ENDPOINT_SNAP_THRESHOLD_PX` (8px) and snaps exactly onto the nearest one, taking priority over Shift's 45-degree angle-constrain (which still applies when no shape is nearby).
  8. **Floating selection toolbar** (`views/selection_toolbar.py`): a small pill (color swatch, stroke width, Duplicate, Delete) anchored above the current selection's combined bounding box, mirroring Canva/Figma's contextual toolbar so the most common actions don't require a trip to the Properties dock. Shows/hides/repositions from `_on_scene_selection_changed` and the existing zoom/scroll repositioning hooks (same pattern as the GD&T/note in-place editors).
  9. **Session-level grouping** (Ctrl+G / Ctrl+Shift+G): deliberately *not* Qt's native `QGraphicsItemGroup`, which would reparent members under a group item -- breaking every part of the app that assumes annotations are direct children of the page item (persistence collection, `detach_children`, alignment, both snapping features). Instead `PdfScene._groups: list[set[AnnotationItem]]` tracks membership by object identity (survives delete/undo naturally, since the same Python objects are removed/re-added rather than recreated); a plain click on a grouped member expands the selection to the whole group before Qt's native drag starts, so the existing multi-select move/delete/duplicate machinery handles the rest for free. Not persisted across save/reopen (documented limitation below) and not itself an undoable action (only the resulting item mutations are).
- **Move-modifier refinements, PowerPoint-style** (2026-07-02): three modifiers now shape an interactive drag, mutually exclusively, all gated the same way as the original smart-guide hook (`_interactive_drag_active`, so programmatic moves are never affected):
  - **Alt disables snapping** for the duration of the drag/resize/draft -- both smart-guide snapping (`maybe_snap_move` returns the raw proposed position and hides any visible guide) and line/arrow endpoint-snapping (`_nearest_shape_snap_point` is simply not called, in both `_update_draft` and the resize branch of `mouseMoveEvent`). Read via `QApplication.keyboardModifiers()` in `maybe_snap_move` (no event object available inside `itemChange`) and via the real event's modifiers at the two endpoint-snap call sites.
  - **Shift axis-locks a move** to whichever of X/Y has the larger cumulative displacement from the drag's start position (`PdfScene._apply_axis_lock`, using the existing `_move_origins` snapshot captured at press time), re-evaluated on every `itemChange` call so the locked axis follows the direction the user is actually dragging in, PowerPoint-style. Unlike guide-snapping, this is *not* restricted to a single selected item: since Qt's native multi-item drag applies the identical mouse delta to every selected item (only their start positions differ), each item's own `itemChange` computes the same locked axis independently, so a whole multi-selection or group locks together consistently.
  - Smart-guide snapping is now also alt/shift-aware within the same `maybe_snap_move` gate rather than three independent checks scattered across callers.
- **Live property updates in the Properties dock** (2026-07-02): every QSpinBox/QDoubleSpinBox/QLineEdit field previously only committed on `editingFinished` (focus-out or Enter) -- clicking a spin box's arrow buttons repeatedly never fires that signal, so nothing visibly changed until the user clicked elsewhere. Every such field now previews live on `valueChanged`/`textChanged` (applied directly to the item, no undo command yet) and still commits exactly one undo step on `editingFinished`, via two new generic helpers: `_wire_live_prop` (for the existing `ChangePropsCommand`-based fields: stroke, corner radius, shape/stamp text, label/font sizes, fill opacity) and `_wire_live_geom` (for the geometry panel's `MoveAnnotationsCommand`/`ResizeCommand`-based X/Y/Width/Height/Length/Angle fields). The pre-edit baseline is captured *lazily*, on the first change of each editing session (not when the dock/field was built), so a second, later edit of the same field -- or editing a sibling field first -- never diffs against a stale snapshot. Checkboxes, combo boxes and the color-picker button were already "live" by nature (their signals only fire on a discrete, completed choice) and are unchanged. The old single-shot `_push_move` / `_push_resize_rect` / `_push_line_geom` methods still exist as thin apply+commit-in-one-call wrappers around the new split halves, so direct callers/tests are unaffected.
- **PowerPoint-like group interaction** (2026-07-02): two additions on top of the original session-level grouping. (1) Clicking *empty space inside a group's combined bounding box* (not just directly on a member shape) now also selects and drags the whole group -- PowerPoint lets you grab a group anywhere inside its silhouette. Implemented as a manual drag (`_begin_group_drag` / `_finish_group_drag`), mirroring the existing Ctrl-drag-duplicate machinery (own press/move/release branches, `PdfScene._group_at_point` hit-tests the union of `item_scene_rect` over each group), since no item is under the cursor for Qt's native per-item drag to grab. It still moves items via plain `setPos()`, so it goes through the same `itemChange` hook as any drag -- axis-lock and Alt-disable apply to a group drag exactly as they do to a single item. (2) A unified dashed outline (`PdfScene._group_box`, a persistent `QGraphicsRectItem` overlay) is drawn around the union of a group's members whenever the *current selection exactly matches* that group, wired to Qt's native `selectionChanged` signal and refreshed after every `mouseMoveEvent` tick (both the manual group-drag branch and the native-drag fallback) so it tracks the shapes during a drag, not just at selection time.

## Discussion #1 backlog (GitHub Discussions)

The user filed 13 UX/feature suggestions in GitHub Discussion #1 after using
the app; being worked through in ordered lots (quickest/safest first),
each ending with a summary like the M1-M5 milestones. Two latent bugs found
during investigation are fixed alongside.

### Lot 1 -- quick fixes (2026-07-02)

- **`Ctrl+G` shortcut collision**: `act_goto` ("Go to Page...") and `act_group` ("Group") were both bound to `Ctrl+G`. `act_group` keeps it (closer to the Office/PowerPoint convention); `act_goto` moved to `Ctrl+Alt+G`.
- **Shift-resize on lines/arrows now preserves the original angle instead of re-snapping it** (item 7): the 2026-06-29 Shift-constrained-resize feature (see above) reused `_snap_angle` (round to the nearest 45-degree step), which rotated the segment instead of keeping its existing alignment while it's lengthened. `PdfScene._constrain_resize` now reads the pre-drag endpoints from `self._resize_snapshot` (not the live, already-mutated `item.line_points()`) and projects the cursor onto the infinite line through them via a new `_project_onto_ray` helper -- the angle never changes, only the length. `tests/test_resize_constrain.py` and `tests/test_endpoint_snap.py` updated to assert angle-preservation instead of 45-degree snapping.
- **GD&T composite rows keep their natural width** (item 4): the "extend the last cell so every row matches the widest one" behavior from the 2026-06-18 composite rework (see above) is removed from `_compute_layout` -- each row now keeps its own content width; the shared symbol column still keeps every row flush-left. Test in `tests/test_gdt.py::test_rows_keep_natural_width_but_stay_left_aligned`.
- **Zero stroke width = no border** (item 9): the Properties dock's Stroke spinbox now allows 0 (was clamped to a 1 px minimum); `Qt.NoPen` is applied explicitly in `_pen()` on `_ShapeItem`, `LineItem`/`ArrowItem`, `_PolyItem` and inline in `FreehandItem.paint()`, since `QPen(width=0)` is a cosmetic hairline in Qt, not "no pen" -- without the explicit style switch a filled rectangle at stroke 0 would still show a thin 1px outline. Tests in `tests/test_zero_stroke.py`.
- **Freehand tool stays armed across strokes** (item 13): `PdfScene._push_add` unconditionally returned to the Select tool after every insertion (a deliberate PowerPoint-style affordance for one-shot shapes) -- but a Freehand stroke isn't one-shot. It now skips that return when the tool that produced the item is `Tool.FREEHAND`; Escape or picking another tool still leaves it. Tests in `tests/test_freehand_tool.py`.
- **Application icon**: `resources/icons/app.ico` / `app.png` (a simple rounded slate-blue tile, white "A", red accent bar) generated via a one-off Pillow script (not checked in). `app.py` sets it via `QApplication.setWindowIcon`, resolving the path the same `sys._MEIPASS`-aware way as `services/theme.py::_themes_dir`. `build.py` passes `--icon` (new `_icon_args()`) so the built `.exe` itself carries it (Explorer/taskbar); the existing `_resource_args()` already bundles the whole `resources/icons/` directory for the runtime `setWindowIcon` call.

### Lot 2 -- single tool-selection surface + toolbar quick styles (2026-07-02)

Items 1-2: tools were pickable in two places (top toolbar + left dock), and
the toolbar had no Office-style quick controls.

- **Toolbar tool buttons removed**: `_TOOLBAR_TOOLS`, the per-tool
  `QAction`s (`_tool_actions` / `_tool_action_group`) and their three
  consumer sites (icon theming, has-doc enabling, `_on_tool_changed`
  check-sync) are gone from `main_window.py`. The left `ToolPalette` dock is
  now the **only** place to pick a drawing tool -- and it gained the
  **GD&T frame** entry, which until now existed only in the toolbar (the
  dock/toolbar lists had silently diverged).
- **Toolbar quick style controls**, Office-style, in the freed space:
  a color action showing the current drawing color as a swatch (click opens
  `QColorDialog` and pushes into `ToolController.set_color`) and a stroke
  width combo over `STROKE_WIDTHS`. Both are two-way synced with
  `ToolController` signals, so the dock and the toolbar always agree; a
  non-preset width set elsewhere leaves the combo untouched (it only offers
  presets). The swatch painter moved from `tool_palette.py::_color_swatch`
  to a shared `views/icons.py::color_swatch_icon`.
- Tests: `tests/test_main_window_wiring.py::test_tools_live_only_in_the_dock_palette`
  and `::test_toolbar_quick_style_controls_follow_controller`.

### Lot 3 -- unsaved-changes prompt (2026-07-02)

Item 11: closing the PDF or the app silently dropped unsaved annotations.

- **Dirty definition**: `MainWindow._has_unsaved_changes()` = any per-page
  `QUndoStack` not `isClean()` (no hand-rolled `_dirty` flag). Each stack's
  `cleanChanged` is connected to `_update_modified_flag` -- a **bound
  method**, not a lambda, because PySide only auto-disconnects bound-method
  connections when the receiver's C++ object dies (a lambda kept firing
  into a deleted MainWindow during test teardown). `_save_to()` calls
  `setClean()` on every page stack on success, which also keeps
  `_reopen_after_save` -> `_open_path` from re-prompting.
- **Native title marker**: `_refresh_window_title` puts Qt's `[*]`
  placeholder in the title; `setWindowModified` drives the `*`.
- **Prompt** (`_confirm_discard_changes`): Save / Discard / Cancel
  `QMessageBox.warning`, returned as a may-proceed bool. Save writes
  directly via `_save_to(self._doc.path)` with **no** second
  overwrite-confirmation (the prompt already named the file). Guards three
  paths: `closeEvent` (Cancel -> `event.ignore()`), File > Close
  (`act_close` now targets `_on_close_requested`, a guarded wrapper --
  `_on_close` itself stays prompt-free for internal callers like
  `_open_path`), and the top of `_open_path` (opening another PDF over a
  dirty document).
- **Test infrastructure**: `tests/conftest.py` gained an autouse fixture
  patching `QMessageBox.warning` to return Discard -- dozens of existing
  tests push commands then `win.close()` in a `finally:`, which would
  otherwise hang the offscreen suite on the new modal. Prompt-specific
  tests re-patch with their own answer. Tests in
  `tests/test_close_prompt.py` (8 cases: dirty tracking, title marker,
  Cancel/Discard/Save on closeEvent, open-over guard, File > Close guard,
  save-marks-clean).

### Lot 4 -- page thumbnails + visible page navigation (2026-07-02)

Item 10: nothing on screen said "this PDF has more pages", and switching
pages required the Page menu or shortcuts.

- **`PageRenderer.render_thumbnail(page_index, max_px)`**
  (`services/pdf_render.py`): renders directly at the reduced zoom (never
  through `render()`, whose full-DPI output would be huge for an A0 page)
  into a plain devicePixelRatio-1 pixmap -- thumbnails are UI icons, not
  scene content. Dedicated per-document `_thumb_cache` dict: sharing the
  3-entry full-page LRU would evict an A0 render per thumbnail.
- **`views/page_thumbnails.py::PageThumbnailDock`**: left-docked
  `QListWidget` in icon mode, one item per page ("Page N" + thumbnail,
  `THUMB_MAX_PX` = 140). Rendering is **lazy, one page per event-loop
  tick** (a 0 ms single-shot `QTimer` chain over a `_pending` queue), so a
  200-page open never blocks the UI; items show a drawn placeholder until
  their render lands. `pageClicked(int)` -> `MainWindow._show_page`;
  `set_current_page` keeps the highlighted row in sync on any page switch;
  `set_document(None)` clears on close.
- **View > Panels** submenu: `toggleViewAction()` for all four docks
  (tools, pages, annotations, properties) -- previously dock visibility
  was only restorable via saved window state.
- **Status-bar page indicator is now a flat `QPushButton`** ("Page 2 / 7",
  pointing-hand cursor, disabled without a document) that opens the Go to
  Page dialog on click, instead of a passive QLabel.
- Tests in `tests/test_page_thumbnails.py` (5 cases: fit+cache, one item
  per page + lazy drain, click-to-navigate + row sync, cleared on close,
  button indicator wiring).

### Lot 5 -- welcome screen (2026-07-02)

Item 12: launching without a PDF showed an empty gray viewport.

- **`views/welcome_screen.py::WelcomeScreen`**: title + Open PDF / New
  blank document buttons + a recent-documents grid (`QListWidget` icon
  mode) with **real first-page thumbnails**. Signals only
  (`openRequested`, `blankRequested`, `openPathRequested(str)`,
  `removePathRequested(str)`); MainWindow owns all the behavior.
  Thumbnails are rendered lazily one file per event-loop tick (same
  QTimer-chain pattern as the page-thumbnail dock) since each one means
  opening the PDF; an unopenable file keeps a red-crossed placeholder and
  offers "Remove from list" in its context menu (`RecentFiles.remove`).
- **Central `QStackedWidget`** (`MainWindow`): welcome page vs `PdfView`,
  switched in `_open_path` (success) and `_on_close`. The welcome recent
  grid refreshes on `RecentFiles.changed`, but only while it is the
  visible page (thumbnail re-renders are not free).
- **New blank document** (`_new_blank_document`): a real single-page A4
  scratch PDF written to a fresh temp dir as `Untitled.pdf`, opened via
  `_open_path(..., add_to_recent=False, untitled=True)`. While
  `_is_untitled` is set, Save (Ctrl+S) and the unsaved-changes prompt's
  Save button both redirect to Save As (`_on_save_as` now returns a
  success bool for that); `_reopen_after_save` then reopens the chosen
  real path with the flag cleared and adds it to recents. Temp dirs are
  left to the OS temp cleanup.
- **Test gotcha documented**: `QSettings` persists nothing until
  org/app names are set (production sets them in `app.main`); MainWindow
  tests that assert on `RecentFiles` need the same isolated-QSettings
  fixture as `tests/test_recent_files.py`. Tests in
  `tests/test_welcome_screen.py` (8 cases).

### Lot 6 -- merged tools with post-draw variants (2026-07-02)

Item 3: related tools (rectangle/cloud, line/arrow/callout,
polyline/polygon) each had their own palette button; the user wanted one
button per family plus an option to switch variants.

- **Palette** (`tool_palette.py::_TOOL_LABELS`): 10 buttons instead of 14 --
  Cloud, Line, Polygon and Callout buttons removed. "Line / Arrow" arms
  `Tool.ARROW` (a plain line is an arrow with both end styles set to None,
  already switchable in the dock); rectangle/cloud and polyline/polygon
  variants are switched after drawing. The `Tool` enum members and the
  scene's drafting branches for the removed tools are **kept** (persistence
  still reconstructs those kinds from PDFs, and tests/clipboard use them).
- **Conversions** (`controllers/convert.py`): pure functions building a
  detached converted item (rect<->cloud, polyline<->polygon, line<->arrow,
  line/arrow->callout, callout->arrow). Deliberately lossy where kinds
  differ: rect->cloud drops the text label + corner radius,
  polygon->polyline drops the fill, callout->arrow drops the text.
  line<->callout converts endpoints through page coordinates (item pos
  folded in) so a previously moved item lands exactly in place; the arrow
  head end (p2) becomes the callout tip, the tail anchors the text box.
- **`ReplaceAnnotationCommand`** (`controllers/commands.py`): generic
  swap-old-for-new under the same parent, selection follows the visible
  item (so the Properties dock rebuilds onto the swapped-in instance);
  one undo step.
- **Properties dock**: single-selection variant rows -- "Outline:
  Straight/Cloud" combo on rect/cloud, "Closed shape" checkbox on
  polyline/polygon, "Label" line edit on line/arrow (typing text converts
  to a callout -- "a callout is just an arrow with text", per user), and a
  "Convert to arrow (drop text)" button on callouts. A double
  `editingFinished` on the label field is a no-op (the converted-away item
  is detected by `scene() is None`).
- Tests in `tests/test_change_kind.py` (10 cases: per-pair round-trips
  incl. style/fill/pos, noop-when-already-target, command undo/redo,
  dock-level outline + label conversions).

### Lot 7 -- bend points on lines and arrows (2026-07-02)

Item 5: elbows/kinks on leader lines. Deliberately implemented **inside
`LineItem`** (per user decision) rather than unifying with `_PolyItem` --
lines/arrows keep their 2-endpoint model plus an ordered `_bends` list.

- **`LineItem`** (`views/items/lines.py`): `bends()/set_bends()`,
  `path_points()` (p1, bends..., p2), `insert_bend_near(local_pos)`
  (projects the click onto the nearest segment and inserts there),
  `remove_bend(i)`, `bend_at(local_pos)`. The shaft paints as a
  `drawPolyline` over `path_points()`; `boundingRect` spans them.
  Handles: `HandleRole.P1`/`P2` plus **int-keyed** handles per bend (the
  same opaque-role pattern `_PolyItem` uses). `geom_snapshot()` grew from
  `(p1, p2)` to `(p1, p2, bends)`; `apply_geom` still accepts legacy
  2-tuples. `ArrowItem` orients each head along its own **end segment**
  (p1->first bend / last bend->p2), not the p1->p2 chord; `clone()` on
  both copies bends.
- **Context menu** (`MainWindow._show_context_menu`): right-clicking a
  line/arrow offers "Add bend point" (at the click's projection), or
  "Remove bend point" when the click lands on one (`bend_at`). Both go
  through the existing `ResizeCommand` (snapshots carry the bends), so
  add/remove/drag are all single undo steps.
- **Shift constraints** (`PdfScene._constrain_resize`): dragging a bend
  with Shift locks its segment from the previous path point to horizontal
  or vertical (new `_axis_lock_point`, larger-displacement axis, as the
  user asked -- "forcer la verticalité/horizontalité des portions"). An
  endpoint of a *bent* line gets the same H/V lock relative to its
  adjacent bend (the p1->p2 chord angle from item 7's fix is meaningless
  once the shaft has kinks); straight lines keep the item-7
  angle-preserving projection.
- **Persistence** (`services/pdf_export.py`): a bent line/arrow saves as
  a native **PolyLine** annot (a Line annot only holds 2 points) with
  `/LE` line-ends (PolyLine supports them, so Acrobat still shows the
  arrowhead) and a `"bent": "line"|"arrow"` tag in the `/Subject` JSON;
  the PolyLine reader rebuilds the Line/Arrow item from first/last
  vertices + middle bends when the tag is present. Straight lines are
  byte-identical to before (still native Line). The Arrow and Line writer
  branches were merged into one (`isinstance(item, LineItem)` covers
  both).
- Tests in `tests/test_line_bends.py` (11 cases: insert/project, path
  order, handles, remove/hit-test, snapshot round-trip incl. legacy
  2-tuple, clone, Shift bend + endpoint locks, PDF round-trip with ends,
  straight-line regression). PyMuPDF gotcha: keep the `page` object
  alive while iterating `page.annots()` or annots unbind.

All 13 items of Discussion #1 are now implemented (lots 1-7), plus the
Ctrl+G shortcut-collision fix found during investigation.

### Lot 8 -- Discussion #1 follow-up comment (2026-07-03)

Four new items from the user's 2026-07-02 comment on Discussion #1, plus
the original item 8 (property icons unreadable in dark theme), which the
first seven lots had left uncovered.

- **Toolbar quick styles restyle the selection too**: picking a color or
  stroke width in the top toolbar now (a) sets the drawing default in
  `ToolController` as before AND (b) applies to the currently selected
  annotations as one undoable `ChangeColorCommand`/`ChangeStrokeCommand`
  (`MainWindow._on_quick_color_picked` / `_on_quick_stroke_picked`),
  matching Office. Deliberately wired at the toolbar-handler level, not
  on the `ToolController` signals -- a dock palette click still only
  changes the default, so programmatic controller changes never restyle
  a selection as a side effect.
- **Office-style color picker** (`views/color_picker.py`):
  `ColorPickerMenu` -- a compact popup with a 16-swatch standard grid
  (via `QWidgetAction`) and a "More colors..." entry that opens the full
  `QColorDialog` only on demand. `popup_color_picker()` helper anchors it
  under a button or at the cursor. Replaces the direct QColorDialog in
  the toolbar color control, the selection color action (Edit menu /
  context menu / floating selection toolbar) and the Properties dock
  color buttons. The left dock palette keeps its inline swatches + "..."
  full dialog (it already is a quick palette).
- **Shift-resize keeps the shape's original aspect ratio**: the
  2026-06-29 behavior forced a *square* on rect/ellipse/cloud corner
  drags. `_constrain_resize` now reads the pre-drag rect from
  `_resize_snapshot` (live rect is already mutated mid-drag) and scales
  it homothetically via the new `_scale_keep_ratio` helper (dominant-axis
  scale, sign-preserving). Drafting a *new* shape with Shift still makes
  a square (`_square_from` unchanged) -- there is no original ratio yet.
- **Format Painter icon redesigned** (`views/icons.py`): large diagonal
  brush (handle / ferrule / bristle wedge) laying down a stroke,
  readable at 20 px; the old design's tiny tuft was ambiguous.
- **Dark-theme property icons** (original item 8): the dash / line-end /
  align combo icons in the Properties dock were always painted
  near-black (`_DEFAULT_FG`) and vanished on the dark theme. The dock
  now carries an `_icon_color` pushed by MainWindow
  (`PropertiesDock.set_icon_color`, same contract as
  `ToolPalette.set_icon_color`, refreshed on every theme switch) --
  QPalette could not be used because the QSS themes do not update it.
- Tests in `tests/test_quick_styles.py` (9 cases: quick color/stroke
  with and without selection + undo, aspect-ratio preservation (2:1 and
  square), `_scale_keep_ratio` math, picker menu signal + entries,
  dock icon-color plumbing).
- **Stroke width: typed value + ladder stepping** (2026-07-04 follow-up):
  the toolbar's 3-choice stroke dropdown became
  `views/stroke_spin.py::StrokeSpinBox` -- a QSpinBox whose up/down
  arrows walk a PowerPoint-font-size-style ladder (`STROKE_LADDER`: 1-6
  by 1, then 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56, 70, 84,
  100) via a `stepBy` override; any value can still be typed freely
  (off-ladder values snap to the nearest rung on the next step).
  `setKeyboardTracking(False)` so typing only commits on Enter/focus
  loss. Deployed in three places: the top toolbar (replacing the combo,
  still restyles the selection through `_on_quick_stroke_picked`), the
  Tools dock (replacing the three fixed preset buttons; both sync from
  `strokeChanged` under `blockSignals` to avoid feedback loops) and the
  Properties dock Stroke field (`minimum=0` keeps the "None" zero-stroke
  option; below the ladder floor the down-arrow decrements plainly to
  reach 0). The Edit-menu "Change Stroke" dialog switched from a 3-item
  list to a free integer `QInputDialog.getInt` (0-100).
  `config.STROKE_WIDTHS` now only feeds the ToolController default.
  Tests: 4 new cases in `tests/test_quick_styles.py` (ladder stepping,
  off-ladder snapping, zero-minimum behavior, dock palette sync) and an
  updated toolbar wiring test.
- **Line/arrow rework: end labels + endpoint menu** (2026-07-04
  follow-up): three user requests landed together.
  - **Labels on BOTH ends, bend-proof**: `LineItem` now carries
    `start_label`/`end_label` strings rendered just past each endpoint
    (`_label_rects`: offset along the OUTWARD direction of that end's
    segment, so bent lines push labels away from their last segment; an
    ArrowItem widens the gap past its head). This **replaces the Lot 6
    "type a Label -> becomes a callout" conversion**, which could only
    label one side and dropped bends. `convert.line_to_callout` was
    removed; the Properties dock shows two live-wired "Start label" /
    "End label" fields instead. `CalloutItem` remains (reading old PDFs,
    "Convert to arrow" button); `line_to_arrow`/`arrow_to_line` now copy
    bends + labels via `LineItem._copy_line_extras_into`.
  - **"Extremity shape" context submenu**: right-clicking within 9 px of
    a line/arrow endpoint offers the eight `EndStyle`s (icons + checked
    current) for THAT endpoint; bend actions keep covering clicks
    elsewhere on the item. On an ArrowItem it pushes a
    `ChangePropsCommand`; on a plain LineItem it promotes to an
    ArrowItem (`ReplaceAnnotationCommand`, both ends None except the
    chosen one -- bends/labels survive). Choosing None on a plain line
    is a no-op. `END_STYLE_LABELS` moved to `model/styles.py`, shared
    with the dock combos.
  - **Persistence**: labels ride in the /Subject JSON
    (`start_label`/`end_label`) for both straight (Line) and bent
    (PolyLine) exports. Native Line/PolyLine annots cannot display text
    and their /Rect is vertex-derived (a rasterized appearance would be
    clipped), so each label also writes a **companion FreeText annot**
    (`_write_line_label_companions`, tagged `"companion": "line_label"`
    in its JSON) purely for external viewers; `read_annotations` drops
    any companion-tagged annot and Annoter re-renders labels from the
    line's own payload.
  - Tests: `tests/test_line_labels.py` (8 cases: bounds/direction/empty,
    endpoint hit-test, arrow restyle undo, line promotion incl.
    bends+labels, None no-op, PDF round-trip counting companions) and
    updated `tests/test_change_kind.py` (in-place label editing, no
    conversion).
- **Discussion #1 third comment (2026-07-04)**: three more items.
  - **Pill "tiny square" bug fixed**: the selection pill sometimes
    rendered as a small empty square (screenshot in the discussion).
    Cause: `adjustSize()` on a hidden, just-rebuilt widget can act on a
    stale sizeHint. `set_context` now forces a synchronous relayout
    (`layout.invalidate()` + `activate()` + `resize(sizeHint())`).
    Regression test asserts a rebuilt hidden pill already has real size.
  - **Datum triangles as extremity shapes** (ISO 5459):
    `EndStyle.TRIANGLE` (hollow) and `TRIANGLE_FILLED` -- flat base
    sitting ON the endpoint, perpendicular to the shaft, apex toward
    the line (equilateral proportions). The hollow variant fills opaque
    white so the shaft doesn't show through (GD&T cell convention).
    Rendered in `ArrowItem._draw_end` and the `end_icon` preview;
    exported as `/LE ClosedArrow` (closest native style; `_PDF_TO_END`
    is built BEFORE the triangle aliases so foreign ClosedArrow annots
    still read back as CLOSED_ARROW; ours restore the exact style from
    the JSON). With a boxed start label this composes a full datum
    feature symbol.
  - **Text outlines (box / ellipse)**: new `TextBorder` enum + shared
    `TEXT_BORDER_LABELS`. `TextAnnotationItem` (and Callout) gained a
    `border` property drawn around the padded `content_rect` -- the
    ellipse is the circumscribed one (`circumscribed_ellipse_rect`,
    semi-axes x sqrt(2)) so text corners are never clipped. Line/arrow
    end labels gained the same via `label_border` (one setting for both
    labels). Properties dock: "Border" combo on text rows, "Label
    border" combo on line rows. Persistence: `border`/`label_border` in
    the JSON payload; a BOX also maps to the native FreeText
    border_color (text items and label companions) so Acrobat shows it;
    ellipses are Annoter-only. Tests in `tests/test_line_labels.py`.
  - **Labels sit flush against the line end** (follow-up report, two
    passes): the original placement centered the label at a worst-case
    diagonal clearance and used the ARROWHEAD gap for both ends even
    when an end had no head, leaving the datum frame floating far from
    the line. `_label_rects` now computes the exact ray-to-outline
    boundary distance and `_label_gap(start)` is per-end AND
    border-aware: a BORDERED label on a bare end touches the line (gap
    0 -- the frame edge lands exactly on the endpoint), plain text
    keeps stroke/2 + 3 px, and an end with a decoration still clears
    its head.
  - **"Tiny square" root-caused to the measurement HUD** (2026-07-05
    report, square still appearing after the pill fix): the stuck small
    DARK square matches `MeasurementHud`'s styling, not the pill.
    `PdfView.mouseReleaseEvent`'s zoom-window and panning branches
    returned early and skipped the HUD hide at the bottom, leaving it
    on screen. The hide now runs first thing on every release; `_place`
    also refuses to show an empty-text HUD. Belt and braces on the pill
    too: its layout uses `SetFixedSize` (the proven GdtInlineEditor
    fix), so it can never render at a stale size.
  - **Per-label frames** (2026-07-05 report: framing one label framed
    both): `label_border` split into `start_label_border` /
    `end_label_border` (the old accessors remain as set-both / read-
    start conveniences). `_label_rects` now yields (rect, text, border)
    triples; gap, outline, companion border and bounds are computed per
    label. Dock shows a "Start frame" / "End frame" combo under each
    label field. Persistence writes per-end keys; the legacy single
    `label_border` key still reads back onto both ends.
  - **Round outline is a true circle** (follow-up report):
    `circumscribed_ellipse_rect` became `circumscribed_circle_rect`
    (diameter = the text rect's diagonal) for both standalone text
    borders and line end labels; the flush-contact math uses the circle
    radius directly. `TextBorder.ELLIPSE` keeps its enum value for
    persistence compatibility but displays as "Circle".
### Document-operations batch (2026-07-16, Discussion #2)

Six items from the "Pour plus tard" discussion, landed as one lot:

- **Images as input** (`MainWindow._open_path` + `_image_to_scratch_pdf`):
  opening/dropping a png/jpg/tif/bmp converts it to a temp single-page
  PDF (`fitz.convert_to_pdf`) opened through the existing
  scratch-document path (`untitled=True`, Save redirects to Save As,
  never enters recents). Open dialog filters and drag&drop
  (`_is_openable_file`) extended.
- **Merge** (`_on_insert_pdf`): File > Insert Pages from PDF appends via
  `raw.insert_pdf`; the inserted pages' own annotations are re-read into
  editable items (edit callbacks re-attached); jumps to the first
  inserted page.
- **Page reorder**: the thumbnail dock is `InternalMove` drag&drop;
  `rowsMoved` converts Qt's pre-removal destination to the final index
  and emits `pageMoved(from, to)` DEFERRED (singleShot) so the drop
  finishes before MainWindow rebuilds the list. `_on_page_reordered`
  maps the final index to `move_page`'s insert-before semantics --
  **gotcha: "move to end" must be `to=-1`; an out-of-range target makes
  PyMuPDF hang**, not raise -- then remaps `_page_items`/`_page_stacks`
  through the old->new order and follows the current page.
- **Document resize** (`_on_resize_document`): File > Resize Document
  offers A0-A4 (per-page orientation preserved). Each page is rebuilt at
  the target size with `show_pdf_page` scaled content; the rebuilt pages
  are swapped INTO the same fitz document (append + delete originals) so
  the path and Save are unaffected. Every item implements
  `scale_geometry(s)` (base scales pos+stroke; subclasses scale rects,
  points, bends, tips, corner radius, font sizes) so annotations follow
  their page exactly. Foreign annot types that `read_annotations` cannot
  map are lost by the rebuild -- documented limitation.
- **Export as images** (`_on_export_images`): renders a throwaway copy
  of the document carrying the CURRENT editor items (`tobytes` +
  `write_annotations`), then encodes via **Pillow** (first real use of
  the dependency): multi-page TIFF (LZW) in one file, or one PNG/JPEG
  per page suffixed `_pN`, at a user-chosen DPI (50-600).
- **Grayscale display** (View > Grayscale Page): `PageRenderer` gained
  `set_grayscale` (renders with `fitz.csGRAY`, `Format_Grayscale8`) and
  a shared `clear_cache()`/`_to_qimage()`; display-only -- the PDF is
  untouched and annotation items keep their colors on top. The setting
  survives document switches (re-applied on renderer creation).
- **Structural dirty flag**: inserted/moved/resized pages live in the
  raw document, not in any undo stack, so `_doc_structure_dirty` (set by
  `_mark_structure_dirty`, cleared on open and successful save) is OR-ed
  into `_has_unsaved_changes` -- the close prompt stays honest about
  them. All structural ops share `_stash_current_page_items()` +
  `_refresh_after_structure_change()` (cache clear, dirty, thumbnails,
  re-show).
- Tests in `tests/test_doc_ops.py` (10 cases).

- **Selection pill rework: contextual actions + stability**
  (2026-07-04 follow-up): the floating selection toolbar was unstable
  (only repositioned on selection change and zoom/scroll -- never
  during a drag -- and its color/stroke chips never refreshed) and
  redundant (color/stroke already live in the top toolbar). Reworked:
  - **Contextual content** (`views/selection_toolbar.py` rewritten):
    the pill now shows type-specific actions -- Edit (text / sticky
    note / GD&T, routed to the right editor by
    `MainWindow._edit_selected`), Outline straight/cloud + Fill toggle
    (rect/cloud), Ends menu (both endpoints, all 8 styles, reuses
    `_set_endpoint_style`) + "+ Bend" (inserted at the midpoint of the
    longest segment) for lines/arrows, Closed toggle
    (polyline/polygon), and Group / Ungroup / Align menu (MainWindow's
    own align QActions) for multi-selections. Duplicate/Delete close
    every variant; color/stroke chips are gone. The widget stays dumb
    (semantic signals; MainWindow owns behavior via the existing
    convert/replace/ChangeProps machinery). `_clear()` must
    `setParent(None)` before `deleteLater`, or the old buttons linger
    until the event loop runs.
  - **Stability**: new `PdfScene.interactiveDragChanged(bool)` signal
    (emitted via `_notify_drag` at every gesture start -- item drag,
    resize, group drag, Ctrl-duplicate drag -- and once on left-button
    release); MainWindow hides the pill during the gesture and
    re-shows it repositioned on release, Figma-style. The pill is also
    rebuilt on every `QUndoGroup.indexChanged`, so its
    Fill/Closed/Ends states track dock edits, toolbar quick styles and
    undo/redo.
  - Tests: `test_selection_toolbar_is_contextual`,
    `test_selection_toolbar_hides_during_drag` (replacing the old
    style-chip test) in `tests/test_main_window_wiring.py`.
- **Tools dock is tools-only** (2026-07-04 follow-up): the dock's Color
  and Stroke sections were removed -- both functions live exclusively in
  the top toolbar quick controls now (one place per function, per user
  feedback). `ToolPalette` shrank to the tool grid + `set_icon_color`;
  its custom-color dialog, swatch row, stroke spin and the associated
  `colorChanged`/`strokeChanged` subscriptions are gone.
  `config.DEFAULT_PALETTE` still seeds the ToolController default color.
  Test: `tests/test_quick_styles.py::test_dock_palette_is_tools_only`.
- **Soft angle magnetism on line/arrow endpoints** (2026-07-04
  follow-up): dragging an endpoint WITHOUT Shift now magnets the
  segment onto 0/45/90-degree multiples when it comes within
  `_ANGLE_MAGNET_DEG` (4 degrees) of one, and stays completely free
  otherwise -- `PdfScene._soft_snap_angle`, a magnet, unlike
  `_snap_angle` which is Shift's hard lock. Applied both while
  resizing an existing endpoint (`_soft_snap_endpoint`, anchored on
  the endpoint's ADJACENT path point so a bent line straightens the
  segment actually being dragged) and while drafting a new line/arrow
  (`_update_draft`). Priority order per move: shape-endpoint snap >
  Shift hard constraint > angle magnet; Alt disables the magnet like
  every other snapping. Tests in `tests/test_resize_constrain.py`
  (magnet in range, free beyond range, Alt off, bent-line anchor).
- **Open arrow head is a true chevron** (2026-07-04 follow-up): the
  OPEN_ARROW head was drawn with `drawPolygon`, which closes the
  triangle and adds a visible bar across the back of the head. It is
  now `drawPolyline` (two barbs only) in all three renderers:
  `ArrowItem._draw_end`, `CalloutItem._draw_arrow_head` and the
  `end_icon` preview in `views/icons.py`. CLOSED_ARROW keeps the filled
  polygon. The PDF side needed no change -- the native `/LE /OpenArrow`
  is already rendered as a chevron by Acrobat/MuPDF.

### Inline text editing keyboard fixes + Dimension annotation (2026-08-01)

- **Inline text editing was fighting `PdfView` for the keyboard**: the
  view's `keyPressEvent` intercepted Space unconditionally (pan
  shortcut) and Left/Right/Up/Down unconditionally (nudge selection)
  even while a `TextAnnotationItem`'s inner `QGraphicsTextItem` had
  scene focus, so it was impossible to type a space or move the
  cursor with the arrow keys. Worse, the "select an item and just
  start typing" convenience path (`_maybe_start_typing`) re-fired on
  *every* keystroke rather than only the one that entered edit mode,
  force-moving the cursor to the end of the text before each
  insertion -- so editing anywhere but the very end of the string was
  impossible (typing "a", "c", Home, Right, "b" produced "acb" instead
  of "abc"). Fixed by adding `PdfView._is_editing_text()` (true
  whenever `scene.focusItem()` is not None -- only text/shape-label
  inner items ever call `setFocus()`) and short-circuiting
  `keyPressEvent` straight to `super()` while it holds, so Qt's normal
  scene -> focus-item delivery handles every key exactly like a native
  text editor. (Investigated and ruled out: Backspace/Delete/Ctrl+A/
  C/X/V/Z/Y do NOT need a `ShortcutOverride` override alongside this --
  `QGraphicsTextItem` already claims those correctly via its internal
  `QTextControl`, confirmed by reverting the guard and re-running the
  regression tests.) Tests in `tests/test_text_edit_keys.py`.
- **New Dimension annotation type**: nominal value + optional prefix
  (Ø/R/SØ/SR/□) + optional tolerance, either symmetric (`±0.05`, one
  line) or bilateral/offset (`+0.10` / `-0.05`, stacked at a reduced
  font size, each line keeping its own natural width). Modeled on the
  GD&T architecture but deliberately un-boxed (a dimension value is
  plain text on a real drawing, unlike an ISO 1101 feature control
  frame): `model/dimension.py` (`DimensionState`/`DimensionPrefix`/
  `ToleranceMode`, serializable), `views/items/dimension.py`
  (`DimensionAnnotationItem`, `QFontMetricsF` layout, corner-resize
  scales `font_size()` like GD&T), `views/dimension_editor.py`
  (`DimensionInlineEditor`, same floating single-row
  commit-or-cancel-only contract as `GdtInlineEditor`). Wiring in
  `main_window.py` (`_on_dimension_placement` /
  `_open_dimension_editor` / `_commit_dimension_editor` /
  `_cancel_dimension_editor`) and `pdf_scene.py`
  (`dimensionPlacementRequested`) mirror the GD&T click-to-place /
  double-click-to-reopen / live-preview-without-undo-until-commit
  flow exactly, including the pre-existing quirk that cancelling an
  edit on an *existing* item restores the old state directly (no undo
  entry, since the live preview never pushed one). Persistence in
  `services/pdf_export.py`: `Square` annot + JSON blob in `Contents`
  (`_DIM_TAG`/`_DIM_CONTENT_PREFIX`) plus a rasterized appearance
  stream, `content_rect()` (not `boundingRect()`) anchors the saved
  `/Rect` -- identical scheme to GD&T. New `Tool.DIMENSION`, palette
  entry, toolbar icon. Tests: `tests/test_dimension.py`,
  `tests/test_dimension_editor.py`, Dimension cases added to
  `tests/test_persistence.py`. Built as two parallel background agents
  (editor+icon; wiring+persistence) against a hand-written core
  model/item, then integration-verified end-to-end through a real
  `MainWindow` (place, edit, undo/redo, re-edit, cancel-restores-state,
  save+reopen round-trip, untouched-placement rollback).

### Known remaining issues

- Page rotation is still view-only (see M4 notes).
- `PageRenderer` renders pages with `annots=True`, so saved annotations are baked into the page pixmap *and* drawn again as editable items on top. The two coincide exactly, so this is invisible in practice, but rendering with `annots=False` would require keeping unknown foreign annot types visible some other way.
- Rendering is synchronous on the UI thread; an A0 page render blocks the UI for its duration. A background-render + progressive-display pass is the next perf candidate for >100 MB files.
- Callout leaders are not drawn by MuPDF-based viewers (only Acrobat-class viewers honor `/CL`); making the leader visible everywhere would require authoring the FreeText `/AP` stream (text + line) with an expanded BBox, as the GD&T appearance does.
- Groups (Ctrl+G) are session-only: membership lives in `PdfScene._groups`, not in the PDF, so it does not survive a save/reopen cycle. Persisting it would need a new JSON key (e.g. a shared group id in each member's `/Subject` payload) plus reconstruction in `read_annotations`.
- Smart guides only engage for a single selected item; group drags get axis-lock but not guide-snapping (snapping each item of a group independently could pull its relative layout apart).
- A selected group shows both the new unified dashed box *and* each member's own per-item selection handles -- mildly busy visually, but avoids a larger refactor of `AnnotationItem`'s selection-chrome painting to suppress the per-item markers when a full group is selected.
- M5 clean-VM / USB-stick verification has still not been executed.

## Roadmap / future work

### How to add a new annotation tool (checklist)

The four post-v1 tools (Cloud, Polyline/Polygon, Callout, Sticky Note)
all touch the same set of files. Use this as the recipe for the next one:

1. **Enum**: add the member to `controllers/tools.py::Tool` (the
   palette/toolbar/icons iterate the enum, so ordering here is the
   display order grouping).
2. **Item**: add a `*Item(AnnotationItem)` (or subclass an existing item
   to inherit behavior, as Callout does from text and Cloud from the
   shape base) in `views/items/`. Implement `boundingRect`, `paint`,
   `clone`, and -- if resizable -- `handle_positions` / `apply_resize` /
   `geom_snapshot` / `apply_geom`. Handle "roles" are opaque to the
   scene, so non-`HandleRole` keys (e.g. vertex indices) are fine.
   Export it from `views/items/__init__.py`.
3. **Drafting** in `views/pdf_scene.py`: drag-based tools go through
   `_make_draft_item` / `_update_draft` / `_draft_is_meaningful`;
   multi-click tools follow the polyline pattern (`_poly_*`); tools that
   need MainWindow-level UI (text edit, GD&T frame, sticky note) emit a
   `*PlacementRequested` signal and defer.
4. **UI surfaces**: `views/tool_palette.py::_TOOL_LABELS` (the dock is
   the single tool-selection surface since Discussion #1 Lot 2 -- the
   toolbar no longer lists tools), a glyph in
   `views/icons.py::tool_icon`, and the crosshair set in
   `views/pdf_view.py::set_tool_cursor_for`. Icons are repainted on
   theme change automatically (no extra wiring).
5. **Properties dock** (`views/properties_dock.py`): add an
   `issubclass` branch if the item has editable props beyond
   color/stroke/dash.
6. **Persistence** (`services/pdf_export.py`): map to a native
   `fitz.Annot` subtype in `_write_item`, reconstruct in
   `_annot_to_items`, and -- for anything MuPDF pads or cannot express
   -- stash the authoritative geometry/props in the `/Subject` JSON
   (`_props_payload` / `_apply_props_to_item`). Two of our items share
   the `Polygon` subtype (Cloud vs Polygon), disambiguated by a `poly`
   tag; keep new shared-subtype items disambiguable the same way.
7. **Tests**: a round-trip in `tests/test_persistence.py` plus an
   interaction test (`tests/test_poly.py`, `tests/test_callout.py`, or
   `tests/test_main_window_wiring.py` for MainWindow-driven flows).
8. Document the tool in this file's post-v1 section.

### Backlog of candidate Acrobat-style tools (not yet built)

Priority reflects value on mechanical drawings, not effort.

- ~~**Stamps**~~ DONE (2026-06-16, see post-v1 section). Possible
  follow-ups: a pre-placement stamp picker in the palette (currently the
  preset is chosen after placement via the dock), date/dynamic stamps,
  and slight rotation for a more rubber-stamp look.
- **Callout / sticky-note appearance for MuPDF viewers**: author the
  FreeText `/AP` so the leader is visible everywhere, not only in
  Acrobat (see Known issues).
- **Dimension / measurement** (distance, with scale calibration): high
  value in mechanical context but explicitly out of v1 scope; needs a
  calibration UI. Native `Line`/`PolyLine` with a measure dictionary.
- **Weld symbols** (ISO 2553): same composite-item approach as GD&T.
- **File attachment** (`FileAttachment`) and **image stamp**: lower
  priority for single-user plan review.
- **Text markups** (Highlight / Underline / StrikeOut / Squiggly): low
  value on scanned/vector plans with no selectable text layer; only
  worth it if an OCR/text layer ever lands.
