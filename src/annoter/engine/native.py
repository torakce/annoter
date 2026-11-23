"""ctypes binding of `native/include/annoter_engine.h`.

Declares the C structures and function signatures, and wraps them in
`NativeEngine` (one per process: threads and budgets) and
`NativeDocument` (open / reload / close, page bounds, hidden annotations,
flags, view requests, tile polling with copy-then-release, evictions).
All calls happen on the Qt UI thread; ctypes releases the GIL while the
library runs.

Scaffold only (step 1 of PLAN.md): written at step 3.
"""
