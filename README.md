# Python Profiler

Author: Serg Parf <sergey.porfiriev@gmail.com>

Lightweight Python profiler. Tracks execution time, call hierarchy, and custom data.
Optional support for distributed tasks (Celery, multiprocessing) via shared Redis storage.

- **Spans** — `with Profiler.i('name')` measures duration, captures call site
- **Decorators** — `@Profiler.profile()` auto-profiles functions
- **Info points** — `Profiler.info/warning/alert()` for events without duration
- **Redis storage** — all workers write to one task-id, atomic RPUSH, 1h TTL
- **Instant mode** — real-time stderr output with nested outline
- **Report** — `Profiler.report()` prints color-coded tree grouped by worker
- **Zero overhead** — disabled profiler returns no-op objects, no conditionals needed

## Quick Start

```python
from profiler import Profiler
import redis

Profiler.init(redis.Redis(host='redis'), 'my-task-123', context={'user_id': 42})

with Profiler.i('ES::search', {'query': q}) as span:
    results = es.search(q)
    span.data({'count': len(results)})
```

## Spans (with duration)

```python
# Context manager — recommended
with Profiler.i('section-name', {'key': 'val'}) as span:
    result = do_work()
    span.data({'rows': len(result)})   # add data mid-execution

# Auto-name from caller (Class.method)
with Profiler.i() as span:
    ...
```

When profiler is disabled, `Profiler.i()` returns a no-op — safe to use without checks.

## Decorator

```python
@Profiler.profile()                    # auto-name: Class.method
def find_listings(params): ...

@Profiler.profile('custom-name')       # explicit name
def helper(): ...
```

## Closure Wrapper

```python
result = Profiler.wrap(lambda: api_call(url), 'API::call', {'url': url})
```

## Info Points (no duration)

```python
Profiler.info('cache-hit', {'key': k})
Profiler.warning('rate-limit', {'remaining': 5})
Profiler.alert('timeout', {'url': url, 'after_ms': 5000})
```

## Setup

```python
# Redis mode — store entries, read report later
Profiler.init(redis_client, task_id, thread_id=None, context=None)

# Instant mode — real-time stderr output
Profiler.init_instant()

# Disable
Profiler.disable()
```

`thread_id` defaults to `threading.current_thread().name`.
`context` is attached to every entry (request info, user_id, etc).

## Reading Results

```python
# Built-in report
Profiler.report()                      # current task
Profiler.report('other-task-id')       # specific task

# Raw entries
entries = Profiler.entries()            # list of dicts
```

## Multi-Process / Celery

Each worker calls `Profiler.init()` with the same `task_id` but different `thread_id`.
Redis RPUSH is atomic — no conflicts.

```python
# Worker 1
Profiler.init(r, 'job-abc', thread_id='w1')

# Worker 2
Profiler.init(r, 'job-abc', thread_id='w2')
```

Report groups entries by `thread_id` automatically.

## Instant Mode

Real-time stderr output with nested outline — no Redis needed:

```python
Profiler.init_instant()

with Profiler.i('DB::query', {'table': 'users'}):
    ...
```

Output:
```
P[0.0] init instant
P[0.1] in DB::query
         app/db.py:45
         table: "users"
P[15.3] out DB::query 15.2ms
         app/db.py:45
         table: "users"
         rows: 150
```

Nested spans are indented. `in` lines are bold, `out` lines are dimmed.

## Example

```bash
python3 example_multiprocess.py --default    # 3 workers + report
python3 example_multiprocess.py --instant    # real-time stderr
```

## See Also

- [internals.md](internals.md) — Redis format, entry structure, implementation details
