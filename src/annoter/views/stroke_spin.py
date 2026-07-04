"""StrokeSpinBox: editable stroke-width field with ladder stepping.

Replaces the old 3-choice dropdown (Discussion #1 follow-up): the user
can type any width, and the up/down buttons walk a PowerPoint-like
ladder (1, 2, 3, 4, 5, 6, 8, 10, 12, ...) instead of stepping linearly.
Shared by the top toolbar, the Tools dock and the Properties dock.
"""

from __future__ import annotations

from PySide6.QtWidgets import QSpinBox, QWidget

# Font-size-style progression: fine steps where precision matters,
# coarser jumps once the values get large.
STROKE_LADDER: tuple[int, ...] = (
    1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56, 70,
    84, 100,
)


class StrokeSpinBox(QSpinBox):
    """QSpinBox whose arrows step along STROKE_LADDER.

    A typed value that is not on the ladder still works: the next
    up-step lands on the closest larger ladder value, the next
    down-step on the closest smaller one.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        minimum: int = 1,
        maximum: int = STROKE_LADDER[-1],
    ) -> None:
        super().__init__(parent)
        self.setRange(minimum, maximum)
        self.setSuffix(" px")
        # Fire valueChanged on Enter/focus-out/arrow clicks, not on
        # every keystroke while a value is being typed.
        self.setKeyboardTracking(False)

    def stepBy(self, steps: int) -> None:
        v = self.value()
        if steps > 0:
            for _ in range(steps):
                v = self._next_up(v)
        elif steps < 0:
            for _ in range(-steps):
                v = self._next_down(v)
        self.setValue(max(self.minimum(), min(self.maximum(), v)))

    @staticmethod
    def _next_up(v: int) -> int:
        for x in STROKE_LADDER:
            if x > v:
                return x
        return STROKE_LADDER[-1]

    @staticmethod
    def _next_down(v: int) -> int:
        for x in reversed(STROKE_LADDER):
            if x < v:
                return x
        return v - 1  # below the ladder floor: plain decrement (0 = None)
