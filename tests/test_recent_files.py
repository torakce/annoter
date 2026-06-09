"""Tests for the MRU `RecentFiles` service."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtCore", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402

from annoter.services.recent_files import RecentFiles  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path):
    """Force QSettings to use a temp file, isolated per test."""
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    QCoreApplication.setOrganizationName("AnnoterTest")
    QCoreApplication.setApplicationName(f"AnnoterTest_{tmp_path.name}")
    yield


def test_add_then_list(tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_text("x")
    mru = RecentFiles(max_entries=10)
    mru.add(f)
    assert mru.list() == [str(f.resolve())]


def test_max_entries_truncation(tmp_path: Path) -> None:
    mru = RecentFiles(max_entries=3)
    paths = []
    for name in ("a", "b", "c", "d"):
        p = tmp_path / f"{name}.pdf"
        p.write_text("x")
        paths.append(p)
        mru.add(p)
    listed = mru.list()
    assert len(listed) == 3
    # Most recent first.
    assert listed[0] == str(paths[3].resolve())
    assert listed[-1] == str(paths[1].resolve())


def test_re_adding_moves_to_front(tmp_path: Path) -> None:
    mru = RecentFiles(max_entries=5)
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_text("x")
    b.write_text("x")
    mru.add(a)
    mru.add(b)
    mru.add(a)
    assert mru.list()[0] == str(a.resolve())


def test_remove_and_clear(tmp_path: Path) -> None:
    mru = RecentFiles(max_entries=5)
    a = tmp_path / "a.pdf"
    a.write_text("x")
    mru.add(a)
    mru.remove(a)
    assert mru.list() == []
    mru.add(a)
    mru.clear()
    assert mru.list() == []
