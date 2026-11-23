/*
 * engine.c -- implementation of annoter_engine.h.
 *
 * Derived from the engine part of Lire's src/core.c (torakce/Lire v1.2.1):
 * documents read into memory, page analysis into display lists by loader
 * threads (each with its own document instance), two lists per page
 * (content, then overlay = annotations not drawn by Annoter + widgets),
 * 512 px tiles rendered by worker threads on the wheel-aligned level grid,
 * prefetch (ring, next / previous notch, rest of the page, neighbour
 * pages), RAM-proportional budgets and eviction. Lire's window, tabs, UI
 * text and OpenGL drawing are not carried over.
 *
 * Scaffold only (step 1 of PLAN.md): written at step 2.
 */
