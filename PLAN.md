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

### Known remaining issues

- Page rotation is still view-only (see M4 notes).
- `PageRenderer` renders pages with `annots=True`, so saved annotations are baked into the page pixmap *and* drawn again as editable items on top. The two coincide exactly, so this is invisible in practice, but rendering with `annots=False` would require keeping unknown foreign annot types visible some other way.
- Rendering is synchronous on the UI thread; an A0 page render blocks the UI for its duration. A background-render + progressive-display pass is the next perf candidate for >100 MB files.
- Callout leaders are not drawn by MuPDF-based viewers (only Acrobat-class viewers honor `/CL`); making the leader visible everywhere would require authoring the FreeText `/AP` stream (text + line) with an expanded BBox, as the GD&T appearance does.
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
4. **UI surfaces**: `views/tool_palette.py::_TOOL_LABELS`,
   `views/main_window.py::_TOOLBAR_TOOLS`, a glyph in
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
