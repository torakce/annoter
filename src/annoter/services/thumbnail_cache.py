"""ThumbnailCache: first-page thumbnails of recent files, kept on disk.

The start page shows a thumbnail, a page count and an annotation count
for every recent file. Getting them means opening each PDF and
rendering its first page -- cheap for a small file, up to a second or
more for a large A0 drawing -- and it used to happen again at every
launch. The results are now stored in the user's cache folder
(`QStandardPaths.CacheLocation`/thumbnails, or `$ANNOTER_CACHE_DIR`
when set), keyed by the file's path, modification time and size, so a
file that has not changed since is shown at once, without opening it.

Each entry is `<key>.png` (the framed thumbnail) plus `<key>.json`
(page count, annotation count, device pixel ratio). Anything unreadable
counts as a miss. The folder keeps the `MAX_ENTRIES` newest entries.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QPixmap

MAX_ENTRIES = 64
ENV_DIR = "ANNOTER_CACHE_DIR"


@dataclass
class CachedThumbnail:
    pixmap: QPixmap
    pages: int
    annotations: int | None


def default_root() -> Path:
    override = os.environ.get(ENV_DIR)
    if override:
        return Path(override) / "thumbnails"
    base = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
    return Path(base) / "thumbnails"


def cache_key(path: str, mtime: float, size: int) -> str:
    raw = f"{os.path.normcase(os.path.abspath(path))}|{mtime!r}|{size}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class ThumbnailCache:
    """Disk store of recent-file thumbnails (see module docstring)."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else default_root()

    @property
    def root(self) -> Path:
        return self._root

    def _files(self, key: str) -> tuple[Path, Path]:
        return self._root / f"{key}.png", self._root / f"{key}.json"

    def load(
        self, path: str, mtime: float, size: int
    ) -> CachedThumbnail | None:
        png, meta = self._files(cache_key(path, mtime, size))
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            pages = int(data["pages"])
            annotations = data.get("annotations")
            annotations = None if annotations is None else int(annotations)
            dpr = float(data.get("dpr", 1.0))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        pm = QPixmap(str(png))
        if pm.isNull():
            return None
        pm.setDevicePixelRatio(max(1.0, dpr))
        return CachedThumbnail(pm, pages, annotations)

    def store(
        self,
        path: str,
        mtime: float,
        size: int,
        pixmap: QPixmap,
        pages: int,
        annotations: int | None,
    ) -> bool:
        """Write one entry; False (and nothing raised) if it cannot."""
        key = cache_key(path, mtime, size)
        png, meta = self._files(key)
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            if not pixmap.save(str(png), "PNG"):
                return False
            meta.write_text(
                json.dumps(
                    {
                        "pages": pages,
                        "annotations": annotations,
                        "dpr": pixmap.devicePixelRatio(),
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            return False
        self._prune()
        return True

    def _prune(self) -> None:
        try:
            metas = sorted(
                self._root.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in metas[MAX_ENTRIES:]:
                old.unlink(missing_ok=True)
                old.with_suffix(".png").unlink(missing_ok=True)
        except OSError:
            pass

    def entry_count(self) -> int:
        try:
            return sum(1 for _ in self._root.glob("*.json"))
        except OSError:
            return 0
