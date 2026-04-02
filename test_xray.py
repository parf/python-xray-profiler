#!/usr/bin/env python3
"""
Xray profiler tests.

Usage:
    python3 test_xray.py
"""

import json
import threading
import time

import redis

from xray import Xray

R = redis.Redis(host='redis')
PASS = 0
FAIL = 0


def tid(name):
    """Task ID (without xray: prefix — Xray adds it)."""
    return f'test-{name}'


def rkey(name):
    """Redis key (with xray: prefix)."""
    return f'xray:test-{name}'


def cleanup(name):
    R.delete(rkey(name))


def entries(name):
    return [json.loads(e) for e in R.lrange(rkey(name), 0, -1)]


def spans(name):
    return [e for e in entries(name) if e['type'] == 'span' and e.get('end')]


def check(test_name, condition, msg=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  ✓ {test_name}')
    else:
        FAIL += 1
        print(f'  ✗ {test_name} — {msg}')


# === Tests ===

def test_basic_span():
    name = 'basic-span'
    cleanup(name)
    Xray.init(R, tid(name))
    with Xray.i('test-span', {'x': 1}) as s:
        time.sleep(0.01)
        s.data({'y': 2})
    Xray.finish()

    ee = entries(name)
    ss = [e for e in ee if e['name'] == 'test-span']
    check('span exists', len(ss) == 1)
    check('span has data', ss[0]['data'] == {'x': 1, 'y': 2})
    check('span has duration', ss[0]['end'] > ss[0]['start'])
    check('span has depth 1', ss[0]['depth'] == 1, f"got {ss[0]['depth']}")
    check('span has mem_kb', ss[0].get('mem_kb', 0) > 0)
    check('span has caller', len(ss[0].get('caller', [])) > 0)
    cleanup(name)


def test_root_span():
    name = 'root-span'
    cleanup(name)
    Xray.init(R, tid(name), thread_id='main')
    with Xray.i('child'):
        pass
    Xray.finish()

    ee = entries(name)
    root = [e for e in ee if e['name'] == 'main' and e['type'] == 'span']
    child = [e for e in ee if e['name'] == 'child']
    check('root span exists', len(root) == 1)
    check('root depth=0', root[0]['depth'] == 0)
    check('child depth=1', child[0]['depth'] == 1)
    check('root wraps child', root[0]['start'] <= child[0]['start'] and root[0]['end'] >= child[0]['end'])
    cleanup(name)


def test_nested_depth():
    name = 'nested'
    cleanup(name)
    Xray.init(R, tid(name))
    with Xray.i('outer'):
        with Xray.i('middle'):
            with Xray.i('inner'):
                pass
    Xray.finish()

    ee = entries(name)
    outer = [e for e in ee if e['name'] == 'outer'][0]
    middle = [e for e in ee if e['name'] == 'middle'][0]
    inner = [e for e in ee if e['name'] == 'inner'][0]
    check('outer depth=1', outer['depth'] == 1)
    check('middle depth=2', middle['depth'] == 2)
    check('inner depth=3', inner['depth'] == 3)
    cleanup(name)


def test_info_warning_alert():
    name = 'info-warn-alert'
    cleanup(name)
    Xray.init(R, tid(name))
    Xray.info('checkpoint', {'step': 1})
    Xray.warning('slow', {'ms': 500})
    Xray.alert('timeout', {'url': '/api'})
    Xray.finish()

    ee = entries(name)
    types = {e['name']: e['type'] for e in ee if e['type'] != 'span'}
    check('info type', types.get('checkpoint') == 'info')
    check('warning type', types.get('slow') == 'warning')
    check('alert type', types.get('timeout') == 'alert')
    check('info has no end', all(e.get('end') is None for e in ee if e['name'] == 'checkpoint'))
    cleanup(name)


def test_decorator():
    name = 'decorator'
    cleanup(name)
    Xray.init(R, tid(name))

    @Xray.profile()
    def my_func(x):
        return x * 2

    @Xray.profile('custom-name')
    def other():
        pass

    result = my_func(21)
    other()
    Xray.finish()

    ee = entries(name)
    check('decorator result', result == 42)
    check('auto-name', any('my_func' in e['name'] for e in ee))
    check('custom-name', any(e['name'] == 'custom-name' for e in ee))
    cleanup(name)


def test_wrap():
    name = 'wrap'
    cleanup(name)
    Xray.init(R, tid(name))
    result = Xray.wrap(lambda: 'hello', 'my-closure')
    Xray.finish()

    check('wrap result', result == 'hello')
    ee = entries(name)
    check('wrap span', any(e['name'] == 'my-closure' for e in ee))
    cleanup(name)


def test_trace_class_all():
    name = 'trace-all'
    cleanup(name)
    Xray.init(R, tid(name))

    @Xray.trace_class()
    class Svc:
        def find(self): return 'found'
        def save(self): return 'saved'
        def _private(self): return 'private'

    s = Svc()
    s.find()
    s.save()
    s._private()
    Xray.finish()

    ee = entries(name)
    names = [e['name'] for e in ee if e['type'] == 'span']
    check('find profiled', 'Svc.find' in names)
    check('save profiled', 'Svc.save' in names)
    check('_private skipped', 'Svc._private' not in names)
    cleanup(name)


def test_trace_class_methods():
    name = 'trace-methods'
    cleanup(name)
    Xray.init(R, tid(name))

    @Xray.trace_class(methods=['save'])
    class Svc:
        def find(self): pass
        def save(self): pass

    s = Svc()
    s.find()
    s.save()
    Xray.finish()

    ee = entries(name)
    names = [e['name'] for e in ee if e['type'] == 'span']
    check('save profiled', 'Svc.save' in names)
    check('find NOT profiled', 'Svc.find' not in names)
    cleanup(name)


def test_trace_class_private():
    name = 'trace-private'
    cleanup(name)
    Xray.init(R, tid(name))

    @Xray.trace_class(skip_private=False)
    class Svc:
        def pub(self): pass
        def _priv(self): pass

    s = Svc()
    s.pub()
    s._priv()
    Xray.finish()

    ee = entries(name)
    names = [e['name'] for e in ee if e['type'] == 'span']
    check('pub profiled', 'Svc.pub' in names)
    check('_priv profiled', 'Svc._priv' in names)
    cleanup(name)


def test_thread_local():
    name = 'thread-local'
    cleanup(name)

    def worker(wid):
        Xray.init(R, tid(name), thread_id=f'w{wid}')
        with Xray.i(f'work-{wid}'):
            time.sleep(0.02)
        Xray.finish()

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    ee = entries(name)
    w1 = [e for e in ee if e.get('thread_id') == 'w1' and e['name'] == 'work-1']
    w2 = [e for e in ee if e.get('thread_id') == 'w2' and e['name'] == 'work-2']
    check('w1 has own span', len(w1) == 1)
    check('w2 has own span', len(w2) == 1)
    check('no cross-contamination', all(e['thread_id'] in ('w1', 'w2') for e in ee))
    cleanup(name)


def test_disabled():
    name = 'disabled'
    cleanup(name)
    Xray.init(R, tid(name))
    Xray.disable()
    with Xray.i('should-not-appear'):
        pass
    Xray.info('also-not')

    ee = entries(name)
    # Only root span (from init, closed by disable)
    non_root = [e for e in ee if e.get('depth', 0) > 0]
    check('disabled: no spans', len(non_root) == 0, f'got {len(non_root)}')
    cleanup(name)


def test_init_false_disables():
    name = 'init-false'
    cleanup(name)
    Xray.init(False)
    with Xray.i('should-not-appear'):
        pass
    Xray.info('also-not')

    check('init(false): task_id empty', Xray.task_id() == '', f'got {Xray.task_id()}')
    check('init(false): no redis entries', entries(name) == [])
    cleanup(name)


def test_sort_order():
    name = 'sort-order'
    cleanup(name)
    Xray.init(R, tid(name))
    with Xray.i('parent'):
        with Xray.i('child'):
            pass
    Xray.finish()

    ee = entries(name)
    # child pushed before parent (exits first), but sort by start should fix
    ee.sort(key=lambda e: e.get('start', 0))
    names = [e['name'] for e in ee if e['type'] == 'span']
    check('sorted: root first', names[0] == 'PROFILER')
    check('sorted: parent before child', names.index('parent') < names.index('child'))
    cleanup(name)


def test_patch_single():
    name = 'patch-single'
    cleanup(name)
    Xray.init(R, tid(name))

    class ExternalLib:
        def query(self, q): return f'result:{q}'
        def connect(self): return True

    Xray.patch(ExternalLib, 'query')
    lib = ExternalLib()
    result = lib.query('test')
    lib.connect()
    Xray.finish()

    ee = entries(name)
    names = [e['name'] for e in ee if e['type'] == 'span']
    check('patch: result preserved', result == 'result:test')
    check('patch: query profiled', 'ExternalLib.query' in names)
    check('patch: connect NOT profiled', 'ExternalLib.connect' not in names)
    cleanup(name)


def test_patch_multiple():
    name = 'patch-multi'
    cleanup(name)
    Xray.init(R, tid(name))

    class ApiClient:
        def get(self, url): return 200
        def post(self, url): return 201
        def delete(self, url): return 204

    Xray.patch(ApiClient, ['get', 'post'])
    c = ApiClient()
    c.get('/users')
    c.post('/users')
    c.delete('/users/1')
    Xray.finish()

    ee = entries(name)
    names = [e['name'] for e in ee if e['type'] == 'span']
    check('patch-multi: get profiled', 'ApiClient.get' in names)
    check('patch-multi: post profiled', 'ApiClient.post' in names)
    check('patch-multi: delete NOT profiled', 'ApiClient.delete' not in names)
    cleanup(name)


def test_patch_all():
    name = 'patch-all'
    cleanup(name)
    Xray.init(R, tid(name))

    class Service:
        def find(self): return 1
        def save(self): return 2
        def _internal(self): return 3

    Xray.patch(Service)
    s = Service()
    s.find()
    s.save()
    s._internal()
    Xray.finish()

    ee = entries(name)
    names = [e['name'] for e in ee if e['type'] == 'span']
    check('patch-all: find profiled', 'Service.find' in names)
    check('patch-all: save profiled', 'Service.save' in names)
    check('patch-all: _internal skipped', 'Service._internal' not in names)
    cleanup(name)


# === Run ===

if __name__ == '__main__':
    print('\n=== Xray Tests ===\n')

    tests = [
        test_basic_span,
        test_root_span,
        test_nested_depth,
        test_info_warning_alert,
        test_decorator,
        test_wrap,
        test_trace_class_all,
        test_trace_class_methods,
        test_trace_class_private,
        test_thread_local,
        test_disabled,
        test_init_false_disables,
        test_sort_order,
        test_patch_single,
        test_patch_multiple,
        test_patch_all,
    ]

    for t in tests:
        print(f'\n--- {t.__name__} ---')
        try:
            t()
        except Exception as e:
            FAIL += 1
            print(f'  ✗ EXCEPTION: {e}')

    print(f'\n{"=" * 40}')
    print(f'  ✓ {PASS} passed, ✗ {FAIL} failed')
    print(f'{"=" * 40}\n')

    exit(1 if FAIL else 0)
