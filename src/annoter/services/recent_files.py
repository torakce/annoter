"""Most-recently-used files list, persisted via QSettings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal


class RecentFiles(QObject):
    """MRU file list capped at `max_entries`. Persists across sessions."""

    changed = Signal()

    _SETTINGS_KEY = "recent_files"

    def __init__(self, max_entries: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._max = max_entries
        self._settings = QSettings()

    def list(self) -> list[str]:
        raw = self._settings.value(self._SETTINGS_KEY, [])
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw]
        return [str(x) for x in raw]

    def add(self, path: str | Path) -> None:
        normalized = self._normalize(path)
        items = [p for p in self.list() if p != normalized]
        items.insert(0, normalized)
        items = items[: self._max]
        self._settings.setValue(self._SETTINGS_KEY, items)
        self._settings.sync()
        self.changed.emit()

    def remove(self, path: str | Path) -> None:
        normalized = self._normalize(path)
        items = [p for p in self.list() if p != normalized]
        self._settings.setValue(self._SETTINGS_KEY, items)
        self._settings.sync()
        self.changed.emit()

    def clear(self) -> None:
        self._settings.setValue(self._SETTINGS_KEY, [])
        self._settings.sync()
        self.changed.emit()

    @staticmethod
    def _normalize(path: str | Path) -> str:
        try:
            return str(Path(path).resolve())
        except (OSError, ValueError):
            return str(path)
