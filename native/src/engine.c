/*
 * engine.c -- implementation of annoter_engine.h.
 *
 * Derived from the engine part of Lire's src/core.c (torakce/Lire v1.2.1).
 * Lire's window, tabs, UI text and OpenGL drawing are not carried over:
 * Annoter draws with Qt and polls this library for finished tiles.
 *
 * Principle (as in Lire):
 *  - Each page is analysed ONCE (by a loader thread) into MuPDF display
 *    lists; renders replay the lists and never interpret the PDF again.
 *    Annoter needs two lists per page: the page content, and an overlay
 *    with the annotations Annoter does not draw itself (plus form
 *    widgets), so hiding an annotation only rebuilds the cheap overlay.
 *  - The view is cut into 512 px tiles rendered in parallel by worker
 *    threads, on a grid of zoom levels aligned on the wheel notches
 *    (x 2^(1/3)): a notch lands on a level already computed or prefetched.
 *  - Once the visible area is sharp, background prefetch: ring around the
 *    view, next and previous notch, rest of the page at the current level,
 *    then the neighbour pages, within RAM-proportional budgets.
 *  - Renders that are no longer useful are aborted at once (fz_cookie).
 *  - All pages are analysed in the background by several loaders, each
 *    with its own instance of the document, the displayed page first.
 *  - The PDF is read into memory: the file is never kept open.
 *
 * Threading: every ann_* function runs on the host's UI thread. Workers
 * and loaders only touch shared state under `e->mu`, and never call back.
 */
#include "annoter_engine.h"
#include "thread.h"

#include <mupdf/fitz.h>
#include <mupdf/pdf.h>

#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TILE 512
#define LPO 768                    /* zoom levels per octave (0.09 % steps) */
#define GRID 256                   /* one wheel notch = 2^(1/3) = x1.26: grid of render levels */
#define PREVIEW_MAX 1024           /* longest side of the low-resolution preview */
#define MAX_WORKERS 32
#define MAX_LOADERS 3
#define HASH_SIZE 8192
#define TILE_BYTES ((size_t)TILE * TILE * 4)
#define PREVIEW_LEVEL INT_MIN

enum { L_NONE, L_QUEUED, L_BUILDING, L_READY, L_ERROR };
enum { J_IDLE, J_QUEUED, J_RUNNING, J_DONE, J_READY, J_FAILED };

typedef struct Job Job;

struct Job {
	ann_doc *doc;
	int page, level, tx, ty;
	int state;              /* under mu */
	int dropped;            /* removed from the cache while running / done: whoever holds it frees it */
	int gray;
	unsigned epoch;
	fz_cookie cookie;
	fz_irect rect;          /* pixels, in the level's space */
	fz_matrix ctm;
	double scale;
	fz_pixmap *pix;         /* finished, waiting to be handed over */
	long long id;           /* given at hand-over */
	size_t bytes;
	unsigned used;          /* last scheduling generation that wanted it */
	unsigned vis_gen;       /* last generation where it was on screen */
	double key;             /* priority, smaller = more urgent */
	Job *hnext, *dnext;
	Job *pnext, *pprev;     /* ready tiles of a page */
};

typedef struct {
	fz_rect bounds;         /* page bounds (points), without the view rotation */
	fz_display_list *list;  /* page content */
	fz_display_list *olist; /* overlay: annotations not drawn by the host, then widgets */
	int lstate;             /* L_READY when both lists exist */
	unsigned lused;
	fz_cookie lcookie;
	fz_rect lbounds;        /* bounds measured when the page was loaded */
	int bounds_differ;
	int urgent;
	size_t lbytes;
	int *hidden, nhidden;   /* sorted object numbers of hidden annotations */
	unsigned epoch;
	Job *ready;
} Page;

typedef struct { long long id; fz_pixmap *pix; } Handed;
typedef struct { Job **v; int n, cap; } JobVec;

struct ann_doc {
	ann_engine *eng;
	ann_doc *next;
	fz_buffer *buf;
	fz_document *doc;                  /* host thread and loader 0 */
	fz_document *ldoc[MAX_LOADERS];    /* instances of the other loaders */
	int ldoc_tried[MAX_LOADERS];
	char *password;
	Page *pages;
	int npages;
	double max_page_dim;
	Job *htab[HASH_SIZE];
	int njobs;
	size_t tex_bytes, list_bytes;
	Job *done_head;

	/* view */
	int has_view, page, rot, flags;
	double grid_base, scale;
	int level, exact_level, min_level, max_level;
	fz_rect vis;                       /* viewport, points of the rotated page frame */
	int prefetch[2];
	unsigned epoch, last_gen;
	int need_schedule;
	int cancelled;                     /* ann_cancel: no work until the next view request */

	int *lq, *lq_old, nlq, nlq_old, lq_pos;

	Handed *handed;
	int nhanded, handed_cap;
	long long *evicted;
	int nevicted, evicted_cap;
	long long next_id;
};

struct ann_engine {
	fz_context *ctx;
	fz_locks_context locks;
	ann_mutex fzmu[FZ_LOCK_MAX];
	ann_mutex mu;
	ann_cond cv_work, cv_load, cv_idle;
	ann_thread th_loader[MAX_LOADERS], th_worker[MAX_WORKERS];
	fz_context *wctx[MAX_WORKERS], *lctx[MAX_LOADERS];
	int nworkers, nloaders;
	int quit, paused, resched;
	int nrunning;
	Job *running[MAX_WORKERS];
	int loader_busy[MAX_LOADERS], loader_page[MAX_LOADERS];
	ann_doc *loader_doc[MAX_LOADERS];
	size_t tex_budget, list_budget;
	JobVec wq, wq_next;
	int wq_pos;
	unsigned gen;
	size_t want_bytes;
	ann_doc *docs;
	ann_doc *active;                   /* document the threads work for */
};

typedef struct { ann_engine *e; int id; } ThreadArg;
static ThreadArg thread_args[MAX_WORKERS + MAX_LOADERS];
static ann_engine *the_engine;         /* one engine per process */

/* ---------- small helpers ---------- */

static int clampi(int v, int a, int b) { return v < a ? a : v > b ? b : v; }
static int floor_div(int a, int b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }
static int grid_floor(int L) { return floor_div(L, GRID) * GRID; }
static int grid_ceil(int L) { return -grid_floor(-L); }
static double level_scale(ann_doc *d, int L)
{
	return L == PREVIEW_LEVEL ? 0 : d->grid_base * exp2((double)L / LPO);
}
static fz_matrix view_ctm(int r, double s) { return fz_concat(fz_rotate((float)r), fz_scale((float)s, (float)s)); }
static fz_irect page_pbox(Page *p, int r, double s) { return fz_round_rect(fz_transform_rect(p->bounds, view_ctm(r, s))); }
static fz_rect rotated_bounds(Page *p, int r) { return fz_transform_rect(p->bounds, fz_rotate((float)r)); }

static void lock_fz(void *u, int l) { mutex_lock(&((ann_engine *)u)->fzmu[l]); }
static void unlock_fz(void *u, int l) { mutex_unlock(&((ann_engine *)u)->fzmu[l]); }

static void jv_push(JobVec *a, Job *j)
{
	if (a->n == a->cap) {
		a->cap = a->cap ? a->cap * 2 : 256;
		a->v = realloc(a->v, a->cap * sizeof *a->v);
	}
	a->v[a->n++] = j;
}

/* Level at which tiles are computed to show level L: the grid level just
   above (shown reduced by 0 to 21 %, hence sharp), except for an "exact"
   level (fit, 100 %, zoom window) rendered as is. */
static int render_level(ann_doc *d, int L)
{
	int R;
	if (L == d->exact_level) return L;
	R = grid_ceil(L);
	return R > d->max_level ? L : R;
}

/* ---------- tile table of a document (host thread, or under mu) ---------- */

static unsigned hkey(int p, int l, int x, int y)
{
	unsigned h = (unsigned)p * 2654435761u ^ (unsigned)l * 40503u ^ (unsigned)x * 73856093u ^ (unsigned)y * 19349663u;
	h ^= h >> 15;
	return h & (HASH_SIZE - 1);
}

static Job *hfind(ann_doc *d, int p, int l, int x, int y)
{
	Job *j;
	for (j = d->htab[hkey(p, l, x, y)]; j; j = j->hnext)
		if (j->page == p && j->level == l && j->tx == x && j->ty == y)
			return j;
	return NULL;
}

static void hremove(Job *j)
{
	ann_doc *d = j->doc;
	Job **pp = &d->htab[hkey(j->page, j->level, j->tx, j->ty)];
	while (*pp && *pp != j) pp = &(*pp)->hnext;
	if (*pp) *pp = j->hnext;
	j->hnext = NULL;
	d->njobs--;
}

static Job *hget(ann_doc *d, int p, int l, int x, int y)
{
	Job *j = hfind(d, p, l, x, y);
	unsigned h;
	if (j) return j;
	j = calloc(1, sizeof *j);
	j->doc = d;
	j->page = p; j->level = l; j->tx = x; j->ty = y;
	j->state = J_IDLE;
	h = hkey(p, l, x, y);
	j->hnext = d->htab[h];
	d->htab[h] = j;
	d->njobs++;
	return j;
}

static void ready_add(Page *p, Job *j)
{
	j->pprev = NULL;
	j->pnext = p->ready;
	if (p->ready) p->ready->pprev = j;
	p->ready = j;
}

static void ready_remove(Page *p, Job *j)
{
	if (j->pprev) j->pprev->pnext = j->pnext; else if (p->ready == j) p->ready = j->pnext;
	if (j->pnext) j->pnext->pprev = j->pprev;
	j->pnext = j->pprev = NULL;
}

static void push_evicted(ann_doc *d, long long id)
{
	if (d->nevicted == d->evicted_cap) {
		d->evicted_cap = d->evicted_cap ? d->evicted_cap * 2 : 256;
		d->evicted = realloc(d->evicted, d->evicted_cap * sizeof *d->evicted);
	}
	d->evicted[d->nevicted++] = id;
}

/* (under mu) frees a job that is neither queued, running nor done */
static void job_free(Job *j)
{
	ann_doc *d = j->doc;
	hremove(j);
	if (j->state == J_READY) {
		ready_remove(&d->pages[j->page], j);
		d->tex_bytes -= j->bytes;
		push_evicted(d, j->id);
	}
	if (j->pix) fz_drop_pixmap(d->eng->ctx, j->pix);
	free(j);
}

static fz_irect level_pbox(ann_doc *d, Page *p, int L)
{
	return page_pbox(p, d->rot, level_scale(d, L));
}

static Job *get_tile(ann_doc *d, int i, int L, int tx, int ty)
{
	Job *j = hget(d, i, L, tx, ty);
	if (j->scale == 0) {
		Page *p = &d->pages[i];
		double s = level_scale(d, L);
		fz_irect pb = level_pbox(d, p, L);
		j->scale = s;
		j->ctm = view_ctm(d->rot, s);
		j->rect.x0 = pb.x0 + tx * TILE;
		j->rect.y0 = pb.y0 + ty * TILE;
		j->rect.x1 = j->rect.x0 + TILE < pb.x1 ? j->rect.x0 + TILE : pb.x1;
		j->rect.y1 = j->rect.y0 + TILE < pb.y1 ? j->rect.y0 + TILE : pb.y1;
		if (j->rect.x1 <= j->rect.x0 || j->rect.y1 <= j->rect.y0) j->state = J_FAILED;
	}
	return j;
}

static Job *get_preview(ann_doc *d, int i)
{
	Job *j = hget(d, i, PREVIEW_LEVEL, 0, 0);
	if (j->scale == 0) {
		Page *p = &d->pages[i];
		fz_rect r = rotated_bounds(p, d->rot);
		double w = r.x1 - r.x0, h = r.y1 - r.y0;
		double s = fmin(PREVIEW_MAX / fmax(w, 1), PREVIEW_MAX / fmax(h, 1));
		j->scale = s;
		j->ctm = view_ctm(d->rot, s);
		j->rect = page_pbox(p, d->rot, s);
		if (j->rect.x1 - j->rect.x0 > PREVIEW_MAX) j->rect.x1 = j->rect.x0 + PREVIEW_MAX;
		if (j->rect.y1 - j->rect.y0 > PREVIEW_MAX) j->rect.y1 = j->rect.y0 + PREVIEW_MAX;
		if (j->rect.x1 <= j->rect.x0 || j->rect.y1 <= j->rect.y0) j->state = J_FAILED;
	}
	return j;
}

/* ---------- worker threads ---------- */

static void to_gray(fz_context *c, fz_pixmap *pix)
{
	int w = fz_pixmap_width(c, pix), h = fz_pixmap_height(c, pix), x, y;
	ptrdiff_t stride = fz_pixmap_stride(c, pix);
	unsigned char *s = fz_pixmap_samples(c, pix);
	for (y = 0; y < h; y++) {
		unsigned char *q = s + y * stride;
		for (x = 0; x < w; x++, q += 4) {
			unsigned v = (q[2] * 77u + q[1] * 150u + q[0] * 29u) >> 8;   /* BGRA, opaque */
			q[0] = q[1] = q[2] = (unsigned char)v;
		}
	}
}

static fz_pixmap *render_job(fz_context *c, Job *j, fz_display_list *list, fz_display_list *olist)
{
	fz_pixmap *pix = NULL;
	fz_device *dev = NULL;
	fz_var(pix);
	fz_var(dev);
	fz_try(c) {
		fz_rect area = fz_rect_from_irect(j->rect);
		pix = fz_new_pixmap_with_bbox(c, fz_device_bgr(c), j->rect, NULL, 1);
		fz_clear_pixmap_with_value(c, pix, 0xff);
		dev = fz_new_draw_device(c, fz_identity, pix);
		fz_run_display_list(c, list, dev, j->ctm, area, &j->cookie);
		if (olist && !j->cookie.abort)
			fz_run_display_list(c, olist, dev, j->ctm, area, &j->cookie);
		fz_close_device(c, dev);
		if (j->gray) to_gray(c, pix);
	}
	fz_always(c)
		fz_drop_device(c, dev);
	fz_catch(c) {
		fz_drop_pixmap(c, pix);
		pix = NULL;
	}
	return pix;
}

static void worker_main(void *arg)
{
	ann_engine *e = ((ThreadArg *)arg)->e;
	int id = ((ThreadArg *)arg)->id;
	fz_context *c = e->wctx[id];
	mutex_lock(&e->mu);
	while (!e->quit) {
		Job *j = NULL;
		Page *p;
		fz_display_list *list, *olist;
		fz_pixmap *pix;
		if (!e->paused)
			while (e->wq_pos < e->wq.n) {
				Job *k = e->wq.v[e->wq_pos++];
				if (k->state == J_QUEUED) { j = k; break; }
			}
		if (!j) { cond_wait(&e->cv_work, &e->mu); continue; }
		p = &j->doc->pages[j->page];
		if (p->lstate != L_READY || !p->list) { j->state = J_IDLE; e->resched = 1; continue; }
		list = fz_keep_display_list(c, p->list);
		olist = fz_keep_display_list(c, p->olist);
		j->state = J_RUNNING;
		j->epoch = p->epoch;
		j->gray = (j->doc->flags & ANN_FLAG_GRAYSCALE) != 0;
		memset(&j->cookie, 0, sizeof j->cookie);
		e->running[id] = j;
		e->nrunning++;
		mutex_unlock(&e->mu);

		pix = render_job(c, j, list, olist);
		fz_drop_display_list(c, list);
		fz_drop_display_list(c, olist);

		mutex_lock(&e->mu);
		e->running[id] = NULL;
		e->nrunning--;
		if (j->dropped) {
			fz_drop_pixmap(c, pix);
			free(j);
		} else if (j->cookie.abort) {
			fz_drop_pixmap(c, pix);
			j->state = J_IDLE;
			e->resched = 1;
		} else if (!pix) {
			j->state = J_FAILED;
		} else {
			j->pix = pix;
			j->state = J_DONE;
			j->dnext = j->doc->done_head;
			j->doc->done_head = j;
		}
		cond_broadcast(&e->cv_idle);
	}
	mutex_unlock(&e->mu);
}

/* ---------- loader threads ---------- */

static int hidden_has(const int *v, int n, int num)
{
	int lo = 0, hi = n - 1;
	while (lo <= hi) {
		int mid = (lo + hi) / 2;
		if (v[mid] == num) return 1;
		if (v[mid] < num) lo = mid + 1; else hi = mid - 1;
	}
	return 0;
}

/* Builds the content list (unless `*list` is already there) and the overlay
   list of page i. Returns 0 on success. */
static int build_lists(fz_context *c, fz_document *doc, int i, fz_cookie *cookie, fz_rect *bounds,
		fz_display_list **list, fz_display_list **olist, const int *hidden, int nhidden)
{
	fz_page *page = NULL;
	fz_device *dev = NULL;
	fz_display_list *l = NULL, *o = NULL;
	int ok = 0;
	fz_var(page);
	fz_var(dev);
	fz_var(l);
	fz_var(o);
	fz_var(ok);
	fz_try(c) {
		pdf_page *pp;
		page = fz_load_page(c, doc, i);
		*bounds = fz_bound_page(c, page);
		if (!*list) {
			l = fz_new_display_list(c, *bounds);
			dev = fz_new_list_device(c, l);
			fz_run_page_contents(c, page, dev, fz_identity, cookie);
			fz_close_device(c, dev);
			fz_drop_device(c, dev);
			dev = NULL;
		}
		o = fz_new_display_list(c, *bounds);
		dev = fz_new_list_device(c, o);
		pp = pdf_page_from_fz_page(c, page);
		if (pp) {
			pdf_annot *a;
			for (a = pdf_first_annot(c, pp); a && !(cookie && cookie->abort); a = pdf_next_annot(c, a)) {
				if (nhidden && hidden_has(hidden, nhidden, pdf_to_num(c, pdf_annot_obj(c, a))))
					continue;
				pdf_run_annot(c, a, dev, fz_identity, cookie);
			}
		} else {
			fz_run_page_annots(c, page, dev, fz_identity, cookie);
		}
		fz_run_page_widgets(c, page, dev, fz_identity, cookie);
		fz_close_device(c, dev);
		ok = 1;
	}
	fz_always(c) {
		fz_drop_device(c, dev);
		fz_drop_page(c, page);
	}
	fz_catch(c) {
		ok = 0;
	}
	if (!ok) {
		fz_drop_display_list(c, l);
		fz_drop_display_list(c, o);
		return -1;
	}
	if (l) *list = l;
	*olist = o;
	return 0;
}

/* Another instance of the same document, so pages can be analysed in
   parallel (an fz_document serves one thread at a time). */
static fz_document *open_extra(fz_context *c, fz_buffer *buf, const char *pw, int npages)
{
	fz_document *doc = NULL;
	fz_var(doc);
	fz_try(c) {
		doc = fz_open_document_with_buffer(c, "application/pdf", buf);
		if (fz_needs_password(c, doc) && !(pw && fz_authenticate_password(c, doc, pw)))
			fz_throw(c, FZ_ERROR_GENERIC, "password");
		if (fz_count_pages(c, doc) != npages)
			fz_throw(c, FZ_ERROR_GENERIC, "page count differs");
	}
	fz_catch(c) {
		fz_drop_document(c, doc);
		doc = NULL;
	}
	return doc;
}

/* (under mu) is a page waiting for analysis in this document's queue? */
static int queued_page(ann_doc *d)
{
	int k;
	for (k = d->lq_pos; k < d->nlq; k++)
		if (d->lq[k] < d->npages && d->pages[d->lq[k]].lstate == L_QUEUED) return 1;
	return 0;
}

static int any_loader_busy(ann_engine *e, ann_doc *d)
{
	int k;
	for (k = 0; k < e->nloaders; k++)
		if (e->loader_busy[k] && (!d || e->loader_doc[k] == d)) return 1;
	return 0;
}

static void loader_main(void *arg)
{
	ann_engine *e = ((ThreadArg *)arg)->e;
	int k = ((ThreadArg *)arg)->id;
	fz_context *c = e->lctx[k];
	mutex_lock(&e->mu);
	while (!e->quit) {
		ann_doc *d = e->active;
		int i = -1, nh, *hidden = NULL;
		Page *p;
		fz_display_list *list, *olist = NULL;
		fz_document *doc;
		fz_rect lb;
		int r;
		if (e->paused || !d || !d->doc || !d->pages || !queued_page(d)) {
			cond_wait(&e->cv_load, &e->mu);
			continue;
		}
		doc = k == 0 ? d->doc : d->ldoc[k];
		if (!doc) {
			fz_buffer *buf;
			char *pw;
			int n;
			if (d->ldoc_tried[k]) { cond_wait(&e->cv_load, &e->mu); continue; }
			/* first time this loader works for this document: its own instance */
			d->ldoc_tried[k] = 1;
			e->loader_busy[k] = 1;
			e->loader_doc[k] = d;
			e->loader_page[k] = -1;
			buf = d->buf;
			pw = d->password;
			n = d->npages;
			mutex_unlock(&e->mu);
			doc = open_extra(c, buf, pw, n);
			mutex_lock(&e->mu);
			d->ldoc[k] = doc;        /* the document cannot go away meanwhile: close quiesces first */
			e->loader_busy[k] = 0;
			e->loader_doc[k] = NULL;
			cond_broadcast(&e->cv_idle);
			continue;
		}
		while (d->lq_pos < d->nlq) {
			int q = d->lq[d->lq_pos++];
			if (q < d->npages && d->pages[q].lstate == L_QUEUED) { i = q; break; }
		}
		if (i < 0) continue;
		p = &d->pages[i];
		p->lstate = L_BUILDING;
		memset(&p->lcookie, 0, sizeof p->lcookie);
		e->loader_busy[k] = 1;
		e->loader_page[k] = i;
		e->loader_doc[k] = d;
		lb = p->bounds;
		list = p->list;              /* content list kept when only the overlay changed */
		nh = p->nhidden;
		if (nh) {
			hidden = malloc(nh * sizeof *hidden);
			memcpy(hidden, p->hidden, nh * sizeof *hidden);
		}
		mutex_unlock(&e->mu);
		r = build_lists(c, doc, i, &p->lcookie, &lb, &list, &olist, hidden, nh);
		free(hidden);

		mutex_lock(&e->mu);
		e->loader_busy[k] = 0;
		e->loader_page[k] = -1;
		e->loader_doc[k] = NULL;
		if (p->lcookie.abort) {
			if (list != p->list) fz_drop_display_list(c, list);
			fz_drop_display_list(c, olist);
			p->lstate = L_NONE;
		} else if (r == 0) {
			if (!fz_is_empty_rect(lb) &&
					(fabsf(lb.x0 - p->bounds.x0) > 0.5f || fabsf(lb.y0 - p->bounds.y0) > 0.5f ||
					 fabsf(lb.x1 - p->bounds.x1) > 0.5f || fabsf(lb.y1 - p->bounds.y1) > 0.5f)) {
				p->lbounds = lb;
				p->bounds_differ = 1;
			}
			d->list_bytes -= p->lbytes;
			p->list = list;
			p->olist = olist;
			p->lbytes = fz_display_list_memory(c, list) + fz_display_list_memory(c, olist);
			d->list_bytes += p->lbytes;
			p->lstate = L_READY;
		} else {
			p->lstate = L_ERROR;
		}
		e->resched = 1;
		cond_broadcast(&e->cv_idle);
	}
	mutex_unlock(&e->mu);
}

/* ---------- cache and thread control ---------- */

/* (under mu) frees both lists of a page */
static void drop_list(ann_doc *d, Page *p)
{
	fz_context *c = d->eng->ctx;
	fz_drop_display_list(c, p->list);
	fz_drop_display_list(c, p->olist);
	p->list = p->olist = NULL;
	d->list_bytes -= p->lbytes;
	p->lbytes = 0;
	p->lstate = L_NONE;
}

/* (under mu) frees the overlay only: the content list stays */
static void drop_overlay(ann_doc *d, Page *p)
{
	fz_context *c = d->eng->ctx;
	size_t before = p->lbytes;
	fz_drop_display_list(c, p->olist);
	p->olist = NULL;
	p->lbytes = p->list ? fz_display_list_memory(c, p->list) : 0;
	d->list_bytes = d->list_bytes - before + p->lbytes;
	if (p->lstate == L_READY) p->lstate = L_NONE;
}

static void clear_lq(ann_doc *d)
{
	int k;
	for (k = 0; k < d->nlq; k++) {
		Page *p = &d->pages[d->lq[k]];
		if (p->lstate == L_QUEUED) p->lstate = L_NONE;
	}
	d->nlq = d->lq_pos = 0;
}

static void clear_wq(ann_engine *e)
{
	int k;
	for (k = e->wq_pos; k < e->wq.n; k++)
		if (e->wq.v[k]->state == J_QUEUED) e->wq.v[k]->state = J_IDLE;
	e->wq.n = e->wq_pos = 0;
}

/* Stops every render and waits until the threads are idle. */
static void quiesce(ann_engine *e)
{
	int k;
	mutex_lock(&e->mu);
	e->paused = 1;
	clear_wq(e);
	if (e->active && e->active->lq) clear_lq(e->active);
	for (k = 0; k < e->nworkers; k++)
		if (e->running[k]) e->running[k]->cookie.abort = 1;
	for (k = 0; k < e->nloaders; k++)
		if (e->loader_doc[k] && e->loader_page[k] >= 0)
			e->loader_doc[k]->pages[e->loader_page[k]].lcookie.abort = 1;
	while (e->nrunning > 0 || any_loader_busy(e, NULL))
		cond_wait(&e->cv_idle, &e->mu);
	mutex_unlock(&e->mu);
}

static void resume(ann_engine *e)
{
	mutex_lock(&e->mu);
	e->paused = 0;
	cond_broadcast(&e->cv_work);
	cond_broadcast(&e->cv_load);
	mutex_unlock(&e->mu);
}

/* (under mu) a document that stops being the active one: its work stops
   without waiting */
static void stop_doc_work(ann_doc *d)
{
	ann_engine *e = d->eng;
	int k;
	clear_wq(e);
	if (d->lq) clear_lq(d);
	for (k = 0; k < e->nworkers; k++)
		if (e->running[k] && e->running[k]->doc == d) e->running[k]->cookie.abort = 1;
	for (k = 0; k < e->nloaders; k++)
		if (e->loader_doc[k] == d && e->loader_page[k] >= 0) d->pages[e->loader_page[k]].lcookie.abort = 1;
}

/* (under mu) removes one job from the cache, whatever its state */
static void drop_job(Job *j)
{
	if (j->state == J_RUNNING || j->state == J_DONE) {
		/* the worker (running) or ann_poll (done) frees it */
		hremove(j);
		j->dropped = 1;
		j->cookie.abort = 1;
	} else {
		if (j->state == J_QUEUED) j->state = J_IDLE;
		job_free(j);
	}
}

/* (under mu) empties the tile cache of a document (page = -1) or of one page */
static void flush_jobs(ann_doc *d, int page)
{
	int h;
	if (d->eng->active == d) clear_wq(d->eng);     /* only the active document has queued jobs */
	for (h = 0; h < HASH_SIZE; h++) {
		Job *j = d->htab[h];
		while (j) {
			Job *next = j->hnext;
			if (page < 0 || j->page == page) drop_job(j);
			j = next;
		}
	}
	d->need_schedule = 1;
}

/* (under mu) done tiles of a closed / reloaded document */
static void drop_done(ann_doc *d)
{
	Job *j = d->done_head;
	d->done_head = NULL;
	while (j) {
		Job *next = j->dnext;
		fz_drop_pixmap(d->eng->ctx, j->pix);
		free(j);              /* dropped already (flush_jobs ran before) */
		j = next;
	}
}

/* Releases everything a document holds (threads quiesced beforehand). */
static void doc_clear(ann_doc *d)
{
	fz_context *c = d->eng->ctx;
	int i;
	mutex_lock(&d->eng->mu);
	flush_jobs(d, -1);
	drop_done(d);
	mutex_unlock(&d->eng->mu);
	for (i = 0; i < d->npages; i++) {
		fz_drop_display_list(c, d->pages[i].list);
		fz_drop_display_list(c, d->pages[i].olist);
		free(d->pages[i].hidden);
	}
	free(d->pages);
	d->pages = NULL;
	d->npages = 0;
	d->list_bytes = 0;
	d->tex_bytes = 0;
	free(d->lq);
	free(d->lq_old);
	d->lq = d->lq_old = NULL;
	d->nlq = d->nlq_old = d->lq_pos = 0;
	for (i = 1; i < MAX_LOADERS; i++) {
		fz_drop_document(c, d->ldoc[i]);
		d->ldoc[i] = NULL;
	}
	memset(d->ldoc_tried, 0, sizeof d->ldoc_tried);
	fz_drop_document(c, d->doc);
	d->doc = NULL;
	fz_drop_buffer(c, d->buf);
	d->buf = NULL;
	if (d->password) {
		memset(d->password, 0, strlen(d->password));
		free(d->password);
		d->password = NULL;
	}
}

/* ---------- opening ---------- */

static void compute_level_limits(ann_doc *d)
{
	double smin = fmax(1e-3, 24.0 / fmax(d->max_page_dim, 1));
	double smax = fmin(64 * d->grid_base, 4.0e6 / fmax(d->max_page_dim, 1));
	d->min_level = (int)ceil(LPO * log2(smin / d->grid_base));
	d->max_level = (int)floor(LPO * log2(smax / d->grid_base));
	if (d->max_level < d->min_level) d->max_level = d->min_level;
}

/* Opens `buf` (kept by the document) and reads page count and bounds. */
static int doc_load(ann_doc *d, fz_buffer *buf, const char *password)
{
	fz_context *c = d->eng->ctx;
	fz_document *doc = NULL;
	pdf_document *pdf;
	int i, tree = 0, status = ANN_OK;
	fz_var(doc);
	fz_var(tree);
	fz_var(i);
	fz_var(pdf);
	fz_try(c) {
		doc = fz_open_document_with_buffer(c, "application/pdf", buf);
	}
	fz_catch(c) {
		fz_drop_buffer(c, buf);
		return ANN_ERR_OPEN;
	}
	if (fz_needs_password(c, doc) && !(password && fz_authenticate_password(c, doc, password))) {
		fz_drop_document(c, doc);
		fz_drop_buffer(c, buf);
		return ANN_ERR_PASSWORD;
	}
	d->buf = buf;
	d->doc = doc;
	d->password = password ? strdup(password) : NULL;
	fz_try(c)
		d->npages = fz_count_pages(c, doc);
	fz_catch(c)
		d->npages = 0;
	if (d->npages <= 0) {
		d->npages = 0;
		return ANN_ERR_OPEN;
	}
	d->pages = calloc(d->npages, sizeof *d->pages);
	d->lq = calloc(d->npages + 8, sizeof *d->lq);
	d->lq_old = calloc(d->npages + 8, sizeof *d->lq_old);
	if (!d->pages || !d->lq || !d->lq_old) return ANN_ERR_MEMORY;
	pdf = pdf_specifics(c, doc);
	if (pdf) {
		fz_try(c) { pdf_load_page_tree(c, pdf); tree = 1; }
		fz_catch(c) { tree = 0; }
	}
	d->max_page_dim = 0;
	for (i = 0; i < d->npages; i++) {
		fz_rect r = fz_empty_rect;
		fz_try(c) {
			if (pdf) {
				fz_rect mb;
				fz_matrix pctm;
				pdf_obj *po = pdf_lookup_page_obj(c, pdf, i);
				pdf_page_obj_transform(c, po, &mb, &pctm);
				r = fz_transform_rect(mb, pctm);
			} else {
				fz_page *pg = fz_load_page(c, doc, i);
				r = fz_bound_page(c, pg);
				fz_drop_page(c, pg);
			}
		}
		fz_catch(c)
			r = fz_empty_rect;
		if (fz_is_empty_rect(r) || r.x1 - r.x0 < 1 || r.y1 - r.y0 < 1)
			r = fz_make_rect(0, 0, 595, 842);
		d->pages[i].bounds = r;
		if (r.x1 - r.x0 > d->max_page_dim) d->max_page_dim = r.x1 - r.x0;
		if (r.y1 - r.y0 > d->max_page_dim) d->max_page_dim = r.y1 - r.y0;
	}
	if (tree) {
		fz_try(c) pdf_drop_page_tree(c, pdf);
		fz_catch(c) {}
	}
	d->epoch++;
	return status;
}

static fz_buffer *read_file(fz_context *c, const char *path)
{
	fz_buffer *buf = NULL;
	fz_var(buf);
	fz_try(c)
		buf = fz_read_file(c, path);
	fz_catch(c)
		buf = NULL;
	return buf;
}

static fz_buffer *copy_memory(fz_context *c, const void *data, size_t size)
{
	fz_buffer *buf = NULL;
	fz_var(buf);
	fz_try(c)
		buf = fz_new_buffer_from_copied_data(c, (const unsigned char *)data, size);
	fz_catch(c)
		buf = NULL;
	return buf;
}

static ann_doc *open_buffer(ann_engine *e, fz_buffer *buf, const char *password, int *status)
{
	ann_doc *d;
	int st;
	if (!buf) { if (status) *status = ANN_ERR_OPEN; return NULL; }
	d = calloc(1, sizeof *d);
	if (!d) { fz_drop_buffer(e->ctx, buf); if (status) *status = ANN_ERR_MEMORY; return NULL; }
	d->eng = e;
	d->grid_base = 1;
	d->exact_level = INT_MIN;
	d->page = -1;
	d->prefetch[0] = d->prefetch[1] = -1;
	st = doc_load(d, buf, password);
	if (st != ANN_OK) {
		doc_clear(d);
		free(d);
		if (status) *status = st;
		return NULL;
	}
	mutex_lock(&e->mu);
	d->next = e->docs;
	e->docs = d;
	mutex_unlock(&e->mu);
	if (status) *status = ANN_OK;
	return d;
}

/* ---------- scheduling (host thread) ---------- */

/* Tiles of level L covering the viewport of page i, widened by mx, my
   pixels of the displayed scale. 0 when nothing is concerned. */
static int tiles_for(ann_doc *d, int i, int L, double mx, double my, int *tx0, int *ty0, int *tx1, int *ty1)
{
	Page *p = &d->pages[i];
	double sL = level_scale(d, L), k = sL / d->scale;
	fz_irect pb = level_pbox(d, p, L);
	double u0 = d->vis.x0 * sL - mx * k, u1 = d->vis.x1 * sL + mx * k;
	double v0 = d->vis.y0 * sL - my * k, v1 = d->vis.y1 * sL + my * k;
	int nx = (pb.x1 - pb.x0 - 1) / TILE, ny = (pb.y1 - pb.y0 - 1) / TILE;
	if (u0 < pb.x0) u0 = pb.x0;
	if (v0 < pb.y0) v0 = pb.y0;
	if (u1 > pb.x1) u1 = pb.x1;
	if (v1 > pb.y1) v1 = pb.y1;
	if (u0 >= u1 || v0 >= v1 || pb.x1 <= pb.x0 || pb.y1 <= pb.y0) return 0;
	*tx0 = clampi((int)floor((u0 - pb.x0) / TILE), 0, nx);
	*tx1 = clampi((int)floor((u1 - pb.x0 - 1e-3) / TILE), 0, nx);
	*ty0 = clampi((int)floor((v0 - pb.y0) / TILE), 0, ny);
	*ty1 = clampi((int)floor((v1 - pb.y0 - 1e-3) / TILE), 0, ny);
	return 1;
}

/* distance (pixels of the displayed scale) between a tile centre and the view centre */
static double tile_dist(ann_doc *d, int L, fz_irect pb, int tx, int ty)
{
	double k = level_scale(d, L) / d->scale;
	double x = (pb.x0 + (tx + 0.5) * TILE) / k, y = (pb.y0 + (ty + 0.5) * TILE) / k;
	double cx = (d->vis.x0 + d->vis.x1) * 0.5 * d->scale, cy = (d->vis.y0 + d->vis.y1) * 0.5 * d->scale;
	return hypot(x - cx, y - cy);
}

static void want(ann_engine *e, Job *j, double key)
{
	if (j->used == e->gen) {        /* already wanted in this pass: keep the strongest priority */
		if (key < j->key) j->key = key;
		if (key < 2e9) j->vis_gen = e->gen;
		return;
	}
	j->used = e->gen;
	j->key = key;
	if (key < 2e9) j->vis_gen = e->gen;
	if (j->state == J_FAILED) return;
	e->want_bytes += j->bytes ? j->bytes : TILE_BYTES;
	if (j->state == J_IDLE || j->state == J_QUEUED) jv_push(&e->wq_next, j);
}

typedef struct { int idx; double d; } Cand;
static Cand *cands;
static int ncand, cand_cap;

static int cmp_cand(const void *a, const void *b)
{
	double x = ((const Cand *)a)->d, y = ((const Cand *)b)->d;
	return x < y ? -1 : x > y;
}

/* Prefetch: tiles of level L around the view (margins mx, my), closest
   first, while the budget allows. */
static void want_area(ann_doc *d, int i, int L, double mx, double my, double base, size_t limit)
{
	ann_engine *e = d->eng;
	fz_irect pb;
	int a0, b0, a1, b1, tx, ty, k;
	if (e->want_bytes >= limit || !tiles_for(d, i, L, mx, my, &a0, &b0, &a1, &b1)) return;
	pb = level_pbox(d, &d->pages[i], L);
	ncand = 0;
	for (ty = b0; ty <= b1; ty++)
		for (tx = a0; tx <= a1; tx++) {
			if (ncand == cand_cap) {
				cand_cap = cand_cap ? cand_cap * 2 : 1024;
				cands = realloc(cands, cand_cap * sizeof *cands);
			}
			cands[ncand].idx = (ty - b0) * (a1 - a0 + 1) + (tx - a0);
			cands[ncand].d = tile_dist(d, L, pb, tx, ty);
			ncand++;
		}
	if (ncand > 1) qsort(cands, ncand, sizeof *cands, cmp_cand);
	for (k = 0; k < ncand && e->want_bytes < limit; k++) {
		int idx = cands[k].idx;
		want(e, get_tile(d, i, L, a0 + idx % (a1 - a0 + 1), b0 + idx / (a1 - a0 + 1)), base + cands[k].d);
	}
}

static int cmp_key(const void *a, const void *b)
{
	double x = (*(Job *const *)a)->key, y = (*(Job *const *)b)->key;
	return x < y ? -1 : x > y;
}

static int evict_R;

static int cmp_used(const void *a, const void *b)
{
	const Job *x = *(Job *const *)a, *y = *(Job *const *)b;
	int dx, dy;
	if (x->used != y->used) return x->used < y->used ? -1 : 1;
	/* same age: free the levels furthest from the current zoom first */
	dx = x->level == PREVIEW_LEVEL ? 0 : abs(x->level - evict_R);
	dy = y->level == PREVIEW_LEVEL ? 0 : abs(y->level - evict_R);
	return dx > dy ? -1 : dx < dy;
}

static ann_doc *evict_doc;
static int evict_cur;

static int cmp_lused(const void *a, const void *b)
{
	const Page *x = &evict_doc->pages[*(const int *)a], *y = &evict_doc->pages[*(const int *)b];
	int dx = abs(*(const int *)a - evict_cur), dy = abs(*(const int *)b - evict_cur);
	if (x->lused != y->lused) return x->lused < y->lused ? -1 : 1;
	return dx > dy ? -1 : dx < dy;
}

static void evict(ann_doc *d)
{
	static Job **cand;
	static int ccap;
	ann_engine *e = d->eng;
	int h, n = 0, k;

	/* display lists: over budget, free the oldest and furthest from the
	   current page (never the ones on screen) */
	if (d->list_bytes > e->list_budget) {
		int *idx = malloc(d->npages * sizeof *idx), m = 0;
		evict_doc = d;
		evict_cur = d->page;
		for (k = 0; k < d->npages; k++) {
			Page *p = &d->pages[k];
			if ((p->lstate == L_READY || p->list) && !(p->lused == e->gen && p->urgent) && p->lstate != L_BUILDING)
				idx[m++] = k;
		}
		if (m > 1) qsort(idx, m, sizeof *idx, cmp_lused);
		mutex_lock(&e->mu);
		for (k = 0; k < m && d->list_bytes > e->list_budget / 100 * 85; k++)
			drop_list(d, &d->pages[idx[k]]);
		mutex_unlock(&e->mu);
		free(idx);
	}

	/* tiles */
	if (d->tex_bytes <= e->tex_budget && d->njobs < 40000) return;
	evict_R = render_level(d, d->level);
	if (ccap < d->njobs) { ccap = d->njobs * 2; cand = realloc(cand, ccap * sizeof *cand); }
	mutex_lock(&e->mu);
	for (h = 0; h < HASH_SIZE; h++) {
		Job *j;
		for (j = d->htab[h]; j; j = j->hnext)
			if (j->used != e->gen && (j->state == J_READY || j->state == J_IDLE || j->state == J_FAILED))
				cand[n++] = j;
	}
	if (n > 1) qsort(cand, n, sizeof *cand, cmp_used);
	for (k = 0; k < n; k++) {
		Job *j = cand[k];
		if (j->state != J_READY) job_free(j);
		else if (d->tex_bytes > e->tex_budget / 10 * 8) job_free(j);
	}
	mutex_unlock(&e->mu);
}

/* (under mu) puts a page in the analysis queue */
static void queue_page(ann_doc *d, int i, int urgent)
{
	Page *p = &d->pages[i];
	p->lused = d->eng->gen;
	p->urgent = urgent;
	if (p->lstate == L_NONE) p->lstate = L_QUEUED;
	if (p->lstate == L_QUEUED) d->lq[d->nlq++] = i;
}

static void schedule(ann_doc *d)
{
	ann_engine *e = d->eng;
	int i, k, cur, *tmp, n = 0, order[3], R, up, dn, all_ready = 1, dist;
	double vw, vh;
	size_t limit = e->tex_budget / 10 * 7;
	d->need_schedule = 0;
	if (!d->has_view || d->cancelled || d->npages <= 0 || e->active != d) return;
	e->gen++;
	d->last_gen = e->gen;
	e->wq_next.n = 0;
	e->want_bytes = 0;
	cur = d->page;
	vw = (d->vis.x1 - d->vis.x0) * d->scale;
	vh = (d->vis.y1 - d->vis.y0) * d->scale;
	R = render_level(d, d->level);
	up = grid_floor(d->level) + GRID;      /* level one wheel notch in / out */
	dn = grid_ceil(d->level) - GRID;
	if (up > d->max_level) up = INT_MIN;
	if (dn < d->min_level) dn = INT_MIN;

	mutex_lock(&e->mu);
	tmp = d->lq_old; d->lq_old = d->lq; d->lq = tmp;
	d->nlq_old = d->nlq;
	d->nlq = d->lq_pos = 0;

	order[n++] = cur;
	for (k = 0; k < 2; k++)
		if (d->prefetch[k] >= 0 && d->prefetch[k] < d->npages && d->prefetch[k] != cur &&
				(n < 2 || order[1] != d->prefetch[k]))
			order[n++] = d->prefetch[k];

	/* pass 1: page analysis, visible tiles and ring, prefetched pages */
	for (k = 0; k < n; k++) {
		Page *p;
		Job *pj;
		int a0, b0, a1, b1, r0, s0, r1, s1, tx, ty, has_fb;
		fz_irect pb;
		i = order[k];
		p = &d->pages[i];
		queue_page(d, i, 1);
		if (p->lstate != L_READY) {
			if (k == 0 && p->lstate != L_ERROR) all_ready = 0;
			continue;
		}
		pb = level_pbox(d, p, R);
		if (k > 0) {
			/* neighbour page: whole if it fits the viewport (next page at once
			   when the page fits), else the same area as the current view */
			pj = get_preview(d, i);
			want(e, pj, 2.45e9);
			if (pb.x1 - pb.x0 <= vw * 1.05 && pb.y1 - pb.y0 <= vh * 1.05) {
				int nx = (pb.x1 - pb.x0 - 1) / TILE, ny = (pb.y1 - pb.y0 - 1) / TILE;
				for (ty = 0; ty <= ny; ty++)
					for (tx = 0; tx <= nx; tx++)
						want(e, get_tile(d, i, R, tx, ty), 2.5e9 + k * 1e6 + (ty * (nx + 1) + tx));
			} else if (tiles_for(d, i, R, 0, 0, &a0, &b0, &a1, &b1)) {
				for (ty = b0; ty <= b1; ty++)
					for (tx = a0; tx <= a1; tx++)
						want(e, get_tile(d, i, R, tx, ty), 2.5e9 + k * 1e6 + tile_dist(d, R, pb, tx, ty));
			}
			continue;
		}
		/* low-resolution preview: only when zoomed well beyond it and with
		   nothing else to stretch meanwhile */
		pj = hfind(d, i, PREVIEW_LEVEL, 0, 0);
		has_fb = (pj && (pj->state == J_READY || pj->state == J_DONE)) || p->ready != NULL;
		if (!pj && !has_fb) pj = get_preview(d, i);
		if (pj && !has_fb && level_scale(d, R) > pj->scale * 1.5) want(e, pj, 0);
		if (!tiles_for(d, i, R, 0, 0, &a0, &b0, &a1, &b1)) continue;
		tiles_for(d, i, R, TILE, TILE, &r0, &s0, &r1, &s1);
		for (ty = s0; ty <= s1; ty++)
			for (tx = r0; tx <= r1; tx++) {
				int inview = tx >= a0 && tx <= a1 && ty >= b0 && ty <= b1;
				want(e, get_tile(d, i, R, tx, ty), (inview ? 1e9 : 2e9) + tile_dist(d, R, pb, tx, ty));
			}
	}

	/* pass 2: neighbour notches on the visible area (wider when zooming out) */
	if (d->pages[cur].lstate == L_READY) {
		if (dn != INT_MIN && dn != R) want_area(d, cur, dn, vw * 0.15, vh * 0.15, 3e9, limit);
		if (up != INT_MIN && up != R) want_area(d, cur, up, 0, 0, 3.5e9, limit);
	}

	/* pass 3: rest of the page at the current level (pans), within budget */
	if (d->pages[cur].lstate == L_READY && e->want_bytes < limit) {
		int a = (int)(vw / TILE) + 2, b = (int)(vh / TILE) + 2, m = 0;
		size_t room = (limit - e->want_bytes) / TILE_BYTES;
		while (m < 64 && (size_t)((a + 2 * (m + 1)) * (b + 2 * (m + 1)) - a * b) <= room) m++;
		if (m > 0) want_area(d, cur, R, (double)m * TILE, (double)m * TILE, 5e9, limit);
	}

	/* pass 4: every other page analysed in the background, closest first,
	   while the display-list memory allows */
	if (all_ready) {
		size_t est, sum = d->list_bytes;
		int nready = 0;
		for (k = 0; k < d->npages; k++)
			if (d->pages[k].lstate == L_READY) nready++;
		est = nready ? d->list_bytes / nready : (size_t)16 << 20;
		for (dist = 1; dist < d->npages; dist++) {
			int side;
			for (side = 0; side < 2; side++) {
				Page *p;
				i = side ? cur - dist : cur + dist;
				if (i < 0 || i >= d->npages) continue;
				p = &d->pages[i];
				if (p->lused == e->gen) continue;
				if (p->lstate == L_READY) { p->lused = e->gen; p->urgent = 0; continue; }
				if (p->lstate == L_ERROR || sum + est > e->list_budget / 10 * 7) continue;
				queue_page(d, i, 0);
				sum += est;
			}
		}
	}

	/* pages no longer wanted */
	for (k = 0; k < d->nlq_old; k++) {
		Page *p = &d->pages[d->lq_old[k]];
		if (p->lstate == L_QUEUED && p->lused != e->gen) p->lstate = L_NONE;
	}
	{
		int need = 0, idle = 0;
		for (k = 0; k < d->nlq; k++)
			if (d->pages[d->lq[k]].urgent && d->pages[d->lq[k]].lstate == L_QUEUED) need++;
		for (k = 0; k < e->nloaders; k++) {
			if (!e->loader_busy[k]) idle++;
			else if (e->loader_doc[k] == d && e->loader_page[k] >= 0 && d->pages[e->loader_page[k]].lused != e->gen)
				d->pages[e->loader_page[k]].lcookie.abort = 1;
		}
		/* a page on screen waits while every loader analyses far pages:
		   interrupt some (they will be taken up again later) */
		for (k = 0; k < e->nloaders && need > idle; k++)
			if (e->loader_busy[k] && e->loader_doc[k] == d && e->loader_page[k] >= 0 &&
					!d->pages[e->loader_page[k]].urgent) {
				d->pages[e->loader_page[k]].lcookie.abort = 1;
				need--;
			}
	}

	/* new work queue */
	for (k = e->wq_pos; k < e->wq.n; k++)
		if (e->wq.v[k]->state == J_QUEUED && e->wq.v[k]->used != e->gen) e->wq.v[k]->state = J_IDLE;
	if (e->wq_next.n > 1) qsort(e->wq_next.v, e->wq_next.n, sizeof *e->wq_next.v, cmp_key);
	for (k = 0; k < e->wq_next.n; k++) e->wq_next.v[k]->state = J_QUEUED;
	{ JobVec tv = e->wq; e->wq = e->wq_next; e->wq_next = tv; }
	e->wq_pos = 0;
	for (k = 0; k < e->nworkers; k++)
		if (e->running[k] && e->running[k]->used != e->gen) e->running[k]->cookie.abort = 1;
	cond_broadcast(&e->cv_work);
	cond_broadcast(&e->cv_load);
	mutex_unlock(&e->mu);

	evict(d);
}

/* ---------- public API ---------- */

int ann_api_version(void) { return ANN_API_VERSION; }

/* MuPDF prints warnings and errors on stderr (e.g. "items left on stack"
   when a render is aborted on purpose). Silent unless ANNOTER_ENGINE_VERBOSE
   is set; failures still come back through the API's status codes. */
static void quiet_message(void *user, const char *message) { (void)user; (void)message; }

static void quiet(fz_context *c)
{
	if (getenv("ANNOTER_ENGINE_VERBOSE")) return;
	fz_set_warning_callback(c, quiet_message, NULL);
	fz_set_error_callback(c, quiet_message, NULL);
}

static int register_handlers(fz_context *c)
{
	int ok = 1;
	fz_var(ok);
	fz_try(c)
		fz_register_document_handlers(c);
	fz_catch(c)
		ok = 0;
	return ok;
}

ann_engine *ann_engine_new(const ann_config *cfg)
{
	ann_engine *e;
	int i, nthreads = cfg ? cfg->render_threads : 0, nl = cfg ? cfg->loader_threads : 0;
	unsigned long long ram;
	if (the_engine) return NULL;
	e = calloc(1, sizeof *e);
	if (!e) return NULL;
	for (i = 0; i < FZ_LOCK_MAX; i++) mutex_init(&e->fzmu[i]);
	mutex_init(&e->mu);
	cond_init(&e->cv_work);
	cond_init(&e->cv_load);
	cond_init(&e->cv_idle);
	e->locks.user = e;
	e->locks.lock = lock_fz;
	e->locks.unlock = unlock_fz;
	e->ctx = fz_new_context(NULL, &e->locks, (size_t)512 << 20);
	if (!e->ctx) { free(e); return NULL; }
	if (!register_handlers(e->ctx)) {
		fz_drop_context(e->ctx);
		free(e);
		return NULL;
	}
	/* Colour management off: much faster, no visible difference on plans.
	   Lines anti-aliased on 64 levels (visually identical, ~1.6x faster);
	   text keeps the full anti-aliasing. (Lire's settings.) */
	quiet(e->ctx);
	fz_disable_icc(e->ctx);
	fz_set_graphics_aa_level(e->ctx, 6);
	fz_set_text_aa_level(e->ctx, 8);

	/* RAM-proportional budgets, a bit below Lire's (Annoter and PyMuPDF
	   hold memory too): 32 GB -> 1 GB of tiles and 2 GB of lists,
	   8 GB -> 0.67 GB and 0.8 GB. */
	ram = ram_bytes();
	e->tex_budget = (size_t)fmin(fmax(ram / 12.0, 256.0 * (1 << 20)), 1024.0 * (1 << 20));
	e->list_budget = (size_t)fmin(fmax(ram / 10.0, 256.0 * (1 << 20)), 2048.0 * (1 << 20));
	if (cfg && cfg->tile_budget > 0) e->tex_budget = (size_t)cfg->tile_budget;
	if (cfg && cfg->list_budget > 0) e->list_budget = (size_t)cfg->list_budget;

	if (nthreads <= 0) nthreads = cpu_count() - 1;
	e->nworkers = clampi(nthreads, 1, MAX_WORKERS);
	if (nl <= 0) nl = cpu_count() / 4;
	e->nloaders = clampi(nl, 1, MAX_LOADERS);
	for (i = 0; i < e->nloaders; i++) {
		e->lctx[i] = fz_clone_context(e->ctx);
		quiet(e->lctx[i]);
		e->loader_page[i] = -1;
	}
	for (i = 0; i < e->nworkers; i++) {
		e->wctx[i] = fz_clone_context(e->ctx);
		quiet(e->wctx[i]);
		fz_set_graphics_aa_level(e->wctx[i], 6);
		fz_set_text_aa_level(e->wctx[i], 8);
	}
	for (i = 0; i < e->nloaders; i++) {
		thread_args[MAX_WORKERS + i].e = e;
		thread_args[MAX_WORKERS + i].id = i;
		thread_create(&e->th_loader[i], loader_main, &thread_args[MAX_WORKERS + i], 1, 16 << 20);
	}
	for (i = 0; i < e->nworkers; i++) {
		thread_args[i].e = e;
		thread_args[i].id = i;
		thread_create(&e->th_worker[i], worker_main, &thread_args[i], 1, 4 << 20);
	}
	the_engine = e;
	return e;
}

static void doc_unlink(ann_doc *d)
{
	ann_engine *e = d->eng;
	ann_doc **pp;
	mutex_lock(&e->mu);
	for (pp = &e->docs; *pp; pp = &(*pp)->next)
		if (*pp == d) { *pp = d->next; break; }
	if (e->active == d) e->active = NULL;
	mutex_unlock(&e->mu);
}

static void doc_free(ann_doc *d)
{
	int k;
	doc_clear(d);
	for (k = 0; k < d->nhanded; k++) fz_drop_pixmap(d->eng->ctx, d->handed[k].pix);
	free(d->handed);
	free(d->evicted);
	free(d);
}

void ann_engine_free(ann_engine *e)
{
	int i;
	if (!e) return;
	quiesce(e);
	while (e->docs) {
		ann_doc *d = e->docs;
		doc_unlink(d);
		doc_free(d);
	}
	mutex_lock(&e->mu);
	e->quit = 1;
	cond_broadcast(&e->cv_work);
	cond_broadcast(&e->cv_load);
	mutex_unlock(&e->mu);
	for (i = 0; i < e->nloaders; i++) thread_join(e->th_loader[i]);
	for (i = 0; i < e->nworkers; i++) thread_join(e->th_worker[i]);
	for (i = 0; i < e->nworkers; i++) fz_drop_context(e->wctx[i]);
	for (i = 0; i < e->nloaders; i++) fz_drop_context(e->lctx[i]);
	free(e->wq.v);
	free(e->wq_next.v);
	fz_drop_context(e->ctx);
	for (i = 0; i < FZ_LOCK_MAX; i++) mutex_free(&e->fzmu[i]);
	mutex_free(&e->mu);
	cond_free(&e->cv_work);
	cond_free(&e->cv_load);
	cond_free(&e->cv_idle);
	free(e);
	the_engine = NULL;
}

ann_doc *ann_open_file(ann_engine *e, const char *utf8_path, const char *password, int *status)
{
	if (!e || !utf8_path) { if (status) *status = ANN_ERR_ARG; return NULL; }
	return open_buffer(e, read_file(e->ctx, utf8_path), password, status);
}

ann_doc *ann_open_memory(ann_engine *e, const void *data, size_t size, const char *password, int *status)
{
	if (!e || !data || !size) { if (status) *status = ANN_ERR_ARG; return NULL; }
	return open_buffer(e, copy_memory(e->ctx, data, size), password, status);
}

/* Replaces the content of `d` by `buf`, keeping the view parameters.
   Every tile handed over before is reported as evicted. */
static int reload_buffer(ann_doc *d, fz_buffer *buf, const char *password)
{
	ann_engine *e = d->eng;
	int st;
	if (!buf) return ANN_ERR_OPEN;
	quiesce(e);
	doc_clear(d);
	st = doc_load(d, buf, password);
	if (st == ANN_OK) {
		compute_level_limits(d);
		if (d->page >= d->npages) d->page = d->npages - 1;
		d->need_schedule = 1;
	} else {
		doc_clear(d);
		d->has_view = 0;
	}
	resume(e);
	return st;
}

int ann_reload_file(ann_doc *d, const char *utf8_path, const char *password)
{
	if (!d || !utf8_path) return ANN_ERR_ARG;
	return reload_buffer(d, read_file(d->eng->ctx, utf8_path), password);
}

int ann_reload_memory(ann_doc *d, const void *data, size_t size, const char *password)
{
	if (!d || !data || !size) return ANN_ERR_ARG;
	return reload_buffer(d, copy_memory(d->eng->ctx, data, size), password);
}

void ann_close(ann_doc *d)
{
	ann_engine *e;
	if (!d) return;
	e = d->eng;
	quiesce(e);
	doc_unlink(d);
	doc_free(d);
	resume(e);
}

int ann_page_count(ann_doc *d) { return d ? d->npages : 0; }

int ann_page_bounds(ann_doc *d, int page, float rect[4])
{
	fz_rect r;
	if (!d || !rect) return ANN_ERR_ARG;
	if (page < 0 || page >= d->npages) return ANN_ERR_PAGE;
	mutex_lock(&d->eng->mu);
	r = d->pages[page].bounds;
	mutex_unlock(&d->eng->mu);
	rect[0] = r.x0; rect[1] = r.y0; rect[2] = r.x1; rect[3] = r.y1;
	return ANN_OK;
}

static int cmp_int(const void *a, const void *b)
{
	int x = *(const int *)a, y = *(const int *)b;
	return x < y ? -1 : x > y;
}

int ann_set_hidden_annots(ann_doc *d, int page, const int *xrefs, int count)
{
	ann_engine *e;
	Page *p;
	int *v = NULL, n = 0, k;
	if (!d || count < 0 || (count && !xrefs)) return ANN_ERR_ARG;
	if (page < 0 || page >= d->npages) return ANN_ERR_PAGE;
	e = d->eng;
	if (count) {
		v = malloc(count * sizeof *v);
		if (!v) return ANN_ERR_MEMORY;
		memcpy(v, xrefs, count * sizeof *v);
		qsort(v, count, sizeof *v, cmp_int);
		for (k = 0; k < count; k++)              /* unique */
			if (n == 0 || v[n - 1] != v[k]) v[n++] = v[k];
	}
	p = &d->pages[page];
	mutex_lock(&e->mu);
	if (n == p->nhidden && (n == 0 || !memcmp(v, p->hidden, n * sizeof *v))) {
		mutex_unlock(&e->mu);
		free(v);
		return ANN_OK;
	}
	free(p->hidden);
	p->hidden = v;
	p->nhidden = n;
	if (p->lstate == L_BUILDING) p->lcookie.abort = 1;     /* rebuilt with the new list */
	else if (p->olist || p->lstate == L_READY) drop_overlay(d, p);
	p->epoch = ++d->epoch;
	flush_jobs(d, page);
	mutex_unlock(&e->mu);
	return ANN_OK;
}

int ann_set_flags(ann_doc *d, int flags)
{
	if (!d) return ANN_ERR_ARG;
	flags &= ANN_FLAG_GRAYSCALE;
	if (flags == d->flags) return ANN_OK;
	mutex_lock(&d->eng->mu);
	d->flags = flags;
	d->epoch++;
	{
		int i;
		for (i = 0; i < d->npages; i++) d->pages[i].epoch = d->epoch;
	}
	flush_jobs(d, -1);
	mutex_unlock(&d->eng->mu);
	return ANN_OK;
}

int ann_request_view(ann_doc *d, const ann_view *v, ann_target *out)
{
	ann_engine *e;
	int rot, L, R;
	Page *p;
	fz_irect pb;
	if (!d || !v || !(v->scale > 0) || !(v->grid_base > 0)) return ANN_ERR_ARG;
	if (v->page < 0 || v->page >= d->npages) return ANN_ERR_PAGE;
	rot = ((v->rotation % 360) + 360) % 360;
	if (rot % 90) return ANN_ERR_ARG;
	e = d->eng;
	mutex_lock(&e->mu);
	if (e->active != d) {
		if (e->active) stop_doc_work(e->active);
		e->active = d;
	}
	if (rot != d->rot || fabs(v->grid_base - d->grid_base) > 1e-9) {
		/* levels or orientation change meaning: start afresh */
		flush_jobs(d, -1);
		d->rot = rot;
		d->grid_base = v->grid_base;
	}
	mutex_unlock(&e->mu);
	compute_level_limits(d);
	L = (int)lround(LPO * log2(v->scale / d->grid_base));
	L = clampi(L, d->min_level, d->max_level);
	d->level = L;
	d->scale = level_scale(d, L);
	if (v->exact) d->exact_level = L;
	else if (d->exact_level != L) d->exact_level = INT_MIN;
	d->page = v->page;
	d->vis = fz_make_rect(v->visible[0], v->visible[1], v->visible[2], v->visible[3]);
	if (d->vis.x1 <= d->vis.x0 || d->vis.y1 <= d->vis.y0) d->vis = rotated_bounds(&d->pages[d->page], d->rot);
	d->prefetch[0] = v->prefetch_pages[0];
	d->prefetch[1] = v->prefetch_pages[1];
	d->has_view = 1;
	d->cancelled = 0;
	schedule(d);
	if (out) {
		R = render_level(d, L);
		p = &d->pages[d->page];
		pb = level_pbox(d, p, R);
		out->level = R;
		out->scale = level_scale(d, R);
		out->box[0] = pb.x0; out->box[1] = pb.y0; out->box[2] = pb.x1; out->box[3] = pb.y1;
		out->tile_size = TILE;
	}
	return ANN_OK;
}

int ann_poll(ann_doc *d, ann_tile *out, int max)
{
	ann_engine *e;
	Job *list, *j;
	int n = 0, k, relayout = 0;
	static JobVec dv;
	if (!d || (max > 0 && !out)) return 0;
	e = d->eng;
	mutex_lock(&e->mu);
	if (e->resched) {
		e->resched = 0;
		if (e->active) e->active->need_schedule = 1;
	}
	/* damaged PDF: a page's real size differs from the announced one */
	for (k = 0; k < d->npages; k++)
		if (d->pages[k].bounds_differ) {
			d->pages[k].bounds = d->pages[k].lbounds;
			d->pages[k].bounds_differ = 0;
			d->pages[k].epoch = ++d->epoch;
			relayout = 1;
		}
	if (relayout) {
		flush_jobs(d, -1);
		compute_level_limits(d);
	}
	list = d->done_head;
	d->done_head = NULL;
	mutex_unlock(&e->mu);
	if (d->need_schedule && e->active == d) schedule(d);

	/* tiles on screen first, prefetch afterwards */
	dv.n = 0;
	for (j = list; j; j = j->dnext) jv_push(&dv, j);
	if (dv.n > 1) qsort(dv.v, dv.n, sizeof *dv.v, cmp_key);
	mutex_lock(&e->mu);
	for (k = 0; k < dv.n; k++) {
		j = dv.v[k];
		j->dnext = NULL;
		if (j->dropped) {
			fz_drop_pixmap(e->ctx, j->pix);
			free(j);
			continue;
		}
		if (n >= max) {             /* back to the done list for the next poll */
			j->dnext = d->done_head;
			d->done_head = j;
			continue;
		}
		if (d->nhanded == d->handed_cap) {
			d->handed_cap = d->handed_cap ? d->handed_cap * 2 : 64;
			d->handed = realloc(d->handed, d->handed_cap * sizeof *d->handed);
		}
		j->id = ++d->next_id;
		d->handed[d->nhanded].id = j->id;
		d->handed[d->nhanded].pix = j->pix;
		d->nhanded++;
		out[n].id = j->id;
		out[n].page = j->page;
		out[n].level = j->level;
		out[n].rotation = d->rot;
		out[n].tx = j->tx;
		out[n].ty = j->ty;
		out[n].epoch = j->epoch;
		out[n].x0 = j->rect.x0;
		out[n].y0 = j->rect.y0;
		out[n].x1 = j->rect.x1;
		out[n].y1 = j->rect.y1;
		out[n].scale = j->scale;
		out[n].stride = (int)fz_pixmap_stride(e->ctx, j->pix);
		out[n].pixels = fz_pixmap_samples(e->ctx, j->pix);
		j->bytes = (size_t)(j->rect.x1 - j->rect.x0) * (j->rect.y1 - j->rect.y0) * 4;
		j->pix = NULL;
		j->state = J_READY;
		d->tex_bytes += j->bytes;
		ready_add(&d->pages[j->page], j);
		n++;
	}
	mutex_unlock(&e->mu);
	return n;
}

void ann_release_tile(ann_doc *d, long long id)
{
	int k;
	if (!d) return;
	for (k = d->nhanded - 1; k >= 0; k--)
		if (d->handed[k].id == id) {
			fz_drop_pixmap(d->eng->ctx, d->handed[k].pix);
			d->handed[k] = d->handed[--d->nhanded];
			return;
		}
}

int ann_poll_evicted(ann_doc *d, long long *ids, int max)
{
	int n;
	if (!d || max <= 0 || !ids) return 0;
	mutex_lock(&d->eng->mu);
	n = d->nevicted < max ? d->nevicted : max;
	if (n > 0) {
		memcpy(ids, d->evicted, n * sizeof *ids);
		memmove(d->evicted, d->evicted + n, (d->nevicted - n) * sizeof *ids);
		d->nevicted -= n;
	}
	mutex_unlock(&d->eng->mu);
	return n;
}

int ann_busy(ann_doc *d)
{
	ann_engine *e;
	int busy, k;
	if (!d) return 0;
	e = d->eng;
	mutex_lock(&e->mu);
	busy = d->done_head != NULL || any_loader_busy(e, d) ||
		(!d->cancelled && (d->need_schedule || (e->resched && e->active == d)));
	for (k = 0; !busy && k < e->nworkers; k++)
		if (e->running[k] && e->running[k]->doc == d) busy = 1;
	for (k = e->wq_pos; !busy && k < e->wq.n; k++)
		if (e->wq.v[k]->state == J_QUEUED && e->wq.v[k]->doc == d) busy = 1;
	if (d->lq)
		for (k = d->lq_pos; !busy && k < d->nlq; k++)
			if (d->pages[d->lq[k]].lstate == L_QUEUED) busy = 1;
	mutex_unlock(&e->mu);
	return busy;
}

int ann_view_complete(ann_doc *d)
{
	int a0, b0, a1, b1, tx, ty, R, done = 1;
	Page *p;
	if (!d || !d->has_view) return 0;
	if (d->need_schedule) return 0;
	p = &d->pages[d->page];
	mutex_lock(&d->eng->mu);
	if (p->lstate == L_ERROR) {
		mutex_unlock(&d->eng->mu);
		return 1;
	}
	if (p->lstate != L_READY) done = 0;
	R = render_level(d, d->level);
	if (done && tiles_for(d, d->page, R, 0, 0, &a0, &b0, &a1, &b1))
		for (ty = b0; ty <= b1 && done; ty++)
			for (tx = a0; tx <= a1; tx++) {
				Job *j = hfind(d, d->page, R, tx, ty);
				if (!j || (j->state != J_READY && j->state != J_FAILED)) { done = 0; break; }
			}
	mutex_unlock(&d->eng->mu);
	return done;
}

void ann_cancel(ann_doc *d)
{
	ann_engine *e;
	if (!d) return;
	e = d->eng;
	mutex_lock(&e->mu);
	stop_doc_work(d);
	d->need_schedule = 0;
	d->cancelled = 1;
	mutex_unlock(&e->mu);
}

void ann_stats(ann_doc *d, char *buf, int size)
{
	ann_engine *e;
	int k, lists = 0;
	if (!buf || size <= 0) return;
	if (!d) { snprintf(buf, size, "no document"); return; }
	e = d->eng;
	mutex_lock(&e->mu);
	for (k = 0; k < d->npages; k++) if (d->pages[k].lstate == L_READY) lists++;
	snprintf(buf, size,
		"pages=%d lists=%d (%.1f MB, budget %.0f) jobs=%d tiles=%.1f MB (budget %.0f) handed=%d evicted=%d "
		"level=%d render=%d scale=%.4f page=%d rot=%d workers=%d loaders=%d",
		d->npages, lists, d->list_bytes / 1048576.0, e->list_budget / 1048576.0, d->njobs,
		d->tex_bytes / 1048576.0, e->tex_budget / 1048576.0, d->nhanded, d->nevicted,
		d->level, render_level(d, d->level), d->scale, d->page, d->rot, e->nworkers, e->nloaders);
	mutex_unlock(&e->mu);
}
