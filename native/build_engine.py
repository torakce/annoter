"""Build the native display engine.

Fetches MuPDF 1.28.6 (official sources and the third-party libraries it
needs), applies `native/patches/mupdf-1.28.6-lire.patch`, builds MuPDF
for PDF only with Lire's options, then links `native/src/engine.c` and
the static MuPDF libraries into one library that exports only the
`ann_*` functions of `native/include/annoter_engine.h`:

    python native/build_engine.py --target win64   # annoter_engine.dll
    python native/build_engine.py --target linux   # libannoter_engine.so

Requirements: git, GNU make, and a C compiler --
  * win64 from Linux or WSL: mingw-w64 (Debian/Ubuntu package "mingw-w64"),
    as Lire's build.sh; from Windows: an MSYS2 "MINGW64" shell (gcc, make);
  * linux: gcc.

The result is checked (exported symbols, imported DLLs) and copied into
`src/annoter/engine/bin/` (gitignored), where Annoter looks for it.
Options: --deps DIR (MuPDF sources, default native/deps), --jobs N,
--no-copy, --mupdf-only.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MUPDF_VERSION = "1.28.6"
MUPDF_URL = "https://github.com/ArtifexSoftware/mupdf.git"
SUBMODULES = (
    "thirdparty/jbig2dec",
    "thirdparty/freetype",
    "thirdparty/harfbuzz",
    "thirdparty/libjpeg",
    "thirdparty/lcms2",
    "thirdparty/openjpeg",
    "thirdparty/zlib",
)
PATCH = HERE / "patches" / f"mupdf-{MUPDF_VERSION}-lire.patch"
BIN_DIR = REPO / "src" / "annoter" / "engine" / "bin"

# PDF (and images) only: no XPS / EPUB / HTML / SVG, no JavaScript, no Noto
# fallback fonts; the standard PDF fonts stay in (Lire's options).
MUPDF_OPTIONS = [
    "build=release",
    "HAVE_X11=no",
    "HAVE_GLUT=no",
    "HAVE_CURL=no",
    "HAVE_LIBCRYPTO=no",
    "html=no",
    "xps=no",
    "svg=no",
    "extract=no",
    "mujs=no",
    "brotli=no",
    "tofu=yes",
    "tofu_cjk_ext=yes",
]

# Functions of annoter_engine.h: the whole public surface of the library.
API = (
    "ann_api_version", "ann_engine_new", "ann_engine_free",
    "ann_open_file", "ann_open_memory", "ann_reload_file", "ann_reload_memory", "ann_close",
    "ann_page_count", "ann_page_bounds", "ann_set_hidden_annots", "ann_set_flags", "ann_set_thumbnails",
    "ann_request_view", "ann_poll", "ann_release_tile", "ann_poll_evicted",
    "ann_busy", "ann_view_complete", "ann_cancel", "ann_stats",
)
# DLLs every Windows has; anything else would have to ship with Annoter.
SYSTEM_DLLS = {"kernel32.dll", "msvcrt.dll", "user32.dll", "advapi32.dll", "gdi32.dll", "shell32.dll"}


def run(cmd: list[str], cwd: Path | None = None, **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=cwd, check=True, **kw)


def fetch_mupdf(deps: Path) -> Path:
    """Official MuPDF sources with the submodules a PDF-only build needs,
    patched once."""
    src = deps / "mupdf"
    if not (src / "Makefile").is_file():
        deps.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "1", "--branch", MUPDF_VERSION, MUPDF_URL, src])
    missing = [m for m in SUBMODULES if not (src / m).is_dir() or not any((src / m).iterdir())]
    if missing:
        run(["git", "-C", src, "submodule", "update", "--init", "--depth", "1", *SUBMODULES])
    applied = subprocess.run(
        ["git", "-C", str(src), "apply", "--reverse", "--check", str(PATCH)],
        capture_output=True,
        check=False,
    ).returncode == 0
    if not applied:
        run(["git", "-C", src, "apply", PATCH])
    return src


def build_mupdf(src: Path, target: str, jobs: int) -> Path:
    """Static libmupdf.a + libmupdf-third.a for `target`; returns their folder."""
    opts = list(MUPDF_OPTIONS)
    on_windows = sys.platform == "win32"
    if target == "win64":
        # ARCH_HAS_SSE=0: only image deskewing uses SSE; the DLL stays
        # compatible with every x64 processor (as Lire.exe).
        opts.append("XCFLAGS=-DARCH_HAS_SSE=0")
        if on_windows:  # MSYS2 MINGW64 shell: native compiler
            opts.append("build_prefix=mingw64-")
        else:
            cross = "x86_64-w64-mingw32-"
            opts += ["OS=mingw-cross", "build_prefix=mingw64-", f"CC={cross}gcc",
                     f"CXX={cross}g++", f"AR={cross}ar"]
    else:
        # Position-independent and hidden: linked into a shared library,
        # and never visible to PyMuPDF's own MuPDF in the same process.
        opts += ["build_prefix=linux-", "XCFLAGS=-fPIC -fvisibility=hidden"]
    run(["make", "-C", src, f"-j{jobs}", *opts, "libs"])
    prefix = "mingw64-" if target == "win64" else "linux-"
    found = sorted((src / "build").glob(f"{prefix}release*"))
    if not found:
        raise SystemExit(f"MuPDF build output not found under {src / 'build'}")
    return found[0]


def build_engine(src: Path, libdir: Path, target: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    common = [
        "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter", "-std=gnu11", "-DANN_BUILDING",
        "-I", HERE / "include", "-I", src / "include",
        HERE / "src" / "engine.c",
    ]
    libs = [libdir / "libmupdf.a", libdir / "libmupdf-third.a"]
    if target == "win64":
        cc = "gcc" if sys.platform == "win32" else "x86_64-w64-mingw32-gcc"
        dll = out / "annoter_engine.dll"
        run([cc, "-shared", "-s", "-static", "-static-libgcc", *common, HERE / "src" / "exports.def",
             *libs, "-lm", "-o", dll])
        return dll
    so = out / "libannoter_engine.so"
    run(["gcc", "-shared", "-fPIC", "-fvisibility=hidden", *common, *libs,
         f"-Wl,--version-script={HERE / 'src' / 'exports.map'}", "-Wl,--exclude-libs,ALL",
         "-Wl,-Bsymbolic", "-Wl,--no-undefined", "-Wl,-z,now", "-s", "-lm", "-lpthread", "-o", so])
    return so


def pe_exports_imports(path: Path) -> tuple[set[str], set[str]]:
    """Exported function names and imported DLL names of a PE32+ file, read
    from its export and import directories (no dependence on the output
    format of a given objdump version)."""
    data = path.read_bytes()

    def u16(o: int) -> int:
        return int.from_bytes(data[o:o + 2], "little")

    def u32(o: int) -> int:
        return int.from_bytes(data[o:o + 4], "little")

    pe = u32(0x3C)
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit(f"{path.name}: not a PE file")
    nsections, opt_size = u16(pe + 6), u16(pe + 20)
    opt = pe + 24
    if u16(opt) != 0x20B:
        raise SystemExit(f"{path.name}: not a 64-bit (PE32+) library")
    dirs = opt + 112                       # data directories of PE32+
    sections = [
        (u32(sh + 12), max(u32(sh + 8), u32(sh + 16)), u32(sh + 20))  # RVA, size, file offset
        for sh in (opt + opt_size + 40 * k for k in range(nsections))
    ]

    def off(rva: int) -> int:
        for va, size, raw in sections:
            if va <= rva < va + size:
                return raw + rva - va
        raise SystemExit(f"{path.name}: RVA {rva:#x} outside the sections")

    def cstr(rva: int) -> str:
        o = off(rva)
        return data[o:data.index(b"\0", o)].decode("ascii")

    exports: set[str] = set()
    exp_rva = u32(dirs)
    if exp_rva:
        e = off(exp_rva)
        names = off(u32(e + 32))
        exports = {cstr(u32(names + 4 * k)) for k in range(u32(e + 24))}
    imports: set[str] = set()
    imp_rva = u32(dirs + 8)
    if imp_rva:
        d = off(imp_rva)
        while any(data[d:d + 20]):
            imports.add(cstr(u32(d + 12)).lower())
            d += 20
    return exports, imports


def check(lib: Path, target: str) -> None:
    """The library exports exactly the API, and (Windows) imports only
    system DLLs."""
    if target == "win64":
        exports, dlls = pe_exports_imports(lib)
        extra = dlls - SYSTEM_DLLS
        if extra:
            raise SystemExit(f"{lib.name} needs non-system DLLs: {sorted(extra)}")
        print(f"imports: {sorted(dlls)}")
    else:
        text = subprocess.run(["nm", "-D", "--defined-only", str(lib)], capture_output=True,
                              text=True, check=True).stdout
        exports = {line.split()[-1] for line in text.splitlines() if line.strip()}
    if exports != set(API):
        raise SystemExit(
            f"{lib.name}: unexpected exports {sorted(exports - set(API))}, "
            f"missing {sorted(set(API) - exports)}"
        )
    print(f"{lib.name}: exports exactly the {len(API)} ann_* functions, {lib.stat().st_size / 1e6:.1f} MB")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    default = "win64" if sys.platform == "win32" else ("linux" if platform.system() == "Linux" else "win64")
    ap.add_argument("--target", choices=("win64", "linux"), default=default)
    ap.add_argument("--deps", default=os.environ.get("ANNOTER_ENGINE_DEPS", str(HERE / "deps")))
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--out", default=str(HERE / "build" / "engine"))
    ap.add_argument("--no-copy", action="store_true", help="do not copy into src/annoter/engine/bin")
    ap.add_argument("--mupdf-only", action="store_true", help="stop after building MuPDF")
    args = ap.parse_args(argv)

    src = fetch_mupdf(Path(args.deps).resolve())
    libdir = build_mupdf(src, args.target, args.jobs)
    if args.mupdf_only:
        return 0
    lib = build_engine(src, libdir, args.target, Path(args.out).resolve() / args.target)
    check(lib, args.target)
    if not args.no_copy:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lib, BIN_DIR / lib.name)
        print(f"copied to {BIN_DIR / lib.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
