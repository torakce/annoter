"""Native display engine (from Lire) used for on-screen page display.

`load_engine()` locates the compiled library, checks its API version and
returns the process-wide `NativeEngine`, or None when it is unavailable --
the caller then falls back to the PyMuPDF display path. Where the library
is looked for, in order:

1. the file named by the environment variable `ANNOTER_ENGINE_PATH`;
2. next to the frozen application (PyInstaller's `sys._MEIPASS`);
3. `bin/` next to this package (filled by `native/build_engine.py`).

Environment variables:
    ANNOTER_RENDERER=pymupdf      never use the engine (fallback path)
    ANNOTER_ENGINE_THREADS=N      render threads (default: cores - 1)
    ANNOTER_ENGINE_TILE_MB=N      tile memory budget (default: RAM / 12, max 1 GB)
    ANNOTER_ENGINE_LIST_MB=N      page-analysis budget (default: RAM / 10, max 2 GB)
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
from pathlib import Path

from annoter.engine.native import (
    EngineError,
    NativeDocument,
    NativeEngine,
    PasswordError,
    Target,
    Tile,
)

__all__ = [
    "EngineError",
    "NativeDocument",
    "NativeEngine",
    "PasswordError",
    "Target",
    "Tile",
    "library_name",
    "library_path",
    "load_engine",
    "unavailable_reason",
]

_log = logging.getLogger(__name__)
_engine: NativeEngine | None = None
_tried = False
_reason = ""


def library_name() -> str:
    if sys.platform == "win32":
        return "annoter_engine.dll"
    if sys.platform == "darwin":
        return "libannoter_engine.dylib"
    return "libannoter_engine.so"


def library_path() -> Path | None:
    """Where the compiled library is, or None."""
    env = os.environ.get("ANNOTER_ENGINE_PATH")
    candidates = [Path(env)] if env else []
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        candidates.append(Path(base) / library_name())
    candidates.append(Path(__file__).resolve().parent / "bin" / library_name())
    for c in candidates:
        if c.is_file():
            return c
    return None


def _env_int(name: str) -> int:
    try:
        return max(0, int(os.environ.get(name, "0")))
    except ValueError:
        return 0


def load_engine() -> NativeEngine | None:
    """The process-wide engine, started on first use; None (with the reason
    in `unavailable_reason()`) when it cannot be used."""
    global _engine, _tried, _reason
    if os.environ.get("ANNOTER_RENDERER", "").strip().lower() == "pymupdf":
        _reason = "disabled by ANNOTER_RENDERER=pymupdf"
        return None
    if _tried:
        return _engine
    _tried = True
    path = library_path()
    if path is None:
        _reason = f"{library_name()} not found"
        _log.info("display engine unavailable: %s", _reason)
        return None
    try:
        _engine = NativeEngine(
            path,
            render_threads=_env_int("ANNOTER_ENGINE_THREADS"),
            tile_budget=_env_int("ANNOTER_ENGINE_TILE_MB") << 20,
            list_budget=_env_int("ANNOTER_ENGINE_LIST_MB") << 20,
        )
    except (OSError, AttributeError, EngineError) as e:
        _reason = f"{path.name}: {e}"
        _log.warning("display engine unavailable: %s", _reason)
        _engine = None
        return None
    atexit.register(_shutdown)
    return _engine


def unavailable_reason() -> str:
    """Why `load_engine()` returned None (empty when it did not)."""
    return _reason


def _shutdown() -> None:
    """Stop the engine's threads before the interpreter goes away."""
    global _engine
    if _engine is not None:
        _engine.close()
        _engine = None
