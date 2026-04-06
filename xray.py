"""
Xray — lightweight Python profiler with Redis storage

Tracks execution time, call sites, and custom data.
Optional support for distributed tasks (Celery, multiprocessing).

Usage:
    from xray import Xray
    import redis

    # Init only when you want profiling ON.
    # Without init(), Xray APIs behave as safe no-ops.

    # Redis mode — store in Redis list
    Xray.init(redis.Redis(), 'task-123')

    # Instant mode — echo to stderr (like PHP --profiler=echo)
    Xray.init_instant()

    # Context manager (span with duration)
    with Xray.i('ES::search', {'query': q}) as span:
        results = es.search(q)
        span.data({'count': len(results)})

    # Decorator
    @Xray.profile()
    def find_listings(params): ...

    # Closure wrapper
    result = Xray.wrap(lambda: api_call(url), 'API::call', {'url': url})

    # Info points (no duration)
    Xray.info('cache-hit', {'key': k})
    Xray.warning('rate-limit', {'remaining': 5})
    Xray.alert('timeout', {'url': url})
"""

import atexit
import os
import sys
import time
import json
import inspect
import threading

__version__ = '0.5.0'


class Xray:
    # Shared (safe across threads)
    _redis = None
    _atexit_registered = False
    VERSION = __version__
    TTL = 300  # Redis key expiry (seconds), override via init(ttl=)

    # Per-thread state (each thread/request gets its own)
    _local = threading.local()

    @classmethod
    def _tl(cls):
        """Get thread-local state, init defaults if needed."""
        tl = cls._local
        if not hasattr(tl, 'enabled'):
            tl.enabled = False
            tl.task_id = None
            tl.thread_id = None
            tl.instant = False
            tl.start_time = 0
            tl.stack = []
            tl.root_span = None
        return tl

    # --- Setup ---

    @classmethod
    def init(cls, redis_client, task_id: str = None, thread_id: str = None, instant: bool = False, ttl: int = None):
        """Init profiler for a task. Pass False to disable profiling explicitly."""
        if ttl is not None:
            cls.TTL = ttl
        if redis_client is False:
            cls._redis = None
            tl = cls._tl()
            if tl.root_span:
                tl.root_span.__exit__(None, None, None)
            tl.task_id = None
            tl.thread_id = thread_id or threading.current_thread().name
            tl.enabled = False
            tl.instant = False
            tl.start_time = 0
            tl.stack = []
            tl.root_span = None
            return
        from uuid import uuid4
        cls._redis = redis_client
        tl = cls._tl()
        tl.task_id = task_id or f'xray-{uuid4().hex[:8]}'
        tl.thread_id = thread_id or threading.current_thread().name
        tl.enabled = True
        tl.instant = instant
        tl.start_time = time.time()
        tl.stack = []
        tl.root_span = None
        if instant:
            _stderr(f'P[0.0] \033[1minit\033[0m task={task_id}')
        # Auto-create root span — all subsequent spans are children
        tl.root_span = cls.i(thread_id or 'PROFILER')
        tl.root_span.__enter__()
        if not cls._atexit_registered:
            atexit.register(cls.finish)
            cls._atexit_registered = True

    @classmethod
    def init_instant(cls, thread_id: str = None):
        """Init instant mode — all calls echoed to stderr. No Redis needed."""
        cls._redis = None
        tl = cls._tl()
        tl.task_id = None
        tl.thread_id = thread_id or threading.current_thread().name
        tl.default_context = {}
        tl.enabled = True
        tl.instant = True
        tl.start_time = time.time()
        tl.stack = []
        tl.root_span = None
        _stderr(f'P[0.0] \033[1minit\033[0m instant')
        tl.root_span = cls.i(thread_id or 'PROFILER')
        tl.root_span.__enter__()
        if not cls._atexit_registered:
            atexit.register(cls.finish)
            cls._atexit_registered = True

    @classmethod
    def task_id(cls) -> str:
        """Current task ID (auto-generated if not set in init)."""
        return cls._tl().task_id or ''

    @classmethod
    def finish(cls):
        """Close root span. Call at end of task/request."""
        tl = cls._tl()
        if tl.root_span:
            tl.root_span.__exit__(None, None, None)
            tl.root_span = None

    @classmethod
    def disable(cls):
        cls.finish()
        cls._tl().enabled = False


    # --- Spans (with duration) ---

    @classmethod
    def i(cls, name: str = None, data: dict = None) -> 'ProfilerSpan':
        """Start a profiling span. Use as context manager."""
        if not cls._tl().enabled:
            return _NullSpan()
        resolved = name or _caller_name()
        if cls._tl().instant:
            return _InstantSpan(cls, resolved, data)
        return ProfilerSpan(cls, resolved, data)

    # --- Info points (no duration) ---

    @classmethod
    def info(cls, name: str, data: dict = None):
        if cls._tl().instant and cls._tl().enabled:
            _stderr_entry(cls._tl().start_time, 'info', name, data, len(cls._tl().stack))
        cls._push('info', name, data)

    @classmethod
    def warning(cls, name: str, data: dict = None):
        if cls._tl().instant and cls._tl().enabled:
            _stderr_entry(cls._tl().start_time, '\033[33mwarning\033[0m', name, data, len(cls._tl().stack))
        cls._push('warning', name, data)

    @classmethod
    def alert(cls, name: str, data: dict = None):
        if cls._tl().instant and cls._tl().enabled:
            _stderr_entry(cls._tl().start_time, '\033[31malert\033[0m', name, data, len(cls._tl().stack))
        cls._push('alert', name, data)

    # --- Decorator ---

    @classmethod
    def profile(cls, name: str = None):
        """Decorator: @Xray.profile() or @Xray.profile('custom-name')"""
        import functools
        def decorator(fn):
            label = name or fn.__qualname__
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                with cls.i(label):
                    return fn(*args, **kwargs)
            return wrapper
        return decorator

    # --- Class decorator ---

    @classmethod
    def trace_class(cls, methods: list = None, skip_private: bool = True):
        """Class decorator: auto-profile all (or specific) methods.

        @Xray.trace_class()                          # all public methods
        @Xray.trace_class(methods=['find', 'save'])   # specific methods only
        @Xray.trace_class(skip_private=False)          # include _private too

        Note: async methods will lose coroutine detection after wrapping
        (inspect.iscoroutinefunction() returns False). Use with Xray.i() manually for async.
        """
        def decorator(klass):
            import functools
            for attr_name in list(vars(klass)):
                if attr_name.startswith('__'):
                    continue
                if skip_private and attr_name.startswith('_'):
                    continue
                if methods and attr_name not in methods:
                    continue
                attr = getattr(klass, attr_name)
                if not callable(attr):
                    continue
                label = f'{klass.__name__}.{attr_name}'

                @functools.wraps(attr)
                def wrapper(*args, _xray_label=label, _xray_fn=attr, **kwargs):
                    with cls.i(_xray_label):
                        return _xray_fn(*args, **kwargs)

                setattr(klass, attr_name, wrapper)
            return klass
        return decorator

    # --- Runtime patching ---

    @classmethod
    def patch(cls, target_class, methods=None):
        """Monkey-patch an existing class to add profiling — no source code changes needed.

        Xray.patch(Elasticsearch, 'search')                    # single method
        Xray.patch(Elasticsearch, ['search', 'index'])         # multiple methods
        Xray.patch(Elasticsearch)                               # all public methods

        Note: async methods will lose coroutine detection after wrapping
        (inspect.iscoroutinefunction() returns False). Use with Xray.i() manually for async.
        """
        import functools
        if isinstance(methods, str):
            methods = [methods]
        for attr_name in (methods or list(vars(target_class))):
            if attr_name.startswith('_'):
                continue
            attr = getattr(target_class, attr_name, None)
            if not callable(attr):
                continue
            if getattr(attr, '_xray_patched', False):
                continue  # already patched — idempotent
            label = f'{target_class.__name__}.{attr_name}'
            original = attr

            @functools.wraps(original)
            def wrapper(*args, _xray_label=label, _xray_fn=original, **kwargs):
                with cls.i(_xray_label):
                    return _xray_fn(*args, **kwargs)

            wrapper._xray_patched = True
            setattr(target_class, attr_name, wrapper)

    # --- Closure helper ---

    @classmethod
    def wrap(cls, fn, name: str = None, data: dict = None):
        """Wrap a callable: result = Xray.wrap(lambda: slow_call(), 'name')"""
        label = name or (fn.__qualname__ if hasattr(fn, '__qualname__') else 'closure')
        with cls.i(label, data):
            return fn()

    # --- Report ---

    @classmethod
    def entries(cls, task_id: str = None, redis_client=None) -> list:
        """Read all profiler entries from Redis for a task."""
        client = redis_client or cls._redis
        if not client:
            return []
        key = f'xray:{task_id or cls._tl().task_id}'
        return [json.loads(e) for e in client.lrange(key, 0, -1)]

    @classmethod
    def json(cls, task_id: str = None, redis_client=None) -> dict:
        """Return sorted entries as a dict ready for JSON serialization."""
        tid = task_id or cls._tl().task_id
        entries = cls.entries(tid, redis_client=redis_client)
        entries.sort(key=lambda e: e.get('start') or 0)
        spans = [e for e in entries if e['type'] == 'span' and e.get('end')]
        first_start = min((e.get('start') or 9e12) for e in entries) if entries else 0
        last_end = max((e.get('end') or 0) for e in spans) if spans else first_start
        total_ms = (last_end - first_start) * 1000 if entries else 0
        warnings = sum(1 for e in entries if e.get('type') == 'warning')
        alerts = sum(1 for e in entries if e.get('type') == 'alert')
        return {
            'task_id': tid,
            'total_ms': round(total_ms, 1),
            'entries': len(entries),
            'spans': len(spans),
            'warnings': warnings,
            'alerts': alerts,
            'data': entries,
        }

    @classmethod
    def report(cls, task_id: str = None, file=None):
        """Print execution report to file (default: stdout)."""
        import sys as _sys
        out = file or _sys.stdout
        tid = task_id or cls._tl().task_id
        entries = cls.entries(tid)

        if not entries:
            out.write('No profiler data found.\n')
            return

        # Sort by start time (spans pushed on exit, not entry) + group by thread
        entries.sort(key=lambda e: e.get('start') or 0)
        threads = {}
        for e in entries:
            threads.setdefault(e.get('thread_id', '?'), []).append(e)

        spans = [e for e in entries if e['type'] == 'span' and e.get('end')]
        infos = [e for e in entries if e['type'] != 'span']
        first_start = min((e.get('start') or 9e12) for e in entries)
        last_end = max((e.get('end') or 0) for e in spans) if spans else first_start
        total_ms = (last_end - first_start) * 1000

        out.write(f'\n\033[1m{"━" * 70}\033[0m\n')
        out.write(f'  \033[1m📊 Xray Report:\033[0m {tid}\n')
        out.write(f'  Entries: {len(entries)} ({len(spans)} spans, {len(infos)} info)\n')
        out.write(f'  Workers: {len(threads)}\n')
        out.write(f'  Total span time: \033[1m{total_ms:.1f}ms\033[0m\n')
        out.write(f'\033[1m{"━" * 70}\033[0m\n')

        for t in sorted(threads):
            thread_entries = threads[t]
            thread_spans = [e for e in thread_entries if e['type'] == 'span' and e.get('end')]
            root_spans = [e for e in thread_spans if e.get('depth', 0) == 0]
            if root_spans:
                thread_total = (root_spans[0]['end'] - root_spans[0]['start']) * 1000
            else:
                t_start = min((e['start'] for e in thread_spans), default=0)
                t_end = max((e['end'] for e in thread_spans), default=0)
                thread_total = (t_end - t_start) * 1000 if t_start else 0

            out.write(f'\n  \033[1m▸ [{t}]\033[0m — {len(thread_entries)} entries, \033[1m{thread_total:.1f}ms\033[0m total\n')
            out.write(f'  {"─" * 60}\n')

            for e in thread_entries:
                indent = '  ' * e.get('depth', 0)
                data = e.get('data') or {}
                if e['type'] == 'span' and e.get('end'):
                    ms = (e['end'] - e['start']) * 1000
                    color = '\033[31m' if ms > 100 else '\033[33m' if ms > 50 else '\033[32m'
                    out.write(f'    {indent}{color}{ms:7.1f}ms\033[0m  \033[1m{e["name"]}\033[0m')
                else:
                    icons = {'info': '·', 'warning': '\033[33m⚠\033[0m', 'alert': '\033[31;1m‼\033[0m'}
                    icon = icons.get(e['type'], '?')
                    name_color = '\033[33;1m' if e['type'] == 'warning' else '\033[31;1m' if e['type'] == 'alert' else '\033[1m'
                    out.write(f'    {indent}{icon}        {name_color}{e["name"]}\033[0m')
                # Data: request/response on own lines, rest inline
                pad = '    ' + indent + ' ' * 11
                inline = {k: v for k, v in data.items() if k not in ('request', 'response')}
                if inline:
                    out.write(f' \033[2m{json.dumps(inline, separators=(",", ":"), default=str)}\033[0m')
                out.write('\n')
                for k in ('request', 'response'):
                    if k in data:
                        out.write(f'{pad}\033[2m{k}: {json.dumps(data[k], separators=(",", ":"), default=str)}\033[0m\n')

        # Top 5 slowest
        non_root = [e for e in spans if e.get('depth', 0) > 0]
        top = sorted(non_root, key=lambda e: (e.get('end') or 0) - (e.get('start') or 0), reverse=True)[:5]
        if top:
            out.write(f'\n  {"─" * 60}\n')
            out.write(f'  \033[1m🔥 Top 5 slowest:\033[0m\n')
            for e in top:
                ms = (e['end'] - e['start']) * 1000
                color = '\033[31m' if ms > 100 else '\033[33m' if ms > 50 else '\033[32m'
                out.write(f'    {color}{ms:7.1f}ms\033[0m  {e["name"]}  \033[2m[{e.get("thread_id", "?")}]\033[0m\n')

        out.write('\n')

    # --- HTML Report ---

    @classmethod
    def html_report(cls, task_id: str = None, redis_client=None) -> str:
        """Render HTML profiler report from Redis."""
        from xray_html import render_from_redis
        return render_from_redis(task_id or cls._tl().task_id, redis_client or cls._redis)

    @classmethod
    def html_snippet(cls, endpoint: str = '/_profiler') -> str:
        """JS snippet to inject into HTML page (async fetch + embed)."""
        from xray_html import snippet
        return snippet(cls._tl().task_id, endpoint)

    @classmethod
    def attach_profiler(cls, response, task_id: str = None, endpoint: str = '/_profiler', delay_ms: int = 0, wait_iframes: bool = False):
        """Finish the current profiling session and attach profiler metadata/UI to a web response."""
        tid = task_id or cls._tl().task_id
        if not tid:
            return response

        cls.finish()

        response.headers['X-Profiler-Key'] = tid
        response.headers['X-Profiler-URL'] = f'{endpoint}?k={tid}'

        if response.content_type and response.content_type.startswith('text/html'):
            from xray_html import snippet
            elapsed = (time.time() - cls._tl().start_time) * 1000 if cls._tl().start_time else 0
            entries = cls.entries(tid)
            warns = sum(1 for e in entries if e.get('type') == 'warning')
            alerts = sum(1 for e in entries if e.get('type') == 'alert')
            html = snippet(tid, endpoint=endpoint, delay_ms=delay_ms, wait_iframes=wait_iframes, elapsed_ms=elapsed, warnings=warns, alerts=alerts)
            response.data = response.data.replace(b'</body>', html.encode() + b'</body>')

        return response

    # --- Internal ---

    @classmethod
    def _push(cls, entry_type: str, name: str, data: dict = None, start: float = None, end: float = None, thread_id: str = None, depth: int = None):
        if not cls._tl().enabled or not cls._redis:
            return
        entry = {
            'type': entry_type,
            'name': name,
            'thread_id': thread_id or cls._tl().thread_id,
            'depth': depth if depth is not None else len(cls._tl().stack),
            'start': start or time.time(),
            'end': end,
            'mem_kb': _mem_kb(),
            'data': data,
            'caller': _caller_stack(3),
        }
        key = f'xray:{cls._tl().task_id}'
        cls._redis.rpush(key, json.dumps(entry, default=_json_default))
        cls._redis.expire(key, cls.TTL)


class ProfilerSpan:
    """Context manager span — auto-records duration on exit."""

    def __init__(self, profiler_cls, name: str, data: dict = None):
        self._profiler = profiler_cls
        self._name = name
        self._data = data or {}
        self._start = time.time()
        self._thread_id = profiler_cls._tl().thread_id
        self._depth = len(profiler_cls._tl().stack)  # capture depth before push

    def data(self, extra: dict):
        """Add data during execution (like PHP $x?->data())."""
        self._data.update(extra)
        return self

    def __enter__(self):
        self._profiler._tl().stack.append(self._name)
        return self

    def __exit__(self, *exc):
        self._profiler._tl().stack.pop() if self._profiler._tl().stack else None
        self._profiler._push(
            'span', self._name, self._data,
            start=self._start, end=time.time(),
            thread_id=self._thread_id,
            depth=self._depth,
        )
        return False


class _InstantSpan:
    """Span that echoes to stderr immediately on enter and exit."""

    def __init__(self, profiler_cls, name: str, data: dict = None):
        self._profiler = profiler_cls
        self._name = name
        self._data = data or {}
        self._start = time.time()
        self._thread_id = profiler_cls._tl().thread_id
        self._depth = len(profiler_cls._tl().stack)

    def data(self, extra: dict):
        self._data.update(extra)
        return self

    def __enter__(self):
        depth = len(self._profiler._tl().stack)
        indent = '  ' * depth
        pad = ' ' * 9 + indent
        caller = _caller_location(2)
        offset = (time.time() - self._profiler._tl().start_time) * 1000
        lines = f'P[{offset:.1f}] {indent}\033[1min {self._name}\033[0m\n{pad}\033[2m{caller}\033[0m'
        lines += _format_data_lines(self._data, pad)
        _stderr(lines)
        self._profiler._tl().stack.append(self._name)
        return self

    def __exit__(self, *exc):
        self._profiler._tl().stack.pop() if self._profiler._tl().stack else None
        end = time.time()
        duration_ms = (end - self._start) * 1000
        depth = len(self._profiler._tl().stack)
        indent = '  ' * depth
        pad = ' ' * 9 + indent
        caller = _caller_location(2)
        offset = (end - self._profiler._tl().start_time) * 1000
        lines = f'\033[2mP[{offset:.1f}] {indent}out {self._name} {duration_ms:.1f}ms\n{pad}{caller}'
        for k, v in (self._data or {}).items():
            lines += f'\n{pad}{k}: {json.dumps(v, default=_json_default)}'
        lines += '\033[0m'
        _stderr(lines)
        # Also push to Redis if available
        self._profiler._push(
            'span', self._name, self._data,
            start=self._start, end=end,
            thread_id=self._thread_id,
            depth=self._depth,
        )
        return False


class _NullSpan:
    """No-op span when profiler disabled."""
    def data(self, extra): return self
    def __enter__(self): return self
    def __exit__(self, *exc): return False


# --- Helpers ---

def _json_default(value):
    """Compact fallback serializer for non-JSON objects."""
    return f'<{type(value).__name__}>'

def _caller_name(depth=2):
    """Auto-detect caller class.method or function name."""
    frame = inspect.stack()[depth]
    cls_name = ''
    if 'self' in frame.frame.f_locals:
        cls_name = type(frame.frame.f_locals['self']).__name__ + '.'
    elif 'cls' in frame.frame.f_locals:
        cls_name = frame.frame.f_locals['cls'].__name__ + '.'
    return f'{cls_name}{frame.function}'


def _caller_stack(depth=3, skip=2):
    """Get call stack: ['file:line function()', ...]."""
    stack = inspect.stack()[skip:skip + depth]
    return [f'{f.filename}:{f.lineno} {f.function}()' for f in stack]


def _caller_location(skip=2):
    """Single caller file:line for instant output."""
    stack = inspect.stack()
    if len(stack) > skip:
        f = stack[skip]
        path = f.filename.replace('./', '')
        return f'{path}:{f.lineno}'


def _mem_kb() -> int:
    """Current process RSS in KB (Linux /proc, fallback resource module)."""
    try:
        with open(f'/proc/{os.getpid()}/statm', 'r') as f:
            return int(f.read().split()[1]) * (os.sysconf('SC_PAGE_SIZE') // 1024)
    except Exception:
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        except Exception:
            return 0
    return '?'


def _stderr(msg: str, _=None):
    sys.stderr.write(msg + '\n')
    sys.stderr.flush()


def _format_data_lines(data: dict, pad: str) -> str:
    """Format data dict as indented key: value lines."""
    if not data:
        return ''
    lines = ''
    for k, v in data.items():
        lines += f'\n{pad}\033[2m{k}: {json.dumps(v, default=str)}\033[0m'
    return lines


def _stderr_entry(start_time: float, entry_type: str, name: str, data: dict = None, depth: int = 0):
    indent = '  ' * depth
    pad = ' ' * 9 + indent
    offset = (time.time() - start_time) * 1000
    caller = _caller_location(3)
    lines = f'P[{offset:.1f}] {indent}{entry_type} \033[1m{name}\033[0m\n{pad}\033[2m{caller}\033[0m'
    lines += _format_data_lines(data, pad)
    _stderr(lines)
