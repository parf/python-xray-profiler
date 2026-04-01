# Xray Internals

Implementation details, Redis format, and architecture.

## Redis Storage

```
Key:  xray:{task_id}    (Redis LIST)
TTL:  1 hour (auto-set on each push)
Each element: JSON string
```

### Entry Format

```json
{
  "type": "span",                          // span | info | warning | alert
  "name": "ES::search",
  "thread_id": "worker-1",
  "depth": 1,                             // nesting depth (0 = root span)
  "start": 1711900000.123,                // unix timestamp (float)
  "end": 1711900000.456,                  // null for info/warning/alert
  "mem_kb": 55296,                        // RSS at push time
  "data": {"query": "miami", "count": 150},
  "context": {"user_id": 42},             // from Xray.init(context=...)
  "caller": [                             // call stack (up to 3 frames)
    "app/tasks.py:45 search_listings()",
    "app/es.py:120 search()",
    "app/es.py:80 _execute()"
  ]
}
```

Duration = `end - start` (computed by reader, not stored).

## Entry Types

| Type | Has duration | Use case |
|------|-------------|----------|
| `span` | yes (start + end) | Timed code section |
| `info` | no | Checkpoint, status |
| `warning` | no | Non-critical issue |
| `alert` | no | Critical issue |

## Thread-Local State

All mutable state lives in `threading.local()` — each thread/request gets its own:
- `task_id`, `thread_id`, `stack`, `root_span`, `start_time`, `enabled`, `instant`

Only `_redis` is shared (thread-safe Redis client).

## Span Classes

| Class | Mode | Description |
|-------|------|-------------|
| `ProfilerSpan` | Redis | Pushes to Redis on `__exit__` |
| `_InstantSpan` | Instant | Writes to stderr on `__enter__`/`__exit__`, also pushes to Redis if available |
| `_NullSpan` | Disabled | No-op, all methods return self |

All spans capture `thread_id` and `depth` at creation time (`__init__`), not at push time (`__exit__`).
This prevents cross-contamination when multiple threads share the class.

## Root Span

`Xray.init()` auto-creates a root span (depth=0) named after `thread_id`.
`Xray.finish()` closes it. `atexit` is a fallback.
Root span duration ≈ total process/request time.
HTML renderer shows root span as the thread header row (not as a separate entry).

## Stack Tracking

`Xray._tl().stack` (list of span names) tracks nesting:
- `__init__` captures `depth = len(stack)` before push
- `__enter__` appends name to stack
- `__exit__` pops from stack, pushes entry with captured depth

## Instrumentation Methods

| Method | Target | When |
|--------|--------|------|
| `Xray.i('name')` | Single block | Manual, inline |
| `@Xray.profile()` | Function | At definition time |
| `@Xray.trace_class()` | Class (all/selected methods) | At definition time |
| `Xray.patch(cls)` | Existing class | At runtime, no source changes |
| `Xray.wrap(fn)` | Closure/lambda | Inline |

## Instant Mode

Real-time stderr — every span `in`/`out` immediately written:

```
P[0.0] in DB::query
         app/tasks.py:45
         table: "listings"
P[15.2] out DB::query 15.2ms
         app/tasks.py:45
         table: "listings"
         rows: 150
```

- `in` lines: bold name, grey caller + data
- `out` lines: fully dimmed
- Nesting shown via indentation

## HTML Report

`Xray.html_report()` / web panel:
- Call Tree table: %%, Block, Params, Mem(MB), Time(ms)
- Thread sections with colored backgrounds (multi-worker)
- Root span shown as thread header (not separate row)
- Typed params: bold keys, green strings, teal ints, blue bools, grey null
- Request/response expandable with [+]/[-]
- Long strings trimmed at 80 chars with tooltip
- Color-coded timing: green < 50ms, yellow 50-100ms, red > 100ms
- Warning ⚠ / Alert ‼ rows and bar badges
- Top 5 slowest (excludes root spans)

## CLI Report

`Xray.report()`:
- Color-coded tree grouped by worker
- Per-thread wall-clock time (from root span, not sum)
- Nested outline with `│` indentation
- Top 5 slowest spans

## JSON

`Xray.json()` returns sorted entries + summary:
```json
{"task_id": "...", "total_ms": 340.1, "entries": 14, "spans": 10, "warnings": 1, "alerts": 1, "data": [...]}
```

## Reading from Other Languages

```bash
redis-cli LRANGE xray:task-id 0 -1
```

## Files

| File | Description |
|------|-------------|
| `xray.py` | Core: Xray class, spans, thread-local state |
| `xray_html.py` | HTML renderer + web snippet |
| `test_xray.py` | 44 tests |
| `example_web.py` | Flask demo (single + multi-worker) |
| `example_multiprocess.py` | CLI demo (3 workers) |
| `README.md` | User documentation |
| `internals.md` | This file |
