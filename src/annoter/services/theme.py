"""Light / dark theme switching: QSS template + QPalette from tokens.

One template, `resources/themes/app.qss`, holds every rule; colors,
radii and fonts are `@name` placeholders filled from the active
`Tokens` (see `services.tokens`). The same tokens also build the
application QPalette, so widgets that read `palette(...)` in their own
QSS, or paint with QPalette roles, follow the theme too -- the old
per-theme QSS files could not do that.

The template path is resolved relative to the package so the bundled
PyInstaller build ships it too (build.py bundles resources/themes/).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from annoter.services.tokens import Theme, Tokens, tokens_for

__all__ = ["Theme", "apply", "build_palette", "load_qss", "render_qss"]

TEMPLATE_NAME = "app.qss"
_PLACEHOLDER = re.compile(r"@([a-z][a-z_]*)")


def _themes_dir() -> Path:
    """Resolve `resources/themes/` in dev and PyInstaller-frozen builds."""
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return Path(base) / "resources" / "themes"
    # src/annoter/services/theme.py -> repo_root/resources/themes
    return Path(__file__).resolve().parents[3] / "resources" / "themes"


def load_template() -> str:
    path = _themes_dir() / TEMPLATE_NAME
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def render_qss(template: str, tokens: Tokens) -> str:
    """Replace every `@name` with the token of that name.

    An unknown placeholder raises KeyError on purpose: a typo in the
    template must fail loudly (and in the tests), not ship an invalid
    stylesheet that Qt would silently half-apply.
    """
    values = tokens.as_dict()

    def sub(m: re.Match[str]) -> str:
        return values[m.group(1)]

    return _PLACEHOLDER.sub(sub, template)


def load_qss(theme: Theme) -> str:
    return render_qss(load_template(), tokens_for(theme))


def build_palette(tokens: Tokens) -> QPalette:
    """QPalette matching the tokens, for widgets that paint with roles."""
    pal = QPalette()
    c = QColor

    def both(role: QPalette.ColorRole, color: str) -> None:
        for group in (QPalette.Active, QPalette.Inactive):
            pal.setColor(group, role, c(color))

    both(QPalette.Window, tokens.panel)
    both(QPalette.WindowText, tokens.text)
    both(QPalette.Base, tokens.field)
    both(QPalette.AlternateBase, tokens.app)
    both(QPalette.Text, tokens.text)
    both(QPalette.Button, tokens.field)
    both(QPalette.ButtonText, tokens.text)
    both(QPalette.BrightText, tokens.danger)
    both(QPalette.Highlight, tokens.accent_bg)
    both(QPalette.HighlightedText, tokens.on_accent)
    both(QPalette.ToolTipBase, tokens.tooltip_bg)
    both(QPalette.ToolTipText, tokens.tooltip_text)
    both(QPalette.PlaceholderText, tokens.text_muted)
    both(QPalette.Link, tokens.accent)
    both(QPalette.Light, tokens.pill)
    both(QPalette.Midlight, tokens.line)
    both(QPalette.Mid, tokens.line_strong)
    both(QPalette.Dark, tokens.line_strong)
    both(QPalette.Shadow, "#000000")
    for role in (
        QPalette.WindowText,
        QPalette.Text,
        QPalette.ButtonText,
        QPalette.PlaceholderText,
    ):
        pal.setColor(QPalette.Disabled, role, c(tokens.text_disabled))
    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
        pal.setColor(QPalette.Disabled, role, pal.color(QPalette.Active, role))
    return pal


def apply(theme: Theme, app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None:
        return
    tokens = tokens_for(theme)
    # Palette first: the stylesheet's `palette(...)` references resolve
    # against it when the sheet is (re)polished.
    app.setPalette(build_palette(tokens))
    app.setStyleSheet(render_qss(load_template(), tokens))
