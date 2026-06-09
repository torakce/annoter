"""PyInstaller driver: produces both onefile and onedir builds.

Usage:
    python build.py                # build both
    python build.py --onefile      # build only the single .exe
    python build.py --onedir       # build only the portable folder
    python build.py --zip-onedir   # also zip the onedir output

Outputs land in `dist-onefile/` and `dist-onedir/` respectively. Each
build keeps its own `build/` work directory under `build-onefile/` /
`build-onedir/` so the two modes don't trample on each other.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "src" / "annoter" / "__main__.py"
RESOURCES = ROOT / "resources"

APP_NAME = "Annoter"


def _resource_args() -> list[str]:
    """Return PyInstaller --add-data arguments for bundled resources.

    PyInstaller takes "<src><pathsep><dest>"; on Windows the separator
    is `;`, on POSIX it's `:`. Each entry maps a host directory into
    the bundle root.
    """
    sep = ";" if sys.platform.startswith("win") else ":"
    bundles: list[tuple[Path, str]] = []
    for sub in ("themes", "icons", "fonts"):
        d = RESOURCES / sub
        if d.is_dir() and any(d.iterdir()):
            bundles.append((d, f"resources/{sub}"))
    args: list[str] = []
    for src, dest in bundles:
        args.extend(["--add-data", f"{src}{sep}{dest}"])
    return args


def _common_args(name: str) -> list[str]:
    return [
        "--name",
        name,
        "--noconfirm",
        "--clean",
        "--windowed",
        "--paths",
        str(ROOT / "src"),
        # PySide6 + PyMuPDF ship as C extensions; --collect-all is the
        # only flag that pulls submodules + binaries + data together.
        # Without it the bundle silently drops the .pyd files.
        "--collect-all",
        "PySide6",
        "--collect-all",
        "shiboken6",
        "--collect-all",
        "pymupdf",
        *_resource_args(),
    ]


def _run(cmd: list[str]) -> int:
    print(">>", " ".join(cmd))
    return subprocess.call(cmd)


def build_onefile() -> int:
    out = ROOT / "dist-onefile"
    work = ROOT / "build-onefile"
    if out.exists():
        shutil.rmtree(out)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--distpath",
        str(out),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        *_common_args(APP_NAME),
        str(ENTRY),
    ]
    return _run(cmd)


def build_onedir() -> int:
    out = ROOT / "dist-onedir"
    work = ROOT / "build-onedir"
    if out.exists():
        shutil.rmtree(out)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--distpath",
        str(out),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        *_common_args(APP_NAME),
        str(ENTRY),
    ]
    return _run(cmd)


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
    args = ap.parse_args()

    do_one = args.onefile
    do_dir = args.onedir or args.zip_onedir
    if not (do_one or do_dir):
        do_one = do_dir = True

    if do_one:
        rc = build_onefile()
        if rc != 0:
            return rc
    if do_dir:
        rc = build_onedir()
        if rc != 0:
            return rc
    if args.zip_onedir:
        rc = zip_onedir()
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
