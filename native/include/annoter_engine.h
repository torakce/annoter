/*
 * annoter_engine.h -- tiled PDF display engine for Annoter (C ABI).
 *
 * The engine comes from the user's viewer Lire (src/core.c): MuPDF 1.28.6
 * with Lire's performance patch, one display list per page built once,
 * 512 px tiles rendered in parallel, zoom levels on the wheel grid
 * (x 2^(1/3) per notch), background prefetch and RAM-proportional budgets.
 * It only *displays* pages; Annoter keeps PyMuPDF for everything else.
 *
 * Rules of use (see PLAN.md, "Native display engine from Lire"):
 *  - every function is called from one thread (Qt's UI thread);
 *  - the engine's threads never call back: the host polls while
 *    ann_busy() reports work in progress;
 *  - the library exports nothing but the ann_* functions below (MuPDF is
 *    linked statically and kept private, so PyMuPDF can live in the same
 *    process);
 *  - the PDF file is read into memory and never kept open.
 *
 * Status: declarations validated on 2026-11-23; implementation in
 * native/src/engine.c (step 2 of the plan).
 */
#ifndef ANNOTER_ENGINE_H
#define ANNOTER_ENGINE_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32) && defined(ANN_BUILDING)
#define ANN_API __declspec(dllexport)
#elif defined(ANN_BUILDING)
#define ANN_API __attribute__((visibility("default")))
#else
#define ANN_API
#endif

#define ANN_API_VERSION 1

/* Status codes. */
enum {
	ANN_OK = 0,
	ANN_ERR_ARG = -1,
	ANN_ERR_OPEN = -2,
	ANN_ERR_PASSWORD = -3,
	ANN_ERR_MEMORY = -4,
	ANN_ERR_PAGE = -5
};

/* Rendering flags (ann_set_flags). */
enum {
	ANN_FLAG_GRAYSCALE = 1
};

typedef struct ann_engine ann_engine;   /* threads and budgets, one per process */
typedef struct ann_doc ann_doc;         /* one open document */

typedef struct {
	int render_threads;                 /* 0 = auto: cores - 1, at least 1 */
	int loader_threads;                 /* 0 = auto: cores / 4, 1 to 3 */
	long long tile_budget;              /* bytes, 0 = auto (RAM-proportional) */
	long long list_budget;              /* bytes, 0 = auto (RAM-proportional) */
} ann_config;

ANN_API int         ann_api_version(void);
ANN_API ann_engine *ann_engine_new(const ann_config *cfg);    /* NULL on failure */
ANN_API void        ann_engine_free(ann_engine *e);           /* aborts work, joins threads */

/* Documents. The file is read into memory and never kept open, so Save can
   replace it. `status` receives an ANN_* code (may be NULL). */
ANN_API ann_doc *ann_open_file(ann_engine *e, const char *utf8_path,
		const char *password, int *status);
ANN_API ann_doc *ann_open_memory(ann_engine *e, const void *data, size_t size,
		const char *password, int *status);                   /* data is copied */
ANN_API int  ann_reload_file(ann_doc *d, const char *utf8_path,
		const char *password);                                /* after Save / Save As; NULL path = same */
ANN_API int  ann_reload_memory(ann_doc *d, const void *data, size_t size,
		const char *password);                                /* after in-memory page operations */
ANN_API void ann_close(ann_doc *d);

ANN_API int  ann_page_count(ann_doc *d);
ANN_API int  ann_page_bounds(ann_doc *d, int page, float rect[4]);  /* points, frame of PyMuPDF page.rect */

/* Annotations Annoter draws as Qt items, by PDF object number (PyMuPDF
   annot.xref): never rasterized. Rebuilds the page's overlay list only. */
ANN_API int  ann_set_hidden_annots(ann_doc *d, int page, const int *xrefs, int count);
ANN_API int  ann_set_flags(ann_doc *d, int flags);            /* drops the tiles */

/* What is on screen. Replaces the previous request; work no longer useful
   is aborted. */
typedef struct {
	int page;
	int rotation;              /* view rotation 0/90/180/270, on top of /Rotate */
	double scale;              /* device pixels per PDF point, as displayed */
	double grid_base;          /* device px per point at 100 % (devicePixelRatio) */
	int exact;                 /* 1: render exactly at scale (fit, 100 %, zoom window) */
	float visible[4];          /* visible part, points, rotated page frame */
	int prefetch_pages[2];     /* neighbours to prepare, -1 = none */
} ann_view;

typedef struct {
	int level;                 /* tile level actually rendered for this view */
	double scale;              /* its device pixels per point */
	int box[4];                /* page box at that level, pixels (tile grid origin) */
	int tile_size;             /* 512 */
} ann_target;

ANN_API int  ann_request_view(ann_doc *d, const ann_view *v, ann_target *out);

/* Finished tiles, most urgent first: premultiplied BGRA, opaque. The host
   copies the pixels, then releases the buffer. The engine keeps the
   bookkeeping (what exists, budget, eviction) and reports evicted tiles. */
typedef struct {
	long long id;
	int page, level, rotation, tx, ty;
	unsigned epoch;            /* bumped by reload / hidden annots / flags */
	int x0, y0, x1, y1;        /* pixels of the rotated page at `scale` */
	double scale;
	int stride;
	const unsigned char *pixels;   /* valid until ann_release_tile */
} ann_tile;

ANN_API int  ann_poll(ann_doc *d, ann_tile *out, int max);
ANN_API void ann_release_tile(ann_doc *d, long long id);
ANN_API int  ann_poll_evicted(ann_doc *d, long long *ids, int max);

ANN_API int  ann_busy(ann_doc *d);           /* 1 while anything is pending, prefetch included */
ANN_API int  ann_view_complete(ann_doc *d);  /* 1 when every visible tile is sharp */
ANN_API void ann_cancel(ann_doc *d);         /* drop queued work, abort renders, keep the cache */
ANN_API void ann_stats(ann_doc *d, char *buf, int size);   /* diagnostics */

#ifdef __cplusplus
}
#endif

#endif /* ANNOTER_ENGINE_H */
