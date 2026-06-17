"""MainWindow: hosts the central PdfView and the dockable panels.

M1 scope: file open/close, MRU, page navigation, zoom (incl. zoom
window), pan, page rotation, drag&drop, status bar. No annotations.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QSettings, QTimer
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
    QColorDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
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
    STROKE_WIDTHS,
    UNDO_STACK_LIMIT,
)
from annoter.controllers.commands import (
    AddAnnotationCommand,
    ChangeColorCommand,
    ChangeGdtCommand,
    ChangePropsCommand,
    ChangeStrokeCommand,
    DeleteAnnotationsCommand,
)
from annoter.controllers.tools import Tool, ToolController
from annoter.model.document import PdfDocument
from annoter.model.gdt import GdtState
from annoter.services.pdf_export import (
    read_annotations,
    write_annotations,
)
from annoter.services.pdf_render import PageRenderer
from annoter.services.recent_files import RecentFiles
from annoter.services.theme import Theme, apply as apply_theme
from annoter.views.annotation_list import AnnotationListDock
from annoter.views.gdt_editor import GdtInlineEditor
from annoter.views.icons import action_icon, tool_icon
from annoter.views.items.base import AnnotationItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.note import StickyNoteItem
from annoter.views.note_editor import NoteEditor
from annoter.views.pdf_scene import PdfScene
from annoter.views.pdf_view import PdfView
from annoter.views.properties_dock import PropertiesDock
from annoter.views.tool_palette import ToolPalette


_TOOLBAR_TOOLS: list[tuple[Tool, str]] = [
    (Tool.SELECT, "Select"),
    (Tool.RECTANGLE, "Rectangle"),
    (Tool.ELLIPSE, "Ellipse"),
    (Tool.CLOUD, "Cloud"),
    (Tool.LINE, "Line"),
    (Tool.ARROW, "Arrow"),
    (Tool.POLYLINE, "Polyline"),
    (Tool.POLYGON, "Polygon"),
    (Tool.TEXT, "Text"),
    (Tool.CALLOUT, "Callout"),
    (Tool.STICKY_NOTE, "Sticky note"),
    (Tool.STAMP, "Stamp"),
    (Tool.FREEHAND, "Freehand"),
    (Tool.GDT, "GD&T frame"),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Annoter")
        self.resize(1280, 800)
        self.setAcceptDrops(True)

        self._theme: Theme = Theme.LIGHT
        self._doc: PdfDocument | None = None
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

        self._view = PdfView(self)
        self._view.setScene(self._scene)
        self.setCentralWidget(self._view)
        self._view.setFocus()
        self._view.contextMenuRequested.connect(self._show_context_menu)

        # In-app clipboard: detached clones produced by Copy/Cut. Paste
        # re-clones from these so multiple pastes work and the clipboard
        # stays independent from any subsequent scene mutation.
        self._clipboard: list[AnnotationItem] = []

        self._tool_palette = ToolPalette(self._tool_controller, self)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._tool_palette)

        self._annotation_list = AnnotationListDock(self)
        self._annotation_list.deleteRequested.connect(self._delete_selected)
        self.addDockWidget(Qt.RightDockWidgetArea, self._annotation_list)

        self._properties_dock = PropertiesDock(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self._properties_dock)
        self.tabifyDockWidget(self._annotation_list, self._properties_dock)

        self._recent = RecentFiles(MAX_RECENT_FILES, self)
        self._recent.changed.connect(self._refresh_recent_menu)

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

        self.act_close = QAction("&Close", self)
        self.act_close.setShortcut("Ctrl+W")
        self.act_close.triggered.connect(self._on_close)

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
        self.act_goto.setShortcut(QKeySequence("Ctrl+G"))
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
        m_edit.addAction(self.act_change_color)
        m_edit.addAction(self.act_change_stroke)

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

        self._tool_actions: dict[Tool, QAction] = {}
        self._tool_action_group = QActionGroup(self)
        self._tool_action_group.setExclusive(True)
        for tool, label in _TOOLBAR_TOOLS:
            act = QAction(label, self)
            act.setCheckable(True)
            act.triggered.connect(
                lambda _checked=False, t=tool: (
                    self._tool_controller.set_tool(t)
                )
            )
            self._tool_action_group.addAction(act)
            tb.addAction(act)
            self._tool_actions[tool] = act
        self._tool_actions[Tool.SELECT].setChecked(True)

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
        for tool, act in self._tool_actions.items():
            act.setIcon(tool_icon(tool, color=c))

    def _build_status_bar(self) -> None:
        self._lbl_path = QLabel("")
        self._lbl_page = QLabel("-")
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
            self.act_cut,
            self.act_copy,
            self.act_paste,
            self.act_duplicate,
            self.act_bring_front,
            self.act_send_back,
        ):
            a.setEnabled(has_doc)
        for a in getattr(self, "_tool_actions", {}).values():
            a.setEnabled(has_doc)

    # ------------------------------------------------------------------
    # file ops
    # ------------------------------------------------------------------
    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF files (*.pdf);;All files (*.*)"
        )
        if path:
            self._open_path(path)

    def open_path(self, path: str | Path) -> None:
        """Public entry point used by app.py and tests."""
        self._open_path(str(path))

    def _open_path(self, path: str) -> None:
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
        self._recent.add(path)
        self._refresh_window_title()
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

    def _on_save_as(self) -> None:
        if self._doc is None:
            return
        self._commit_gdt_editor_if_open()
        self._commit_note_editor_if_open()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save As",
            str(self._doc.path),
            "PDF files (*.pdf);;All files (*.*)",
        )
        if not path:
            return
        target = Path(path)
        if self._save_to(target):
            self._reopen_after_save(target)

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
        return True

    def _reopen_after_save(self, target: Path) -> None:
        # The doc was closed by `_save_to`; reopen the (possibly renamed)
        # file so the user can continue editing.
        self._doc = None
        self._open_path(str(target))

    def _on_close(self) -> None:
        # Drop any in-progress in-place edits with the document.
        self._cancel_gdt_editor()
        self._cancel_note_editor()
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
        self._annotation_list.set_page_item(None)
        self._refresh_window_title()
        self._lbl_page.setText("-")
        self._update_actions_enabled()

    def _refresh_window_title(self) -> None:
        if self._doc is None:
            self.setWindowTitle("Annoter")
            self._lbl_path.setText("")
        else:
            self.setWindowTitle(f"{self._doc.path.name} - Annoter")
            self._lbl_path.setText(str(self._doc.path))

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

    def _on_view_scrolled(self, _value: int) -> None:
        if hasattr(self, "_hires_timer"):
            self._hires_timer.start()
        self._position_gdt_editor()
        self._position_note_editor()

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
        initial = items[0].color()
        color = QColorDialog.getColor(
            initial, self, "Change annotation color"
        )
        if not color.isValid():
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
        choices = [f"{w:g} px" for w in STROKE_WIDTHS]
        current = f"{items[0].stroke():g} px"
        idx = choices.index(current) if current in choices else 1
        choice, ok = QInputDialog.getItem(
            self, "Change Stroke", "Width:", choices, idx, False
        )
        if not ok:
            return
        width = STROKE_WIDTHS[choices.index(choice)]
        stack = self._undo_group.activeStack()
        cmd = ChangeStrokeCommand(items, width)
        if stack is not None:
            stack.push(cmd)
        else:
            cmd.redo()

    def _on_annotations_changed(self) -> None:
        self._annotation_list.refresh()

    def _on_scene_selection_changed(self) -> None:
        self._annotation_list.sync_selection_from_scene()
        self._properties_dock.set_items(self._selected_annotations())

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
    # GD&T (M3) -- in-place editing
    # ------------------------------------------------------------------
    def _on_tool_changed(self, tool: Tool) -> None:
        # Keep the viewport cursor in sync with the active tool.
        self._view.set_tool_cursor_for(tool)
        # Mirror the change into the toolbar's checkable actions.
        act = getattr(self, "_tool_actions", {}).get(tool)
        if act is not None and not act.isChecked():
            act.setChecked(True)

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
            u.toLocalFile().lower().endswith(".pdf")
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local.lower().endswith(".pdf"):
                self._open_path(local)
                event.acceptProposedAction()
                return
        event.ignore()

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
        self._save_settings()
        self._on_close()
        super().closeEvent(event)
