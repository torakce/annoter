"""Tests of the ctypes interface to the native engine (step 3 of PLAN.md).

Planned: open / close, page count and bounds equal to PyMuPDF's, tile
pixels close to PyMuPDF's render of the same area, hidden annotations,
reload, cancel, errors (missing file, wrong password, damaged PDF), and
PyMuPDF plus the engine in the same process. Skipped when the library is
not built.
"""
