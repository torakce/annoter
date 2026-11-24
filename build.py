"""PyInstaller driver: produces both onefile and onedir builds.

Usage:
    python build.py                # build both
    python build.py --onefile      # build only the single .exe
    python build.py --onedir       # build only the portable folder
    python build.py --zip-onedir   # also zip the onedir output
    python build.py --no-splash    # skip the launch splash screen
    python build.py --smoke        # after building, launch each build
                                   # once and report its start-up time
                                   # and which page display it uses
    python build.py --require-engine   # fail if the native display
                                   # engine library is missing (CI)

Outputs land in `dist-onefile/` and `dist-onedir/` respectively. Each
build keeps its own work directory under `build-onefile/` /
`build-onedir/` so the two modes don't trample on each other.

Start-up time (2026-09): the bundle used to be built with
`--collect-all PySide6`, which copied the whole of Qt -- WebEngine,
Qt Quick / QML, 3D, Multimedia, Designer... -- about 790 MB for an app
that only uses QtCore / QtGui / QtWidgets / QtSvg. A one-file .exe
unpacks everything into a temp folder on *every* launch, and Windows
Defender scans each new DLL, so the first launch took very long.
The build now goes through a generated .spec: PyInstaller's own hooks
collect what the code imports, and the Qt pieces that only come in
through plugins nobody here uses (QML / Quick via the virtual keyboard,
Qt PDF via its image-format plugin, networking, the software OpenGL
renderer, Qt's own translations) are filtered out. UPX is off: packed
DLLs are slower to load and trip antivirus heuristics. A splash screen
(PyInstaller's, shown by the bootloader before Python starts) covers
the remaining unpacking time; the app closes it once its window is up.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "src" / "annoter" / "__main__.py"
RESOURCES = ROOT / "resources"
SPLASH_IMAGE = RESOURCES / "splash.png"

APP_NAME = "Annoter"

# Python modules never needed at run time. The PySide6 ones are Qt
# add-ons the app does not import; listing them keeps PyInstaller from
# following optional imports into them.
EXCLUDED_MODULES: tuple[str, ...] = (
    "tkinter",
    "PySide6.QtNetwork",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtMultimedia",
)

# Bundle entries (destination paths, "/"-separated) dropped after the
# analysis: Qt libraries and plugins pulled in only by plugins the app
# never loads, plus Qt's translation catalogs (the UI is English only
# and no QTranslator is installed). Matching is case-insensitive and
# covers the Windows (Qt6Quick.dll) and Linux (libQt6Quick.so.6)
# spellings.
DROPPED_PATTERNS: tuple[str, ...] = (
    r"(^|/)(lib)?Qt6(Qml|Quick|VirtualKeyboard|Pdf|Network|WebEngine|"
    r"Multimedia|3D|Charts|DataVisualization|Graphs|Designer|Test|"
    r"Location|Positioning|Sensors|SerialPort|Bluetooth|Nfc|WebSockets|"
    r"WebChannel|RemoteObjects|Scxml|StateMachine|TextToSpeech|"
    r"SpatialAudio|HttpServer|ShaderTools|Lottie)[^/]*$",
    r"(^|/)plugins/(tls|networkinformation|qmltooling|scenegraph|"
    r"position|multimedia|sqldrivers|canbus|sensors|texttospeech|"
    r"webview)/",
    # The virtual keyboard input context drags in QML / Quick; the
    # others (compose, ibus: dead keys and IMEs on Linux) stay.
    r"(^|/)plugins/platforminputcontexts/[^/]*virtualkeyboard[^/]*$",
    r"(^|/)plugins/imageformats/[^/]*pdf[^/]*$",
    r"(^|/)opengl32sw\.dll$",
    r"(^|/)d3dcompiler_\d+\.dll$",
    r"(^|/)PySide6/Qt/translations/",
    r"(^|/)PySide6/translations/",
)


def dropped(dest: str) -> bool:
    """True when a bundle entry (destination path) is filtered out."""
    path = dest.replace("\\", "/")
    return any(re.search(p, path, re.IGNORECASE) for p in DROPPED_PATTERNS)


def _datas() -> list[tuple[str, str]]:
    """Resource folders copied into the bundle as (source, dest dir)."""
    out: list[tuple[str, str]] = []
    for sub in ("themes", "icons", "fonts"):
        d = RESOURCES / sub
        if d.is_dir() and any(d.iterdir()):
            out.append((str(d), f"resources/{sub}"))
    return out


def engine_library() -> Path | None:
    """The native display engine built for this platform
    (native/build_engine.py copies it into src/annoter/engine/bin), or
    None: the build then falls back to the PyMuPDF display."""
    if sys.platform.startswith("win"):
        name = "annoter_engine.dll"
    elif sys.platform == "darwin":
        name = "libannoter_engine.dylib"
    else:
        name = "libannoter_engine.so"
    lib = ROOT / "src" / "annoter" / "engine" / "bin" / name
    return lib if lib.is_file() else None


def _binaries() -> list[tuple[str, str]]:
    """Extra binaries as (source, dest dir): the engine library lands
    next to the executable (sys._MEIPASS), where annoter.engine looks."""
    lib = engine_library()
    return [(str(lib), ".")] if lib is not None else []


def splash_available() -> bool:
    """PyInstaller's splash needs Tcl/Tk in the building Python."""
    if not SPLASH_IMAGE.is_file():
        return False
    try:
        import _tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def make_spec(onefile: bool, splash: bool) -> str:
    """The .spec PyInstaller runs (see the module docstring)."""
    icon = RESOURCES / "icons" / "app.ico"
    icon_arg = repr(str(icon)) if icon.is_file() else "None"
    lines = [
        "# Generated by build.py -- edit build.py, not this file.",
        "import re",
        "",
        f"DROPPED = {list(DROPPED_PATTERNS)!r}",
        "",
        "def _keep(entry):",
        "    path = entry[0].replace('\\\\', '/')",
        "    return not any(",
        "        re.search(p, path, re.IGNORECASE) for p in DROPPED",
        "    )",
        "",
        "a = Analysis(",
        f"    [{str(ENTRY)!r}],",
        f"    pathex=[{str(ROOT / 'src')!r}],",
        f"    datas={_datas()!r},",
        f"    binaries={_binaries()!r},",
        f"    excludes={list(EXCLUDED_MODULES)!r},",
        ")",
        "a.binaries = [e for e in a.binaries if _keep(e)]",
        "a.datas = [e for e in a.datas if _keep(e)]",
        "pyz = PYZ(a.pure)",
    ]
    splash_parts = ""
    if splash:
        lines += [
            "splash = Splash(",
            f"    {str(SPLASH_IMAGE)!r},",
            "    binaries=a.binaries,",
            "    datas=a.datas,",
            "    text_pos=None,",
            "    always_on_top=False,",
            ")",
        ]
        splash_parts = "splash, "
    common = (
        f"name={APP_NAME!r}, console=False, upx=False, "
        f"icon={icon_arg}, debug=False, strip=False"
    )
    if onefile:
        extra = "splash.binaries, " if splash else ""
        lines += [
            f"exe = EXE(pyz, a.scripts, {splash_parts}{extra}"
            f"a.binaries, a.datas, [], {common}, runtime_tmpdir=None)",
        ]
    else:
        extra = "splash.binaries, " if splash else ""
        lines += [
            f"exe = EXE(pyz, a.scripts, {splash_parts}[], "
            f"exclude_binaries=True, {common})",
            f"coll = COLLECT(exe, {extra}a.binaries, a.datas, "
            f"upx=False, strip=False, name={APP_NAME!r})",
        ]
    return "\n".join(lines) + "\n"


def _run(cmd: list[str]) -> int:
    print(">>", " ".join(cmd))
    return subprocess.call(cmd)


def _build(onefile: bool, splash: bool) -> int:
    kind = "onefile" if onefile else "onedir"
    out = ROOT / f"dist-{kind}"
    work = ROOT / f"build-{kind}"
    if out.exists():
        shutil.rmtree(out)
    work.mkdir(parents=True, exist_ok=True)
    spec = work / f"{APP_NAME}.spec"
    spec.write_text(make_spec(onefile, splash), encoding="utf-8")
    rc = _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(out),
            "--workpath",
            str(work),
            str(spec),
        ]
    )
    if rc == 0:
        size = sum(
            p.stat().st_size
            for p in out.rglob("*")
            if p.is_file() and not p.is_symlink()
        )
        print(f">> {kind}: {size / 1e6:.0f} MB in {out}")
    return rc


def build_onefile(splash: bool = True) -> int:
    return _build(True, splash)


def build_onedir(splash: bool = True) -> int:
    return _build(False, splash)


def executable(onefile: bool) -> Path:
    suffix = ".exe" if sys.platform.startswith("win") else ""
    if onefile:
        return ROOT / "dist-onefile" / f"{APP_NAME}{suffix}"
    return ROOT / "dist-onedir" / APP_NAME / f"{APP_NAME}{suffix}"


def smoke(onefile: bool, require_engine: bool = False) -> int:
    """Launch a build with --smoke-test (it quits once its window is
    shown), report the wall-clock start-up time and which page display
    the build uses; with `require_engine`, fail unless it is the native
    engine."""
    exe = executable(onefile)
    if not exe.is_file():
        print(f"!! {exe} not found")
        return 1
    report = (ROOT / f"build-{'onefile' if onefile else 'onedir'}" / "smoke-report.txt")
    report.unlink(missing_ok=True)
    env = dict(os.environ, ANNOTER_SMOKE_REPORT=str(report))
    start = time.perf_counter()
    rc = subprocess.call([str(exe), "--smoke-test"], timeout=300, env=env)
    took = time.perf_counter() - start
    kind = "onefile" if onefile else "onedir"
    print(f">> {kind} start-up: {took:.1f} s (exit code {rc})")
    info = report.read_text(encoding="utf-8").split() if report.is_file() else []
    renderer = next((x.split("=", 1)[1] for x in info if x.startswith("renderer=")), "unknown")
    print(f">> {kind} page display: {renderer}")
    if rc == 0 and require_engine and renderer != "engine":
        print(f"!! {kind}: the native display engine did not load ({' '.join(info)})")
        return 1
    return rc


def zip_onedir() -> int:
    src = ROOT / "dist-onedir" / APP_NAME
    if not src.is_dir():
        print(f"!! {src} not found; build onedir first.")
        return 1
    target = ROOT / "dist-onedir" / f"{APP_NAME}-portable.zip"
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in src.rglob("*"):
            zf.write(p, p.relative_to(src.parent))
    print(f">> wrote {target} ({target.stat().st_size / 1e6:.1f} MB)")
    return 0


def _check_environment() -> None:
    """Refuse to run with a Python that can't import the app's deps.

    PyInstaller bundles whatever is importable from `sys.executable`;
    running `python build.py` with the system Python instead of the
    project's `.venv` produces an .exe that crashes with
    `ModuleNotFoundError: No module named 'PySide6'` at startup.
    """
    missing: list[str] = []
    for mod in ("PySide6", "pymupdf", "PIL"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise SystemExit(
            f"!! cannot build: {', '.join(missing)} not importable from "
            f"{sys.executable}.\n"
            f"   Run this script with the project's venv interpreter, e.g.\n"
            f"   .venv\\Scripts\\python build.py"
        )


def main() -> int:
    _check_environment()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--onefile", action="store_true")
    ap.add_argument("--onedir", action="store_true")
    ap.add_argument(
        "--zip-onedir",
        action="store_true",
        help="Zip the onedir output (implies --onedir if neither selected).",
    )
    ap.add_argument(
        "--no-splash",
        action="store_true",
        help="Build without the launch splash screen.",
    )
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="Launch each build once and report its start-up time.",
    )
    ap.add_argument(
        "--require-engine",
        action="store_true",
        help="Fail if the native display engine library is missing "
        "(and, with --smoke, if a build does not use it).",
    )
    args = ap.parse_args()

    lib = engine_library()
    if lib is None:
        msg = (
            "no native display engine in src/annoter/engine/bin (build it "
            "with native/build_engine.py): the builds will use the "
            "PyMuPDF display."
        )
        if args.require_engine:
            print(f"!! {msg}")
            return 1
        print(f"!! {msg}")
    else:
        print(f">> native display engine: {lib.name} ({lib.stat().st_size / 1e6:.1f} MB)")

    do_one = args.onefile
    do_dir = args.onedir or args.zip_onedir
    if not (do_one or do_dir):
        do_one = do_dir = True

    splash = not args.no_splash and splash_available()
    if not args.no_splash and not splash:
        print(
            "!! no splash screen: PyInstaller's needs Tcl/Tk in this "
            "Python (install it with the python.org installer) and "
            f"{SPLASH_IMAGE.relative_to(ROOT)}."
        )

    if do_one:
        rc = build_onefile(splash)
        if rc != 0:
            return rc
        if args.smoke and smoke(True, args.require_engine) != 0:
            return 1
    if do_dir:
        rc = build_onedir(splash)
        if rc != 0:
            return rc
        if args.smoke and smoke(False, args.require_engine) != 0:
            return 1
    if args.zip_onedir:
        rc = zip_onedir()
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
