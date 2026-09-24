"""Start-up time: slim PyInstaller bundle, splash screen, smoke flag and
the on-disk thumbnail cache of the start page (2026-09)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import fitz  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build  # noqa: E402

from annoter import app as app_module  # noqa: E402
from annoter.services import thumbnail_cache as tc  # noqa: E402
from annoter.services.thumbnail_cache import ThumbnailCache  # noqa: E402
from annoter.views.welcome_screen import WelcomeScreen  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _pdf(path: Path, annots: int = 0) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    for k in range(annots):
        page.add_rect_annot(fitz.Rect(10 + 30 * k, 10, 30 + 30 * k, 30))
    doc.save(str(path))
    doc.close()
    return path


# ----------------------------------------------------------------------
# build.py
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "dest",
    [
        "PySide6/Qt6Quick.dll",
        "PySide6/Qt6Qml.dll",
        "PySide6/Qt6QmlModels.dll",
        "PySide6/Qt6VirtualKeyboard.dll",
        "PySide6/Qt6Pdf.dll",
        "PySide6/Qt6Network.dll",
        "PySide6/Qt6WebEngineCore.dll",
        "PySide6/Qt/lib/libQt6Quick.so.6",
        "PySide6/opengl32sw.dll",
        "PySide6/d3dcompiler_47.dll",
        "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
        "PySide6/plugins/imageformats/qpdf.dll",
        "PySide6/plugins/tls/qschannelbackend.dll",
        "PySide6\\translations\\qtbase_fr.qm",
        "PySide6/Qt/translations/qtbase_de.qm",
    ],
)
def test_unused_qt_pieces_are_dropped(dest: str) -> None:
    assert build.dropped(dest)


@pytest.mark.parametrize(
    "dest",
    [
        "PySide6/Qt6Core.dll",
        "PySide6/Qt6Gui.dll",
        "PySide6/Qt6Widgets.dll",
        "PySide6/Qt6Svg.dll",
        "PySide6/Qt6OpenGL.dll",
        "PySide6/QtCore.pyd",
        "PySide6/plugins/platforms/qwindows.dll",
        "PySide6/plugins/styles/qmodernwindowsstyle.dll",
        "PySide6/plugins/imageformats/qico.dll",
        "PySide6/plugins/imageformats/qsvg.dll",
        "PySide6/plugins/iconengines/qsvgicon.dll",
        "PySide6/Qt/plugins/platforminputcontexts/"
        "libcomposeplatforminputcontextplugin.so",
        "PySide6/Qt/lib/libQt6XcbQpa.so.6",
        "pymupdf/mupdfcpp64.dll",
        "resources/themes/app.qss",
    ],
)
def test_needed_pieces_are_kept(dest: str) -> None:
    assert not build.dropped(dest)


@pytest.mark.parametrize("onefile", [True, False])
@pytest.mark.parametrize("splash", [True, False])
def test_generated_spec(onefile: bool, splash: bool) -> None:
    spec = build.make_spec(onefile, splash)
    compile(spec, "Annoter.spec", "exec")  # valid Python
    assert "upx=False" in spec
    assert ("Splash(" in spec) is splash
    assert ("COLLECT(" in spec) is not onefile
    assert "collect_all" not in spec
    for mod in ("tkinter", "PySide6.QtQuick", "PySide6.QtWebEngineCore"):
        assert repr(mod) in spec
    for sub in ("themes", "icons", "fonts"):
        assert f"resources/{sub}" in spec


def test_build_no_longer_copies_all_of_qt() -> None:
    """Regression guard: `--collect-all PySide6` made a ~790 MB bundle
    that a one-file .exe unpacked at every launch."""
    source = (ROOT / "build.py").read_text(encoding="utf-8")
    assert '"--collect-all"' not in source


def test_splash_image_is_bundled_asset() -> None:
    img = QImage(str(build.SPLASH_IMAGE))
    assert not img.isNull()
    assert (img.width(), img.height()) == (480, 260)


# ----------------------------------------------------------------------
# app entry point
# ----------------------------------------------------------------------
def test_close_splash_is_a_no_op_outside_a_build() -> None:
    app_module.close_splash()  # must not raise


def test_smoke_flag_starts_and_quits(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(ROOT / "src")
    env["ANNOTER_CACHE_DIR"] = str(tmp_path / "cache")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "annoter", app_module.SMOKE_FLAG],
        env=env,
        timeout=120,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ----------------------------------------------------------------------
# thumbnail cache
# ----------------------------------------------------------------------
def _thumb(w: int = 40, h: int = 30, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor("#336699"))
    pm.setDevicePixelRatio(dpr)
    return pm


def test_cache_round_trip(qapp, tmp_path: Path) -> None:
    cache = ThumbnailCache(tmp_path / "thumbs")
    assert cache.store("/plans/a.pdf", 1000.0, 42, _thumb(), 12, 9)
    hit = cache.load("/plans/a.pdf", 1000.0, 42)
    assert hit is not None
    assert (hit.pages, hit.annotations) == (12, 9)
    assert hit.pixmap.devicePixelRatio() == 2.0
    assert (hit.pixmap.width(), hit.pixmap.height()) == (40, 30)
    # A changed file (new mtime or size) is a miss.
    assert cache.load("/plans/a.pdf", 1001.0, 42) is None
    assert cache.load("/plans/a.pdf", 1000.0, 43) is None
    assert cache.load("/plans/b.pdf", 1000.0, 42) is None


def test_cache_unknown_annotation_count(qapp, tmp_path: Path) -> None:
    cache = ThumbnailCache(tmp_path)
    cache.store("/x.pdf", 1.0, 1, _thumb(), 400, None)
    assert cache.load("/x.pdf", 1.0, 1).annotations is None


def test_cache_ignores_damaged_entries(qapp, tmp_path: Path) -> None:
    cache = ThumbnailCache(tmp_path)
    cache.store("/x.pdf", 1.0, 1, _thumb(), 2, 0)
    key = tc.cache_key("/x.pdf", 1.0, 1)
    (tmp_path / f"{key}.json").write_text("{broken", encoding="utf-8")
    assert cache.load("/x.pdf", 1.0, 1) is None
    cache.store("/y.pdf", 1.0, 1, _thumb(), 2, 0)
    key = tc.cache_key("/y.pdf", 1.0, 1)
    (tmp_path / f"{key}.png").write_bytes(b"not a png")
    assert cache.load("/y.pdf", 1.0, 1) is None


def test_cache_keeps_the_newest_entries(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(tc, "MAX_ENTRIES", 3)
    cache = ThumbnailCache(tmp_path)
    for i in range(5):
        cache.store(f"/f{i}.pdf", 1.0, 1, _thumb(), 1, 0)
        meta = tmp_path / f"{tc.cache_key(f'/f{i}.pdf', 1.0, 1)}.json"
        os.utime(meta, (1000 + i, 1000 + i))
    cache.store("/last.pdf", 1.0, 1, _thumb(), 1, 0)
    assert cache.entry_count() == 3
    assert cache.load("/last.pdf", 1.0, 1) is not None
    assert cache.load("/f0.pdf", 1.0, 1) is None


def test_cache_write_failure_is_quiet(qapp, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cache = ThumbnailCache(blocker / "thumbs")  # parent is a file
    assert cache.store("/x.pdf", 1.0, 1, _thumb(), 1, 0) is False
    assert cache.load("/x.pdf", 1.0, 1) is None


def test_cache_location_follows_the_override(tmp_path: Path) -> None:
    assert tc.default_root() == Path(os.environ[tc.ENV_DIR]) / "thumbnails"


# ----------------------------------------------------------------------
# start page with the cache
# ----------------------------------------------------------------------
def test_start_page_reuses_thumbnails_across_restarts(
    qapp, tmp_path: Path
) -> None:
    pdf = _pdf(tmp_path / "plan.pdf", annots=2)
    cache = ThumbnailCache(tmp_path / "thumbs")
    first = WelcomeScreen(thumbnails=cache)
    first.set_recent([str(pdf)])
    assert len(first._pending) == 1
    while first._pending:
        first._render_next()
    assert cache.entry_count() == 1

    # "Next launch": a new page, nothing read from the PDF.
    second = WelcomeScreen(thumbnails=cache)
    second.set_recent([str(pdf)])
    assert second._pending == []
    card = second.cards()[0]
    assert card.thumbnail() is not None
    assert card.badge_text() == "2 annotations"
    assert "1 page" in card.meta_text()


def test_main_window_uses_the_disk_cache(qapp) -> None:
    from annoter.views.main_window import MainWindow

    win = MainWindow()
    try:
        disk = win._welcome._disk
        assert isinstance(disk, ThumbnailCache)
        assert disk.root == Path(os.environ[tc.ENV_DIR]) / "thumbnails"
    finally:
        win.close()


# ----------------------------------------------------------------------
# release (see .github/workflows/release.yml)
# ----------------------------------------------------------------------
def _project_version() -> str:
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    return data["project"]["version"]


def test_version_is_the_same_everywhere() -> None:
    """The release workflow refuses a tag whose version differs from
    pyproject.toml or `annoter.__version__`."""
    import annoter

    assert annoter.__version__ == _project_version()


def test_release_notes_exist_for_the_current_version() -> None:
    notes = ROOT / ".github" / "release-notes" / f"v{_project_version()}.md"
    assert notes.is_file()
    text = notes.read_text("utf-8")
    assert "Annoter-portable.zip" in text and "Annoter.exe" in text


def test_release_workflow_matches_build_py() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        "utf-8"
    )
    assert "python build.py --onefile --zip-onedir --smoke" in workflow
    assert "dist-onefile/Annoter.exe" in workflow
    assert "dist-onedir/Annoter-portable.zip" in workflow
    pins = (ROOT / ".github" / "requirements-release.txt").read_text("utf-8")
    for pkg in ("PySide6==", "PyMuPDF==", "Pillow==", "pyinstaller=="):
        assert pkg in pins
