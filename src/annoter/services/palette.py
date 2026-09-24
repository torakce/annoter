"""PaletteStore: the user's own quick colors (UI redesign, Lot F).

The swatch rows of the inspector show these colors. The user can
recolor, rename, reorder, add (up to MAX_SWATCHES) and remove them, or
reset to the defaults; the list is kept in QSettings (`ui/palette`, a
JSON list of [name, "#RRGGBB"]) like the recent files, so it survives
restarts. Names are shown as tooltips.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QColor

from annoter.config import DEFAULT_PALETTE

MAX_SWATCHES = 10
SETTINGS_KEY = "ui/palette"

# Default names for config.DEFAULT_PALETTE, in the same order.
_DEFAULT_NAMES = ("Red", "Blue", "Green", "Yellow", "Black")

Swatch = tuple[str, str]  # (name, "#RRGGBB")


def default_swatches() -> list[Swatch]:
    names = list(_DEFAULT_NAMES) + [
        f"Color {i + 1}" for i in range(len(_DEFAULT_NAMES), len(DEFAULT_PALETTE))
    ]
    return [
        (names[i], QColor(hex_).name().upper())
        for i, hex_ in enumerate(DEFAULT_PALETTE)
    ]


def _clean(swatches: list) -> list[Swatch]:
    """Keep well-formed, valid entries only, at most MAX_SWATCHES."""
    out: list[Swatch] = []
    for entry in swatches:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        name, hex_ = entry
        color = QColor(str(hex_))
        if not color.isValid():
            continue
        out.append((str(name).strip() or color.name().upper(), color.name().upper()))
        if len(out) >= MAX_SWATCHES:
            break
    return out


class PaletteStore(QObject):
    """Holds the swatch list; emits `changed` after every edit."""

    changed = Signal()

    def __init__(
        self,
        settings: QSettings | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._swatches: list[Swatch] = self._load()

    # ------------------------------------------------------------------
    def swatches(self) -> list[Swatch]:
        return list(self._swatches)

    def colors(self) -> list[QColor]:
        return [QColor(h) for _n, h in self._swatches]

    def set_swatches(self, swatches: list) -> None:
        cleaned = _clean(list(swatches)) or default_swatches()
        if cleaned == self._swatches:
            return
        self._swatches = cleaned
        self._save()
        self.changed.emit()

    def reset(self) -> None:
        self.set_swatches(default_swatches())

    def is_default(self) -> bool:
        return self._swatches == default_swatches()

    # ------------------------------------------------------------------
    def _load(self) -> list[Swatch]:
        if self._settings is None:
            return default_swatches()
        raw = self._settings.value(SETTINGS_KEY, "")
        if not raw:
            return default_swatches()
        try:
            data = json.loads(str(raw))
        except (TypeError, ValueError):
            return default_swatches()
        return _clean(data) if isinstance(data, list) and _clean(data) else (
            default_swatches()
        )

    def _save(self) -> None:
        if self._settings is None:
            return
        self._settings.setValue(
            SETTINGS_KEY, json.dumps([list(s) for s in self._swatches])
        )
