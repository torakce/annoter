"""DocumentSidebar: the left panel with Pages / Annotations tabs (Lot E).

One dock instead of two: the page thumbnails (formerly their own left
dock) and the annotation list (formerly a right dock tabbed with the
Properties) now share a panel, switched by a two-segment control that
also shows how many pages and annotations the document has. The dock's
title bar is hidden -- the segmented control is the header -- and the
panel is still listed in View > Panels.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDockWidget,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from annoter.views.annotation_list import AnnotationTree
from annoter.views.page_thumbnails import PageThumbnailList

SIDEBAR_WIDTH = 256
TAB_PAGES = 0
TAB_ANNOTATIONS = 1


class DocumentSidebar(QDockWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Pages and Annotations", parent)
        self.setObjectName("DocumentSidebar")
        self.setFeatures(QDockWidget.DockWidgetClosable)
        self.setTitleBarWidget(QWidget(self))  # the tabs are the header
        self.setMinimumWidth(200)

        body = QWidget(self)
        body.setObjectName("SidebarBody")
        col = QVBoxLayout(body)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(10)

        tabs = QWidget(body)
        tabs.setObjectName("SidebarTabs")
        row = QHBoxLayout(tabs)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self.pages_tab = self._tab_button("Pages", tabs)
        self.annotations_tab = self._tab_button("Annotations", tabs)
        for i, b in enumerate((self.pages_tab, self.annotations_tab)):
            self._tab_group.addButton(b, i)
            row.addWidget(b, 1)
        self._tab_group.idClicked.connect(self.show_tab)
        col.addWidget(tabs)

        self._stack = QStackedWidget(body)
        self.pages = PageThumbnailList(self._stack)
        self.annotations = AnnotationTree(self._stack)
        self._stack.addWidget(self.pages)
        self._stack.addWidget(self.annotations)
        col.addWidget(self._stack, 1)
        self.setWidget(body)

        self.set_counts(0, 0)
        self.show_tab(TAB_PAGES)

    def _tab_button(self, text: str, parent: QWidget) -> QPushButton:
        b = QPushButton(text, parent)
        b.setObjectName("SidebarTab")
        b.setCheckable(True)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        return b

    # ------------------------------------------------------------------
    def show_tab(self, index: int) -> None:
        index = TAB_ANNOTATIONS if index == TAB_ANNOTATIONS else TAB_PAGES
        self._stack.setCurrentIndex(index)
        (self.annotations_tab if index else self.pages_tab).setChecked(True)

    def current_tab(self) -> int:
        return self._stack.currentIndex()

    def set_counts(self, pages: int, annotations: int) -> None:
        self.pages_tab.setText(f"Pages  {pages}" if pages else "Pages")
        self.annotations_tab.setText(
            f"Annotations  {annotations}" if annotations else "Annotations"
        )
