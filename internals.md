# Profiler Internals

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
  "depth": 1,                             // nesting depth (0 = top level)
  "start": 1711900000.123,                // unix timestamp (float)
  "end": 1711900000.456,                  // null for info/warning/alert
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

## Span Classes

| Class | Mode | Description |
|-------|------|-------------|
| `ProfilerSpan` | Redis | Pushes to Redis on `__exit__` |
| `_InstantSpan` | Instant | Writes to stderr on `__enter__` and `__exit__`, also pushes to Redis if available |
| `_NullSpan` | Disabled | No-op, all methods return self |

## Stack Tracking

`Xray._stack` (list of span names) tracks nesting depth:
- `ProfilerSpan.__enter__` → push name
- `ProfilerSpan.__exit__` → pop name
- `_push()` reads `len(_stack)` → stored as `depth` in entry

Used for indented outline in both instant stderr and report.

## Instant Mode

Like `--profiler=echo` — every span enter/exit immediately written to stderr:

```
P[0.0] in DB::query
         /app/tasks.py:45
         table: "listings"
P[15.2] out DB::query 15.2ms
         /app/tasks.py:45
         table: "listings"
         rows: 150
```

- `in` lines: bold name, grey caller + data
- `out` lines: fully dimmed (name, duration, caller, data)
- Nesting shown via indentation

## Report Format

`Xray.report()` reads Redis entries and prints:
- Header with totals
- Per-thread sections with indented outline
- Color-coded durations: green < 50ms, yellow 50-100ms, red > 100ms
- Warning (⚠ yellow) and alert (‼ red) highlights
- `request`/`response` data on own indented lines
- Top 5 slowest spans

## Reading from Other Languages

From Python:
```python
entries = Xray.entries('task-id')
```

Raw Redis:
```bash
redis-cli LRANGE xray:task-id 0 -1
```

## Files

| File | Description |
|------|-------------|
| `xray.py` | Core module |
| `README.md` | User documentation |
| `XRAY-internals.md` | This file |
| `example_multiprocess.py` | Multi-process example |
