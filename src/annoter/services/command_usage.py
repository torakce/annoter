"""CommandUsage: what the user runs from the command palette, and when.

UI redesign, Lot H. The palette ranks equally good text matches by how
often and how recently each command was run from it ("frecency"), and
lists the last few under "Recently used" before anything is typed.

Only runs from the palette are counted -- menus, shortcuts and the tool
rail are already one gesture away, so they say little about what people
look for. Entries are keyed by a stable string (`CommandEntry.key`,
"<group>/<label>"); the store keeps the most recent `MAX_ENTRIES` keys
in QSettings (`ui/command_usage`, JSON {key: [count, last_used]}), and
anything unreadable there is ignored rather than raised.
"""

from __future__ import annotations

import json
import time

from PySide6.QtCore import QObject, QSettings

SETTINGS_KEY = "ui/command_usage"
MAX_ENTRIES = 100
# A run loses half its weight after this many days.
HALF_LIFE_DAYS = 14.0


class CommandUsage(QObject):
    """Run counts and last-run times per command key."""

    def __init__(
        self,
        settings: QSettings | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._data: dict[str, tuple[int, float]] = self._load()

    # ------------------------------------------------------------------
    def record(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        count, _last = self._data.get(key, (0, 0.0))
        self._data[key] = (count + 1, now)
        if len(self._data) > MAX_ENTRIES:
            oldest = sorted(self._data, key=lambda k: self._data[k][1])
            for k in oldest[: len(self._data) - MAX_ENTRIES]:
                del self._data[k]
        self._save()

    def count(self, key: str) -> int:
        return self._data.get(key, (0, 0.0))[0]

    def last_used(self, key: str) -> float:
        return self._data.get(key, (0, 0.0))[1]

    def score(self, key: str, now: float | None = None) -> float:
        """Run count decayed by age: recent habits beat old ones."""
        count, last = self._data.get(key, (0, 0.0))
        if count <= 0:
            return 0.0
        now = time.time() if now is None else now
        age_days = max(0.0, now - last) / 86400.0
        return count * 0.5 ** (age_days / HALF_LIFE_DAYS)

    def recent(self, limit: int | None = None) -> list[str]:
        """Keys, most recently run first."""
        keys = sorted(self._data, key=lambda k: -self._data[k][1])
        return keys if limit is None else keys[:limit]

    def clear(self) -> None:
        self._data = {}
        self._save()

    # ------------------------------------------------------------------
    def _load(self) -> dict[str, tuple[int, float]]:
        if self._settings is None:
            return {}
        raw = self._settings.value(SETTINGS_KEY, "")
        try:
            parsed = json.loads(str(raw)) if raw else {}
        except (TypeError, ValueError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        data: dict[str, tuple[int, float]] = {}
        for key, value in parsed.items():
            try:
                count, last = value
                count = int(count)
                last = float(last)
            except (TypeError, ValueError):
                continue
            if isinstance(key, str) and count > 0:
                data[key] = (count, last)
        return data

    def _save(self) -> None:
        if self._settings is None:
            return
        payload = {k: [c, t] for k, (c, t) in self._data.items()}
        self._settings.setValue(SETTINGS_KEY, json.dumps(payload))
