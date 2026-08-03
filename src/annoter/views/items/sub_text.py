"""SubTextItem: an editable text living *inside* another annotation.

Some annotations carry free text that is part of them rather than an
annotation of its own -- the note above or below a GD&T feature control
frame, for instance. Such a text must be authored exactly like any
other text (contextual bar, symbols, inline tolerance runs), yet it
must not behave like a separate annotation: it cannot be selected,
dragged or deleted on its own, it does not show up in the annotation
list, and it is saved inside its owner rather than beside it.

Being a child of the owner item gets nearly all of that for free.
Every collection site in the app walks `page.childItems()` -- direct
children of the page -- so a nested text is naturally invisible to
selection, to the annotation list and to the save loop, while the PDF
appearance rasterizer walks the item tree and picks it up as part of
its owner.

The owner keeps the text's serialized runs in its own state; this item
is the live, editable view of them.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem

from annoter.views.items.text import TextAnnotationItem


class SubTextItem(TextAnnotationItem):
    """A text annotation owned by, and nested inside, another item."""

    KIND = "subtext"

    def __init__(
        self,
        owner: QGraphicsItem,
        role: str,
        runs: list[dict] | None = None,
    ) -> None:
        super().__init__(QPointF(0.0, 0.0), "", parent=owner)
        self._owner = owner
        self._role = role
        # Not an annotation in its own right: no selection, no drag, no
        # resize handles. The owner positions it.
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        if runs:
            self.set_rich_runs(runs)

    def owner(self) -> QGraphicsItem:
        return self._owner

    def role(self) -> str:
        """Which slot of the owner this text fills, e.g. "upper"."""
        return self._role

    def is_blank(self) -> bool:
        return not self.text().strip() and not self.has_tolerance_runs()

    def label(self) -> str:
        return f"{self._role} text"

    def clone(self) -> "SubTextItem":
        # Cloned through its owner, never on its own -- a detached sub
        # text would have nothing to belong to.
        c = SubTextItem(self._owner, self._role, self.rich_runs())
        c.set_color(self.color())
        c.set_font_family(self.font_family())
        c.set_font_size(self.font_size())
        c.set_bold(self.bold())
        c.set_italic(self.italic())
        return c
