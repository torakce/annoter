"""MainWindow: hosts the central PdfView and the dockable panels.

M1 scope: file open/close, MRU, page navigation, zoom (incl. zoom
window), pan, page rotation, drag&drop, status bar. No annotations.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, Qt, QSettings, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeySequence,
    QUndoGroup,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from annoter.config import (
    BASE_RENDER_DPI,
    HIGH_DPI_ZOOM_EXIT,
    HIGH_DPI_ZOOM_THRESHOLD,
    HIGH_RENDER_DPI,
    HIRES_DEBOUNCE_MS,
    HIRES_MAX_PIXELS,
    HIRES_OVERLAY_MARGIN,
    MAX_RECENT_FILES,
    PIXMAP_CACHE_PAGES,
    UNDO_STACK_LIMIT,
)
from annoter.controllers import bend_radius
from annoter.controllers.align import AlignMode, compute_align_moves
from annoter.controllers.geometry import pt_to_px, px_to_pt
from annoter.controllers.convert import (
    convert_poly_closed,
    convert_shape_outline,
    line_to_arrow,
)
from annoter.controllers.stacking import (
    LOWER,
    RAISE,
    TO_BACK,
    TO_FRONT,
    can_restack,
    page_stack,
    restacked,
)
from annoter.controllers.commands import (
    AddAnnotationCommand,
    ChangeColorCommand,
    ChangeGdtCommand,
    ChangePropsCommand,
    ChangeStrokeCommand,
    DeleteAnnotationsCommand,
    MoveAnnotationsCommand,
    ReorderCommand,
    ReplaceAnnotationCommand,
    ResizeCommand,
)
from annoter.controllers.tools import Tool, ToolController
from annoter.model.document import PdfDocument
from annoter.model.gdt import GdtState
from annoter.model.styles import (
    DIM_END_LABELS,
    DIM_ORIENTATION_LABELS,
    END_STYLE_LABELS,
    DimOrientation,
    EndStyle,
    HandleRole,
)
from annoter.services.pdf_export import (
    read_annotations,
    write_annotations,
)
from annoter.services import doc_info
from annoter.services.command_usage import CommandUsage
from annoter.services.pdf_render import PageRenderer
from annoter.services.recent_files import RecentFiles
from annoter.services.thumbnail_cache import ThumbnailCache
from annoter.services.theme import Theme, apply as apply_theme
from annoter.services.palette import PaletteStore
from annoter.services.tokens import tokens_for
from annoter.views.canvas_overlays import (
    CanvasNavPill,
    CanvasToast,
    DraftBar,
    ToolHintChip,
    hint_for,
)
from annoter.views.inspector_widgets import PaletteEditor
from annoter.views.annotation_picker import AnnotationPicker
from annoter.views.context_menu import (
    AnnotationContextMenu,
    IconCommand,
    MenuSpinBox,
)
from annoter.views.command_palette import (
    FILES_GROUP,
    CommandEntry,
    CommandPalette,
    entries_from_menu,
)
from annoter.views.line_icons import line_icon
from annoter.views.top_bar import TopBar
from annoter.views.color_picker import popup_color_picker
from annoter.views.edit_toolbar import EditToolbar
from annoter.views.gdt_editor import GdtFrameBuilder
from annoter.views.icons import action_icon, end_icon
from annoter.views.items.base import AnnotationItem
from annoter.views.items.callout import CalloutItem
from annoter.views.items.dimension import VALUE_RUN, DimensionItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.note import StickyNoteItem
from annoter.views.items.poly import PolygonItem, PolylineItem
from annoter.views.items.shapes import CloudItem, RectangleItem
from annoter.views.items.stamp import STAMP_PRESETS
from annoter.views.items.sub_text import SubTextItem
from annoter.views.items.text import TextAnnotationItem
from annoter.views.note_editor import NoteEditor
from annoter.views.document_sidebar import (
    SIDEBAR_WIDTH,
    TAB_ANNOTATIONS,
    DocumentSidebar,
)
from annoter.views.pdf_scene import PdfScene
from annoter.views.pdf_view import PdfView
from annoter.views.properties_dock import INSPECTOR_WIDTH, PropertiesDock
from annoter.views.stroke_spin import STROKE_LADDER
from annoter.views.tool_rail import TOOL_LABELS, ToolFlyout, ToolRail
from annoter.views.welcome_screen import WelcomeScreen, format_size
from annoter.views.document_properties import DocumentPropertiesDialog


# Style properties the Format Painter may copy. Captured from the source
# via `getattr(item, name)()`, applied to a target via `set_<name>` --
# only when the target actually has that setter, so e.g. a GD&T frame's
# font size never leaks onto a rectangle's corner radius.
_PAINTABLE_PROPS: tuple[str, ...] = (
    "color",
    "stroke",
    "dash_style",
    "fill_enabled",
    "fill_color",
    "fill_opacity",
    "corner_radius",
    "font_family",
    "font_size",
    "bold",
    "italic",
    "align",
    "label_font_size",
    "start_end",
    "end_end",
)


# Bumped when the dock / tool-bar layout changes incompatibly, so a
# layout saved by an older version is ignored instead of misplacing the
# new panels (2: UI redesign, Lot E -- one left sidebar).
_WINDOW_STATE_VERSION = 2


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Annoter")
        self.resize(1280, 800)
        self.setAcceptDrops(True)

        self._theme: Theme = Theme.LIGHT
        self._doc: PdfDocument | None = None
        # Start page shown full width (UI redesign, Lot G): the panels
        # step aside and come back as the user left them.
        self._welcome_mode: bool = False
        self._panel_visibility: dict[QWidget, bool] = {}
        # Last dock / tool-bar layout seen with a document open -- what
        # gets saved even when the app is closed from the start page.
        self._layout_state: QByteArray | None = None
        # True while the open document is an unsaved scratch PDF created
        # by "New blank document" -- Save then redirects to Save As.
        self._is_untitled: bool = False
        # Structural edits (inserted/moved pages, document resize) live
        # in the raw fitz document, not in any undo stack; this flag
        # keeps the unsaved-changes prompt honest about them.
        self._doc_structure_dirty: bool = False
        self._renderer: PageRenderer | None = None
        self._page_index: int = 0
        self._page_rotation: int = 0  # multiples of 90, in [0, 360)
        # Supersampling factor for the current render (1.0 = base DPI).
        # Driven hysteretically by the zoom factor; see _on_zoom_changed.
        self._render_scale: float = 1.0

        # Per-page annotation buckets and undo stacks (M2: in-memory; M4
        # promotes them to native PDF annotations).
        self._page_items: dict[int, list[AnnotationItem]] = {}
        self._page_stacks: dict[int, QUndoStack] = {}
        self._undo_group = QUndoGroup(self)

        self._tool_controller = ToolController(self)
        self._tool_controller.toolChanged.connect(self._on_tool_changed)

        # In-place GD&T editing (one editor at a time, anchored to the
        # item being created or edited).
        self._gdt_editor: GdtFrameBuilder | None = None
        self._gdt_edit_item: GdtAnnotationItem | None = None
        self._gdt_edit_is_new: bool = False
        self._gdt_old_state: GdtState | None = None
        # What a new frame was opened with: closing it unchanged drops it.
        self._gdt_initial_state: GdtState | None = None
        # New frames start from the last characteristic placed (Lot I):
        # a drawing usually repeats the same control many times.
        self._last_gdt_characteristic = GdtState().characteristic

        # Floating sticky-note editor (one at a time, like the GD&T one).
        self._note_editor: NoteEditor | None = None
        self._note_edit_item: StickyNoteItem | None = None
        self._note_edit_is_new: bool = False
        self._note_old_text: str | None = None

        self._scene = PdfScene(self)
        self._scene.set_tool_controller(self._tool_controller)
        self._scene.annotationsChanged.connect(self._on_annotations_changed)
        self._scene.selectionChanged.connect(self._on_scene_selection_changed)
        self._scene.gdtPlacementRequested.connect(self._on_gdt_placement)
        self._scene.notePlacementRequested.connect(self._on_note_placement)
        self._scene.formatPaintRequested.connect(self._on_format_paint_requested)

        self._view = PdfView(self)
        self._view.setScene(self._scene)
        self._view.contextMenuRequested.connect(self._show_context_menu)

        # Central area: welcome page while no document is open, the PDF
        # view once one is (Discussion #1, item 12).
        self._welcome = WelcomeScreen(self, thumbnails=ThumbnailCache())
        self._welcome.openRequested.connect(self._on_open)
        self._welcome.blankRequested.connect(self._new_blank_document)
        self._welcome.openPathRequested.connect(self._open_path)
        self._welcome.removePathRequested.connect(self._recent_remove)
        self._welcome.clearRecentRequested.connect(self._clear_recent)
        self._central = QStackedWidget(self)
        self._central.addWidget(self._welcome)
        self._central.addWidget(self._view)
        self.setCentralWidget(self._central)
        self._view.setFocus()

        # Floating contextual bar for the item being EDITED. Symbols and
        # tolerances are inserted from here, so they are part of writing
        # a text rather than tools of their own. (The bar that used to
        # float over a SELECTED item is gone since Lot J: its commands
        # live in the right-click menu and the Inspector.)
        self._text_edit_item: TextAnnotationItem | None = None
        et = EditToolbar(self._view.viewport())
        self._edit_toolbar = et
        et.fontSizeChanged.connect(
            lambda size: self._push_edit_prop("font_size", int(size))
        )
        et.boldToggled.connect(
            lambda on: self._push_edit_prop("bold", bool(on))
        )
        et.italicToggled.connect(
            lambda on: self._push_edit_prop("italic", bool(on))
        )
        et.alignPicked.connect(
            lambda align: self._push_edit_prop("align", align)
        )
        et.borderPicked.connect(
            lambda border: self._push_edit_prop("border", border)
        )
        et.symbolPicked.connect(self._insert_symbol_in_edit)
        et.tolerancePicked.connect(self._insert_tolerance_in_edit)
        et.refocusRequested.connect(self._refocus_text_edit)
        self._scene.textEditingStarted.connect(self._on_text_editing_started)
        self._scene.textEditingFinished.connect(self._on_text_editing_finished)
        # Alt+Click: choose among the annotations stacked at the click.
        self._scene.pickRequested.connect(self._on_pick_requested)
        # "Add Leader" mode: hint and cursor while the target is picked.
        self._scene.leaderPlacementChanged.connect(
            self._on_leader_placement_changed
        )

        # The edit bar hides during interactive drags and resizes and
        # comes back, repositioned, on release.
        self._scene.interactiveDragChanged.connect(self._on_drag_state)

        # In-app clipboard: detached clones produced by Copy/Cut. Paste
        # re-clones from these so multiple pastes work and the clipboard
        # stays independent from any subsequent scene mutation.
        self._clipboard: list[AnnotationItem] = []

        # Style captured by the Format Painter toggle, applied to every
        # annotation clicked afterwards until it is toggled off.
        self._format_paint_style: dict[str, object] | None = None

        # Vertical icon rail (UI redesign, Lot C): the single place to
        # pick a drawing tool, as the old Tools dock was.
        self._tool_rail = ToolRail(self._tool_controller, self)
        self.addToolBar(Qt.LeftToolBarArea, self._tool_rail)
        # Variant chooser shown next to the rail for Line / arrow and
        # Stamp; floats over the window, does not grab input.
        fly = ToolFlyout(STAMP_PRESETS, self)
        self._tool_flyout = fly
        fly.lineKindPicked.connect(self._tool_controller.set_line_kind)
        fly.stampPicked.connect(self._tool_controller.set_stamp_preset)
        fly.customStampRequested.connect(self._ask_custom_stamp)
        self._tool_controller.lineKindChanged.connect(fly.sync_line_kind)
        self._tool_controller.stampPresetChanged.connect(fly.sync_stamp)

        # Left panel (Lot E): Pages / Annotations tabs in one dock.
        self._sidebar = DocumentSidebar(self)
        self._page_list = self._sidebar.pages
        self._page_list.pageClicked.connect(self._show_page)
        self._page_list.pageMoved.connect(self._on_page_reordered)
        self._annotation_tree = self._sidebar.annotations
        tree = self._annotation_tree
        tree.deleteRequested.connect(self._delete_selected)
        tree.selectionRequested.connect(self._on_sidebar_selection)
        tree.jumpRequested.connect(self._on_sidebar_jump)
        tree.pageRequested.connect(self._show_page)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._sidebar)
        self.resizeDocks([self._sidebar], [SIDEBAR_WIDTH], Qt.Horizontal)
        # Undo / redo add and remove items without annotationsChanged.
        self._undo_group.indexChanged.connect(
            lambda _i: self._refresh_sidebar()
        )

        # The user's quick colors (Lot F), shown by the inspector.
        # QSettings() follows the organization / application names app.py
        # sets ("Annoter" / "Annoter"), so tests can redirect it.
        self._palette = PaletteStore(QSettings(), self)

        # Right panel: the inspector (Lot F).
        pd = PropertiesDock(self, palette=self._palette)
        self._properties_dock = pd
        pd.colorPicked.connect(self._on_quick_color_picked)
        pd.strokePicked.connect(self._on_quick_stroke_picked)
        pd.strokeCommitted.connect(self._tool_controller.set_stroke)
        pd.documentPropertiesRequested.connect(self._show_document_properties)
        pd.editPaletteRequested.connect(self._edit_palette)
        pd.editRequested.connect(self._edit_selected)
        pd.duplicateRequested.connect(self._duplicate_selected)
        pd.deleteRequested.connect(self._delete_selected)
        # A first bend added (or the last removed) brings (drops) the
        # inspector's Bend radius row without a selection change.
        self._undo_group.indexChanged.connect(
            lambda _i: pd.sync_structure()
        )
        self._tool_controller.colorChanged.connect(self._sync_inspector_defaults)
        self._tool_controller.strokeChanged.connect(self._sync_inspector_defaults)
        self.addDockWidget(Qt.RightDockWidgetArea, pd)
        self.resizeDocks([pd], [INSPECTOR_WIDTH], Qt.Horizontal)

        self._recent = RecentFiles(MAX_RECENT_FILES, self)
        self._recent.changed.connect(self._refresh_recent_menu)
        self._recent.changed.connect(self._refresh_welcome_recent)
        self._welcome.set_recent(self._recent.list())

        self._build_actions()
        self._build_menus()
        self._build_top_bar()
        self._build_toolbar()
        self._build_canvas_overlays()
        self._properties_dock.set_actions(
            bring_front=self.act_bring_front,
            raise_one=self.act_raise,
            lower_one=self.act_lower,
            send_back=self.act_send_back,
            format_painter=self.act_format_painter,
        )
        self._sync_inspector_defaults()
        self._refresh_recent_menu()

        self._view.zoomChanged.connect(self._on_zoom_changed)
        self._on_zoom_changed(self._view.zoom())
        self._update_actions_enabled()

        # Hi-res viewport overlay: refreshed shortly after the view
        # settles (zoom, pan or page switch).
        self._hires_timer = QTimer(self)
        self._hires_timer.setSingleShot(True)
        self._hires_timer.setInterval(HIRES_DEBOUNCE_MS)
        self._hires_timer.timeout.connect(self._refresh_hires_overlay)
        self._view.horizontalScrollBar().valueChanged.connect(
            self._on_view_scrolled
        )
        self._view.verticalScrollBar().valueChanged.connect(
            self._on_view_scrolled
        )

        # Persistent prefs (window geometry / dock state / theme).
        self._settings = QSettings()
        self._restore_settings()
        self._set_welcome_mode(self._doc is None)

    # ------------------------------------------------------------------
    # build UI
    # ------------------------------------------------------------------
    def _build_actions(self) -> None:
        self.act_open = QAction("&Open...", self)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.triggered.connect(self._on_open)

        self.act_save = QAction("&Save", self)
        self.act_save.setShortcut(QKeySequence.Save)
        self.act_save.triggered.connect(self._on_save)

        self.act_save_as = QAction("Save &As...", self)
        self.act_save_as.setShortcut(QKeySequence.SaveAs)
        self.act_save_as.triggered.connect(self._on_save_as)

        self.act_insert_pdf = QAction("&Insert Pages from PDF...", self)
        self.act_insert_pdf.triggered.connect(self._on_insert_pdf)

        self.act_resize_doc = QAction("&Resize Document...", self)
        self.act_resize_doc.triggered.connect(self._on_resize_document)

        self.act_doc_properties = QAction("Document &Properties...", self)
        self.act_doc_properties.setShortcut(QKeySequence("Alt+Return"))
        self.act_doc_properties.triggered.connect(
            self._show_document_properties
        )

        self.act_export_images = QAction("&Export as Images...", self)
        self.act_export_images.triggered.connect(self._on_export_images)

        self.act_grayscale = QAction("&Grayscale Page", self)
        self.act_grayscale.setCheckable(True)
        self.act_grayscale.toggled.connect(self._on_grayscale_toggled)

        self.act_close = QAction("&Close", self)
        self.act_close.setShortcut("Ctrl+W")
        self.act_close.triggered.connect(self._on_close_requested)

        self.act_quit = QAction("&Quit", self)
        self.act_quit.setShortcut("Ctrl+Q")
        self.act_quit.triggered.connect(self.close)

        self.act_clear_recent = QAction("Clear Recent Files", self)
        self.act_clear_recent.triggered.connect(self._recent.clear)

        self.act_zoom_in = QAction("Zoom &In", self)
        self.act_zoom_in.setShortcuts(
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")]
        )
        self.act_zoom_in.triggered.connect(self._view.zoom_in)

        self.act_zoom_out = QAction("Zoom &Out", self)
        self.act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        self.act_zoom_out.triggered.connect(self._view.zoom_out)

        self.act_zoom_fit = QAction("&Fit Page", self)
        self.act_zoom_fit.setShortcut(QKeySequence("Ctrl+0"))
        self.act_zoom_fit.triggered.connect(self._view.zoom_to_fit)

        self.act_zoom_actual = QAction("&Actual Size", self)
        self.act_zoom_actual.setShortcut(QKeySequence("Ctrl+1"))
        self.act_zoom_actual.triggered.connect(self._view.zoom_to_actual)

        self.act_zoom_window = QAction("Zoom &Window", self)
        self.act_zoom_window.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        self.act_zoom_window.triggered.connect(self._view.arm_zoom_window)

        self.act_prev = QAction("&Previous Page", self)
        self.act_prev.setShortcut(QKeySequence(Qt.Key_PageUp))
        self.act_prev.triggered.connect(self._goto_prev_page)

        self.act_next = QAction("&Next Page", self)
        self.act_next.setShortcut(QKeySequence(Qt.Key_PageDown))
        self.act_next.triggered.connect(self._goto_next_page)

        self.act_first = QAction("F&irst Page", self)
        self.act_first.setShortcut(QKeySequence("Ctrl+Home"))
        self.act_first.triggered.connect(lambda: self._show_page(0))

        self.act_last = QAction("&Last Page", self)
        self.act_last.setShortcut(QKeySequence("Ctrl+End"))
        self.act_last.triggered.connect(self._goto_last_page)

        self.act_goto = QAction("&Go to Page...", self)
        self.act_goto.setShortcut(QKeySequence("Ctrl+Alt+G"))
        self.act_goto.triggered.connect(self._goto_page_dialog)

        self.act_rotate_cw = QAction("Rotate &Right 90°", self)
        self.act_rotate_cw.setShortcut(QKeySequence("Ctrl+R"))
        self.act_rotate_cw.triggered.connect(lambda: self._rotate(90))

        self.act_rotate_ccw = QAction("Rotate &Left 90°", self)
        self.act_rotate_ccw.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.act_rotate_ccw.triggered.connect(lambda: self._rotate(-90))

        self.act_rotate_180 = QAction("Rotate 1&80°", self)
        self.act_rotate_180.triggered.connect(lambda: self._rotate(180))

        self.act_rotate_reset = QAction("R&eset Rotation", self)
        self.act_rotate_reset.triggered.connect(self._rotate_reset)

        # ---- edit ----
        self.act_undo = self._undo_group.createUndoAction(self, "&Undo")
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_redo = self._undo_group.createRedoAction(self, "&Redo")
        self.act_redo.setShortcuts(
            [QKeySequence.Redo, QKeySequence("Ctrl+Y")]
        )

        self.act_delete = QAction("&Delete Selection", self)
        self.act_delete.setShortcuts(
            [QKeySequence(Qt.Key_Delete), QKeySequence(Qt.Key_Backspace)]
        )
        self.act_delete.triggered.connect(self._delete_selected)

        self.act_select_all = QAction("Select &All", self)
        self.act_select_all.setShortcut(QKeySequence.SelectAll)
        self.act_select_all.triggered.connect(self._select_all)

        self.act_change_color = QAction("Change &Color...", self)
        self.act_change_color.triggered.connect(self._change_selection_color)

        self.act_change_stroke = QAction("Change &Stroke...", self)
        self.act_change_stroke.triggered.connect(
            self._change_selection_stroke
        )

        # ---- format painter (copy style, PowerPoint-style) ----
        self.act_format_painter = QAction("Format &Painter", self)
        self.act_format_painter.setCheckable(True)
        self.act_format_painter.setToolTip(
            "Select one annotation, then click Format Painter and click "
            "other annotations to copy its style onto them. Toggle off "
            "or press Esc to stop."
        )
        self.act_format_painter.toggled.connect(
            self._on_format_painter_toggled
        )

        # ---- clipboard ----
        self.act_cut = QAction("Cu&t", self)
        self.act_cut.setShortcut(QKeySequence.Cut)
        self.act_cut.triggered.connect(self._cut_selected)

        self.act_copy = QAction("&Copy", self)
        self.act_copy.setShortcut(QKeySequence.Copy)
        self.act_copy.triggered.connect(self._copy_selected)

        self.act_paste = QAction("&Paste", self)
        self.act_paste.setShortcut(QKeySequence.Paste)
        self.act_paste.triggered.connect(self._paste_from_clipboard)

        self.act_duplicate = QAction("&Duplicate", self)
        self.act_duplicate.setShortcut(QKeySequence("Ctrl+D"))
        self.act_duplicate.triggered.connect(self._duplicate_selected)

        # ---- stacking order (Lot J: one step at a time, undoable) ----
        self.act_bring_front = QAction("Bring to &Front", self)
        self.act_bring_front.setShortcut(QKeySequence("Ctrl+Shift+]"))
        self.act_bring_front.triggered.connect(
            lambda: self._restack_selection(TO_FRONT)
        )

        self.act_raise = QAction("Bring &Forward", self)
        self.act_raise.setShortcut(QKeySequence("Ctrl+]"))
        self.act_raise.setToolTip(
            "Bring forward: above the next annotation it overlaps"
        )
        self.act_raise.triggered.connect(
            lambda: self._restack_selection(RAISE)
        )

        self.act_lower = QAction("Send Back&ward", self)
        self.act_lower.setShortcut(QKeySequence("Ctrl+["))
        self.act_lower.setToolTip(
            "Send backward: below the next annotation it overlaps"
        )
        self.act_lower.triggered.connect(
            lambda: self._restack_selection(LOWER)
        )

        self.act_send_back = QAction("Send to &Back", self)
        self.act_send_back.setShortcut(QKeySequence("Ctrl+Shift+["))
        self.act_send_back.triggered.connect(
            lambda: self._restack_selection(TO_BACK)
        )

        # ---- align & distribute (PowerPoint/Canva-style) ----
        self.act_align_left = QAction("Align &Left", self)
        self.act_align_left.triggered.connect(
            lambda: self._align_selection(AlignMode.LEFT)
        )
        self.act_align_center_h = QAction("Align &Center", self)
        self.act_align_center_h.triggered.connect(
            lambda: self._align_selection(AlignMode.CENTER_H)
        )
        self.act_align_right = QAction("Align &Right", self)
        self.act_align_right.triggered.connect(
            lambda: self._align_selection(AlignMode.RIGHT)
        )
        self.act_align_top = QAction("Align &Top", self)
        self.act_align_top.triggered.connect(
            lambda: self._align_selection(AlignMode.TOP)
        )
        self.act_align_middle_v = QAction("Align &Middle", self)
        self.act_align_middle_v.triggered.connect(
            lambda: self._align_selection(AlignMode.MIDDLE_V)
        )
        self.act_align_bottom = QAction("Align &Bottom", self)
        self.act_align_bottom.triggered.connect(
            lambda: self._align_selection(AlignMode.BOTTOM)
        )
        self.act_distribute_h = QAction("Distribute &Horizontally", self)
        self.act_distribute_h.triggered.connect(
            lambda: self._align_selection(AlignMode.DISTRIBUTE_H)
        )
        self.act_distribute_v = QAction("Distribute &Vertically", self)
        self.act_distribute_v.triggered.connect(
            lambda: self._align_selection(AlignMode.DISTRIBUTE_V)
        )

        # ---- grouping (session-level; see PdfScene._groups) ----
        self.act_group = QAction("&Group", self)
        self.act_group.setShortcut(QKeySequence("Ctrl+G"))
        self.act_group.triggered.connect(self._group_selected)

        self.act_ungroup = QAction("&Ungroup", self)
        self.act_ungroup.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self.act_ungroup.triggered.connect(self._ungroup_selected)

        # ---- leaders (Lot M, CATIA's "Add Leader") ----
        self.act_add_leader = QAction("Add &Leader", self)
        self.act_add_leader.setToolTip(
            "Add a leader to the selected text or GD&T frame, then click "
            "the point it designates"
        )
        self.act_add_leader.triggered.connect(self._add_leader_to_selected)

        self.act_focus_properties = QAction("&Properties", self)
        self.act_focus_properties.triggered.connect(self._focus_properties)

        self.act_edit_text = QAction("Edit &Text", self)
        self.act_edit_text.triggered.connect(self._begin_text_edit_selected)

        # ---- theme ----
        self.act_theme_light = QAction("&Light", self)
        self.act_theme_light.setCheckable(True)
        self.act_theme_light.triggered.connect(
            lambda: self._set_theme(Theme.LIGHT)
        )

        self.act_theme_dark = QAction("&Dark", self)
        self.act_theme_dark.setCheckable(True)
        self.act_theme_dark.triggered.connect(
            lambda: self._set_theme(Theme.DARK)
        )

        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_group.addAction(self.act_theme_light)
        self._theme_group.addAction(self.act_theme_dark)

        # One-click light/dark switch for the top bar.
        self.act_toggle_theme = QAction("Switch &Light / Dark Theme", self)
        self.act_toggle_theme.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.act_toggle_theme.triggered.connect(
            lambda: self._set_theme(
                Theme.LIGHT if self._theme is Theme.DARK else Theme.DARK
            )
        )

        # ---- canvas tool hints (Lot D) ----
        self.act_show_hints = QAction("Show Tool &Hints", self)
        self.act_show_hints.setCheckable(True)
        self.act_show_hints.setChecked(True)
        self.act_show_hints.toggled.connect(self._set_tool_hints_enabled)

        # ---- command palette (Ctrl+K) ----
        self.act_command_palette = QAction("Search &Commands...", self)
        self.act_command_palette.setShortcut(QKeySequence("Ctrl+K"))
        self.act_command_palette.triggered.connect(self._open_command_palette)

    def _build_menus(self) -> None:
        # UI redesign, Lot B: one main menu behind the top bar's menu
        # button replaces the menu bar. File commands sit at its root;
        # Edit / View / Page keep their former content as submenus.
        mb = QMenu(self)
        mb.setObjectName("MainMenu")
        self._main_menu = mb

        mb.addAction(self.act_open)
        self._menu_recent = mb.addMenu("Open &Recent")
        mb.addSeparator()
        mb.addAction(self.act_save)
        mb.addAction(self.act_save_as)
        mb.addAction(self.act_export_images)
        mb.addSeparator()

        m_edit = mb.addMenu("&Edit")
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)
        m_edit.addSeparator()
        m_edit.addAction(self.act_cut)
        m_edit.addAction(self.act_copy)
        m_edit.addAction(self.act_paste)
        m_edit.addAction(self.act_duplicate)
        m_edit.addSeparator()
        m_edit.addAction(self.act_delete)
        m_edit.addAction(self.act_select_all)
        m_edit.addSeparator()
        m_edit.addAction(self.act_bring_front)
        m_edit.addAction(self.act_raise)
        m_edit.addAction(self.act_lower)
        m_edit.addAction(self.act_send_back)
        m_edit.addSeparator()
        m_edit.addAction(self.act_group)
        m_edit.addAction(self.act_ungroup)
        m_edit.addAction(self.act_add_leader)
        m_edit.addSeparator()
        m_align = m_edit.addMenu("&Align")
        m_align.addAction(self.act_align_left)
        m_align.addAction(self.act_align_center_h)
        m_align.addAction(self.act_align_right)
        m_align.addSeparator()
        m_align.addAction(self.act_align_top)
        m_align.addAction(self.act_align_middle_v)
        m_align.addAction(self.act_align_bottom)
        m_align.addSeparator()
        m_align.addAction(self.act_distribute_h)
        m_align.addAction(self.act_distribute_v)
        m_edit.addSeparator()
        m_edit.addAction(self.act_change_color)
        m_edit.addAction(self.act_change_stroke)
        m_edit.addAction(self.act_format_painter)

        m_view = mb.addMenu("&View")
        m_view.addAction(self.act_zoom_in)
        m_view.addAction(self.act_zoom_out)
        m_view.addAction(self.act_zoom_fit)
        m_view.addAction(self.act_zoom_actual)
        m_view.addAction(self.act_zoom_window)
        m_view.addSeparator()
        m_view.addAction(self.act_rotate_cw)
        m_view.addAction(self.act_rotate_ccw)
        m_view.addAction(self.act_rotate_180)
        m_view.addAction(self.act_rotate_reset)
        m_view.addSeparator()
        m_view.addAction(self.act_grayscale)
        m_view.addSeparator()
        m_panels = m_view.addMenu("&Panels")
        for dock in (
            self._tool_rail,
            self._sidebar,
            self._properties_dock,
        ):
            m_panels.addAction(dock.toggleViewAction())
        m_view.addAction(self.act_show_hints)
        m_view.addSeparator()
        m_theme = m_view.addMenu("&Theme")
        m_theme.addAction(self.act_theme_light)
        m_theme.addAction(self.act_theme_dark)

        m_page = mb.addMenu("&Page")
        m_page.addAction(self.act_prev)
        m_page.addAction(self.act_next)
        m_page.addAction(self.act_first)
        m_page.addAction(self.act_last)
        m_page.addAction(self.act_goto)

        mb.addSeparator()
        mb.addAction(self.act_insert_pdf)
        mb.addAction(self.act_resize_doc)
        mb.addAction(self.act_doc_properties)
        mb.addSeparator()
        mb.addAction(self.act_command_palette)
        mb.addSeparator()
        mb.addAction(self.act_close)
        mb.addAction(self.act_quit)

        # Actions living only in a popup menu (or nowhere visible) need
        # to be on the window for their shortcuts to fire.
        self.addActions(
            [
                v
                for k, v in vars(self).items()
                if k.startswith("act_") and isinstance(v, QAction)
            ]
        )

    def _build_top_bar(self) -> None:
        bar = TopBar(self)
        bar.set_menu(self._main_menu)
        bar.bind(
            undo=self.act_undo,
            redo=self.act_redo,
            save=self.act_save,
            toggle_theme=self.act_toggle_theme,
        )
        bar.searchRequested.connect(self._open_command_palette)
        bar.titleClicked.connect(self._show_document_properties)
        self.setMenuWidget(bar)
        self._top_bar = bar
        self._command_palette: CommandPalette | None = None
        # What the palette ranks by (Lot H); same store as the prefs.
        self._command_usage = CommandUsage(QSettings(), self)

    # ------------------------------------------------------------------
    # command palette
    # ------------------------------------------------------------------
    def _command_entries(self) -> list[CommandEntry]:
        # Open Recent's file actions are replaced by the palette's own
        # "Recent files" group; its Clear Recent Files command stays.
        file_actions = [
            a
            for a in self._menu_recent.actions()
            if a is not self.act_clear_recent
        ]
        entries = entries_from_menu(self._main_menu, skip=file_actions)
        for e in entries:
            if e.group == "Open Recent":  # i.e. Clear Recent Files
                e.group = "File"
        for tool, label in TOOL_LABELS:
            entries.append(
                CommandEntry(
                    label=f"{label} tool",
                    group="Tools",
                    run=lambda t=tool: self._tool_controller.set_tool(t),
                    enabled=self._doc is not None or tool is Tool.SELECT,
                )
            )
        entries.extend(self._recent_file_entries())
        return entries

    def _recent_file_entries(self) -> list[CommandEntry]:
        """Recent files as palette entries (Lot H): searchable by name
        and folder. Files that are gone and the open document are left
        out -- neither is something to open from here."""
        current = ""
        if self._doc is not None and not self._is_untitled:
            current = str(Path(self._doc.path).resolve())
        icon = line_icon("file", self._gdt_icon_color())
        out: list[CommandEntry] = []
        for path in self._recent.list():
            p = Path(path)
            if path == current or not p.is_file():
                continue
            out.append(
                CommandEntry(
                    label=p.name,
                    group=FILES_GROUP,
                    run=lambda f=path: self._open_path(f),
                    icon=icon,
                    detail=str(p.parent),
                )
            )
        return out

    def _open_command_palette(self) -> None:
        if self._command_palette is None:
            self._command_palette = CommandPalette(
                self, usage=self._command_usage
            )
            self._command_palette.set_colors(tokens_for(self._theme))
        self._command_palette.open_with(
            self._command_entries(), anchor=self
        )

    def _build_toolbar(self) -> None:
        """No tool bar left (UI redesign): Open / Save / Undo / Redo live
        in the top bar (Lot B), zoom in the canvas pill (Lot D), color /
        stroke and Format Painter in the inspector (Lot F). What remains
        is the per-action tooltip and icon setup."""
        # Tooltips advertise the keyboard shortcut where one exists (the
        # top bar and the canvas pill mirror these tooltips).
        for name, act in vars(self).items():
            if not name.startswith("act_") or not isinstance(act, QAction):
                continue
            seq = act.shortcut()
            if not seq.isEmpty():
                plain = act.text().replace("&", "").rstrip(".")
                act.setToolTip(f"{plain} ({seq.toString()})")

        self._apply_icon_theme()

    def _build_canvas_overlays(self) -> None:
        """Hint chip, page / zoom pill and toast over the view (Lot D)."""
        vp = self._view.viewport()
        self._tool_hint = ToolHintChip(vp)
        self._nav_pill = CanvasNavPill(
            vp,
            prev_page=self.act_prev,
            next_page=self.act_next,
            zoom_out=self.act_zoom_out,
            zoom_in=self.act_zoom_in,
            zoom_fit=self.act_zoom_fit,
            zoom_actual=self.act_zoom_actual,
            zoom_window=self.act_zoom_window,
            rotate=self.act_rotate_cw,
        )
        self._nav_pill.pageRequested.connect(self._show_page)
        self._nav_pill.zoomRequested.connect(self._view.set_zoom)
        self._toast = CanvasToast(vp)
        # Finish / Remove last point / Cancel while a polyline is drawn.
        self._draft_bar = DraftBar(vp)
        self._draft_bar.finishClicked.connect(self._scene.finish_poly_draft)
        self._draft_bar.removePointClicked.connect(
            self._scene.remove_last_poly_point
        )
        self._draft_bar.cancelClicked.connect(self._scene.cancel_poly_draft)
        self._draft_bar.set_icon_color(
            self._gdt_icon_color(), QColor(tokens_for(self._theme).on_accent)
        )
        self._scene.polyDraftChanged.connect(
            lambda n: self._draft_bar.set_point_count(
                n, self._scene.poly_draft_min_points()
            )
        )
        tc = self._tool_controller
        tc.toolChanged.connect(self._refresh_tool_hint)
        tc.lineKindChanged.connect(self._refresh_tool_hint)
        tc.stampPresetChanged.connect(self._refresh_tool_hint)
        self._refresh_tool_hint()

    def _refresh_tool_hint(self, *_args) -> None:
        tc = self._tool_controller
        self._tool_hint.set_hint(
            hint_for(tc.tool(), tc.line_kind(), tc.stamp_preset()[0])
        )

    def _set_tool_hints_enabled(self, on: bool) -> None:
        self._tool_hint.set_hints_enabled(on)
        if hasattr(self, "_settings"):
            self._settings.setValue("ui/show_tool_hints", bool(on))

    def _show_message(self, text: str, msec: int = 4000) -> None:
        """Transient message over the canvas (formerly the status bar)."""
        self._toast.show_message(text, msec)

    def _apply_icon_theme(self) -> None:
        """Repaint code-drawn toolbar icons in the theme's glyph color."""
        c = self._gdt_icon_color()
        tokens = tokens_for(self._theme)
        self.act_open.setIcon(line_icon("open", c))
        self.act_save.setIcon(line_icon("save", c))
        self.act_undo.setIcon(line_icon("undo", c))
        self.act_redo.setIcon(line_icon("redo", c))
        self.act_export_images.setIcon(line_icon("export-image", c))
        self.act_insert_pdf.setIcon(line_icon("insert-pages", c))
        self.act_resize_doc.setIcon(line_icon("resize", c))
        self.act_doc_properties.setIcon(line_icon("file", c))
        self.act_command_palette.setIcon(line_icon("search", c))
        self.act_close.setIcon(line_icon("close-doc", c))
        self.act_prev.setIcon(line_icon("chevron-left", c))
        self.act_next.setIcon(line_icon("chevron-right", c))
        self.act_zoom_in.setIcon(line_icon("zoom-in", c))
        self.act_zoom_out.setIcon(line_icon("zoom-out", c))
        self.act_zoom_fit.setIcon(line_icon("fit", c))
        self.act_zoom_window.setIcon(line_icon("zoom-area", c))
        self.act_rotate_cw.setIcon(line_icon("rotate-cw", c))
        self._menu_recent.setIcon(line_icon("recent", c))
        # The theme button shows where a click takes you.
        self.act_toggle_theme.setIcon(
            line_icon("sun" if self._theme is Theme.DARK else "moon", c)
        )
        self.act_toggle_theme.setToolTip(
            "Switch to light theme (Ctrl+Shift+L)"
            if self._theme is Theme.DARK
            else "Switch to dark theme (Ctrl+Shift+L)"
        )
        self._top_bar.set_icon_color(c, QColor(tokens.on_accent))
        self.act_format_painter.setIcon(action_icon("format-painter", color=c))
        if hasattr(self, "_draft_bar"):  # built after the toolbar
            self._draft_bar.set_icon_color(c, QColor(tokens.on_accent))

    # ------------------------------------------------------------------
    # toolbar quick style controls
    # ------------------------------------------------------------------
    def _sync_inspector_defaults(self, *_args) -> None:
        self._properties_dock.set_defaults(
            self._tool_controller.color(), self._tool_controller.stroke()
        )

    def _edit_palette(self) -> None:
        dlg = PaletteEditor(
            self._palette, self, icon_color=self._gdt_icon_color()
        )
        dlg.exec()

    def _on_quick_color_picked(self, color: QColor) -> None:
        """Inspector color choice (formerly the toolbar's): sets the
        drawing color for future annotations AND recolors the current
        selection (undoably), like Office's color controls."""
        self._tool_controller.set_color(color)
        items = self._selected_annotations()
        if not items:
            return
        stack = self._undo_group.activeStack()
        cmd = ChangeColorCommand(items, QColor(color))
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()

    def _on_quick_stroke_picked(self, width: float) -> None:
        """Inspector stroke preset: same dual behavior as the color."""
        self._tool_controller.set_stroke(width)
        items = self._selected_annotations()
        if not items:
            return
        stack = self._undo_group.activeStack()
        cmd = ChangeStrokeCommand(items, width)
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()

    def _refresh_recent_menu(self) -> None:
        self._menu_recent.clear()
        items = self._recent.list()
        if not items:
            empty = self._menu_recent.addAction("(empty)")
            empty.setEnabled(False)
        else:
            for path in items:
                act = self._menu_recent.addAction(path)
                act.triggered.connect(
                    lambda checked=False, p=path: self._open_path(p)
                )
        self._menu_recent.addSeparator()
        self._menu_recent.addAction(self.act_clear_recent)

    def _update_actions_enabled(self) -> None:
        has_doc = self._doc is not None
        for a in (
            self.act_close,
            self.act_save,
            self.act_save_as,
            self.act_export_images,
            self.act_insert_pdf,
            self.act_resize_doc,
            self.act_doc_properties,
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_zoom_fit,
            self.act_zoom_actual,
            self.act_zoom_window,
            self.act_prev,
            self.act_next,
            self.act_first,
            self.act_last,
            self.act_goto,
            self.act_rotate_cw,
            self.act_rotate_ccw,
            self.act_rotate_180,
            self.act_rotate_reset,
            self.act_delete,
            self.act_select_all,
            self.act_change_color,
            self.act_change_stroke,
            self.act_format_painter,
            self.act_align_left,
            self.act_align_center_h,
            self.act_align_right,
            self.act_align_top,
            self.act_align_middle_v,
            self.act_align_bottom,
            self.act_distribute_h,
            self.act_distribute_v,
            self.act_group,
            self.act_ungroup,
            self.act_add_leader,
            self.act_cut,
            self.act_copy,
            self.act_paste,
            self.act_duplicate,
            self.act_bring_front,
            self.act_raise,
            self.act_lower,
            self.act_send_back,
        ):
            a.setEnabled(has_doc)
        # Drawing tools mean nothing without a page to draw on.
        if hasattr(self, "_tool_rail"):
            self._tool_rail.setEnabled(has_doc)
            if not has_doc:
                self._tool_flyout.dismiss()

    # ------------------------------------------------------------------
    # file ops
    # ------------------------------------------------------------------
    _IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open PDF or Image",
            "",
            "PDF and images (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp)"
            ";;PDF files (*.pdf)"
            ";;Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)"
            ";;All files (*.*)",
        )
        if path:
            self._open_path(path)

    def open_path(self, path: str | Path) -> None:
        """Public entry point used by app.py and tests."""
        self._open_path(str(path))

    def _refresh_welcome_recent(self) -> None:
        # Only rebuild (and re-render thumbnails) while the welcome page
        # is actually visible; it is refreshed on every _on_close anyway.
        if self._central.currentWidget() is self._welcome:
            self._welcome.set_recent(self._recent.list())

    def _recent_remove(self, path: str) -> None:
        self._recent.remove(path)

    def _clear_recent(self) -> None:
        self._recent.clear()

    # ------------------------------------------------------------------
    # start page vs document view
    # ------------------------------------------------------------------
    def _chrome_panels(self) -> tuple[QWidget, ...]:
        return (self._tool_rail, self._sidebar, self._properties_dock)

    def _set_welcome_mode(self, on: bool) -> None:
        """Give the start page the full width.

        The tool rail, the sidebar and the inspector mean nothing
        without a document, so they are hidden while the start page
        shows and restored exactly as the user left them (a panel they
        had closed stays closed). Their View > Panels toggles are
        disabled meanwhile so the two states cannot get crossed.
        """
        if on == self._welcome_mode:
            return
        self._welcome_mode = on
        panels = self._chrome_panels()
        if on:
            if self.isVisible():
                self._layout_state = self.saveState(_WINDOW_STATE_VERSION)
            self._panel_visibility = {p: not p.isHidden() for p in panels}
            for p in panels:
                p.hide()
        else:
            for p in panels:
                p.setVisible(self._panel_visibility.get(p, True))
        for p in panels:
            p.toggleViewAction().setEnabled(not on)

    def is_welcome_mode(self) -> bool:
        return self._welcome_mode

    @staticmethod
    def _image_to_scratch_pdf(path: str) -> Path | None:
        """Convert an image file to a temp single-page PDF, or None."""
        import tempfile

        import fitz

        try:
            img = fitz.open(path)
            try:
                pdf_bytes = img.convert_to_pdf()
            finally:
                img.close()
            tmp_dir = Path(tempfile.mkdtemp(prefix="annoter_"))
            target = tmp_dir / (Path(path).stem + ".pdf")
            pdf = fitz.open("pdf", pdf_bytes)
            try:
                pdf.save(str(target))
            finally:
                pdf.close()
            return target
        except Exception:
            return None

    def _new_blank_document(self) -> None:
        """Create and open an untitled single-page A4 scratch PDF.

        The file lives in a temp directory until the user saves;
        Save (Ctrl+S) redirects to Save As while `_is_untitled` is set,
        and the temp path never enters the recent-files list.
        """
        import tempfile

        import fitz

        tmp_dir = Path(tempfile.mkdtemp(prefix="annoter_"))
        target = tmp_dir / "Untitled.pdf"
        doc = fitz.open()
        doc.new_page(width=595, height=842)  # A4 portrait, points
        doc.save(str(target))
        doc.close()
        self._open_path(str(target), add_to_recent=False, untitled=True)

    def _open_path(
        self,
        path: str,
        add_to_recent: bool = True,
        untitled: bool = False,
    ) -> None:
        if not self._confirm_discard_changes():
            return
        if path.lower().endswith(self._IMAGE_SUFFIXES):
            # An image opens as a single-page scratch PDF (its page has
            # the image's pixel size in points) -- annotate it, then
            # Save As decides where the real PDF lives, exactly like a
            # blank document.
            converted = self._image_to_scratch_pdf(path)
            if converted is None:
                QMessageBox.critical(
                    self,
                    "Open failed",
                    f"Could not open image\n{path}",
                )
                return
            self._open_path(str(converted), add_to_recent=False, untitled=True)
            return
        try:
            doc = PdfDocument(Path(path))
        except Exception as e:
            QMessageBox.critical(
                self,
                "Open failed",
                f"Could not open\n{path}\n\n{e}",
            )
            self._recent.remove(path)
            return

        self._on_close()
        self._doc = doc
        self._renderer = PageRenderer(
            doc, BASE_RENDER_DPI, PIXMAP_CACHE_PAGES
        )
        self._renderer.set_grayscale(self.act_grayscale.isChecked())
        self._page_index = 0
        self._page_rotation = 0
        self._render_scale = 1.0
        self._page_stacks = {}
        # Reconstruct any annotations the PDF already contains so they
        # appear as editable items on first display of each page.
        try:
            self._page_items = read_annotations(doc.raw, BASE_RENDER_DPI)
        except Exception:
            self._page_items = {}
        # Re-attach the edit callbacks that read_annotations could not
        # know about.
        for items in self._page_items.values():
            for it in items:
                self._hook_item_callbacks(it)
        self._is_untitled = untitled
        self._doc_structure_dirty = False
        if add_to_recent:
            self._recent.add(path)
        self._refresh_window_title()
        self._page_list.set_document(self._renderer, doc.page_count)
        self._central.setCurrentWidget(self._view)
        self._set_welcome_mode(False)
        self._show_page(0, _is_initial=True)
        # Defer fit to let the layout settle when called during startup.
        QTimer.singleShot(0, self._view.zoom_to_fit)
        self._update_actions_enabled()

    def _hook_item_callbacks(self, item: AnnotationItem) -> None:
        """Attach the per-item hooks an item's origin cannot know about.

        Every annotation that enters the window from somewhere other
        than its own tool -- PDF reopen, insert-PDF, paste/duplicate --
        goes through here: the double-click editors for GD&T frames and
        sticky notes, and the edit-session relay that raises the
        contextual edit bar over a text (callouts included, they derive
        from TextAnnotationItem).
        """
        if isinstance(item, GdtAnnotationItem):
            item.set_edit_callback(self._open_gdt_editor)
            # Its notes are editable texts in their own right, so they
            # go through the same edit-session relay as any other text.
            item.set_sub_text_hook(self._scene.hook_text_item)
        elif isinstance(item, StickyNoteItem):
            item.set_edit_callback(self._open_note_editor)
        elif isinstance(item, TextAnnotationItem):
            self._scene.hook_text_item(item)
        elif isinstance(item, DimensionItem):
            # Its value is a text edited in place, with the edit bar.
            self._scene.hook_text_item(item.label_item())

    # ------------------------------------------------------------------
    # save / save as
    # ------------------------------------------------------------------
    def _collect_all_page_items(self) -> dict[int, list[AnnotationItem]]:
        """Snapshot every page's items, including the one on screen."""
        result: dict[int, list[AnnotationItem]] = {
            i: list(items) for i, items in self._page_items.items()
        }
        page = self._scene.page_item()
        if page is not None:
            current = [
                c for c in page.childItems() if isinstance(c, AnnotationItem)
            ]
            result[self._page_index] = current
        return result

    def _on_save(self) -> None:
        if self._doc is None:
            return
        if self._is_untitled:
            # A scratch "Untitled" document has no real path to overwrite.
            self._on_save_as()
            return
        self._commit_gdt_editor_if_open()
        self._commit_note_editor_if_open()
        target = self._doc.path
        confirm = QMessageBox.question(
            self,
            "Overwrite file?",
            f"Overwrite the original file?\n\n{target}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        if self._save_to(target):
            self._reopen_after_save(target)

    def _on_save_as(self) -> bool:
        if self._doc is None:
            return False
        self._commit_gdt_editor_if_open()
        self._commit_note_editor_if_open()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save As",
            str(self._doc.path),
            "PDF files (*.pdf);;All files (*.*)",
        )
        if not path:
            return False
        target = Path(path)
        if self._save_to(target):
            self._reopen_after_save(target)
            return True
        return False

    def _save_to(self, target: Path) -> bool:
        """Serialize items into a temp copy, then atomically replace `target`."""
        if self._doc is None or self._renderer is None:
            return False
        from tempfile import NamedTemporaryFile

        items_map = self._collect_all_page_items()
        try:
            write_annotations(self._doc.raw, items_map, self._renderer.dpi)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False

        # Save to a sibling temp file then move into place. We close the
        # source doc first because PyMuPDF holds the original open and
        # Windows refuses to replace open files.
        tmp = NamedTemporaryFile(
            "wb",
            delete=False,
            dir=str(target.parent),
            suffix=".tmp.pdf",
        )
        tmp.close()
        tmp_path = Path(tmp.name)
        try:
            self._doc.raw.save(str(tmp_path), garbage=3, deflate=True)
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            QMessageBox.critical(self, "Save failed", str(e))
            return False

        # Close original, swap, then signal caller to reopen.
        self._doc.close()
        try:
            tmp_path.replace(target)
        except OSError as e:
            QMessageBox.critical(
                self,
                "Save failed",
                f"Could not replace target file:\n{e}",
            )
            tmp_path.unlink(missing_ok=True)
            return False
        # Everything undoable is now on disk: mark every page stack clean
        # so the dirty flag (and the reopen below) see a saved document.
        for stack in self._page_stacks.values():
            stack.setClean()
        self._doc_structure_dirty = False
        return True

    def _reopen_after_save(self, target: Path) -> None:
        # The doc was closed by `_save_to`; reopen the (possibly renamed)
        # file so the user can continue editing.
        self._doc = None
        self._open_path(str(target))

    # ------------------------------------------------------------------
    # unsaved-changes guard
    # ------------------------------------------------------------------
    def _has_unsaved_changes(self) -> bool:
        return self._doc is not None and (
            self._doc_structure_dirty
            or any(
                not stack.isClean()
                for stack in self._page_stacks.values()
            )
        )

    def _mark_structure_dirty(self) -> None:
        self._doc_structure_dirty = True
        self._update_modified_flag()

    def _update_modified_flag(self, _clean: bool = False) -> None:
        # Drives the native "*" marker in the window title (via the [*]
        # placeholder set in _refresh_window_title). `_clean` absorbs the
        # cleanChanged(bool) signal argument; the flag is recomputed over
        # every page stack, not just the emitting one.
        unsaved = self._has_unsaved_changes()
        self.setWindowModified(unsaved)
        self._top_bar.set_unsaved(unsaved)

    def _confirm_discard_changes(self) -> bool:
        """Prompt to save when the document has unsaved changes.

        Returns True when the caller may proceed (saved, discarded, or
        nothing to save); False when the user cancelled.
        """
        if not self._has_unsaved_changes():
            return True
        self._commit_gdt_editor_if_open()
        self._commit_note_editor_if_open()
        choice = QMessageBox.warning(
            self,
            "Unsaved changes",
            f"Save changes to\n{self._doc.path.name}\nbefore closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if choice == QMessageBox.Cancel:
            return False
        if choice == QMessageBox.Save:
            if self._is_untitled:
                # Scratch document: only Save As gives it a real path.
                return self._on_save_as()
            # Direct save to the current path -- the prompt already named
            # the file, so no second overwrite confirmation.
            return self._save_to(self._doc.path)
        return True

    def _on_close_requested(self) -> None:
        """File > Close (Ctrl+W): guarded by the unsaved-changes prompt."""
        if self._confirm_discard_changes():
            self._on_close()

    def _on_close(self) -> None:
        # Drop any in-progress in-place edits with the document.
        self._cancel_gdt_editor()
        self._cancel_note_editor()
        self._close_edit_toolbar()
        if self._doc is not None:
            self._doc.close()
        self._doc = None
        self._renderer = None
        for stack in self._page_stacks.values():
            self._undo_group.removeStack(stack)
        self._page_stacks = {}
        self._page_items = {}
        # Before clear_page deletes the items: the tree must not keep
        # rows pointing at them.
        self._annotation_tree.clear_all()
        self._scene.clear_page()
        self._page_index = 0
        self._is_untitled = False
        self._page_list.set_document(None)
        self._sidebar.set_counts(0, 0)
        self._refresh_window_title()
        self._nav_pill.set_page(0, 0)
        self._refresh_document_summary()
        self._central.setCurrentWidget(self._welcome)
        self._set_welcome_mode(True)
        self._welcome.set_recent(self._recent.list())
        self._update_actions_enabled()

    # ------------------------------------------------------------------
    # document-level operations (merge / reorder / resize / export)
    # ------------------------------------------------------------------
    def _stash_current_page_items(self) -> None:
        """Park the on-screen page's items back into _page_items so a
        structural operation can touch every page uniformly."""
        self._commit_gdt_editor_if_open()
        self._commit_note_editor_if_open()
        if self._scene.page_item() is not None:
            self._page_items[self._page_index] = self._scene.detach_children()

    def _refresh_after_structure_change(self, show_index: int) -> None:
        self._renderer.clear_cache()
        self._mark_structure_dirty()
        self._page_list.set_document(
            self._renderer, self._doc.page_count
        )
        self._show_page(show_index, _is_initial=True)

    def _on_insert_pdf(self) -> None:
        if self._doc is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Insert Pages from PDF",
            "",
            "PDF files (*.pdf);;All files (*.*)",
        )
        if not path:
            return
        import fitz

        self._stash_current_page_items()
        first_new = self._doc.page_count
        try:
            other = fitz.open(path)
            try:
                if other.needs_pass:
                    raise ValueError("Encrypted PDFs are not supported.")
                self._doc.raw.insert_pdf(other)
            finally:
                other.close()
        except Exception as e:
            QMessageBox.critical(self, "Insert failed", str(e))
            return
        # The inserted pages' own annotations become editable items too.
        try:
            inserted = read_annotations(self._doc.raw, BASE_RENDER_DPI)
            for idx in range(first_new, self._doc.page_count):
                items = inserted.get(idx, [])
                for it in items:
                    self._hook_item_callbacks(it)
                self._page_items[idx] = items
        except Exception:
            pass
        self._refresh_after_structure_change(first_new)

    def _on_page_reordered(self, frm: int, to: int) -> None:
        """The thumbnail dock moved a page by drag & drop."""
        if self._doc is None or frm == to:
            return
        self._stash_current_page_items()
        try:
            # move_page's `to` means "insert BEFORE this position" in the
            # pre-removal numbering: moving down needs +1 to land AT the
            # requested final index, and "end of document" must be -1
            # (an out-of-range target hangs PyMuPDF).
            count = self._doc.page_count
            if to > frm:
                target = -1 if to >= count - 1 else to + 1
            else:
                target = to
            self._doc.raw.move_page(frm, target)
        except Exception as e:
            QMessageBox.critical(self, "Move failed", str(e))
            self._refresh_after_structure_change(self._page_index)
            return
        # Remap every per-page dict through the old->new index order.
        count = self._doc.page_count
        order = list(range(count))
        page = order.pop(frm)
        order.insert(to, page)
        self._page_items = {
            new: self._page_items[old]
            for new, old in enumerate(order)
            if old in self._page_items
        }
        self._page_stacks = {
            new: self._page_stacks[old]
            for new, old in enumerate(order)
            if old in self._page_stacks
        }
        current = order.index(self._page_index)
        self._refresh_after_structure_change(current)

    _PAPER_FORMATS: dict[str, tuple[float, float]] = {
        "A0 (841 x 1189 mm)": (2384.0, 3370.0),
        "A1 (594 x 841 mm)": (1684.0, 2384.0),
        "A2 (420 x 594 mm)": (1191.0, 1684.0),
        "A3 (297 x 420 mm)": (842.0, 1191.0),
        "A4 (210 x 297 mm)": (595.0, 842.0),
    }

    def _on_resize_document(self) -> None:
        """Rescale every page (content + annotations) to a paper format,
        e.g. an A3 plan becomes a true A0."""
        if self._doc is None:
            return
        choice, ok = QInputDialog.getItem(
            self,
            "Resize Document",
            "Target format (orientation is preserved):",
            list(self._PAPER_FORMATS),
            0,
            False,
        )
        if not ok:
            return
        import fitz

        tw_portrait, th_portrait = self._PAPER_FORMATS[choice]
        self._stash_current_page_items()
        raw = self._doc.raw
        n = raw.page_count
        factors: list[float] = []
        rebuilt = fitz.open()
        for i in range(n):
            page = raw[i]
            sw, sh = page.rect.width, page.rect.height
            landscape = sw > sh
            tw = th_portrait if landscape else tw_portrait
            th = tw_portrait if landscape else th_portrait
            s = min(tw / sw, th / sh)
            factors.append(s)
            np = rebuilt.new_page(width=tw, height=th)
            np.show_pdf_page(fitz.Rect(0, 0, sw * s, sh * s), raw, i)
        # Swap the rebuilt pages into the SAME document object so the
        # file path (and Save) are unaffected: append, then drop the
        # originals.
        raw.insert_pdf(rebuilt)
        rebuilt.close()
        raw.delete_pages(0, n - 1)
        # Items live in page pixels: scale each page's items by its own
        # factor so annotations land exactly where they were.
        for idx, items in self._page_items.items():
            s = factors[idx] if idx < len(factors) else 1.0
            for it in items:
                it.scale_geometry(s)
        self._refresh_after_structure_change(self._page_index)

    def _on_export_images(self) -> None:
        """Render every page (with annotations) to TIFF/PNG/JPEG.

        TIFF produces one multi-page file; PNG/JPEG produce one file per
        page suffixed _p<N>. Uses Pillow for encoding.
        """
        if self._doc is None:
            return
        path, selected = QFileDialog.getSaveFileName(
            self,
            "Export as Images",
            str(self._doc.path.with_suffix("")),
            "TIFF, multi-page (*.tif);;PNG (*.png);;JPEG (*.jpg)",
        )
        if not path:
            return
        dpi, ok = QInputDialog.getInt(
            self, "Export as Images", "Resolution (DPI):", 150, 50, 600
        )
        if not ok:
            return
        import fitz
        from PIL import Image

        self._stash_current_page_items()
        try:
            # Render from a throwaway copy carrying the CURRENT editor
            # items, so unsaved annotations are part of the export.
            copy = fitz.open("pdf", self._doc.raw.tobytes())
            try:
                write_annotations(
                    copy, self._collect_all_page_items(), BASE_RENDER_DPI
                )
                images = []
                for page in copy:
                    pix = page.get_pixmap(dpi=dpi, alpha=False)
                    images.append(
                        Image.frombytes(
                            "RGB",
                            (pix.width, pix.height),
                            pix.samples,
                        )
                    )
            finally:
                copy.close()
            target = Path(path)
            if "TIFF" in selected or target.suffix.lower() in (
                ".tif",
                ".tiff",
            ):
                images[0].save(
                    str(target),
                    save_all=True,
                    append_images=images[1:],
                    compression="tiff_lzw",
                    dpi=(dpi, dpi),
                )
                written = [target]
            else:
                written = []
                for i, img in enumerate(images):
                    p = (
                        target
                        if len(images) == 1
                        else target.with_name(
                            f"{target.stem}_p{i + 1}{target.suffix}"
                        )
                    )
                    img.save(str(p), dpi=(dpi, dpi))
                    written.append(p)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        finally:
            # Put the on-screen page's items back.
            self._show_page(self._page_index, _is_initial=True)
        self._show_message(
            f"Exported {len(written)} file(s)", 5000
        )

    def _on_grayscale_toggled(self, checked: bool) -> None:
        if self._renderer is not None:
            self._renderer.set_grayscale(checked)
            self._stash_current_page_items()
            self._page_list.set_document(
                self._renderer, self._doc.page_count
            )
            self._show_page(self._page_index, _is_initial=True)

    # ------------------------------------------------------------------
    # document properties (2026-09-28)
    # ------------------------------------------------------------------
    def _annotation_count(self) -> int:
        return sum(len(v) for v in self._collect_all_page_items().values())

    def _build_document_properties(self) -> DocumentPropertiesDialog | None:
        if self._doc is None:
            return None
        info = doc_info.collect(self._doc.raw, self._doc.path)
        return DocumentPropertiesDialog(
            info,
            self._annotation_count(),
            self,
            saved=not self._has_unsaved_changes(),
        )

    def _show_document_properties(self) -> None:
        dlg = self._build_document_properties()
        if dlg is not None:
            dlg.exec()

    def _refresh_document_summary(self) -> None:
        """The Inspector's "Document" rows (shown with nothing selected)."""
        if self._doc is None:
            self._properties_dock.set_document_summary(None)
            return
        raw = self._doc.raw
        rows: list[tuple[str, str]] = [
            ("File", self._doc.path.name),
            ("Pages", str(raw.page_count)),
        ]
        if 0 <= self._page_index < raw.page_count:
            rect = raw[self._page_index].rect
            rows.append(
                (
                    "This page",
                    f"{doc_info.paper_format(rect.width, rect.height)}, "
                    f"{doc_info.size_mm_text(rect.width, rect.height)}",
                )
            )
        try:
            rows.append(
                ("Size on disk", format_size(self._doc.path.stat().st_size))
            )
        except OSError:
            pass
        self._properties_dock.set_document_summary(rows)

    def _refresh_window_title(self) -> None:
        # [*] is Qt's windowModified placeholder: it renders as "*" while
        # setWindowModified(True) and disappears otherwise.
        if self._doc is None:
            self.setWindowTitle("Annoter")
            self._top_bar.set_document_name(None)
            self.setWindowModified(False)
            self._top_bar.set_unsaved(False)
        else:
            self.setWindowTitle(f"{self._doc.path.name}[*] - Annoter")
            self._top_bar.set_document_name(
                self._doc.path.name, tooltip=str(self._doc.path)
            )
            self._update_modified_flag()

    # ------------------------------------------------------------------
    # pages
    # ------------------------------------------------------------------
    def _show_page(self, index: int, _is_initial: bool = False) -> None:
        if self._renderer is None or self._doc is None:
            return
        index = max(0, min(self._doc.page_count - 1, index))
        # An in-progress in-place edit belongs to the leaving page.
        self._commit_gdt_editor_if_open()
        self._commit_note_editor_if_open()
        self._close_edit_toolbar()

        # Stash the leaving page's annotations before swapping the pixmap.
        if not _is_initial and self._scene.page_item() is not None:
            self._page_items[self._page_index] = self._scene.detach_children()

        self._page_index = index
        pixmap = self._renderer.render(
            index, self._page_rotation, self._render_scale
        )
        self._scene.set_page_pixmap(pixmap)

        # Activate the page's undo stack (creating it on first visit).
        stack = self._page_stacks.get(index)
        if stack is None:
            stack = QUndoStack(self)
            stack.setUndoLimit(UNDO_STACK_LIMIT)
            # Bound method (not a lambda) so PySide auto-disconnects it
            # when the window's C++ object is destroyed.
            stack.cleanChanged.connect(self._update_modified_flag)
            self._undo_group.addStack(stack)
            self._page_stacks[index] = stack
        self._undo_group.setActiveStack(stack)
        self._scene.set_undo_stack(stack)
        self._properties_dock.set_undo_stack(stack)

        # Restore this page's annotations.
        self._scene.attach_children(self._page_items.get(index, []))
        self._refresh_sidebar()

        self._nav_pill.set_page(index, self._doc.page_count)
        self._page_list.set_current_page(index)
        self._refresh_document_summary()
        if hasattr(self, "_hires_timer"):
            self._hires_timer.start()

    def _goto_prev_page(self) -> None:
        self._show_page(self._page_index - 1)

    def _goto_next_page(self) -> None:
        self._show_page(self._page_index + 1)

    def _goto_last_page(self) -> None:
        if self._doc is not None:
            self._show_page(self._doc.page_count - 1)

    def _goto_page_dialog(self) -> None:
        if self._doc is None:
            return
        n, ok = QInputDialog.getInt(
            self,
            "Go to Page",
            "Page number:",
            self._page_index + 1,
            1,
            self._doc.page_count,
        )
        if ok:
            self._show_page(n - 1)

    def _rotate(self, delta: int) -> None:
        self._page_rotation = (self._page_rotation + delta) % 360
        self._show_page(self._page_index)
        QTimer.singleShot(0, self._view.zoom_to_fit)

    def _rotate_reset(self) -> None:
        self._page_rotation = 0
        self._show_page(self._page_index)
        QTimer.singleShot(0, self._view.zoom_to_fit)

    # ------------------------------------------------------------------
    # zoom display
    # ------------------------------------------------------------------
    def _on_zoom_changed(self, factor: float) -> None:
        self._nav_pill.set_zoom(factor)
        self._maybe_rerender_for_zoom(factor)
        if hasattr(self, "_hires_timer"):
            self._hires_timer.start()
        self._position_gdt_editor()
        self._position_note_editor()
        self._position_edit_toolbar()

    def _on_view_scrolled(self, _value: int) -> None:
        if hasattr(self, "_hires_timer"):
            self._hires_timer.start()
        self._position_gdt_editor()
        self._position_note_editor()
        self._position_edit_toolbar()

    def _maybe_rerender_for_zoom(self, factor: float) -> None:
        """Hysteretic high-DPI re-render.

        Above HIGH_DPI_ZOOM_THRESHOLD the page is re-rasterized at
        HIGH_RENDER_DPI; below HIGH_DPI_ZOOM_EXIT it drops back to the
        base DPI. The pixmap's devicePixelRatio keeps logical geometry
        constant, so child annotations and undo history are untouched.
        """
        if self._renderer is None or self._doc is None:
            return
        target = self._render_scale
        if factor >= HIGH_DPI_ZOOM_THRESHOLD:
            target = HIGH_RENDER_DPI / BASE_RENDER_DPI
        elif factor <= HIGH_DPI_ZOOM_EXIT:
            target = 1.0
        if target == self._render_scale:
            return
        self._render_scale = target
        pixmap = self._renderer.render(
            self._page_index, self._page_rotation, self._render_scale
        )
        self._scene.set_page_pixmap(pixmap)

    def _refresh_hires_overlay(self) -> None:
        """Render the visible clip at exact screen resolution.

        Runs after the view has settled (debounced). The overlay pixmap
        is roughly viewport-sized regardless of the zoom level, so the
        memory cost is bounded even on A0 plans at maximum zoom. When the
        full-page pixmap is already sharp enough, the overlay is removed.
        """
        if self._renderer is None or self._doc is None:
            return
        page = self._scene.page_item()
        if page is None:
            return
        dpr = self._view.viewport().devicePixelRatioF()
        needed = self._view.zoom() * 72.0 / BASE_RENDER_DPI * dpr
        if needed <= self._render_scale * 1.01:
            self._scene.clear_hires_overlay()
            return
        visible = self._view.mapToScene(
            self._view.viewport().rect()
        ).boundingRect()
        mx = visible.width() * HIRES_OVERLAY_MARGIN
        my = visible.height() * HIRES_OVERLAY_MARGIN
        clip = visible.adjusted(-mx, -my, mx, my).intersected(
            page.boundingRect()
        )
        if clip.isEmpty():
            self._scene.clear_hires_overlay()
            return
        max_scale = (
            HIRES_MAX_PIXELS / (clip.width() * clip.height())
        ) ** 0.5
        scale = min(needed, max_scale)
        if scale <= self._render_scale * 1.01:
            self._scene.clear_hires_overlay()
            return
        pixmap, pos = self._renderer.render_clip(
            self._page_index, self._page_rotation, clip, scale
        )
        self._scene.set_hires_overlay(pixmap, pos)

    # ------------------------------------------------------------------
    # annotations: selection / edit ops
    # ------------------------------------------------------------------
    def _selected_annotations(self) -> list[AnnotationItem]:
        return [
            it
            for it in self._scene.selectedItems()
            if isinstance(it, AnnotationItem)
        ]

    def _delete_selected(self) -> None:
        if self._scene.poly_draft_active():
            # Delete / Backspace while drawing a polyline: take back the
            # last point, not the selection.
            self._scene.remove_last_poly_point()
            return
        items = self._selected_annotations()
        if not items:
            return
        stack = self._undo_group.activeStack()
        cmd = DeleteAnnotationsCommand(self._scene, items)
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._on_annotations_changed()

    def _select_all(self) -> None:
        page = self._scene.page_item()
        if page is None:
            return
        for child in page.childItems():
            if isinstance(child, AnnotationItem):
                child.setSelected(True)

    def _change_selection_color(self) -> None:
        items = self._selected_annotations()
        if not items:
            return
        popup_color_picker(
            self, items[0].color(), self._apply_selection_color
        )

    def _apply_selection_color(self, color: QColor) -> None:
        items = self._selected_annotations()
        if not items or not color.isValid():
            return
        stack = self._undo_group.activeStack()
        cmd = ChangeColorCommand(items, QColor(color))
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()

    def _change_selection_stroke(self) -> None:
        items = self._selected_annotations()
        if not items:
            return
        value, ok = QInputDialog.getInt(
            self,
            "Change Stroke",
            "Width (px, 0 = none):",
            int(round(items[0].stroke())),
            0,
            STROKE_LADDER[-1],
        )
        if not ok:
            return
        stack = self._undo_group.activeStack()
        cmd = ChangeStrokeCommand(items, float(value))
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()

    def _on_format_painter_toggled(self, checked: bool) -> None:
        if not checked:
            self._format_paint_style = None
            if self._tool_controller.tool() is Tool.FORMAT_PAINTER:
                self._tool_controller.set_tool(Tool.SELECT)
            return
        items = self._selected_annotations()
        if len(items) != 1:
            # Nothing (or too much) selected to copy a style from --
            # bounce the toggle back off instead of entering a mode with
            # no captured style.
            self.act_format_painter.setChecked(False)
            self._show_message(
                "Select exactly one annotation to copy its style, "
                "then turn on Format Painter.",
                4000,
            )
            return
        source = items[0]
        self._format_paint_style = {
            name: getattr(source, name)()
            for name in _PAINTABLE_PROPS
            if hasattr(source, name)
        }
        self._tool_controller.set_tool(Tool.FORMAT_PAINTER)
        self._show_message(
            "Format Painter: click annotations to apply the copied "
            "style. Esc or toggle off to stop.",
            4000,
        )

    def _on_format_paint_requested(self, item: AnnotationItem) -> None:
        style = self._format_paint_style
        if not style:
            return
        changes = []
        for name, value in style.items():
            if not hasattr(item, f"set_{name}"):
                continue
            old = getattr(item, name)()
            if old != value:
                changes.append((item, name, old, value))
        if not changes:
            return
        stack = self._undo_group.activeStack()
        cmd = ChangePropsCommand(changes, label="Copy style")
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._on_annotations_changed()

    def _on_annotations_changed(self) -> None:
        self._refresh_sidebar()

    # ------------------------------------------------------------------
    # left sidebar (Lot E)
    # ------------------------------------------------------------------
    def _annotations_by_page(self) -> dict[int, list[AnnotationItem]]:
        """Live annotations of every page: the stashed buckets for the
        other pages, the scene's children for the page on screen (its
        bucket may be stale once items were added or deleted)."""
        if self._doc is None:
            return {}
        pages = {
            p: list(items)
            for p, items in self._page_items.items()
            if p != self._page_index
        }
        page_item = self._scene.page_item()
        if page_item is not None:
            pages[self._page_index] = [
                c
                for c in page_item.childItems()
                if isinstance(c, AnnotationItem)
            ]
        return pages

    def _refresh_sidebar(self) -> None:
        if not hasattr(self, "_annotation_tree"):
            return
        pages = self._annotations_by_page()
        self._annotation_tree.set_annotations(pages, self._page_index)
        self._annotation_tree.sync_selection(self._scene.selectedItems())
        self._page_list.set_annotation_counts(
            {p: len(v) for p, v in pages.items()}
        )
        self._sidebar.set_counts(
            self._doc.page_count if self._doc is not None else 0,
            sum(len(v) for v in pages.values()),
        )

    def _on_sidebar_selection(self, items: list) -> None:
        """Rows picked in the Annotations tab (current page only)."""
        wanted = {id(it) for it in items}
        for it in self._scene.selectedItems():
            if id(it) not in wanted:
                it.setSelected(False)
        for it in items:
            if it.scene() is self._scene:
                it.setSelected(True)
        if len(items) == 1 and items[0].scene() is self._scene:
            self._view.ensureVisible(items[0], 40, 40)

    def _on_sidebar_jump(self, page: int, item: AnnotationItem) -> None:
        """A row on another page: go there, then select the annotation."""
        self._show_page(page)
        if item.scene() is self._scene:
            self._on_sidebar_selection([item])

    def show_annotations_tab(self) -> None:
        self._sidebar.show()
        self._sidebar.show_tab(TAB_ANNOTATIONS)

    def _on_scene_selection_changed(self) -> None:
        self._annotation_tree.sync_selection(self._scene.selectedItems())
        items = self._selected_annotations()
        self._properties_dock.set_items(items)

    def _on_drag_state(self, active: bool) -> None:
        if active:
            self._edit_toolbar.hide()
        else:
            self._position_edit_toolbar()

    # ------------------------------------------------------------------
    # selection commands (context menu, Inspector)
    # ------------------------------------------------------------------
    def _single_selected(self) -> AnnotationItem | None:
        items = self._selected_annotations()
        return items[0] if len(items) == 1 else None

    def _edit_selected(self) -> None:
        item = self._single_selected()
        if item is None:
            return
        if isinstance(item, GdtAnnotationItem):
            self._open_gdt_editor(item)
        elif isinstance(item, StickyNoteItem):
            self._open_note_editor(item)
        else:
            self._begin_text_edit_selected()

    def _replace_selected_item(
        self, old: AnnotationItem, new: AnnotationItem, label: str
    ) -> None:
        stack = self._undo_group.activeStack()
        cmd = ReplaceAnnotationCommand(
            self._scene, old.parentItem(), old, new, label=label
        )
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()

    def _set_selected_outline(self, cloudy: bool) -> None:
        item = self._single_selected()
        if item is None:
            return
        converted = convert_shape_outline(item, cloudy)
        if converted is not None:
            self._replace_selected_item(item, converted, "Change outline")

    def _set_selected_closed(self, closed: bool) -> None:
        item = self._single_selected()
        if item is None:
            return
        converted = convert_poly_closed(item, closed)
        if converted is not None:
            self._replace_selected_item(item, converted, "Change path kind")

    def _set_selected_fill(self, enabled: bool) -> None:
        changes = [
            (it, "fill_enabled", it.fill_enabled(), bool(enabled))
            for it in self._selected_annotations()
            if hasattr(it, "set_fill_enabled")
            and it.fill_enabled() != bool(enabled)
        ]
        if not changes:
            return
        stack = self._undo_group.activeStack()
        cmd = ChangePropsCommand(changes, label="Toggle fill")
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()

    def _add_bend_to_selected(self) -> None:
        """Insert a bend at the midpoint of the item's longest segment."""
        item = self._single_selected()
        if not isinstance(item, LineItem):
            return
        pts = item.path_points()
        best = max(
            range(len(pts) - 1),
            key=lambda i: (pts[i + 1].x() - pts[i].x()) ** 2
            + (pts[i + 1].y() - pts[i].y()) ** 2,
        )
        mid = QPointF(
            (pts[best].x() + pts[best + 1].x()) / 2.0,
            (pts[best].y() + pts[best + 1].y()) / 2.0,
        )
        self._add_bend_at(item, mid)

    # ------------------------------------------------------------------
    # contextual edit bar (shown while an item is being edited)
    # ------------------------------------------------------------------
    def _on_text_editing_started(self, item: TextAnnotationItem) -> None:
        self._text_edit_item = item
        self._edit_toolbar.set_context(
            item, icon_color=self._gdt_icon_color()
        )
        self._position_edit_toolbar()

    def _edit_gdt_note(self, role: str) -> None:
        """Hand over from the GD&T panel to the note's in-place editor.

        The panel is committed first: the notes are rich text edited on
        the frame itself, so keeping both surfaces open would leave two
        editors fighting over the keyboard.
        """
        item = self._gdt_edit_item
        if item is None:
            return
        self._commit_gdt_editor(keep_empty=True)
        sub = item.ensure_sub_text(role)
        self._scene.hook_text_item(sub)
        sub.begin_edit()

    def _commit_sub_text(self, sub: SubTextItem) -> None:
        """Fold a finished note back into its owner's state, undoably."""
        owner = sub.owner()
        if isinstance(owner, DimensionItem):
            self._commit_dimension_value(owner, sub)
            return
        if not isinstance(owner, GdtAnnotationItem):
            return
        runs = [] if sub.is_blank() else sub.rich_runs()
        old_state = owner.state()
        new_state = owner.state_with_sub_text(sub.role(), runs)
        if new_state == old_state:
            # Nothing to record, but a note left blank still has to go:
            # re-applying the state drops the empty sub-item.
            owner.set_state(old_state)
            return
        cmd = ChangeGdtCommand(owner, old_state, new_state)
        stack = self._undo_group.activeStack()
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._on_annotations_changed()

    def _commit_dimension_value(self, owner: DimensionItem, sub) -> None:  # noqa: ANN001
        """A dimension's value edited in place: stored with the measured
        number as a placeholder when it is still in the text, so it keeps
        following the points (see views/items/dimension.py)."""
        old = owner.value_runs()
        new = owner.runs_from_label(sub.rich_runs())
        if new == old:
            owner.set_value_runs(old)  # shows the stored text again
            return
        self._push_props([(owner, "value_runs", old, new)], "Edit dimension value")

    def _push_props(self, changes: list, label: str) -> None:
        if not changes:
            return
        cmd = ChangePropsCommand(changes, label=label)
        stack = self._undo_group.activeStack()
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._on_annotations_changed()

    def _set_dimension_orientation(
        self, item: DimensionItem, orientation: DimOrientation
    ) -> None:
        old = item.geom_snapshot()
        item.switch_orientation(orientation)
        new = item.geom_snapshot()
        if new != old:
            self._push_geom_change(item, old, new, "Dimension orientation")
            self._on_annotations_changed()

    def _use_measured_value(self, item: DimensionItem) -> None:
        self._push_props(
            [(item, "value_runs", item.value_runs(), [dict(VALUE_RUN)])],
            "Use measured value",
        )

    def _on_text_editing_finished(self, item: TextAnnotationItem) -> None:
        if isinstance(item, SubTextItem):
            self._commit_sub_text(item)
        if self._text_edit_item is not item:
            return
        self._close_edit_toolbar()

    def _close_edit_toolbar(self) -> None:
        self._text_edit_item = None
        self._edit_toolbar.set_context(None)
        self._edit_toolbar.hide()

    def _position_edit_toolbar(self) -> None:
        item = self._text_edit_item
        toolbar = self._edit_toolbar
        if item is None or item.scene() is None:
            toolbar.hide()
            return
        toolbar.adjustSize()
        rect = item.mapToScene(item.content_rect()).boundingRect()
        vp = self._view.viewport()
        above = self._view.mapFromScene(rect.topLeft())
        below = self._view.mapFromScene(rect.bottomLeft())
        x = above.x()
        y = above.y() - toolbar.height() - 8
        if y < 4:
            y = below.y() + 8
        x = max(4, min(x, vp.width() - toolbar.width() - 4))
        y = max(4, min(y, vp.height() - toolbar.height() - 4))
        toolbar.move(int(x), int(y))
        toolbar.show()
        toolbar.raise_()

    def _refocus_text_edit(self) -> None:
        """Hand the caret back after one of the bar's menus closed."""
        item = self._text_edit_item
        if item is None or item.scene() is None:
            return
        self._view.setFocus()
        item.refocus_editor()

    def _push_edit_prop(self, name: str, value: object) -> None:
        """Apply a style property of the item being edited, undoably.

        Same contract as the Properties dock (`ChangePropsCommand`), so
        the bar and the dock produce interchangeable undo entries.
        """
        item = self._text_edit_item
        if item is None:
            return
        try:
            old = getattr(item, name)()
        except AttributeError:
            return
        if old == value:
            return
        cmd = ChangePropsCommand([(item, name, old, value)])
        stack = self._undo_group.activeStack()
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._position_edit_toolbar()
        self._refocus_text_edit()

    def _insert_symbol_in_edit(self, symbol: str) -> None:
        """Insert a character at the caret.

        No command is pushed: the edit session is the undo boundary for
        content (typing is not undoable step by step either, the whole
        text commits when the session ends).
        """
        item = self._text_edit_item
        if item is None:
            return
        item.insert_symbol(symbol)
        self._position_edit_toolbar()
        self._refocus_text_edit()

    def _insert_tolerance_in_edit(self, tol: object) -> None:
        item = self._text_edit_item
        if item is None:
            return
        item.insert_tolerance(tol)
        self._position_edit_toolbar()
        self._refocus_text_edit()

    # ------------------------------------------------------------------
    # clipboard / duplicate / z-order / context menu
    # ------------------------------------------------------------------
    def _copy_selected(self) -> None:
        items = self._selected_annotations()
        if not items:
            return
        # Store detached clones so subsequent mutations don't affect the
        # clipboard contents.
        self._clipboard = [it.clone() for it in items]

    def _cut_selected(self) -> None:
        items = self._selected_annotations()
        if not items:
            return
        self._clipboard = [it.clone() for it in items]
        self._delete_selected()

    def _paste_from_clipboard(self) -> None:
        if not self._clipboard:
            return
        page = self._scene.page_item()
        if page is None:
            return
        # Offset pasted items so they don't sit perfectly on top of the
        # source. 12 scene units works for both A0 plans and tighter shots.
        offset_x, offset_y = 12.0, 12.0
        stack = self._undo_group.activeStack()
        clones: list[AnnotationItem] = []
        for src in self._clipboard:
            try:
                c = src.clone()
            except NotImplementedError:
                continue
            c.setPos(c.pos().x() + offset_x, c.pos().y() + offset_y)
            self._hook_item_callbacks(c)
            clones.append(c)
        if not clones:
            return
        if stack is not None:
            stack.beginMacro("Paste annotation(s)")
        for c in clones:
            cmd = AddAnnotationCommand(self._scene, page, c)
            if stack is not None:
                stack.push(cmd)
            else:
                cmd.redo()
        if stack is not None:
            stack.endMacro()
        # Select the freshly pasted items.
        for it in self._scene.selectedItems():
            it.setSelected(False)
        for c in clones:
            c.setSelected(True)
        self._on_annotations_changed()

    def _duplicate_selected(self) -> None:
        """Copy + paste in one gesture; preserves clipboard contents."""
        items = self._selected_annotations()
        if not items:
            return
        saved = self._clipboard
        self._clipboard = [it.clone() for it in items]
        self._paste_from_clipboard()
        self._clipboard = saved

    def _restack_selection(self, move: str) -> bool:
        """Move the selection in the page's stacking order (see
        controllers.stacking). False when nothing changed."""
        items = self._selected_annotations()
        page = self._scene.page_item()
        if not items or page is None:
            return False
        old = page_stack(page)
        new = restacked(old, items, move)
        if new == old:
            return False
        cmd = ReorderCommand(page, old, new)
        stack = self._undo_group.activeStack()
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._on_annotations_changed()
        return True

    def _can_restack(self, move: str) -> bool:
        return can_restack(
            self._scene.page_item(), self._selected_annotations(), move
        )

    def _align_selection(self, mode: AlignMode) -> None:
        items = self._selected_annotations()
        moves = compute_align_moves(items, mode)
        if not moves:
            return
        stack = self._undo_group.activeStack()
        cmd = MoveAnnotationsCommand(moves, label="Align annotation(s)")
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._on_annotations_changed()

    def _group_selected(self) -> None:
        """Session-level grouping: not an undoable command (it only
        affects future selection/drag behavior, not annotation data)."""
        self._scene.group_selection()

    def _ungroup_selected(self) -> None:
        self._scene.ungroup_selection()

    def _begin_text_edit_selected(self) -> None:
        items = self._selected_annotations()
        if not items:
            return
        target = items[0]
        if hasattr(target, "begin_text_edit"):
            target.begin_text_edit()
        elif hasattr(target, "begin_edit"):
            target.begin_edit()

    def _focus_properties(self) -> None:
        self._properties_dock.show()
        self._properties_dock.raise_()

    # ------------------------------------------------------------------
    # Alt+Click picker (Lot K)
    # ------------------------------------------------------------------
    def _on_pick_requested(self, scene_pos: QPointF, screen_pos) -> None:  # noqa: ANN001
        # Opened once the mouse release that asked for it has finished
        # unwinding through the scene.
        QTimer.singleShot(
            0, lambda: self._show_picker(QPointF(scene_pos), screen_pos)
        )

    def _show_picker(self, scene_pos: QPointF, screen_pos) -> None:  # noqa: ANN001
        menu = self._build_picker(scene_pos)
        if menu is not None:
            menu.exec(screen_pos)

    def _build_picker(self, scene_pos: QPointF) -> AnnotationPicker | None:
        """The list of annotations stacked at `scene_pos`; None when there
        is nothing to choose between (fewer than two)."""
        if self._doc is None:
            return None
        items = self._scene.annotations_at(scene_pos)
        if len(items) < 2:
            return None
        return AnnotationPicker(
            items,
            self._gdt_icon_color(),
            self._scene.set_pick_highlight,
            self._pick_annotation,
            self,
        )

    def _pick_annotation(self, item: AnnotationItem) -> None:
        self._scene.set_pick_highlight(None)
        if item.scene() is not self._scene:
            return
        for it in self._scene.selectedItems():
            it.setSelected(False)
        item.setSelected(True)

    # ------------------------------------------------------------------
    # leaders on texts and GD&T frames (Lot M)
    # ------------------------------------------------------------------
    @staticmethod
    def _leader_host(item: AnnotationItem | None) -> AnnotationItem | None:
        """`item` when it can carry leaders: a text or a GD&T frame (a
        callout already has its own leader; a frame's note belongs to
        the frame)."""
        if isinstance(item, GdtAnnotationItem):
            return item
        if isinstance(item, TextAnnotationItem) and not isinstance(
            item, (CalloutItem, SubTextItem)
        ):
            return item
        return None

    def _add_leader_to_selected(self) -> None:
        host = self._leader_host(self._single_selected())
        if host is None:
            self._show_message("Select one text or GD&T frame first")
            return
        self._scene.begin_leader_placement(host)

    def _on_leader_placement_changed(self, active: bool) -> None:
        if active:
            self._tool_hint.set_hint(
                (
                    "Add leader",
                    "Click the point the leader designates · Esc to cancel",
                )
            )
            self._view.viewport().setCursor(Qt.CrossCursor)
        else:
            self._refresh_tool_hint()
            self._view.set_tool_cursor_for(self._tool_controller.tool())

    def _push_leaders(self, item, new: list, label: str) -> None:  # noqa: ANN001
        old = item.leaders()
        if new == old:
            return
        cmd = ChangePropsCommand([(item, "leaders", old, new)], label=label)
        stack = self._undo_group.activeStack()
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._on_annotations_changed()

    def _set_leader_end(self, item, index: int, style: EndStyle) -> None:  # noqa: ANN001
        from dataclasses import replace

        leaders = item.leaders()
        if 0 <= index < len(leaders):
            leaders[index] = replace(leaders[index], end=style)
            self._push_leaders(item, leaders, "Change leader end")

    def _remove_leader(self, item, index: int | None) -> None:  # noqa: ANN001
        """Remove leader `index`, or every leader when None."""
        leaders = item.leaders()
        if index is None:
            self._push_leaders(item, [], "Remove leaders")
        elif 0 <= index < len(leaders):
            del leaders[index]
            self._push_leaders(item, leaders, "Remove leader")

    def _remove_leader_bend(self, item, index: int, bend: int) -> None:  # noqa: ANN001
        leaders = item.leaders()
        leaders[index] = leaders[index].without_bend(bend)
        self._push_leaders(item, leaders, "Remove bend point")

    def _show_context_menu(self, global_pos, scene_pos) -> None:
        if self._scene.leader_placement_owner() is not None:
            # A right-click while picking a leader's target cancels it.
            self._scene.end_leader_placement()
            return
        if self._scene.poly_draft_active():
            # ...and while drawing a polyline it finishes it (CAD habit).
            self._scene.finish_poly_draft()
            return
        if self._scene.dimension_draft_active():
            # A half-placed dimension is dropped.
            self._scene.cancel_dimension_draft()
            return
        menu = self._build_context_menu(scene_pos)
        if menu is not None and menu.actions():
            menu.exec(global_pos)

    def _build_context_menu(self, scene_pos) -> AnnotationContextMenu | None:
        """The right-click menu for the click at `scene_pos` (Lot J):
        icon rows for the frequent commands, entries for the rest. See
        views/context_menu.py for the layout."""
        if self._doc is None:
            return None
        # The menu acts on the selection when the click lands on a
        # selected annotation -- even one hidden under an unselected one
        # (2026-09-28, same rule as dragging). Otherwise a right-click on
        # an annotation selects it, so the menu acts on what was clicked.
        clicked = self._scene.selected_annotation_at(scene_pos)
        if clicked is None:
            clicked = self._scene._topmost_annotation_at(scene_pos)
            if clicked is not None and not clicked.isSelected():
                for it in self._scene.selectedItems():
                    it.setSelected(False)
                clicked.setSelected(True)

        items = self._selected_annotations()
        color = self._gdt_icon_color()

        def icon(name: str):  # noqa: ANN202
            return line_icon(name, color)

        def tip(text: str, action=None) -> str:  # noqa: ANN001
            keys = action.shortcut().toString(QKeySequence.NativeText) if action else ""
            return f"{text} ({keys})" if keys else text

        menu = AnnotationContextMenu(self)
        has_clip = bool(self._clipboard)
        if not items:
            if has_clip:
                menu.add_entry(
                    icon("paste"), "Paste", self._paste_from_clipboard,
                    self.act_paste.shortcut().toString(QKeySequence.NativeText),
                )
            menu.add_entry(
                icon("select-all"), "Select All", self._select_all,
                self.act_select_all.shortcut().toString(QKeySequence.NativeText),
            )
            return menu

        menu.add_icon_row(
            [
                IconCommand("cut", icon("cut"), tip("Cut", self.act_cut),
                            self._cut_selected),
                IconCommand("copy", icon("copy"), tip("Copy", self.act_copy),
                            self._copy_selected),
                IconCommand("paste", icon("paste"),
                            tip("Paste", self.act_paste),
                            self._paste_from_clipboard, enabled=has_clip),
                IconCommand("duplicate", icon("duplicate"),
                            tip("Duplicate", self.act_duplicate),
                            self._duplicate_selected),
                IconCommand("delete", line_icon("trash", QColor(tokens_for(self._theme).danger)),
                            tip("Delete", self.act_delete),
                            self._delete_selected),
            ]
        )
        menu.addSeparator()
        menu.add_icon_row(
            [
                IconCommand("to_back", icon("to-back"),
                            tip("Send to back", self.act_send_back),
                            lambda: self._restack_selection(TO_BACK),
                            enabled=lambda: self._can_restack(TO_BACK)),
                IconCommand("lower", icon("lower"),
                            tip("Send backward: below the next annotation "
                                "it overlaps", self.act_lower),
                            lambda: self._restack_selection(LOWER),
                            enabled=lambda: self._can_restack(LOWER),
                            keep_open=True),
                IconCommand("raise", icon("raise"),
                            tip("Bring forward: above the next annotation "
                                "it overlaps", self.act_raise),
                            lambda: self._restack_selection(RAISE),
                            enabled=lambda: self._can_restack(RAISE),
                            keep_open=True),
                IconCommand("to_front", icon("to-front"),
                            tip("Bring to front", self.act_bring_front),
                            lambda: self._restack_selection(TO_FRONT),
                            enabled=lambda: self._can_restack(TO_FRONT)),
            ],
            caption="Order",
        )

        single = items[0] if len(items) == 1 else None
        host = self._leader_host(single)
        leader = None
        if host is not None and clicked is host:
            leader = host.leader_at(host.mapFromScene(scene_pos))
        rows_added = self._add_kind_rows(menu, single, color, leader)
        corner = None
        if single is not None and clicked is single:
            corner = bend_radius.corner_at(
                single, single.mapFromScene(scene_pos)
            )
        if corner is not None:
            if not rows_added:
                menu.addSeparator()
            self._add_radius_row(menu, single, corner, icon)
            rows_added = True
        if len(items) >= 2:
            if not rows_added:
                menu.addSeparator()
            self._add_align_row(menu, len(items), icon)
        menu.addSeparator()

        if single is not None and self._is_editable(single):
            menu.add_entry(
                icon("pencil"), self._edit_label(single), self._edit_selected,
                "Double-click",
            )
        if isinstance(single, DimensionItem) and not single.is_measured():
            menu.add_entry(
                icon("dimension"), "Use Measured Value",
                lambda it=single: self._use_measured_value(it),
            )
        if isinstance(single, LineItem):
            local = single.mapFromScene(scene_pos)
            bend = single.bend_at(local) if clicked is single else None
            if bend is not None:
                menu.add_entry(
                    icon("bend-remove"), "Remove Bend Point",
                    lambda it=single, i=bend: self._remove_bend(it, i),
                )
            elif clicked is single:
                menu.add_entry(
                    icon("bend-add"), "Add Bend Point",
                    lambda it=single, lp=local: self._add_bend_at(it, lp),
                )
            else:
                menu.add_entry(
                    icon("bend-add"), "Add Bend Point",
                    self._add_bend_to_selected,
                )
        if host is not None:
            menu.add_entry(icon("leader"), "Add Leader", lambda it=host: (
                self._scene.begin_leader_placement(it)
            ))
            if leader is not None:
                local = host.mapFromScene(scene_pos)
                bend = host.leader_bend_at(leader, local)
                if bend is not None:
                    menu.add_entry(
                        icon("bend-remove"), "Remove Bend Point",
                        lambda it=host, i=leader, j=bend: (
                            self._remove_leader_bend(it, i, j)
                        ),
                    )
                else:
                    menu.add_entry(
                        icon("bend-add"), "Add Bend Point",
                        lambda it=host, i=leader, lp=local: self._push_leaders(
                            it, it.leaders_with_bend_added(i, lp),
                            "Add bend point",
                        ),
                    )
                menu.add_entry(
                    icon("trash"), "Remove Leader",
                    lambda it=host, i=leader: self._remove_leader(it, i),
                )
            elif host.has_leaders():
                menu.add_entry(
                    icon("trash"), "Remove Leaders",
                    lambda it=host: self._remove_leader(it, None),
                )
        if len(items) >= 2:
            menu.add_entry(
                icon("group"), "Group", self._group_selected,
                self.act_group.shortcut().toString(QKeySequence.NativeText),
            )
        if self._scene.has_group_in_selection():
            menu.add_entry(
                icon("ungroup"), "Ungroup", self._ungroup_selected,
                self.act_ungroup.shortcut().toString(QKeySequence.NativeText),
            )
        menu.add_entry(icon("sliders"), "Properties", self._focus_properties)
        return menu

    @staticmethod
    def _is_editable(item: AnnotationItem) -> bool:
        return isinstance(
            item, (TextAnnotationItem, StickyNoteItem, GdtAnnotationItem)
        ) or hasattr(item, "begin_text_edit") or hasattr(item, "begin_edit")

    @staticmethod
    def _edit_label(item: AnnotationItem) -> str:
        if isinstance(item, GdtAnnotationItem):
            return "Edit Frame"
        if isinstance(item, DimensionItem):
            return "Edit Value"
        if isinstance(item, StickyNoteItem):
            return "Edit Note"
        return "Edit Text"

    def _add_kind_rows(
        self,
        menu: AnnotationContextMenu,
        item,  # noqa: ANN001
        color: QColor,
        leader: int | None = None,
    ) -> bool:
        """Rows specific to one selected annotation's kind. True when at
        least one row was added (a separator precedes them). `leader`:
        index of the leader under the click, whose end gets a row."""
        if item is None:
            return False
        rows: list[tuple[list[IconCommand | None], str, object]] = []
        if leader is not None:
            current = item.leaders()[leader].end
            rows.append(
                (
                    [
                        IconCommand(
                            f"leader:{style.value}",
                            end_icon(style, size=36, color=color),
                            f"Leader end: {label}",
                            lambda it=item, i=leader, st=style: (
                                self._set_leader_end(it, i, st)
                            ),
                            checked=style is current,
                        )
                        for style, label in END_STYLE_LABELS
                    ],
                    "Leader",
                    line_icon("leader", color),
                )
            )
        if isinstance(item, LineItem):
            for role, caption, glyph in (
                (HandleRole.P1, "Start", "line-start"),
                (HandleRole.P2, "End", "line-end"),
            ):
                current = self._endpoint_style(item, role)
                prefix = "start" if role is HandleRole.P1 else "end"
                cmds: list[IconCommand | None] = []
                for style, label in END_STYLE_LABELS:
                    cmds.append(
                        IconCommand(
                            f"{prefix}:{style.value}",
                            # Rendered at 2x the button's icon size (not
                            # the 64 px default) so the 2 px stroke
                            # stays legible once scaled down.
                            end_icon(style, size=36, color=color,
                                     mirrored=role is HandleRole.P1),
                            f"{caption}: {label}",
                            lambda it=item, r=role, st=style: (
                                self._set_endpoint_style(it, r, st)
                            ),
                            checked=style is current,
                        )
                    )
                rows.append((cmds, caption, line_icon(glyph, color)))
        if isinstance(item, DimensionItem):
            rows.append(
                (
                    [
                        IconCommand(
                            f"dim:{o.value}",
                            line_icon(f"dim-{o.value}", color),
                            f"{label} dimension",
                            lambda it=item, v=o: (
                                self._set_dimension_orientation(it, v)
                            ),
                            checked=o is item.orientation(),
                        )
                        for o, label in DIM_ORIENTATION_LABELS
                    ],
                    "Measure",
                    None,
                )
            )
            rows.append(
                (
                    [
                        IconCommand(
                            f"dim-end:{st.value}",
                            end_icon(st, size=36, color=color),
                            f"Ends: {label}",
                            lambda it=item, v=st: self._push_props(
                                [(it, "end_style", it.end_style(), v)],
                                "Dimension ends",
                            ),
                            checked=st is item.end_style(),
                        )
                        for st, label in DIM_END_LABELS
                    ],
                    "Ends",
                    None,
                )
            )
        outline = isinstance(item, (RectangleItem, CloudItem))
        fill = hasattr(item, "set_fill_enabled")
        if outline or fill:
            cmds = []
            if outline:
                cloudy = isinstance(item, CloudItem)
                cmds += [
                    IconCommand("outline:straight",
                                line_icon("rectangle", color),
                                "Straight outline",
                                lambda: self._set_selected_outline(False),
                                checked=not cloudy),
                    IconCommand("outline:cloud", line_icon("cloud", color),
                                "Revision cloud outline",
                                lambda: self._set_selected_outline(True),
                                checked=cloudy),
                ]
            if fill:
                if cmds:
                    cmds.append(None)
                filled = bool(item.fill_enabled())
                cmds.append(
                    IconCommand("fill", line_icon("fill", color),
                                "Remove fill" if filled else "Fill",
                                lambda v=not filled: self._set_selected_fill(v),
                                checked=filled)
                )
            rows.append((cmds, "Shape", None))
        if isinstance(item, (PolylineItem, PolygonItem)):
            closed = isinstance(item, PolygonItem)
            rows.append(
                (
                    [
                        IconCommand("path:open", line_icon("polyline", color),
                                    "Open path",
                                    lambda: self._set_selected_closed(False),
                                    checked=not closed),
                        IconCommand("path:closed",
                                    line_icon("polygon", color),
                                    "Closed shape",
                                    lambda: self._set_selected_closed(True),
                                    checked=closed),
                    ],
                    "Path",
                    None,
                )
            )
        if not rows:
            return False
        menu.addSeparator()
        for cmds, caption, caption_icon in rows:
            menu.add_icon_row(cmds, caption=caption, caption_icon=caption_icon)
        return True

    def _add_align_row(self, menu: AnnotationContextMenu, count: int, icon) -> None:  # noqa: ANN001
        cmds: list[IconCommand | None] = [
            IconCommand(f"align:{glyph}", icon(glyph), act.text().replace("&", ""),
                        act.trigger)
            for glyph, act in (
                ("align-left", self.act_align_left),
                ("align-center-h", self.act_align_center_h),
                ("align-right", self.act_align_right),
                ("align-top", self.act_align_top),
                ("align-middle-v", self.act_align_middle_v),
                ("align-bottom", self.act_align_bottom),
            )
        ]
        if count >= 3:
            cmds.append(None)
            cmds += [
                IconCommand("align:distribute-h", icon("distribute-h"),
                            "Distribute horizontally",
                            self.act_distribute_h.trigger),
                IconCommand("align:distribute-v", icon("distribute-v"),
                            "Distribute vertically",
                            self.act_distribute_v.trigger),
            ]
        menu.add_icon_row(cmds, caption="Align")

    # ------------------------------------------------------------------
    # line/arrow bend points
    # ------------------------------------------------------------------
    def _push_geom_change(
        self, item, old: object, new: object, label: str
    ) -> None:  # noqa: ANN001
        stack = self._undo_group.activeStack()
        cmd = ResizeCommand(item, old, new, label=label)
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()

    def _add_bend_at(self, item: LineItem, local_pos) -> None:  # noqa: ANN001
        old = item.geom_snapshot()
        item.insert_bend_near(local_pos)
        self._push_geom_change(
            item, old, item.geom_snapshot(), "Add bend point"
        )

    def _remove_bend(self, item: LineItem, index: int) -> None:
        old = item.geom_snapshot()
        item.remove_bend(index)  # the other bends keep their radius
        self._push_geom_change(
            item, old, item.geom_snapshot(), "Remove bend point"
        )

    # ------------------------------------------------------------------
    # corner radius on bends (2026-09-28)
    # ------------------------------------------------------------------
    def _add_radius_row(self, menu, item, corner, icon) -> None:  # noqa: ANN001
        """The context menu's Radius row for the bend under the click: a
        spin box (inspector unit) previewing live on that bend, and a
        button giving every bend of the annotation the same radius. The
        whole edit lands as one undo step when the menu closes; Escape
        in the spin box restores the radii the menu opened with."""
        unit = self._properties_dock.unit()
        per_pt = doc_info.MM_PER_PT if unit == "mm" else 1.0

        def to_px(value: float) -> float:
            return pt_to_px(value / per_pt)

        spin = MenuSpinBox()
        spin.setObjectName("ContextRadiusSpin")
        spin.setDecimals(2 if unit == "mm" else 1)
        spin.setRange(0.0, 1000.0 * per_pt)
        spin.setSingleStep(0.5 if unit == "mm" else 1.0)
        spin.setSuffix(f" {unit}")
        spin.setMinimumWidth(max(112, spin.sizeHint().width()))
        spin.setValue(px_to_pt(bend_radius.radius(item, corner)) * per_pt)
        spin.setToolTip(
            "Corner radius of this bend (0: sharp corner). "
            "Enter to apply, Esc to cancel"
        )
        spin.setAccessibleName("Bend radius")
        state = {"original": item.geom_snapshot()}

        def preview(_value: float) -> None:
            bend_radius.set_radius(item, corner, to_px(spin.value()))

        def commit() -> None:
            old, new = state["original"], item.geom_snapshot()
            if new == old:
                return
            state["original"] = new
            self._push_geom_change(item, old, new, "Bend radius")
            self._on_annotations_changed()

        def cancel() -> None:
            item.apply_geom(state["original"])
            menu.close()

        def apply_all() -> None:
            bend_radius.set_all_radii(item, to_px(spin.value()))
            commit()
            menu.close()

        def accept() -> None:
            commit()
            menu.close()

        spin.valueChanged.connect(preview)
        spin.accepted.connect(accept)
        spin.cancelled.connect(cancel)
        menu.aboutToHide.connect(commit)
        count = len(bend_radius.corners(item))
        menu.add_icon_row(
            [
                IconCommand(
                    "radius_all", icon("bend-radius-all"),
                    "Same radius on every bend", apply_all,
                    enabled=count > 1, keep_open=True,
                ),
            ],
            caption="Radius",
            caption_icon=icon("bend-radius"),
            leading=[spin],
        )

    # ------------------------------------------------------------------
    # line/arrow endpoint styles (context menu)
    # ------------------------------------------------------------------
    _ENDPOINT_HIT_RADIUS = 9.0  # screen pixels

    def _endpoint_at(self, item: LineItem, local: QPointF):  # noqa: ANN201
        """HandleRole.P1/P2 when `local` lands on an endpoint, else None."""
        p1, p2 = item.line_points()
        r2 = (self._ENDPOINT_HIT_RADIUS * item.screen_px()) ** 2
        for role, pt in ((HandleRole.P1, p1), (HandleRole.P2, p2)):
            dx, dy = local.x() - pt.x(), local.y() - pt.y()
            if dx * dx + dy * dy <= r2:
                return role
        return None

    @staticmethod
    def _endpoint_style(item: LineItem, role) -> EndStyle:  # noqa: ANN001
        if isinstance(item, ArrowItem):
            return (
                item.start_end() if role is HandleRole.P1 else item.end_end()
            )
        return EndStyle.NONE  # plain line: both ends bare

    def _set_endpoint_style(
        self, item: LineItem, role, style: EndStyle
    ) -> None:  # noqa: ANN001
        prop = "start_end" if role is HandleRole.P1 else "end_end"
        stack = self._undo_group.activeStack()
        if isinstance(item, ArrowItem):
            old = getattr(item, prop)()
            if old is style:
                return
            cmd = ChangePropsCommand(
                [(item, prop, old, style)], label="Change extremity shape"
            )
        else:
            # Plain LineItem has no end-style storage: promote it to an
            # ArrowItem (keeps bends/labels) with only the chosen end.
            if style is EndStyle.NONE:
                return
            arrow = line_to_arrow(item)
            arrow.set_start_end(EndStyle.NONE)
            arrow.set_end_end(EndStyle.NONE)
            getattr(arrow, f"set_{prop}")(style)
            cmd = ReplaceAnnotationCommand(
                self._scene,
                item.parentItem(),
                item,
                arrow,
                label="Change extremity shape",
            )
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()

    # ------------------------------------------------------------------
    # GD&T (M3) -- in-place editing
    # ------------------------------------------------------------------
    def _on_tool_changed(self, tool: Tool) -> None:
        # Keep the viewport cursor in sync with the active tool.
        self._view.set_tool_cursor_for(tool)
        self._sync_tool_flyout(tool)
        # Leaving Format Painter through any other path (Escape, picking
        # a drawing tool) must also un-toggle its button and drop the
        # captured style.
        if tool is not Tool.FORMAT_PAINTER and self.act_format_painter.isChecked():
            self.act_format_painter.setChecked(False)

    def _sync_tool_flyout(self, tool: Tool | None = None) -> None:
        """Show the variant flyout next to the active tool's rail button
        (Line / arrow, Stamp); hide it for every other tool."""
        fly = getattr(self, "_tool_flyout", None)
        if fly is None:
            return
        tool = tool if tool is not None else self._tool_controller.tool()
        anchor = self._tool_rail.button(tool)
        if (
            tool in (Tool.ARROW, Tool.STAMP)
            and anchor is not None
            and self._doc is not None
        ):
            fly.show_for(
                tool,
                self._tool_controller.line_kind(),
                self._tool_controller.stamp_preset(),
                anchor,
            )
        else:
            fly.dismiss()

    def _ask_custom_stamp(self) -> None:
        current, _color = self._tool_controller.stamp_preset()
        text, ok = QInputDialog.getText(
            self, "Custom stamp", "Stamp text:", text=current
        )
        text = text.strip()
        if ok and text:
            self._tool_controller.set_stamp_preset(
                text, self._tool_controller.color()
            )

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        if getattr(self, "_tool_flyout", None) is not None:
            if self._tool_flyout.isVisible():
                self._sync_tool_flyout()

    def _on_gdt_placement(self, scene_pos) -> None:
        page = self._scene.page_item()
        if page is None:
            return
        # Clicking elsewhere normally commits via the focus watcher, but
        # be defensive against paths that bypass it.
        self._commit_gdt_editor_if_open()
        # Draft item: parented directly, no undo entry yet. The commit
        # pushes the AddAnnotationCommand; cancel simply removes it
        # (same rollback contract as empty text annotations).
        item = GdtAnnotationItem(
            GdtState(characteristic=self._last_gdt_characteristic),
            scene_pos,
        )
        item.set_color(self._tool_controller.color())
        item.set_stroke(self._tool_controller.stroke())
        item.set_edit_callback(self._open_gdt_editor)
        item.set_sub_text_hook(self._scene.hook_text_item)
        item.setParentItem(page)
        self._open_gdt_inline(item, is_new=True)

    def _open_gdt_editor(self, item: GdtAnnotationItem) -> None:
        """Double-click entry point (edit callback on every GD&T item)."""
        self._commit_gdt_editor_if_open()
        self._open_gdt_inline(item, is_new=False)

    def _open_gdt_inline(
        self, item: GdtAnnotationItem, *, is_new: bool
    ) -> None:
        self._gdt_edit_item = item
        self._gdt_edit_is_new = is_new
        self._gdt_old_state = None if is_new else item.state()
        self._gdt_initial_state = item.state()
        editor = GdtFrameBuilder(
            item.state(),
            self._view.viewport(),
            tokens=tokens_for(self._theme),
            frame_color=item.color(),
            is_new=is_new,
        )
        editor.stateEdited.connect(self._on_gdt_state_edited)
        editor.committed.connect(self._commit_gdt_editor)
        editor.cancelled.connect(self._cancel_gdt_editor)
        editor.noteEditRequested.connect(self._edit_gdt_note)
        self._gdt_editor = editor
        self._position_gdt_editor()
        editor.open()

    def _on_gdt_state_edited(self, state: GdtState) -> None:
        # Live preview: the scene item itself shows every keystroke. The
        # builder only moves if the growing frame runs under it.
        if self._gdt_edit_item is not None:
            self._gdt_edit_item.set_state(state)
            self._position_gdt_editor(only_if_covering=True)

    def _commit_gdt_editor_if_open(self) -> None:
        if self._gdt_editor is not None:
            self._commit_gdt_editor()

    def _commit_gdt_editor(self, *, keep_empty: bool = False) -> None:
        """Close the GD&T panel, recording whatever it holds.

        `keep_empty` suppresses the discard-an-untouched-frame rule: the
        user is handing over to the note editor, so authoring continues
        and the frame must survive even though the panel itself is still
        blank.
        """
        editor = self._gdt_editor
        item = self._gdt_edit_item
        is_new = self._gdt_edit_is_new
        old_state = self._gdt_old_state
        if editor is None or item is None:
            return
        new_state = editor.current_state()
        initial_state = self._gdt_initial_state
        self._close_gdt_editor()
        self._last_gdt_characteristic = new_state.characteristic

        if is_new:
            # Untouched frame -> rollback, like an empty text annotation.
            if new_state == initial_state and not keep_empty:
                if item.scene() is not None:
                    self._scene.removeItem(item)
                return
            item.set_state(new_state)
            if item.scene() is not None:
                self._scene.removeItem(item)
            self._scene.push_add(item)
            return

        if old_state is None or new_state == old_state:
            return
        stack = self._undo_group.activeStack()
        cmd = ChangeGdtCommand(item, old_state, new_state)
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._on_annotations_changed()

    def _cancel_gdt_editor(self) -> None:
        item = self._gdt_edit_item
        is_new = self._gdt_edit_is_new
        old_state = self._gdt_old_state
        self._close_gdt_editor()
        if item is None:
            return
        if is_new:
            if item.scene() is not None:
                self._scene.removeItem(item)
        elif old_state is not None:
            item.set_state(old_state)

    def _close_gdt_editor(self) -> None:
        editor = self._gdt_editor
        self._gdt_editor = None
        self._gdt_edit_item = None
        self._gdt_edit_is_new = False
        self._gdt_old_state = None
        self._gdt_initial_state = None
        if editor is not None:
            editor.hide()
            editor.deleteLater()
        self._view.setFocus()

    def _position_gdt_editor(self, *, only_if_covering: bool = False) -> None:
        """Put the builder beside the frame, clamped to the viewport.

        Left of the frame first (frames grow to the right as they are
        typed), then right, then below / above; the body scrolls when the
        viewport is shorter than the builder. With `only_if_covering`
        the builder stays put unless the frame now runs under it.
        """
        editor = self._gdt_editor
        item = self._gdt_edit_item
        if editor is None or item is None:
            return
        vp = self._view.viewport()
        rect = item.mapToScene(item.content_rect()).boundingRect()
        frame = self._view.mapFromScene(rect).boundingRect()
        if only_if_covering and not editor.geometry().intersects(frame):
            return
        editor.set_max_height(vp.height() - 16)
        w, h = editor.width(), editor.height()
        gap = 16
        y = frame.top() - 24
        if frame.left() - gap - w >= 8:
            x = frame.left() - gap - w
        elif frame.right() + gap + w <= vp.width() - 8:
            x = frame.right() + gap
        else:
            # No room beside it: hug the viewport edge that hides the
            # least of the frame (the card at the top shows it anyway).
            left, right = 8, vp.width() - w - 8

            def covered(x0: int) -> int:
                return max(
                    0, min(x0 + w, frame.right()) - max(x0, frame.left())
                )

            x = left if covered(left) <= covered(right) else right
        x = max(8, min(x, vp.width() - w - 8))
        y = max(8, min(y, vp.height() - h - 8))
        editor.move(int(x), int(y))

    # ------------------------------------------------------------------
    # sticky note: floating editor lifecycle (mirrors the GD&T flow)
    # ------------------------------------------------------------------
    def _on_note_placement(self, scene_pos) -> None:
        page = self._scene.page_item()
        if page is None:
            return
        self._commit_note_editor_if_open()
        item = StickyNoteItem(scene_pos)
        item.set_color(self._tool_controller.color())
        item.set_stroke(self._tool_controller.stroke())
        item.set_edit_callback(self._open_note_editor)
        item.setParentItem(page)
        self._open_note_inline(item, is_new=True)

    def _open_note_editor(self, item: StickyNoteItem) -> None:
        """Double-click entry point (edit callback on every note item)."""
        self._commit_note_editor_if_open()
        self._open_note_inline(item, is_new=False)

    def _open_note_inline(
        self, item: StickyNoteItem, *, is_new: bool
    ) -> None:
        self._note_edit_item = item
        self._note_edit_is_new = is_new
        self._note_old_text = None if is_new else item.text()
        editor = NoteEditor(item.text(), self._view.viewport())
        editor.committed.connect(self._commit_note_editor)
        editor.cancelled.connect(self._cancel_note_editor)
        self._note_editor = editor
        self._position_note_editor()
        editor.open()

    def _commit_note_editor_if_open(self) -> None:
        if self._note_editor is not None:
            self._commit_note_editor()

    def _commit_note_editor(self) -> None:
        editor = self._note_editor
        item = self._note_edit_item
        is_new = self._note_edit_is_new
        old_text = self._note_old_text
        if editor is None or item is None:
            return
        new_text = editor.current_text()
        self._close_note_editor()

        if is_new:
            # An empty new note rolls back, like an empty text annotation.
            if not new_text.strip():
                if item.scene() is not None:
                    self._scene.removeItem(item)
                return
            item.set_text(new_text)
            if item.scene() is not None:
                self._scene.removeItem(item)
            self._scene.push_add(item)
            return

        if old_text is None or new_text == old_text:
            return
        stack = self._undo_group.activeStack()
        cmd = ChangePropsCommand([(item, "text", old_text, new_text)])
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()
        self._on_annotations_changed()

    def _cancel_note_editor(self) -> None:
        item = self._note_edit_item
        is_new = self._note_edit_is_new
        old_text = self._note_old_text
        self._close_note_editor()
        if item is None:
            return
        if is_new:
            if item.scene() is not None:
                self._scene.removeItem(item)
        elif old_text is not None:
            item.set_text(old_text)

    def _close_note_editor(self) -> None:
        editor = self._note_editor
        self._note_editor = None
        self._note_edit_item = None
        self._note_edit_is_new = False
        self._note_old_text = None
        if editor is not None:
            editor.hide()
            editor.deleteLater()
        self._view.setFocus()

    def _position_note_editor(self) -> None:
        editor = self._note_editor
        item = self._note_edit_item
        if editor is None or item is None:
            return
        editor.adjustSize()
        rect = item.mapToScene(item.content_rect()).boundingRect()
        vp = self._view.viewport()
        below = self._view.mapFromScene(rect.bottomRight())
        x = below.x() + 8
        y = below.y() + 8
        if x + editor.width() > vp.width() - 4:
            x = self._view.mapFromScene(rect.topLeft()).x() - editor.width() - 8
        if y + editor.height() > vp.height() - 4:
            y = vp.height() - editor.height() - 4
        x = max(4, min(x, vp.width() - editor.width() - 4))
        y = max(4, min(y, vp.height() - editor.height() - 4))
        editor.move(int(x), int(y))

    # ------------------------------------------------------------------
    # drag & drop
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(
            self._is_openable_file(u.toLocalFile())
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            if self._central.currentWidget() is self._welcome:
                self._welcome.set_drag_active(True)
            return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._welcome.set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._welcome.set_drag_active(False)
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if self._is_openable_file(local):
                self._open_path(local)
                event.acceptProposedAction()
                return
        event.ignore()

    @classmethod
    def _is_openable_file(cls, path: str) -> bool:
        lower = path.lower()
        return lower.endswith(".pdf") or lower.endswith(
            tuple(cls._IMAGE_SUFFIXES)
        )

    # ------------------------------------------------------------------
    # close
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # theme + prefs
    # ------------------------------------------------------------------
    def _set_theme(self, theme: Theme) -> None:
        self._theme = theme
        apply_theme(theme)
        self.act_theme_light.setChecked(theme is Theme.LIGHT)
        self.act_theme_dark.setChecked(theme is Theme.DARK)
        # Code-drawn icons are pre-rasterized, so they need an explicit
        # repaint when the theme changes (light glyph on dark, vice versa).
        tokens = tokens_for(theme)
        self._tool_rail.set_colors(
            QColor(tokens.icon),
            QColor(tokens.soft_text),
            mark=QColor(tokens.text_muted),
        )
        self._tool_flyout.set_colors(
            QColor(tokens.icon), QColor(tokens.accent)
        )
        self._apply_icon_theme()
        self._properties_dock.set_icon_color(
            self._gdt_icon_color(), QColor(tokens.text)
        )
        self._welcome.set_colors(tokens)
        if self._gdt_editor is not None:
            self._gdt_editor.set_colors(tokens)
        if self._command_palette is not None:
            self._command_palette.set_colors(tokens)

    def _gdt_icon_color(self) -> QColor:
        """Glyph color for code-drawn icons, from the theme tokens."""
        return QColor(tokens_for(self._theme).icon)

    def _restore_settings(self) -> None:
        geom = self._settings.value("window/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        state = self._settings.value("window/state")
        if state is not None:
            self.restoreState(state, _WINDOW_STATE_VERSION)
            self._layout_state = QByteArray(state)
        theme_name = str(self._settings.value("ui/theme", Theme.LIGHT.value))
        try:
            theme = Theme(theme_name)
        except ValueError:
            theme = Theme.LIGHT
        self._set_theme(theme)
        hints = self._settings.value("ui/show_tool_hints", True, type=bool)
        self.act_show_hints.setChecked(bool(hints))

    def _save_settings(self) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())
        if not self._welcome_mode:
            state = self.saveState(_WINDOW_STATE_VERSION)
        else:
            # The start page hides the panels; save the layout the user
            # works in instead (or nothing, if there never was one).
            state = self._layout_state
        if state is not None:
            self._settings.setValue("window/state", state)
        self._settings.setValue("ui/theme", self._theme.value)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if not self._confirm_discard_changes():
            event.ignore()
            return
        self._save_settings()
        self._on_close()
        super().closeEvent(event)
