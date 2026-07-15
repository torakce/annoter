"""MainWindow: hosts the central PdfView and the dockable panels.

M1 scope: file open/close, MRU, page navigation, zoom (incl. zoom
window), pan, page rotation, drag&drop, status bar. No annotations.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QSize, Qt, QSettings, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QUndoGroup,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolBar,
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
from annoter.controllers.align import AlignMode, compute_align_moves
from annoter.controllers.convert import (
    convert_poly_closed,
    convert_shape_outline,
    line_to_arrow,
)
from annoter.controllers.geometry import item_scene_rect
from annoter.controllers.commands import (
    AddAnnotationCommand,
    ChangeColorCommand,
    ChangeGdtCommand,
    ChangePropsCommand,
    ChangeStrokeCommand,
    DeleteAnnotationsCommand,
    MoveAnnotationsCommand,
    ReplaceAnnotationCommand,
    ResizeCommand,
)
from annoter.controllers.tools import Tool, ToolController
from annoter.model.document import PdfDocument
from annoter.model.gdt import GdtState
from annoter.model.styles import END_STYLE_LABELS, EndStyle, HandleRole
from annoter.services.pdf_export import (
    read_annotations,
    write_annotations,
)
from annoter.services.pdf_render import PageRenderer
from annoter.services.recent_files import RecentFiles
from annoter.services.theme import Theme, apply as apply_theme
from annoter.views.annotation_list import AnnotationListDock
from annoter.views.color_picker import popup_color_picker
from annoter.views.gdt_editor import GdtInlineEditor
from annoter.views.icons import action_icon, color_swatch_icon, end_icon
from annoter.views.items.base import AnnotationItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.note import StickyNoteItem
from annoter.views.note_editor import NoteEditor
from annoter.views.page_thumbnails import PageThumbnailDock
from annoter.views.pdf_scene import PdfScene
from annoter.views.pdf_view import PdfView
from annoter.views.properties_dock import PropertiesDock
from annoter.views.selection_toolbar import SelectionToolbar
from annoter.views.stroke_spin import STROKE_LADDER, StrokeSpinBox
from annoter.views.tool_palette import ToolPalette
from annoter.views.welcome_screen import WelcomeScreen


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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Annoter")
        self.resize(1280, 800)
        self.setAcceptDrops(True)

        self._theme: Theme = Theme.LIGHT
        self._doc: PdfDocument | None = None
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
        self._gdt_editor: GdtInlineEditor | None = None
        self._gdt_edit_item: GdtAnnotationItem | None = None
        self._gdt_edit_is_new: bool = False
        self._gdt_old_state: GdtState | None = None

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
        self._welcome = WelcomeScreen(self)
        self._welcome.openRequested.connect(self._on_open)
        self._welcome.blankRequested.connect(self._new_blank_document)
        self._welcome.openPathRequested.connect(self._open_path)
        self._welcome.removePathRequested.connect(self._recent_remove)
        self._central = QStackedWidget(self)
        self._central.addWidget(self._welcome)
        self._central.addWidget(self._view)
        self.setCentralWidget(self._central)
        self._view.setFocus()

        # Floating contextual action bar shown near the current selection.
        st = SelectionToolbar(self._view.viewport())
        self._selection_toolbar = st
        st.editClicked.connect(self._edit_selected)
        st.outlinePicked.connect(self._set_selected_outline)
        st.fillToggled.connect(self._set_selected_fill)
        st.endStylePicked.connect(self._on_pill_end_style)
        st.addBendClicked.connect(self._add_bend_to_selected)
        st.closedToggled.connect(self._set_selected_closed)
        st.groupClicked.connect(self._group_selected)
        st.ungroupClicked.connect(self._ungroup_selected)
        st.duplicateClicked.connect(self._duplicate_selected)
        st.deleteClicked.connect(self._delete_selected)
        # Stability: hide the pill during interactive drags/resizes and
        # re-show it (repositioned) on release; refresh it after every
        # undo-stack change so its buttons track the item's real state.
        self._scene.interactiveDragChanged.connect(self._on_drag_state)
        self._undo_group.indexChanged.connect(
            lambda _i: self._update_selection_toolbar()
        )

        # In-app clipboard: detached clones produced by Copy/Cut. Paste
        # re-clones from these so multiple pastes work and the clipboard
        # stays independent from any subsequent scene mutation.
        self._clipboard: list[AnnotationItem] = []

        # Style captured by the Format Painter toggle, applied to every
        # annotation clicked afterwards until it is toggled off.
        self._format_paint_style: dict[str, object] | None = None

        self._tool_palette = ToolPalette(self._tool_controller, self)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._tool_palette)

        self._thumbnail_dock = PageThumbnailDock(self)
        self._thumbnail_dock.pageClicked.connect(self._show_page)
        self._thumbnail_dock.pageMoved.connect(self._on_page_reordered)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._thumbnail_dock)

        self._annotation_list = AnnotationListDock(self)
        self._annotation_list.deleteRequested.connect(self._delete_selected)
        self.addDockWidget(Qt.RightDockWidgetArea, self._annotation_list)

        self._properties_dock = PropertiesDock(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self._properties_dock)
        self.tabifyDockWidget(self._annotation_list, self._properties_dock)

        self._recent = RecentFiles(MAX_RECENT_FILES, self)
        self._recent.changed.connect(self._refresh_recent_menu)
        self._recent.changed.connect(self._refresh_welcome_recent)
        self._welcome.set_recent(self._recent.list())

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()
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
        self._settings = QSettings("Annoter", "Annoter")
        self._restore_settings()

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

        self.act_bring_front = QAction("Bring to &Front", self)
        self.act_bring_front.setShortcut(QKeySequence("Ctrl+Shift+]"))
        self.act_bring_front.triggered.connect(
            lambda: self._reorder_selection(to_front=True)
        )

        self.act_send_back = QAction("Send to &Back", self)
        self.act_send_back.setShortcut(QKeySequence("Ctrl+Shift+["))
        self.act_send_back.triggered.connect(
            lambda: self._reorder_selection(to_front=False)
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

    def _build_menus(self) -> None:
        mb = self.menuBar()

        m_file = mb.addMenu("&File")
        m_file.addAction(self.act_open)
        m_file.addAction(self.act_save)
        m_file.addAction(self.act_save_as)
        m_file.addAction(self.act_export_images)
        m_file.addSeparator()
        m_file.addAction(self.act_insert_pdf)
        m_file.addAction(self.act_resize_doc)
        m_file.addSeparator()
        m_file.addAction(self.act_close)
        m_file.addSeparator()
        self._menu_recent = m_file.addMenu("Recent &Files")
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

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
        m_edit.addAction(self.act_send_back)
        m_edit.addSeparator()
        m_edit.addAction(self.act_group)
        m_edit.addAction(self.act_ungroup)
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
            self._tool_palette,
            self._thumbnail_dock,
            self._annotation_list,
            self._properties_dock,
        ):
            m_panels.addAction(dock.toggleViewAction())
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

    def _build_toolbar(self) -> None:
        tb = QToolBar("Quick Access", self)
        tb.setObjectName("MainToolBar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(Qt.TopToolBarArea, tb)
        self._toolbar = tb

        tb.addAction(self.act_open)
        tb.addAction(self.act_save)
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()

        # Office-style quick style controls, bound to the ToolController
        # (the left Tools dock stays the single place to pick a tool).
        self._toolbar_color_act = QAction("Color", self)
        self._toolbar_color_act.setToolTip(
            "Drawing color (applies to the next annotation)"
        )
        self._toolbar_color_act.triggered.connect(
            self._pick_toolbar_color
        )
        tb.addAction(self._toolbar_color_act)

        self._toolbar_stroke_spin = StrokeSpinBox()
        self._toolbar_stroke_spin.setToolTip(
            "Stroke width (type a value, arrows step through presets)"
        )
        self._toolbar_stroke_spin.valueChanged.connect(
            lambda v: self._on_quick_stroke_picked(float(v))
        )
        tb.addWidget(self._toolbar_stroke_spin)

        self._tool_controller.colorChanged.connect(
            self._sync_toolbar_color
        )
        self._tool_controller.strokeChanged.connect(
            self._sync_toolbar_stroke
        )
        self._sync_toolbar_color(self._tool_controller.color())
        self._sync_toolbar_stroke(self._tool_controller.stroke())

        tb.addSeparator()
        tb.addAction(self.act_format_painter)

        tb.addSeparator()
        tb.addAction(self.act_zoom_out)
        tb.addAction(self.act_zoom_in)
        tb.addAction(self.act_zoom_fit)
        tb.addAction(self.act_zoom_actual)

        # Tooltips advertise the keyboard shortcut where one exists.
        for act in tb.actions():
            seq = act.shortcut()
            if not seq.isEmpty():
                plain = act.text().replace("&", "")
                act.setToolTip(f"{plain} ({seq.toString()})")

        self._apply_icon_theme()

    def _apply_icon_theme(self) -> None:
        """Repaint code-drawn toolbar icons in the theme's glyph color."""
        c = self._gdt_icon_color()
        self.act_open.setIcon(action_icon("open", color=c))
        self.act_save.setIcon(action_icon("save", color=c))
        self.act_undo.setIcon(action_icon("undo", color=c))
        self.act_redo.setIcon(action_icon("redo", color=c))
        self.act_zoom_in.setIcon(action_icon("zoom-in", color=c))
        self.act_zoom_out.setIcon(action_icon("zoom-out", color=c))
        self.act_zoom_fit.setIcon(action_icon("zoom-fit", color=c))
        self.act_zoom_actual.setIcon(action_icon("zoom-actual", color=c))
        self.act_format_painter.setIcon(action_icon("format-painter", color=c))

    # ------------------------------------------------------------------
    # toolbar quick style controls
    # ------------------------------------------------------------------
    def _pick_toolbar_color(self) -> None:
        # Anchor the popup under the toolbar button, like Office.
        widget = self._toolbar.widgetForAction(self._toolbar_color_act)
        pos = (
            widget.mapToGlobal(widget.rect().bottomLeft())
            if widget is not None
            else None
        )
        popup_color_picker(
            self,
            self._tool_controller.color(),
            self._on_quick_color_picked,
            global_pos=pos,
        )

    def _on_quick_color_picked(self, color: QColor) -> None:
        """Toolbar color choice: sets the drawing color for future
        annotations AND recolors the current selection (undoably), like
        Office's color controls."""
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
        """Toolbar stroke choice: same dual behavior as the color."""
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

    def _sync_toolbar_color(self, color: QColor) -> None:
        self._toolbar_color_act.setIcon(color_swatch_icon(color))

    def _sync_toolbar_stroke(self, width: float) -> None:
        spin = self._toolbar_stroke_spin
        # Programmatic sync must not loop back into the quick-stroke
        # handler (which would restyle the selection as a side effect).
        spin.blockSignals(True)
        spin.setValue(max(spin.minimum(), int(round(width))))
        spin.blockSignals(False)

    def _build_status_bar(self) -> None:
        self._lbl_path = QLabel("")
        # Flat button, not a label: one click opens Go to Page, making
        # the page indicator an obvious navigation affordance.
        self._lbl_page = QPushButton("-")
        self._lbl_page.setFlat(True)
        self._lbl_page.setCursor(Qt.PointingHandCursor)
        self._lbl_page.setToolTip("Go to page... (Ctrl+Alt+G)")
        self._lbl_page.clicked.connect(self._goto_page_dialog)
        self._lbl_zoom = QLabel("100 %")
        sb = self.statusBar()
        sb.addWidget(self._lbl_path, 1)
        sb.addPermanentWidget(self._lbl_page)
        sb.addPermanentWidget(self._lbl_zoom)

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
            self.act_cut,
            self.act_copy,
            self.act_paste,
            self.act_duplicate,
            self.act_bring_front,
            self.act_send_back,
        ):
            a.setEnabled(has_doc)
        if hasattr(self, "_lbl_page"):
            self._lbl_page.setEnabled(has_doc)

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
                if isinstance(it, GdtAnnotationItem):
                    it.set_edit_callback(self._open_gdt_editor)
                elif isinstance(it, StickyNoteItem):
                    it.set_edit_callback(self._open_note_editor)
        self._is_untitled = untitled
        self._doc_structure_dirty = False
        if add_to_recent:
            self._recent.add(path)
        self._refresh_window_title()
        self._thumbnail_dock.set_document(self._renderer, doc.page_count)
        self._central.setCurrentWidget(self._view)
        self._show_page(0, _is_initial=True)
        # Defer fit to let the layout settle when called during startup.
        QTimer.singleShot(0, self._view.zoom_to_fit)
        self._update_actions_enabled()

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
        self.setWindowModified(self._has_unsaved_changes())

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
        self._selection_toolbar.hide()
        if self._doc is not None:
            self._doc.close()
        self._doc = None
        self._renderer = None
        for stack in self._page_stacks.values():
            self._undo_group.removeStack(stack)
        self._page_stacks = {}
        self._page_items = {}
        self._scene.clear_page()
        self._page_index = 0
        self._is_untitled = False
        self._annotation_list.set_page_item(None)
        self._thumbnail_dock.set_document(None)
        self._refresh_window_title()
        self._lbl_page.setText("-")
        self._central.setCurrentWidget(self._welcome)
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
        self._thumbnail_dock.set_document(
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
                    if isinstance(it, GdtAnnotationItem):
                        it.set_edit_callback(self._open_gdt_editor)
                    elif isinstance(it, StickyNoteItem):
                        it.set_edit_callback(self._open_note_editor)
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
        self.statusBar().showMessage(
            f"Exported {len(written)} file(s)", 5000
        )

    def _on_grayscale_toggled(self, checked: bool) -> None:
        if self._renderer is not None:
            self._renderer.set_grayscale(checked)
            self._stash_current_page_items()
            self._thumbnail_dock.set_document(
                self._renderer, self._doc.page_count
            )
            self._show_page(self._page_index, _is_initial=True)

    def _refresh_window_title(self) -> None:
        # [*] is Qt's windowModified placeholder: it renders as "*" while
        # setWindowModified(True) and disappears otherwise.
        if self._doc is None:
            self.setWindowTitle("Annoter")
            self._lbl_path.setText("")
            self.setWindowModified(False)
        else:
            self.setWindowTitle(f"{self._doc.path.name}[*] - Annoter")
            self._lbl_path.setText(str(self._doc.path))
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
        self._annotation_list.set_page_item(self._scene.page_item())

        self._lbl_page.setText(
            f"Page {index + 1} / {self._doc.page_count}"
        )
        self._thumbnail_dock.set_current_page(index)
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
        self._lbl_zoom.setText(f"{factor * 100:.0f} %")
        self._maybe_rerender_for_zoom(factor)
        if hasattr(self, "_hires_timer"):
            self._hires_timer.start()
        self._position_gdt_editor()
        self._position_note_editor()
        self._position_selection_toolbar()

    def _on_view_scrolled(self, _value: int) -> None:
        if hasattr(self, "_hires_timer"):
            self._hires_timer.start()
        self._position_gdt_editor()
        self._position_note_editor()
        self._position_selection_toolbar()

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
            self.statusBar().showMessage(
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
        self.statusBar().showMessage(
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
        self._annotation_list.refresh()

    def _on_scene_selection_changed(self) -> None:
        self._annotation_list.sync_selection_from_scene()
        items = self._selected_annotations()
        self._properties_dock.set_items(items)
        self._update_selection_toolbar(items)

    def _update_selection_toolbar(
        self, items: list[AnnotationItem] | None = None
    ) -> None:
        if items is None:
            items = self._selected_annotations()
        if not items:
            self._selection_toolbar.hide()
            return
        align_actions = [
            self.act_align_left,
            self.act_align_center_h,
            self.act_align_right,
            self.act_align_top,
            self.act_align_middle_v,
            self.act_align_bottom,
        ]
        if len(items) >= 3:
            align_actions += [self.act_distribute_h, self.act_distribute_v]
        self._selection_toolbar.set_context(
            items,
            align_actions=align_actions,
            has_group=self._scene.has_group_in_selection(),
            icon_color=self._gdt_icon_color(),
        )
        self._position_selection_toolbar(items)

    def _on_drag_state(self, active: bool) -> None:
        if active:
            self._selection_toolbar.hide()
        else:
            self._update_selection_toolbar()

    # ------------------------------------------------------------------
    # selection pill handlers
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

    def _on_pill_end_style(self, role, style) -> None:  # noqa: ANN001
        item = self._single_selected()
        if isinstance(item, LineItem):
            self._set_endpoint_style(item, role, style)

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

    def _position_selection_toolbar(
        self, items: list[AnnotationItem] | None = None
    ) -> None:
        if items is None:
            items = self._selected_annotations()
        if not items:
            self._selection_toolbar.hide()
            return
        union = None
        for it in items:
            r = item_scene_rect(it)
            union = r if union is None else union.united(r)
        toolbar = self._selection_toolbar
        toolbar.adjustSize()
        vp = self._view.viewport()
        above = self._view.mapFromScene(union.topLeft())
        below = self._view.mapFromScene(union.bottomLeft())
        x = above.x()
        y = above.y() - toolbar.height() - 8
        if y < 4:
            y = below.y() + 8
        x = max(4, min(x, vp.width() - toolbar.width() - 4))
        y = max(4, min(y, vp.height() - toolbar.height() - 4))
        toolbar.move(int(x), int(y))
        toolbar.show()
        toolbar.raise_()

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
            if isinstance(c, GdtAnnotationItem):
                c.set_edit_callback(self._open_gdt_editor)
            elif isinstance(c, StickyNoteItem):
                c.set_edit_callback(self._open_note_editor)
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

    def _reorder_selection(self, to_front: bool) -> None:
        items = self._selected_annotations()
        page = self._scene.page_item()
        if not items or page is None:
            return
        siblings = [
            c
            for c in page.childItems()
            if isinstance(c, AnnotationItem)
        ]
        if not siblings:
            return
        z_values = [s.zValue() for s in siblings]
        if to_front:
            top = max(z_values) if z_values else 0.0
            for i, it in enumerate(items):
                it.setZValue(top + 1.0 + i)
        else:
            bottom = min(z_values) if z_values else 0.0
            for i, it in enumerate(items):
                it.setZValue(bottom - 1.0 - i)

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

    def _show_context_menu(self, global_pos, scene_pos) -> None:
        if self._doc is None:
            return
        # If the right-click landed on an annotation that wasn't part of
        # the current selection, select it so the menu actions target it.
        clicked = self._scene._topmost_annotation_at(scene_pos)
        if clicked is not None and not clicked.isSelected():
            for it in self._scene.selectedItems():
                it.setSelected(False)
            clicked.setSelected(True)

        has_sel = bool(self._selected_annotations())
        has_clip = bool(self._clipboard)
        can_edit_text = has_sel and any(
            hasattr(it, "begin_text_edit") or hasattr(it, "begin_edit")
            for it in self._selected_annotations()
        )

        menu = QMenu(self)
        # Line/arrow point actions (Discussion #1, item 5 + follow-up):
        # right-click on an ENDPOINT offers its extremity shape, on a
        # BEND offers removal, anywhere else on the item offers
        # inserting a bend at the click position.
        if isinstance(clicked, LineItem):
            local = clicked.mapFromScene(scene_pos)
            endpoint = self._endpoint_at(clicked, local)
            bend_idx = clicked.bend_at(local)
            if endpoint is not None:
                sub = menu.addMenu("Extremity shape")
                current = self._endpoint_style(clicked, endpoint)
                icon_color = self._gdt_icon_color()
                for style, label in END_STYLE_LABELS:
                    act = sub.addAction(end_icon(style, color=icon_color), label)
                    act.setCheckable(True)
                    act.setChecked(style is current)
                    act.triggered.connect(
                        lambda _c=False, it=clicked, ep=endpoint, st=style: (
                            self._set_endpoint_style(it, ep, st)
                        )
                    )
            elif bend_idx is not None:
                act = menu.addAction("Remove bend point")
                act.triggered.connect(
                    lambda _c=False, it=clicked, i=bend_idx: (
                        self._change_bends(
                            it, [b for j, b in enumerate(it.bends()) if j != i]
                        )
                    )
                )
            else:
                act = menu.addAction("Add bend point")
                act.triggered.connect(
                    lambda _c=False, it=clicked, lp=local: (
                        self._add_bend_at(it, lp)
                    )
                )
            menu.addSeparator()
        if has_sel:
            menu.addAction(self.act_cut)
            menu.addAction(self.act_copy)
        if has_clip:
            menu.addAction(self.act_paste)
        if has_sel:
            menu.addAction(self.act_duplicate)
            menu.addSeparator()
            menu.addAction(self.act_delete)
            menu.addSeparator()
            menu.addAction(self.act_bring_front)
            menu.addAction(self.act_send_back)
            if len(self._selected_annotations()) >= 2:
                menu.addSeparator()
                menu.addAction(self.act_group)
            if self._scene.has_group_in_selection():
                menu.addAction(self.act_ungroup)
            if len(self._selected_annotations()) >= 2:
                menu.addSeparator()
                m_align = menu.addMenu("Align")
                m_align.addAction(self.act_align_left)
                m_align.addAction(self.act_align_center_h)
                m_align.addAction(self.act_align_right)
                m_align.addSeparator()
                m_align.addAction(self.act_align_top)
                m_align.addAction(self.act_align_middle_v)
                m_align.addAction(self.act_align_bottom)
                if len(self._selected_annotations()) >= 3:
                    m_align.addSeparator()
                    m_align.addAction(self.act_distribute_h)
                    m_align.addAction(self.act_distribute_v)
            menu.addSeparator()
            if can_edit_text:
                menu.addAction(self.act_edit_text)
            menu.addAction(self.act_change_color)
            menu.addAction(self.act_change_stroke)
            menu.addSeparator()
            menu.addAction(self.act_focus_properties)
        else:
            menu.addAction(self.act_select_all)
            if has_clip:
                menu.addSeparator()
                menu.addAction(self.act_paste)

        if menu.actions():
            menu.exec(global_pos)

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

    def _change_bends(self, item: LineItem, bends: list) -> None:
        old = item.geom_snapshot()
        item.set_bends(bends)
        self._push_geom_change(
            item, old, item.geom_snapshot(), "Remove bend point"
        )

    # ------------------------------------------------------------------
    # line/arrow endpoint styles (context menu)
    # ------------------------------------------------------------------
    _ENDPOINT_HIT_RADIUS = 9.0

    def _endpoint_at(self, item: LineItem, local: QPointF):  # noqa: ANN201
        """HandleRole.P1/P2 when `local` lands on an endpoint, else None."""
        p1, p2 = item.line_points()
        r2 = self._ENDPOINT_HIT_RADIUS**2
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
        # Leaving Format Painter through any other path (Escape, picking
        # a drawing tool) must also un-toggle its button and drop the
        # captured style.
        if tool is not Tool.FORMAT_PAINTER and self.act_format_painter.isChecked():
            self.act_format_painter.setChecked(False)

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
        item = GdtAnnotationItem(GdtState(), scene_pos)
        item.set_color(self._tool_controller.color())
        item.set_stroke(self._tool_controller.stroke())
        item.set_edit_callback(self._open_gdt_editor)
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
        editor = GdtInlineEditor(
            item.state(),
            self._view.viewport(),
            icon_color=self._gdt_icon_color(),
        )
        editor.stateEdited.connect(self._on_gdt_state_edited)
        editor.committed.connect(self._commit_gdt_editor)
        editor.cancelled.connect(self._cancel_gdt_editor)
        self._gdt_editor = editor
        self._position_gdt_editor()
        editor.open()

    def _on_gdt_state_edited(self, state: GdtState) -> None:
        # Live preview: the scene item itself shows every keystroke.
        if self._gdt_edit_item is not None:
            self._gdt_edit_item.set_state(state)
            self._position_gdt_editor()

    def _commit_gdt_editor_if_open(self) -> None:
        if self._gdt_editor is not None:
            self._commit_gdt_editor()

    def _commit_gdt_editor(self) -> None:
        editor = self._gdt_editor
        item = self._gdt_edit_item
        is_new = self._gdt_edit_is_new
        old_state = self._gdt_old_state
        if editor is None or item is None:
            return
        new_state = editor.current_state()
        self._close_gdt_editor()

        if is_new:
            # Untouched frame -> rollback, like an empty text annotation.
            if new_state == GdtState():
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
        if editor is not None:
            editor.hide()
            editor.deleteLater()
        self._view.setFocus()

    def _position_gdt_editor(self) -> None:
        """Anchor the editor under the frame, clamped to the viewport."""
        editor = self._gdt_editor
        item = self._gdt_edit_item
        if editor is None or item is None:
            return
        editor.adjustSize()
        rect = item.mapToScene(item.content_rect()).boundingRect()
        vp = self._view.viewport()
        below = self._view.mapFromScene(rect.bottomLeft())
        x = below.x()
        y = below.y() + 8
        if y + editor.height() > vp.height() - 4:
            above = self._view.mapFromScene(rect.topLeft())
            y = above.y() - editor.height() - 8
        x = max(4, min(x, vp.width() - editor.width() - 4))
        y = max(4, min(y, vp.height() - editor.height() - 4))
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
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
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
        self._tool_palette.set_icon_color(self._gdt_icon_color())
        self._apply_icon_theme()
        self._properties_dock.set_icon_color(self._gdt_icon_color())

    def _gdt_icon_color(self) -> QColor:
        return (
            QColor("#e0e0e0")
            if self._theme is Theme.DARK
            else QColor("#212121")
        )

    def _restore_settings(self) -> None:
        geom = self._settings.value("window/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        state = self._settings.value("window/state")
        if state is not None:
            self.restoreState(state)
        theme_name = str(self._settings.value("ui/theme", Theme.LIGHT.value))
        try:
            theme = Theme(theme_name)
        except ValueError:
            theme = Theme.LIGHT
        self._set_theme(theme)

    def _save_settings(self) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/state", self.saveState())
        self._settings.setValue("ui/theme", self._theme.value)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if not self._confirm_discard_changes():
            event.ignore()
            return
        self._save_settings()
        self._on_close()
        super().closeEvent(event)
