/*
 * thread.h -- minimal threads, mutexes, condition variables, CPU count and
 * physical RAM for Win32 and POSIX. Taken from Lire's src/thread.h
 * (torakce/Lire v1.2.1), comments translated.
 */
#ifndef ANN_THREAD_H
#define ANN_THREAD_H

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <process.h>

typedef CRITICAL_SECTION ann_mutex;
typedef CONDITION_VARIABLE ann_cond;
typedef HANDLE ann_thread;

static inline void mutex_init(ann_mutex *m) { InitializeCriticalSectionAndSpinCount(m, 2000); }
static inline void mutex_free(ann_mutex *m) { DeleteCriticalSection(m); }
static inline void mutex_lock(ann_mutex *m) { EnterCriticalSection(m); }
static inline void mutex_unlock(ann_mutex *m) { LeaveCriticalSection(m); }
static inline void cond_init(ann_cond *c) { InitializeConditionVariable(c); }
static inline void cond_free(ann_cond *c) { (void)c; }
static inline void cond_wait(ann_cond *c, ann_mutex *m) { SleepConditionVariableCS(c, m, INFINITE); }
static inline void cond_signal(ann_cond *c) { WakeConditionVariable(c); }
static inline void cond_broadcast(ann_cond *c) { WakeAllConditionVariable(c); }

typedef struct { void (*fn)(void *); void *arg; } ann_thread_start;
static unsigned __stdcall ann_thread_tramp(void *p)
{
	ann_thread_start s = *(ann_thread_start *)p;
	HeapFree(GetProcessHeap(), 0, p);
	s.fn(s.arg);
	return 0;
}
/* low_prio: render threads run below the UI thread */
static inline int thread_create(ann_thread *t, void (*fn)(void *), void *arg, int low_prio, size_t stack)
{
	ann_thread_start *s = HeapAlloc(GetProcessHeap(), 0, sizeof *s);
	s->fn = fn; s->arg = arg;
	*t = (HANDLE)_beginthreadex(NULL, (unsigned)stack, ann_thread_tramp, s, 0, NULL);
	if (!*t) return -1;
	if (low_prio) SetThreadPriority(*t, THREAD_PRIORITY_BELOW_NORMAL);
	return 0;
}
static inline void thread_join(ann_thread t) { WaitForSingleObject(t, INFINITE); CloseHandle(t); }
static inline int cpu_count(void)
{
	DWORD n = GetActiveProcessorCount(ALL_PROCESSOR_GROUPS);
	return n > 0 ? (int)n : 1;
}
static inline unsigned long long ram_bytes(void)
{
	MEMORYSTATUSEX m;
	m.dwLength = sizeof m;
	return GlobalMemoryStatusEx(&m) ? m.ullTotalPhys : (4ULL << 30);
}

#else /* POSIX */
#include <pthread.h>
#include <unistd.h>

typedef pthread_mutex_t ann_mutex;
typedef pthread_cond_t ann_cond;
typedef pthread_t ann_thread;

static inline void mutex_init(ann_mutex *m) { pthread_mutex_init(m, NULL); }
static inline void mutex_free(ann_mutex *m) { pthread_mutex_destroy(m); }
static inline void mutex_lock(ann_mutex *m) { pthread_mutex_lock(m); }
static inline void mutex_unlock(ann_mutex *m) { pthread_mutex_unlock(m); }
static inline void cond_init(ann_cond *c) { pthread_cond_init(c, NULL); }
static inline void cond_free(ann_cond *c) { pthread_cond_destroy(c); }
static inline void cond_wait(ann_cond *c, ann_mutex *m) { pthread_cond_wait(c, m); }
static inline void cond_signal(ann_cond *c) { pthread_cond_signal(c); }
static inline void cond_broadcast(ann_cond *c) { pthread_cond_broadcast(c); }

typedef struct { void (*fn)(void *); void *arg; } ann_thread_start;
#include <stdlib.h>
static void *ann_thread_tramp(void *p)
{
	ann_thread_start s = *(ann_thread_start *)p;
	free(p);
	s.fn(s.arg);
	return NULL;
}
static inline int thread_create(ann_thread *t, void (*fn)(void *), void *arg, int low_prio, size_t stack)
{
	pthread_attr_t a;
	ann_thread_start *s = malloc(sizeof *s);
	(void)low_prio;
	s->fn = fn; s->arg = arg;
	pthread_attr_init(&a);
	if (stack) pthread_attr_setstacksize(&a, stack);
	int r = pthread_create(t, &a, ann_thread_tramp, s);
	pthread_attr_destroy(&a);
	return r;
}
static inline void thread_join(ann_thread t) { pthread_join(t, NULL); }
static inline int cpu_count(void)
{
	long n = sysconf(_SC_NPROCESSORS_ONLN);
	return n > 0 ? (int)n : 1;
}
static inline unsigned long long ram_bytes(void)
{
	long p = sysconf(_SC_PHYS_PAGES), s = sysconf(_SC_PAGE_SIZE);
	return p > 0 && s > 0 ? (unsigned long long)p * s : (4ULL << 30);
}
#endif

#endif
