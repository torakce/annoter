"""Theme service: QSS template rendering, palette and design tokens."""

from __future__ import annotations

import os
import re
from dataclasses import fields

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtGui import QColor, QFontDatabase, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from annoter.services.fonts import register_ui_fonts  # noqa: E402
from annoter.services.theme import (  # noqa: E402
    Theme,
    apply,
    build_palette,
    load_qss,
    load_template,
    render_qss,
)
from annoter.services.tokens import (  # noqa: E402
    DARK,
    LIGHT,
    contrast_ratio,
    tokens_for,
)

_HEX = re.compile(r"#[0-9A-Fa-f]{3,8}\b")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ----------------------------------------------------------------------
# template
# ----------------------------------------------------------------------
def test_load_qss_returns_non_empty_for_both_themes() -> None:
    assert load_qss(Theme.LIGHT).strip() != ""
    assert load_qss(Theme.DARK).strip() != ""


def test_every_placeholder_resolves_in_both_themes() -> None:
    for theme in Theme:
        assert "@" not in load_qss(theme), theme


def test_template_hard_codes_no_color() -> None:
    """Colors belong in tokens.py, so both themes stay in step."""
    template = load_template()
    # Strip comments before looking for hex literals.
    code = re.sub(r"/\*.*?\*/", "", template, flags=re.DOTALL)
    assert _HEX.findall(code) == []


def test_themes_render_differently_with_their_tokens() -> None:
    light, dark = load_qss(Theme.LIGHT), load_qss(Theme.DARK)
    assert light != dark
    assert LIGHT.canvas in light and DARK.canvas in dark


def test_unknown_placeholder_fails_loudly() -> None:
    with pytest.raises(KeyError):
        render_qss("QWidget { color: @no_such_token; }", LIGHT)


def test_floating_widgets_are_themed_by_the_template() -> None:
    qss = load_qss(Theme.LIGHT)
    for name in (
        "ContextIconButton",
        "EditToolbar",
        "GdtFrameBuilder",
    ):
        assert f"#{name}" in qss, name


# ----------------------------------------------------------------------
# tokens
# ----------------------------------------------------------------------
def test_both_themes_define_every_token() -> None:
    for f in fields(LIGHT):
        assert getattr(LIGHT, f.name) and getattr(DARK, f.name), f.name


def test_tokens_for_maps_theme() -> None:
    assert tokens_for(Theme.LIGHT) is LIGHT
    assert tokens_for(Theme.DARK) is DARK


@pytest.mark.parametrize("tokens", [LIGHT, DARK], ids=["light", "dark"])
def test_text_contrast_is_legible(tokens) -> None:
    # WCAG AA: 4.5:1 for body text, on every surface text sits on.
    for surface in (tokens.app, tokens.panel, tokens.field, tokens.pill):
        assert contrast_ratio(tokens.text, surface) >= 4.5
        assert contrast_ratio(tokens.text_muted, surface) >= 4.5
    assert contrast_ratio(tokens.soft_text, tokens.soft) >= 4.5
    assert contrast_ratio(tokens.on_accent, tokens.accent_bg) >= 4.5
    assert contrast_ratio(tokens.tooltip_text, tokens.tooltip_bg) >= 4.5
    # Icons and the focus/selection accent: 3:1 (non-text UI).
    assert contrast_ratio(tokens.icon, tokens.app) >= 3.0
    assert contrast_ratio(tokens.accent, tokens.panel) >= 3.0


# ----------------------------------------------------------------------
# palette + apply
# ----------------------------------------------------------------------
def test_build_palette_uses_tokens() -> None:
    pal = build_palette(DARK)
    assert pal.color(QPalette.Active, QPalette.Window) == QColor(DARK.panel)
    assert pal.color(QPalette.Active, QPalette.Text) == QColor(DARK.text)
    assert pal.color(QPalette.Active, QPalette.Highlight) == QColor(
        DARK.accent_bg
    )
    assert pal.color(QPalette.Disabled, QPalette.Text) == QColor(
        DARK.text_disabled
    )


def test_apply_sets_stylesheet_and_palette(qapp) -> None:
    apply(Theme.DARK, qapp)
    assert qapp.styleSheet().strip() != ""
    assert qapp.palette().color(QPalette.Window) == QColor(DARK.panel)
    apply(Theme.LIGHT, qapp)
    assert qapp.palette().color(QPalette.Window) == QColor(LIGHT.panel)
    # Reset to a known state for other tests.
    qapp.setStyleSheet("")
    qapp.setPalette(QPalette())


# ----------------------------------------------------------------------
# fonts
# ----------------------------------------------------------------------
def test_bundled_ui_fonts_register(qapp) -> None:
    families = register_ui_fonts()
    assert "IBM Plex Sans" in families
    assert "IBM Plex Mono" in families
    assert "IBM Plex Sans" in QFontDatabase.families()


def test_ui_font_does_not_leak_into_the_application_font(qapp) -> None:
    """Annotation items rely on explicit fonts (Helvetica, OSIFont); the UI
    font goes through QSS only, never QApplication.setFont."""
    before = qapp.font().family()
    register_ui_fonts()
    apply(Theme.LIGHT, qapp)
    assert qapp.font().family() == before
    qapp.setStyleSheet("")
    qapp.setPalette(QPalette())


def test_stylesheet_parses_without_qt_warnings(qapp) -> None:
    """Qt silently drops a sheet it cannot parse (only a warning is
    logged), which would leave the app unstyled: catch that here."""
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtWidgets import QLineEdit, QMainWindow, QMenu, QToolButton

    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _t, _c, msg: messages.append(msg))
    try:
        for theme in Theme:
            apply(theme, qapp)
            win = QMainWindow()
            for w in (QLineEdit(win), QToolButton(win), QMenu(win)):
                w.ensurePolished()
            win.ensurePolished()
            win.deleteLater()
    finally:
        qInstallMessageHandler(previous)
        qapp.setStyleSheet("")
        qapp.setPalette(QPalette())
    assert not [m for m in messages if "style" in m.lower()], messages
