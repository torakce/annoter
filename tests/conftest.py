"""Shared test fixtures.

Qt tests run under QT_QPA_PLATFORM=offscreen so they work in CI
without a display server.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
