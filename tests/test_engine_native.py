"""Tests of the ctypes interface to the native engine (step 3 of PLAN.md).

Open / close, page count and bounds equal to PyMuPDF's, tile pixels equal
to PyMuPDF's render of the same area, rotations, hidden annotations,
grayscale, thumbnails (also beyond the page-analysis budget), reloads,
cancel, errors, and PyMuPDF plus the engine in the same process. Headless: PyMuPDF and ctypes only, no Qt. Skipped when the
library is not built (`python native/build_engine.py`).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import fitz
import pytest

from annoter.engine import THUMB_LEVEL, EngineError, PasswordError, load_engine

ENGINE = None if os.environ.get("ANNOTER_RENDERER") == "pymupdf" else load_engine()
pytestmark = pytest.mark.skipif(ENGINE is None, reason="native engine library not built")

GRAY = 1


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _drawing(page: fitz.Page) -> None:
    """Thin and thick lines, text and fills: enough to catch any shift."""
    r = page.rect
    shape = page.new_shape()
    for k in range(40):
        x = r.x0 + 10 + k * (r.width - 20) / 40
        shape.draw_line((x, r.y0 + 10), (r.x1 - 10 - k * 3, r.y1 - 10))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.draw_circle(r.tl + (r.width / 2, r.height / 2), min(r.width, r.height) / 4)
    shape.finish(color=(0.6, 0, 0), fill=(0.9, 0.9, 1.0), width=2)
    shape.commit()
    page.insert_text(r.tl + (30, 40), "ENGINE TEST 123", fontsize=18)


@pytest.fixture(scope="module")
def plan(tmp_path_factory) -> Path:
    """Three pages: A3 landscape, A4 portrait with /Rotate 90, and a page
    whose MediaBox does not start at the origin."""
    path = tmp_path_factory.mktemp("engine") / "plan.pdf"
    doc = fitz.open()
    _drawing(doc.new_page(width=1191, height=842))
    p = doc.new_page(width=595, height=842)
    _drawing(p)
    p.set_rotation(90)
    p = doc.new_page(width=600, height=400)
    p.set_mediabox(fitz.Rect(100, 50, 700, 450))
    _drawing(p)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def annotated(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("engine") / "annotated.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((60, 300), "highlighted words here", fontsize=20)
    sq = page.add_rect_annot(fitz.Rect(50, 50, 250, 150))
    sq.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
    sq.update()
    hl = page.add_highlight_annot(fitz.Rect(58, 282, 300, 306))
    hl.update()
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def doc(plan):
    d = ENGINE.open_file(plan)
    yield d
    d.close()


@pytest.fixture
def aa6():
    """PyMuPDF anti-aliasing at the engine's levels (Lire's: 64 levels for
    lines, full for text) so pixels can be compared exactly."""
    before = fitz.TOOLS.show_aa_level()
    fitz.TOOLS.set_aa_level(6)
    fitz.mupdf.fz_set_text_aa_level(8)  # text keeps full anti-aliasing, as in the engine
    try:
        yield
    finally:
        fitz.TOOLS.set_aa_level(before["graphics"])
        fitz.mupdf.fz_set_text_aa_level(before["text"])


def _render(d, page, scale, rotation=0, visible=None, exact=True, timeout=60.0):
    """Request a view, poll until complete; returns (target, tiles at the
    target level, every tile id seen, evicted ids)."""
    if visible is None:
        r = fitz.Rect(d.page_bounds(page)) * fitz.Matrix(rotation)
        visible = (r.x0, r.y0, r.x1, r.y1)
    target = d.request_view(page, scale, visible, rotation=rotation, exact=exact)
    tiles, seen, evicted = {}, set(), []
    t0 = time.monotonic()
    while True:
        got = d.poll()
        for t in got:
            seen.add(t.id)
            if t.page == page and t.level == target.level:
                tiles[(t.tx, t.ty)] = t
        evicted += d.evicted()
        if d.view_complete() and not got:
            break
        assert time.monotonic() - t0 < timeout, d.stats()
        time.sleep(0.002)
    return target, tiles, seen, evicted


def _thumbnails(d, timeout=60.0) -> dict:
    """Poll until the engine is idle; returns the thumbnails by page."""
    thumbs = {}
    t0 = time.monotonic()
    while True:
        got = d.poll()
        for t in got:
            if t.level == THUMB_LEVEL:
                assert t.page not in thumbs, "thumbnail handed twice"
                thumbs[t.page] = t
        d.evicted()
        if not got and not d.busy():
            return thumbs
        assert time.monotonic() - t0 < timeout, d.stats()
        time.sleep(0.002)


def _tile_pixmap(t) -> fitz.Pixmap:
    """One tile as an RGB pixmap at its own origin."""
    return _assemble(type("T", (), {"box": t.rect})(), {(0, 0): t})


def _assemble(target, tiles) -> fitz.Pixmap:
    """Paste the tiles into one RGB pixmap covering the target box."""
    x0, y0, x1, y1 = target.box
    w = x1 - x0
    out = bytearray(b"\xff" * (w * (y1 - y0) * 3))
    for t in tiles.values():
        for yy in range(t.height):
            row = t.pixels[yy * t.stride: yy * t.stride + t.width * 4]
            dst = ((t.rect[1] - y0 + yy) * w + (t.rect[0] - x0)) * 3
            out[dst: dst + t.width * 3: 3] = row[2::4]
            out[dst + 1: dst + t.width * 3: 3] = row[1::4]
            out[dst + 2: dst + t.width * 3: 3] = row[0::4]
    pix = fitz.Pixmap(fitz.csRGB, w, y1 - y0, bytes(out), False)
    pix.set_origin(x0, y0)
    return pix


def _crop(pix: fitz.Pixmap, box: fitz.IRect) -> fitz.Pixmap:
    out = fitz.Pixmap(fitz.csRGB, box, False)
    out.copy(pix, box)
    return out


def _diff(a: fitz.Pixmap, b: fitz.Pixmap) -> tuple[float, float]:
    """(mean absolute difference per channel, share of pixels off by > 48)."""
    assert (a.irect, a.n) == (b.irect, b.n)
    sa, sb = a.samples, b.samples
    total = sum(abs(x - y) for x, y in zip(sa, sb))
    off = sum(
        1
        for i in range(0, len(sa), 3)
        if max(abs(sa[i] - sb[i]), abs(sa[i + 1] - sb[i + 1]), abs(sa[i + 2] - sb[i + 2])) > 48
    )
    return total / len(sa), off / (len(sa) / 3)


def _covered(tiles) -> fitz.IRect:
    return fitz.IRect(
        min(t.rect[0] for t in tiles.values()),
        min(t.rect[1] for t in tiles.values()),
        max(t.rect[2] for t in tiles.values()),
        max(t.rect[3] for t in tiles.values()),
    )


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
def test_engine_loads_and_reports(doc) -> None:
    assert not ENGINE.closed
    assert "pages=3" in doc.stats()


def test_page_count_and_bounds_match_pymupdf(doc, plan) -> None:
    with fitz.open(str(plan)) as ref:
        assert doc.page_count == ref.page_count == 3
        for i in range(ref.page_count):
            assert doc.page_bounds(i) == pytest.approx(tuple(ref[i].rect), abs=0.01)


@pytest.mark.parametrize("page", [0, 1, 2])
def test_whole_page_pixels_match_pymupdf(doc, plan, aa6, page) -> None:
    target, tiles, _seen, _ev = _render(doc, page, 0.8)
    with fitz.open(str(plan)) as ref:
        expected = ref[page].get_pixmap(matrix=fitz.Matrix(target.scale, target.scale), alpha=False)
    got = _assemble(target, tiles)
    assert got.irect == expected.irect
    mean, off = _diff(got, expected)
    # Lire's patch draws opaque 1-3 px strokes itself (8-10x faster); its
    # edge coverage differs slightly from MuPDF's rasterizer, on a few
    # edge pixels only. LIRE_NOHL=1 turns it off and makes this exact.
    limit = (0.5, 0.001) if os.environ.get("LIRE_NOHL") == "1" else (2.0, 0.01)
    assert mean < limit[0] and off < limit[1], (mean, off)


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_view_matches_pymupdf(doc, plan, aa6, rotation) -> None:
    target, tiles, _seen, _ev = _render(doc, 0, 1.5, rotation=rotation)
    m = fitz.Matrix(target.scale, target.scale).prerotate(rotation)
    with fitz.open(str(plan)) as ref:
        expected = ref[0].get_pixmap(matrix=m, alpha=False)
    got = _assemble(target, tiles)
    assert got.irect == expected.irect
    mean, off = _diff(got, expected)
    assert mean < 0.5 and off < 0.001, (mean, off)


def test_partial_view_renders_the_visible_tiles_first(doc, plan, aa6) -> None:
    visible = (300.0, 200.0, 500.0, 330.0)  # points
    target, tiles, _seen, _ev = _render(doc, 0, 4.0, visible=visible, exact=False)
    s = target.scale
    assert target.level % 256 == 0  # on the wheel grid
    need = fitz.IRect(int(visible[0] * s), int(visible[1] * s), int(visible[2] * s) - 1, int(visible[3] * s) - 1)
    box = _covered(tiles)
    assert box.contains(need)
    clip = fitz.Rect(box.x0 - 2, box.y0 - 2, box.x1 + 2, box.y1 + 2) * fitz.Matrix(1 / s, 1 / s)
    with fitz.open(str(plan)) as ref:
        expected = _crop(ref[0].get_pixmap(matrix=fitz.Matrix(s, s), clip=clip, alpha=False), box)
    got = _crop(_assemble(target, tiles), box)
    assert got.irect == expected.irect
    mean, _off = _diff(got, expected)
    assert mean < 0.5


def test_exact_and_grid_levels(doc) -> None:
    grid = doc.request_view(0, 1.1, (0, 0, 100, 100), exact=False)
    exact = doc.request_view(0, 1.1, (0, 0, 100, 100), exact=True)
    assert grid.level % 256 == 0 and grid.scale >= 1.1
    assert exact.scale == pytest.approx(1.1, rel=1e-3)
    assert exact.tile_size == 512
    doc.cancel()


def test_hidden_annotations(annotated, aa6) -> None:
    with fitz.open(str(annotated)) as ref:
        square = [a.xref for a in ref[0].annots() if a.type[1] == "Square"]
    d = ENGINE.open_file(annotated)
    try:
        target, tiles, seen, _ev = _render(d, 0, 2.0)
        img = _assemble(target, tiles)
        red = img.pixel(int(150 * target.scale), int(100 * target.scale))
        assert red[0] > 200 and red[1] < 80  # the square is drawn

        d.set_hidden_annots(0, square)
        target, tiles, _seen2, evicted = _render(d, 0, 2.0)
        assert seen & set(evicted)  # the old tiles were reported
        img = _assemble(target, tiles)
        assert img.pixel(int(150 * target.scale), int(100 * target.scale)) == (255, 255, 255)
        yellow = img.pixel(int(280 * target.scale), int(285 * target.scale))
        assert yellow[0] > 200 and yellow[1] > 200 and yellow[2] < 120  # highlight kept

        d.set_hidden_annots(0, [])
        target, tiles, _seen, _ev = _render(d, 0, 2.0)
        img = _assemble(target, tiles)
        assert img.pixel(int(150 * target.scale), int(100 * target.scale))[1] < 80
    finally:
        d.close()


def test_grayscale(doc) -> None:
    doc.set_grayscale(True)
    try:
        target, tiles, _seen, _ev = _render(doc, 0, 0.6)
        s = _assemble(target, tiles).samples
        assert all(s[i] == s[i + 1] == s[i + 2] for i in range(0, len(s), 3 * 101))
    finally:
        doc.set_grayscale(False)


def test_reload_from_memory_after_a_page_operation(doc, plan) -> None:
    _target, _tiles, seen, before = _render(doc, 0, 0.5)
    with fitz.open(str(plan)) as raw:
        raw.delete_page(1)
        data = raw.tobytes()
    doc.reload_bytes(data)
    assert doc.page_count == 2
    assert seen <= set(before) | set(doc.evicted())  # no old tile stays alive
    target, tiles, _seen, _ev = _render(doc, 1, 0.5)
    assert tiles and target.box[2] > 0


def test_file_is_not_kept_open(plan, tmp_path) -> None:
    """Save replaces the file: the engine must not hold it (Windows)."""
    path = tmp_path / "copy.pdf"
    path.write_bytes(plan.read_bytes())
    d = ENGINE.open_file(path)
    try:
        _render(d, 0, 0.3)
        with fitz.open(str(path)) as raw:
            raw.delete_page(0)
            raw.save(str(tmp_path / "new.pdf"))
        os.replace(tmp_path / "new.pdf", path)
        d.reload_file(path)
        assert d.page_count == 2
    finally:
        d.close()


def test_cancel_stops_the_work(plan) -> None:
    d = ENGINE.open_file(plan)
    try:
        d.request_view(0, 6.0, (0, 0, 1191, 842), exact=False)
        d.cancel()
        t0 = time.monotonic()
        while d.busy():
            d.poll()
            assert time.monotonic() - t0 < 30
            time.sleep(0.005)
    finally:
        d.close()


def test_unicode_path(plan, tmp_path) -> None:
    path = tmp_path / "plan d'ensemble é.pdf"
    path.write_bytes(plan.read_bytes())
    with ENGINE.open_file(path) as d:
        assert d.page_count == 3


def test_errors(tmp_path) -> None:
    with pytest.raises(EngineError):
        ENGINE.open_file(tmp_path / "missing.pdf")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a PDF at all" * 10)
    with pytest.raises(EngineError):
        ENGINE.open_file(bad)
    enc = tmp_path / "enc.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "secret")
    doc.save(str(enc), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    doc.close()
    with pytest.raises(PasswordError):
        ENGINE.open_file(enc)
    with pytest.raises(PasswordError):
        ENGINE.open_file(enc, password="wrong")
    with ENGINE.open_file(enc, password="pw") as d:
        assert d.page_count == 1
    d = ENGINE.open_bytes((tmp_path / "enc.pdf").read_bytes(), password="pw")
    d.close()
    d.close()  # idempotent
    with pytest.raises(EngineError):
        d.page_count  # noqa: B018
    with fitz.open(str(enc)) as ok:  # noqa: F841 -- PyMuPDF untouched
        pass


def test_page_out_of_range(doc) -> None:
    with pytest.raises(EngineError):
        doc.page_bounds(3)
    with pytest.raises(EngineError):
        doc.request_view(7, 1.0, (0, 0, 10, 10))


def test_pymupdf_and_engine_side_by_side(plan) -> None:
    """Both MuPDF copies in one process, used alternately."""
    with fitz.open(str(plan)) as ref:
        before = ref[0].get_pixmap(matrix=fitz.Matrix(0.2, 0.2)).samples
        with ENGINE.open_file(plan) as d:
            _render(d, 0, 0.2)
            middle = ref[0].get_pixmap(matrix=fitz.Matrix(0.2, 0.2)).samples
            _render(d, 2, 0.4)
        after = ref[0].get_pixmap(matrix=fitz.Matrix(0.2, 0.2)).samples
    assert before == middle == after


def test_thumbnails_of_every_page(doc, plan, aa6) -> None:
    """Whole page fitting the square, rotation 0 whatever the view's, same
    pixels as PyMuPDF's thumbnail; handed once, after the view."""
    doc.set_thumbnails(128)
    try:
        doc.request_view(0, 0.5, (0, 0, 400, 400), rotation=90)
        thumbs = _thumbnails(doc)
        assert sorted(thumbs) == [0, 1, 2]
        with fitz.open(str(plan)) as ref:
            for i, t in thumbs.items():
                rect = ref[i].rect
                assert t.rotation == 0
                assert t.scale == pytest.approx(128 / max(rect.width, rect.height), rel=1e-6)
                expected = ref[i].get_pixmap(matrix=fitz.Matrix(t.scale, t.scale), alpha=False)
                got = _tile_pixmap(t)
                assert got.irect == expected.irect
                mean, off = _diff(got, expected)
                # exact with LIRE_NOHL=1; Lire's direct drawing of thin
                # strokes shows more at this scale (every line is thin)
                limit = (0.5, 0.001) if os.environ.get("LIRE_NOHL") == "1" else (6.0, 0.04)
                assert mean < limit[0] and off < limit[1], (i, mean, off)
        # nothing more to do: the thumbnails are kept, never re-rendered
        doc.request_view(1, 0.5, (0, 0, 400, 400))
        assert not _thumbnails(doc)
    finally:
        doc.set_thumbnails(0)


def test_thumbnails_show_every_annotation(annotated) -> None:
    """The page list shows the PDF as saved: hidden annotations included."""
    with fitz.open(str(annotated)) as ref:
        square = [a.xref for a in ref[0].annots() if a.type[1] == "Square"]
    d = ENGINE.open_file(annotated)
    try:
        d.set_hidden_annots(0, square)
        d.set_thumbnails(300)
        d.request_view(0, 1.0, (0, 0, 600, 400))
        t = _thumbnails(d)[0]
        px = _tile_pixmap(t).pixel(int(150 * t.scale), int(100 * t.scale))
        assert px[0] > 200 and px[1] < 80, px
    finally:
        d.close()


def test_thumbnails_and_grayscale(doc) -> None:
    doc.set_thumbnails(64)
    try:
        doc.request_view(0, 0.5, (0, 0, 400, 400))
        _thumbnails(doc)
        doc.set_grayscale(True)
        doc.request_view(0, 0.5, (0, 0, 400, 400))
        thumbs = _thumbnails(doc)
        assert sorted(thumbs) == [0, 1, 2]  # rendered again, in gray
        s = thumbs[0].pixels
        assert all(s[i] == s[i + 1] == s[i + 2] for i in range(0, len(s), 4 * 7))
    finally:
        doc.set_grayscale(False)
        doc.set_thumbnails(0)


def test_thumbnails_beyond_the_list_budget(tmp_path) -> None:
    """Pages the analysis budget cannot keep still get their thumbnail (the
    loaders render it, then free the page's lists at once). Own process:
    the budget is fixed when the engine starts."""
    path = tmp_path / "many.pdf"
    doc = fitz.open()
    for k in range(10):
        page = doc.new_page(width=1191, height=842)
        shape = page.new_shape()
        for n in range(4000):
            x = 20 + (n * 37) % 1150
            y = 20 + (n * 53) % 800
            shape.draw_line((x, y), (x + 15, y + 9))
            shape.finish(color=((n % 7) / 7, 0, 0), width=0.2 + (n % 5) / 10)
        shape.commit()
        page.insert_text((60, 60), f"PAGE {k}", fontsize=40)
    doc.save(str(path))
    doc.close()
    script = textwrap.dedent(
        f"""
        import re, sys, time
        from annoter.engine import THUMB_LEVEL, load_engine
        e = load_engine()
        assert e is not None
        d = e.open_file({str(path)!r})
        d.set_thumbnails(96)
        d.request_view(0, 0.3, (0, 0, 1191, 842))
        thumbs, peak, t0 = set(), 0.0, time.monotonic()
        while True:
            got = d.poll()
            thumbs |= {{t.page for t in got if t.level == THUMB_LEVEL}}
            d.evicted()
            peak = max(peak, float(re.search(r"lists=\\d+ \\(([0-9.]+) MB", d.stats()).group(1)))
            if not got and not d.busy():
                break
            assert time.monotonic() - t0 < 120, d.stats()
            time.sleep(0.002)
        print("THUMBS", sorted(thumbs))
        print("PEAK", peak)
        print("STATS", d.stats())
        d.close()
        """
    )
    env = dict(os.environ, ANNOTER_ENGINE_LIST_MB="1", ANNOTER_ENGINE_THREADS="2")
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, out.stderr
    lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
    assert lines["THUMBS"] == str(list(range(10))), out.stdout
    stats = lines["STATS"]
    kept = int(stats.split("lists=")[1].split()[0])
    assert kept < 10, stats  # most pages' lists were not kept
