"""Build the native display engine (step 2 of PLAN.md).

Fetches MuPDF 1.28.6 (official sources), applies
`native/patches/mupdf-1.28.6-lire.patch`, builds MuPDF for PDF only with
Lire's options, then links `native/src/engine.c` and the static MuPDF
libraries into one library exporting only the `ann_*` functions:

    python native/build_engine.py --target win64   # annoter_engine.dll (mingw-w64)
    python native/build_engine.py --target linux   # libannoter_engine.so (tests, CI)

The result is copied into `src/annoter/engine/bin/` (gitignored).

Scaffold only (step 1 of PLAN.md): written at step 2.
"""
