# Xray

Author: Serg Parf <sergey.porfiriev@gmail.com>

Lightweight Python profiler. Tracks execution time, call hierarchy, and custom data.
Optional support for distributed tasks (Celery, multiprocessing) via shared Redis storage.

- **Spans** — `with Xray.i('name')` measures duration, captures call site
- **Decorators** — `@Xray.profile()` auto-profiles functions
- **Info points** — `Xray.info/warning/alert()` for events without duration
- **Redis storage** — all workers write to one task-id, atomic RPUSH, 1h TTL
- **Instant mode** — real-time stderr output with nested outline
- **Report** — `Xray.report()` prints color-coded tree grouped by worker
- **Zero overhead** — disabled profiler returns no-op objects, no conditionals needed

![Xray Web Report](screenshot.png)

## Quick Start

```python
from xray import Xray
import redis

Xray.init(redis.Redis(host='redis'), 'my-task-123', context={'user_id': 42})

with Xray.i('ES::search', {'query': q}) as span:
    results = es.search(q)
    span.data({'count': len(results)})
```

## Spans (with duration)

```python
# Context manager — recommended
with Xray.i('section-name', {'key': 'val'}) as span:
    result = do_work()
    span.data({'rows': len(result)})   # add data mid-execution

# Auto-name from caller (Class.method)
with Xray.i() as span:
    ...
```

When profiler is disabled, `Xray.i()` returns a no-op — safe to use without checks.

## Decorator

```python
@Xray.profile()                    # auto-name: Class.method
def find_listings(params): ...

@Xray.profile('custom-name')       # explicit name
def helper(): ...
```

## Class Decorator

Auto-profile all (or specific) methods of a class. Each call creates a span
named `ClassName.method_name`. Private methods (`_name`) are skipped by default.

```python
# All public methods — every call to find/enrich/save is auto-profiled
@Xray.trace_class()
class SearchService:
    def find(self, q): ...           # → span "SearchService.find"
    def enrich(self, data): ...      # → span "SearchService.enrich"
    def save(self, item): ...        # → span "SearchService.save"
    def _internal(self): ...         # skipped (private)

# Specific methods only
@Xray.trace_class(methods=['find', 'save'])
class SearchService:
    def find(self, q): ...           # profiled
    def save(self, item): ...        # profiled
    def enrich(self, data): ...      # NOT profiled

# Include private methods too
@Xray.trace_class(skip_private=False)
class Service:
    def run(self): ...               # profiled
    def _setup(self): ...            # profiled (skip_private=False)
```

Useful for instrumenting service classes, repositories, and API clients
without adding `with Xray.i()` to every method.

## Closure Wrapper

```python
result = Xray.wrap(lambda: api_call(url), 'API::call', {'url': url})
```

## Info Points (no duration)

```python
Xray.info('cache-hit', {'key': k})
Xray.warning('rate-limit', {'remaining': 5})
Xray.alert('timeout', {'url': url, 'after_ms': 5000})
```

## Setup

```python
# Redis mode — store entries, read report later
Xray.init(redis_client, task_id, thread_id=None, context=None)

# Instant mode — real-time stderr output
Xray.init_instant()

# Disable
Xray.disable()
```

`thread_id` defaults to `threading.current_thread().name`.
`context` is attached to every entry (request info, user_id, etc).

## Reading Results

```python
# Built-in report
Xray.report()                      # current task
Xray.report('other-task-id')       # specific task

# Raw entries
entries = Xray.entries()            # list of dicts
```

## Multi-Process / Celery

Each worker calls `Xray.init()` with the same `task_id` but different `thread_id`.
Redis RPUSH is atomic — no conflicts.

```python
# Worker 1
Xray.init(r, 'job-abc', thread_id='w1')

# Worker 2
Xray.init(r, 'job-abc', thread_id='w2')
```

Report groups entries by `thread_id` automatically.

## Instant Mode

Real-time stderr output with nested outline — no Redis needed:

```python
Xray.init_instant()

with Xray.i('DB::query', {'table': 'users'}):
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

## Examples

### CLI (multiprocess)

```bash
python3 example_multiprocess.py --default    # 3 workers + Redis report
python3 example_multiprocess.py --instant    # real-time stderr output
```

### Web (Flask)

```bash
pip3 install flask redis
python3 example_web.py
```

Open http://localhost:5000/ — auto-profiled page with execution panel at the bottom.

| URL | Description |
|-----|-------------|
| `/` | Single-process demo with DB, ES, API, AI calls |
| `/threaded` | Multi-worker demo (two iframe workers share task-id) |
| `/api/search?q=miami` | JSON API (profiler key in `X-Xray-Key` header) |
| `/_profiler?k=KEY` | Standalone HTML report |

## See Also

- [internals.md](internals.md) — Redis format, entry structure, implementation details
