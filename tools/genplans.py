"""Generate heavy synthetic engineering plans for display benchmarks.

The user's real plans (CATIA exports, A0, about 2 MB per page) are
confidential, so measurements run on look-alikes. Derived from Lire's
`tests/genpdf.py`, with what CATIA V5 exports tend to contain: many short
stroked elements each with its own line width and colour, circles with
dashed centre lines, hatched sections, dimensions with arrows, title
block, and text both as real text and as small vectorized glyph strokes.

Usage (any shell):

    python tools/genplans.py                      # -> bench/plans/ in the repo
    python tools/genplans.py C:/some/dir --pages 15 --mb-per-page 2
    python tools/genplans.py --annotated          # also an annotated copy

Files written:
    plans_<N>xA0.pdf             N A0 landscape pages of about `mb-per-page`
    plans_<N>xA0_annotated.pdf   (with --annotated) the same plan carrying
                                 Annoter's own annotations and foreign ones
                                 (Acrobat-like square, stamp, highlight...)
                                 on its first two pages

The plan itself needs only the standard library; the annotated copy also
needs PyMuPDF and PySide6 (Annoter's own annotations are written through
`annoter.services.pdf_export.write_annotations`, as the app does).
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
import zlib
from pathlib import Path

A0_LANDSCAPE = (3370.4, 2383.9)  # points
REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "bench" / "plans"

# Line widths (points) of the usual ISO pens: 0.13 / 0.18 / 0.25 / 0.35 /
# 0.5 / 0.7 mm.
PENS = (0.37, 0.51, 0.71, 0.99, 1.42, 1.98)
COLORS = ("0 0 0", "0 0 0", "0 0 0", "0.1 0.1 0.6", "0.6 0 0", "0 0.4 0")

TEXTS = (
    "%%c%.1f H7",
    "R%.1f",
    "%.2f +/-0.05",
    "A-A",
    "DETAIL B (2:1)",
    "Ra 1.6",
    "M%d x %.2f",
    "%.1f x 45 deg",
    "4 x %%c%.1f THRU",
)


def _text(r: random.Random) -> str:
    t = r.choice(TEXTS)
    if t.startswith("M%d"):
        return t % (r.randint(3, 24), r.choice([0.5, 0.75, 1.0, 1.25, 1.5]))
    if "%" in t.replace("%%", ""):
        return (t % r.uniform(1, 500)).replace("%c", "\xd8")
    return t


def _vector_glyphs(a, r: random.Random, x: float, y: float, size: float, n: int) -> None:
    """Text drawn as strokes, as CATIA does for its stroke fonts."""
    w = size * 0.6
    parts = []
    for i in range(n):
        gx = x + i * w * 1.1
        for _ in range(r.randint(2, 5)):
            x0, y0 = gx + r.uniform(0, w), y + r.uniform(0, size)
            x1, y1 = gx + r.uniform(0, w), y + r.uniform(0, size)
            parts.append(f"{x0:.2f} {y0:.2f} m {x1:.2f} {y1:.2f} l")
    a(" ".join(parts) + " S")


def drawing(w: float, h: float, target_raw: int, seed: int) -> bytes:
    """Content stream of one plan, about `target_raw` bytes uncompressed."""
    r = random.Random(seed)
    out: list[str] = []
    size = 0

    def a(s: str) -> None:
        nonlocal size
        out.append(s)
        size += len(s) + 1

    m = 30
    a("1 J 1 j 0.71 w 0 G")
    # frame, zone marks and title block
    a(f"2.8 w {m} {m} {w - 2 * m} {h - 2 * m} re S 0.71 w")
    for i in range(1, 16):
        x = m + i * (w - 2 * m) / 16
        a(f"{x:.1f} {m} m {x:.1f} {m + 12} l {x:.1f} {h - m} m {x:.1f} {h - m - 12} l S")
    tb_x, tb_y, tb_w, tb_h = w - m - 760, m, 760, 260
    a(f"1.42 w {tb_x} {tb_y} {tb_w} {tb_h} re S 0.71 w")
    for k in range(1, 8):
        yy = tb_y + k * tb_h / 8
        a(f"{tb_x} {yy:.1f} m {tb_x + tb_w} {yy:.1f} l S")
    a(f"BT /F1 30 Tf {tb_x + 20} {tb_y + 200} Td (SYNTHETIC PLAN - Annoter bench) Tj ET")
    a(f"BT /F1 14 Tf {tb_x + 20} {tb_y + 160} Td (Seed {seed} - not a real part) Tj ET")

    # Drawing views: geometry is grouped in a few areas with white space
    # around them, like the views of a real sheet.
    views = []
    cell_w, cell_h = (w - 2 * m) / 3, (h - 2 * m - tb_h) / 2
    cells = [(c, row) for row in range(2) for c in range(3)]
    r.shuffle(cells)
    for c, row in cells[: r.randint(4, 6)]:
        vw_, vh_ = cell_w * r.uniform(0.55, 0.8), cell_h * r.uniform(0.55, 0.8)
        vx = m + c * cell_w + r.uniform(0.05 * cell_w, cell_w - vw_ - 0.05 * cell_w)
        vy = m + tb_h + row * cell_h + r.uniform(0.05 * cell_h, cell_h - vh_ - 0.05 * cell_h)
        views.append((vx, vy, vw_, vh_))
        a(f"q 0.37 w [20 4 3 4] 0 d {vx + vw_ / 2:.1f} {vy - 20:.1f} m {vx + vw_ / 2:.1f} {vy + vh_ + 20:.1f} l S Q")
        a(f"BT /F1 14 Tf {vx:.1f} {vy - 30:.1f} Td (VIEW {chr(65 + len(views) - 1)}-{chr(65 + len(views) - 1)}) Tj ET")
    areas = [vw_ * vh_ for (_, _, vw_, vh_) in views]

    while size < target_raw:
        kind = r.random()
        vx, vy, vw_, vh_ = r.choices(views, weights=areas)[0]
        cx = r.uniform(vx, vx + vw_)
        cy = r.uniform(vy, vy + vh_)
        pen = r.choice(PENS)
        col = r.choice(COLORS)
        if kind < 0.30:  # contour: chain of short separate elements
            n = r.randint(8, 60)
            parts = [f"q {pen:.2f} w {col} RG"]
            for _ in range(n):
                nx = min(max(cx + r.uniform(-18, 18), m), w - m)
                ny = min(max(cy + r.uniform(-18, 18), m), h - m)
                parts.append(f"{cx:.3f} {cy:.3f} m {nx:.3f} {ny:.3f} l S")
                cx, cy = nx, ny
            parts.append("Q")
            a(" ".join(parts))
        elif kind < 0.50:  # circle (4 Beziers) + dashed centre lines
            rad = r.uniform(1.5, 60)
            k = rad * 0.5523
            a(
                f"q {pen:.2f} w {col} RG "
                f"{cx + rad:.3f} {cy:.3f} m {cx + rad:.3f} {cy + k:.3f} {cx + k:.3f} {cy + rad:.3f} {cx:.3f} {cy + rad:.3f} c "
                f"{cx - k:.3f} {cy + rad:.3f} {cx - rad:.3f} {cy + k:.3f} {cx - rad:.3f} {cy:.3f} c "
                f"{cx - rad:.3f} {cy - k:.3f} {cx - k:.3f} {cy - rad:.3f} {cx:.3f} {cy - rad:.3f} c "
                f"{cx + k:.3f} {cy - rad:.3f} {cx + rad:.3f} {cy - k:.3f} {cx + rad:.3f} {cy:.3f} c S Q"
            )
            a(
                f"q 0.37 w [12 3 2 3] 0 d {cx - rad - 8:.2f} {cy:.2f} m {cx + rad + 8:.2f} {cy:.2f} l "
                f"{cx:.2f} {cy - rad - 8:.2f} m {cx:.2f} {cy + rad + 8:.2f} l S Q"
            )
        elif kind < 0.62:  # hatched section
            bw, bh = r.uniform(15, 110), r.uniform(15, 110)
            step = r.uniform(2.0, 5.0)
            lines = []
            t = -bh
            while t < bw:
                lines.append(f"{cx + t:.2f} {cy:.2f} m {cx + t + bh:.2f} {cy + bh:.2f} l")
                t += step
            a(f"q {cx:.2f} {cy:.2f} {bw:.2f} {bh:.2f} re W n 0.37 w " + " ".join(lines) + " S Q")
            a(f"q 0.99 w {cx:.2f} {cy:.2f} {bw:.2f} {bh:.2f} re S Q")
        elif kind < 0.63:  # filled grey area
            bw, bh = r.uniform(5, 40), r.uniform(5, 40)
            a(f"q 0.85 g {cx:.2f} {cy:.2f} {bw:.2f} {bh:.2f} re f Q")
        elif kind < 0.82:  # dimension: extension lines, arrows, value
            length = r.uniform(10, 180)
            ang = r.choice((0.0, math.pi / 2))
            dx, dy = math.cos(ang), math.sin(ang)
            ex, ey = cx + dx * length, cy + dy * length
            px, py = -dy * 6, dx * 6
            a(
                f"q 0.37 w {cx:.2f} {cy:.2f} m {ex:.2f} {ey:.2f} l S "
                f"{cx:.2f} {cy:.2f} m {cx + dx * 7 + px * 0.4:.2f} {cy + dy * 7 + py * 0.4:.2f} l "
                f"{cx + dx * 7 - px * 0.4:.2f} {cy + dy * 7 - py * 0.4:.2f} l f "
                f"{ex:.2f} {ey:.2f} m {ex - dx * 7 + px * 0.4:.2f} {ey - dy * 7 + py * 0.4:.2f} l "
                f"{ex - dx * 7 - px * 0.4:.2f} {ey - dy * 7 - py * 0.4:.2f} l f "
                f"{cx - px:.2f} {cy - py:.2f} m {cx + px * 3:.2f} {cy + py * 3:.2f} l "
                f"{ex - px:.2f} {ey - py:.2f} m {ex + px * 3:.2f} {ey + py * 3:.2f} l S Q"
            )
            s = _text(r).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            a(f"BT /F1 {r.choice([3.5, 5, 7])} Tf {(cx + ex) / 2:.2f} {(cy + ey) / 2 + 3:.2f} Td ({s}) Tj ET")
        elif kind < 0.94:  # vectorized text
            _vector_glyphs(a, r, cx, cy, r.choice([2.5, 3.5, 5.0]), r.randint(3, 14))
        else:  # real text
            s = _text(r).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            a(f"BT /F1 {r.choice([3.5, 5, 7, 10])} Tf {cx:.2f} {cy:.2f} Td ({s}) Tj ET")
    return "\n".join(out).encode("latin-1", "replace")


def write_pdf(path: Path, pages: list[tuple[float, float, bytes]]) -> None:
    """Minimal PDF writer (from Lire's genpdf.py): one Helvetica font,
    one Flate-compressed content stream per page."""
    objs: list[bytes | None] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objs.append(None)
    pages_id = len(objs)
    kids = []
    for w, h, content in pages:
        comp = zlib.compress(content, 6)
        cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream")
        pid = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>" % (pages_id, w, h, font, cid)
        )
        kids.append(pid)
    objs[pages_id - 1] = (
        b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)
    )
    cat = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    with open(path, "wb") as f:
        f.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offs = []
        for i, o in enumerate(objs):
            offs.append(f.tell())
            f.write(b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n")
        x = f.tell()
        f.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
        for o in offs:
            f.write(b"%010d 00000 n \n" % o)
        f.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, cat, x))


def plan_pages(n: int, mb_per_page: float, seed: int) -> list[tuple[float, float, bytes]]:
    """`n` A0 pages whose compressed content is about `mb_per_page` MB."""
    w, h = A0_LANDSCAPE
    target = int(mb_per_page * 1024 * 1024)
    # Compression ratio measured once on a sample of the same generator.
    sample = drawing(w, h, 1 << 20, seed)
    ratio = len(sample) / len(zlib.compress(sample, 6))
    return [(w, h, drawing(w, h, int(target * ratio), seed + i)) for i in range(n)]


def annotate(src: Path, dst: Path) -> None:
    """Copy `src` to `dst` with Annoter-owned and foreign annotations on
    pages 1 and 2 (the visual-parity cases of PLAN.md, step 5)."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import fitz
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from annoter.config import BASE_RENDER_DPI
    from annoter.model.gdt import GdtState
    from annoter.services.pdf_export import write_annotations
    from annoter.views.items.gdt import GdtAnnotationItem
    from annoter.views.items.lines import ArrowItem
    from annoter.views.items.shapes import CloudItem, EllipseItem, RectangleItem
    from annoter.views.items.stamp import StampItem
    from annoter.views.items.text import TextAnnotationItem

    _app = QApplication.instance() or QApplication([])
    doc = fitz.open(str(src))
    k = BASE_RENDER_DPI / 72.0

    # Annoter's own annotations (written the way Save writes them).
    own = []
    rect = RectangleItem(QRectF(0, 0, 400 * k, 250 * k))
    rect.setPos(300 * k, 300 * k)
    rect.set_color(QColor("#E53935"))
    rect.set_stroke(3.5)
    own.append(rect)
    ell = EllipseItem(QRectF(0, 0, 300 * k, 300 * k))
    ell.setPos(900 * k, 400 * k)
    ell.set_color(QColor("#1E88E5"))
    own.append(ell)
    cloud = CloudItem(QRectF(0, 0, 350 * k, 200 * k))
    cloud.setPos(1500 * k, 350 * k)
    cloud.set_color(QColor("#E53935"))
    own.append(cloud)
    arrow = ArrowItem(QPointF(0, 0), QPointF(250 * k, -120 * k))
    arrow.setPos(400 * k, 900 * k)
    own.append(arrow)
    text = TextAnnotationItem(QPointF(700 * k, 850 * k), "CHECK THIS RADIUS")
    own.append(text)
    gdt = GdtAnnotationItem(GdtState(), QPointF(1200 * k, 900 * k))
    own.append(gdt)
    stamp = StampItem(QPointF(2000 * k, 300 * k), "APPROVED")
    own.append(stamp)
    write_annotations(doc, {0: own}, BASE_RENDER_DPI)

    # Foreign annotations, as Acrobat / Foxit would leave them.
    page = doc[1] if doc.page_count > 1 else doc[0]
    sq = page.add_rect_annot(fitz.Rect(300, 300, 700, 550))
    sq.set_colors(stroke=(1, 0, 0))
    sq.set_border(width=3)
    sq.update()
    ci = page.add_circle_annot(fitz.Rect(900, 300, 1200, 600))
    ci.set_colors(stroke=(0, 0, 1))
    ci.update()
    ln = page.add_line_annot(fitz.Point(300, 800), fitz.Point(700, 700))
    ln.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
    ln.update()
    ink = page.add_ink_annot([[(1400, 800), (1450, 760), (1500, 820), (1560, 770)]])
    ink.update()
    ft = page.add_freetext_annot(fitz.Rect(800, 800, 1200, 860), "Foreign note", fontsize=14)
    ft.update()
    st = page.add_stamp_annot(fitz.Rect(2000, 300, 2350, 420), stamp=0)
    st.update()
    note = page.add_text_annot(fitz.Point(1800, 900), "Sticky note text")
    note.update()
    page.insert_text((300, 1200), "highlighted dimension text", fontsize=24)
    hl = page.add_highlight_annot(fitz.Rect(296, 1178, 620, 1206))
    hl.update()
    ul = page.add_underline_annot(fitz.Rect(296, 1178, 620, 1206))
    ul.update()
    doc.save(str(dst), garbage=0, deflate=True)
    doc.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", nargs="?", default=str(DEFAULT_OUT))
    ap.add_argument("--pages", type=int, default=15)
    ap.add_argument("--mb-per-page", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--annotated", action="store_true", help="also write the annotated copy")
    args = ap.parse_args(argv)

    out = Path(args.outdir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    plan = out / f"plans_{args.pages}xA0.pdf"
    write_pdf(plan, plan_pages(args.pages, args.mb_per_page, args.seed))
    print(f"{plan}  {plan.stat().st_size / 1e6:.1f} MB  ({time.perf_counter() - t0:.0f} s)")
    if args.annotated:
        dst = out / f"plans_{args.pages}xA0_annotated.pdf"
        annotate(plan, dst)
        print(f"{dst}  {dst.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
