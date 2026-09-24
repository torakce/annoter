"""Registers the bundled UI fonts (IBM Plex Sans / Mono, OFL).

Called once at startup, before the first widget is built. The UI font
is applied through the QSS template (`QWidget { font-family: ... }`),
never through `QApplication.setFont`, so annotation items keep the
explicit fonts they rely on for PDF compatibility (Helvetica for
FreeText, OSIFont for GD&T -- see PLAN.md, "Fonts").
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFontDatabase

# Regular + Bold only: Qt groups fonts by their legacy family name, and
# Plex's Medium / SemiBold files declare separate families ("IBM Plex
# Sans Medm", ...), so `font-weight: 600` in QSS would never pick them;
# it resolves to Bold within "IBM Plex Sans".
UI_FONT_FILES: tuple[str, ...] = (
    "IBMPlexSans-Regular.ttf",
    "IBMPlexSans-Bold.ttf",
    "IBMPlexMono-Regular.ttf",
)


def fonts_dir() -> Path:
    """Resolve `resources/fonts/` in dev and PyInstaller-frozen builds."""
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return Path(base) / "resources" / "fonts"
    # src/annoter/services/fonts.py -> repo_root/resources/fonts
    return Path(__file__).resolve().parents[3] / "resources" / "fonts"


def register_ui_fonts() -> list[str]:
    """Load every bundled UI font file; return the families now known.

    Missing or unreadable files are skipped: the QSS font stacks fall
    back to Segoe UI / Consolas, so a stripped build still works.
    """
    families: list[str] = []
    root = fonts_dir()
    for name in UI_FONT_FILES:
        path = root / name
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        for fam in QFontDatabase.applicationFontFamilies(font_id):
            if fam not in families:
                families.append(fam)
    return families
