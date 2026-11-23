"""Display benchmark: what the user waits for, before / after the engine.

Drives a real Annoter window through the scenario of PLAN.md ("Native
display engine from Lire", measurement protocol) and reports, for each
action, the time until the visible area is sharp and the longest freeze
of the UI (longest gap between two ticks of a 5 ms heartbeat timer).
Between two actions it waits for the application to go quiet, like
someone reading the plan, so background work (thumbnails, prefetch)
does not count against the next action -- the same rule as the
"interactif" scenario of Lire's tests/harness.c.

Usage (PowerShell or bash; paths may be absolute):

    python tools/bench_display.py PLAN.pdf                     # current renderer
    python tools/bench_display.py PLAN.pdf --renderer pymupdf  # force the PyMuPDF path
    python tools/bench_display.py PLAN.pdf --renderer both     # before / after table
    python tools/bench_display.py PLAN.pdf --offscreen         # no window (CI, servers)

Options: --size 1600x1000 (window size), --zoom 3.0 (detail zoom),
--json FILE (raw results), --threads N / --ram-gb N (engine limits, to
stand in for a modest PC; ignored by the PyMuPDF path).

The renderer is chosen through ANNOTER_RENDERER (pymupdf / engine),
honoured by the app once the engine is wired (step 4 of the plan); until
then every run uses the PyMuPDF path, and `--renderer engine` stops with
a message.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCENARIO = (
    ("open", "Open -> sharp page (fit)"),
    ("detail", "Zoom to detail (fit -> {zoom:.0f} %)"),
    ("notch", "1 wheel notch"),
    ("pan", "Pan 800 x 400 px"),
    ("next", "Next page"),
    ("fit", "Back to whole page"),
)
QUIET_GAP_MS = 40.0       # a tick later than this means the UI was busy
QUIET_FOR_S = 1.0         # how long the UI must stay responsive to be "quiet"
ACTION_TIMEOUT_S = 300.0


def peak_rss_mb() -> float:
    """Peak resident memory of this process, in MB (0 if unknown)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL("kernel32")
            psapi = ctypes.WinDLL("psapi")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return pmc.PeakWorkingSetSize / 2**20
            return 0.0
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024
    except Exception:  # noqa: BLE001 -- informative only, never fail the bench on it
        return 0.0
    return 0.0


def busiest_point(path: Path, page: int = 0) -> tuple[float, float]:
    """Centre (in PDF points) of the busiest cell of an 8 x 8 grid, from a
    small PyMuPDF preview -- where an engineer would zoom in."""
    import fitz

    with fitz.open(str(path)) as doc:
        p = doc[page]
        r = p.rect
        zoom = 400 / max(r.width, r.height)
        pix = p.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
        w, h, s = pix.width, pix.height, pix.samples
        best, bx, by = -1, 0, 0
        for gy in range(8):
            for gx in range(8):
                dark = 0
                for y in range(gy * h // 8, (gy + 1) * h // 8, 2):
                    row = y * pix.stride
                    for x in range(gx * w // 8, (gx + 1) * w // 8, 2):
                        dark += 255 - s[row + x]
                if dark > best:
                    best, bx, by = dark, gx, gy
        return (r.x0 + (bx + 0.5) * r.width / 8, r.y0 + (by + 0.5) * r.height / 8)


def run_scenario(args: argparse.Namespace) -> dict:
    """One renderer, in this process. Returns the results as a dict."""
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if args.renderer in ("pymupdf", "engine"):
        os.environ["ANNOTER_RENDERER"] = args.renderer
    if args.threads:
        os.environ["ANNOTER_ENGINE_THREADS"] = str(args.threads)
    if args.ram_gb:
        ram = args.ram_gb * 1024
        os.environ["ANNOTER_ENGINE_TILE_MB"] = str(int(min(ram / 12, 1024)))
        os.environ["ANNOTER_ENGINE_LIST_MB"] = str(int(min(ram / 10, 2048)))

    import fitz
    from PySide6.QtCore import (
        QCoreApplication,
        QElapsedTimer,
        QEventLoop,
        QPoint,
        QPointF,
        Qt,
        QTimer,
    )
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    path = Path(args.pdf).resolve()
    detail_pt = busiest_point(path)

    QCoreApplication.setOrganizationName("AnnoterBench")
    QCoreApplication.setApplicationName("AnnoterBench")
    app = QApplication.instance() or QApplication([sys.argv[0]])
    from annoter.config import BASE_RENDER_DPI
    from annoter.services.fonts import register_ui_fonts
    from annoter.views.main_window import MainWindow

    register_ui_fonts()  # as app.main() does, before the first widget

    win = MainWindow()
    w, h = (int(v) for v in args.size.lower().split("x"))
    win.resize(w, h)
    win.show()
    app.processEvents()
    view = win._view

    clock = QElapsedTimer()
    clock.start()
    beat = {"last": clock.nsecsElapsed() / 1e6, "max_gap": 0.0, "quiet_since": clock.nsecsElapsed() / 1e6}

    def on_beat() -> None:
        now = clock.nsecsElapsed() / 1e6
        gap = now - beat["last"]
        beat["last"] = now
        beat["max_gap"] = max(beat["max_gap"], gap)
        if gap > QUIET_GAP_MS:
            beat["quiet_since"] = now

    heartbeat = QTimer()
    heartbeat.setInterval(5)
    heartbeat.timeout.connect(on_beat)
    heartbeat.start()

    def now_ms() -> float:
        return clock.nsecsElapsed() / 1e6

    def renderer_name() -> str:
        item = win._scene.page_item()
        return "engine" if type(item).__name__ == "TiledPageItem" else "pymupdf"

    def settled() -> bool:
        """True once the visible area is as sharp as the renderer makes it."""
        if win._doc is None or win._scene.page_item() is None:
            return False
        display = getattr(win, "_engine_display", None)
        if display is not None and renderer_name() == "engine":
            return bool(display.settled())  # EngineDisplay, step 4
        # PyMuPDF path: renders are synchronous; the only deferred work
        # is the hi-res overlay, behind its debounce timer.
        return not win._hires_timer.isActive()

    def pump(ms: int = 5) -> None:
        app.processEvents(QEventLoop.AllEvents, ms)

    def measure(action) -> tuple[float, float]:
        """Run `action` from the event loop; return (ms until sharp and
        painted, longest UI freeze in ms)."""
        state = {"done": False}

        def go() -> None:
            action()
            state["done"] = True

        beat["max_gap"] = 0.0
        t0 = now_ms()
        QTimer.singleShot(0, go)
        while True:
            pump()
            if state["done"] and settled():
                break
            if now_ms() - t0 > ACTION_TIMEOUT_S * 1000:
                print("  !! timeout", file=sys.stderr)
                break
        view.viewport().repaint()  # the frame the user finally sees
        sharp = now_ms() - t0
        pump()
        return sharp, beat["max_gap"]

    def wait_quiet() -> None:
        """Let background work finish: UI responsive for QUIET_FOR_S."""
        t0 = now_ms()
        beat["quiet_since"] = now_ms()
        while now_ms() - beat["quiet_since"] < QUIET_FOR_S * 1000:
            pump(20)
            if now_ms() - t0 > ACTION_TIMEOUT_S * 1000:
                break

    k = BASE_RENDER_DPI / 72.0
    vp = view.viewport()

    def act_open() -> None:
        win._open_path(str(path), add_to_recent=False)

    def act_detail() -> None:
        view.set_zoom(args.zoom)
        view.centerOn(QPointF(detail_pt[0] * k, detail_pt[1] * k))

    def act_notch() -> None:
        c = vp.rect().center()
        ev = QWheelEvent(
            QPointF(c), QPointF(vp.mapToGlobal(c)), QPoint(0, 0), QPoint(0, 120),
            Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False,
        )
        QApplication.sendEvent(vp, ev)

    def act_pan() -> None:
        hb, vb = view.horizontalScrollBar(), view.verticalScrollBar()
        for _ in range(20):  # a drag: 20 moves of 40 x 20 px
            hb.setValue(hb.value() + 40)
            vb.setValue(vb.value() + 20)
            app.processEvents(QEventLoop.ExcludeUserInputEvents)

    def act_next() -> None:
        win._goto_next_page()

    def act_fit() -> None:
        view.zoom_to_fit()

    actions = {
        "open": act_open, "detail": act_detail, "notch": act_notch,
        "pan": act_pan, "next": act_next, "fit": act_fit,
    }
    results = {}
    for key, _label in SCENARIO:
        sharp, freeze = measure(actions[key])
        results[key] = {"sharp_ms": round(sharp, 1), "freeze_ms": round(freeze, 1), "zoom": round(view.zoom(), 3)}
        print(f"  {key:7s} sharp {sharp:9.0f} ms   longest freeze {freeze:9.0f} ms   zoom {view.zoom():.2f}")
        wait_quiet()

    with fitz.open(str(path)) as d:
        pages = d.page_count
    info = {
        "renderer": renderer_name(),
        "file": path.name,
        "file_mb": round(path.stat().st_size / 1e6, 1),
        "pages": pages,
        "window": args.size,
        "device_pixel_ratio": vp.devicePixelRatioF(),
        "cpus": os.cpu_count(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pymupdf": fitz.VersionBind,
        "threads": args.threads or "auto",
        "ram_gb_budget": args.ram_gb or "machine",
        "peak_rss_mb": round(peak_rss_mb()),
    }
    heartbeat.stop()
    win._on_close()
    win.close()
    return {"info": info, "results": results}


def table(runs: list[dict], zoom: float) -> str:
    """Markdown comparison table (one column pair per run)."""
    head = "| Action | " + " | ".join(
        f"{r['info']['renderer']}: sharp | {r['info']['renderer']}: longest freeze" for r in runs
    ) + " |"
    sep = "|---|" + "---:|---:|" * len(runs)
    rows = [head, sep]
    for key, label in SCENARIO:
        cells = []
        for r in runs:
            v = r["results"].get(key, {})
            cells.append(f"{v.get('sharp_ms', float('nan')) / 1000:.2f} s")
            cells.append(f"{v.get('freeze_ms', float('nan')) / 1000:.2f} s")
        rows.append(f"| {label.format(zoom=zoom * 100)} | " + " | ".join(cells) + " |")
    rows.append("| Peak memory of the process | " + " | ".join(f"{r['info']['peak_rss_mb']} MB | " for r in runs) + "|")
    i = runs[0]["info"]
    rows.append("")
    rows.append(
        f"{i['file']} ({i['file_mb']} MB, {i['pages']} pages), window {i['window']}, "
        f"devicePixelRatio {i['device_pixel_ratio']}, {i['cpus']} CPUs, {i['platform']}, "
        f"Python {i['python']}, PyMuPDF {i['pymupdf']}."
    )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pdf", nargs="?", default=str(REPO / "bench" / "plans" / "plans_15xA0.pdf"))
    ap.add_argument("--renderer", choices=("current", "pymupdf", "engine", "both"), default="current")
    ap.add_argument("--offscreen", action="store_true")
    ap.add_argument("--size", default="1600x1000")
    ap.add_argument("--zoom", type=float, default=3.0)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--ram-gb", type=float, default=0)
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    if not Path(args.pdf).is_file():
        print(f"missing {args.pdf}: run tools/genplans.py first", file=sys.stderr)
        return 2

    if args.renderer == "both":
        runs = []
        for name in ("pymupdf", "engine"):
            print(f"== {name}")
            out = REPO / "bench" / f"result_{name}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, str(Path(__file__).resolve()), args.pdf, "--renderer", name,
                   "--size", args.size, "--zoom", str(args.zoom), "--json", str(out)]
            if args.offscreen:
                cmd.append("--offscreen")
            if args.threads:
                cmd += ["--threads", str(args.threads)]
            if args.ram_gb:
                cmd += ["--ram-gb", str(args.ram_gb)]
            if subprocess.call(cmd) == 0 and out.is_file():
                runs.append(json.loads(out.read_text(encoding="utf-8")))
        if not runs:
            return 1
        print()
        print(table(runs, args.zoom))
        return 0

    print(f"== {args.renderer} on {Path(args.pdf).name}")
    run = run_scenario(args)
    if args.renderer == "engine" and run["info"]["renderer"] != "engine":
        print("the engine is not active yet (it is wired at step 4 of the plan)", file=sys.stderr)
        return 3
    if args.json:
        Path(args.json).write_text(json.dumps(run, indent=2), encoding="utf-8")
    print()
    print(table([run], args.zoom))
    return 0


if __name__ == "__main__":
    sys.exit(main())
