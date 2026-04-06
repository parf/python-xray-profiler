#!/usr/bin/env python3
"""
Multi-process profiler example.

Spawns 3 worker processes writing to the same task-id,
then reads back and prints a grouped CLI execution report.

What this demo shows:
  - multiple workers writing to one Redis-backed trace
  - per-worker root spans and nested child spans
  - request/response payload capture
  - warning/alert events in the same execution timeline

Usage:
    python3 example_multiprocess.py --redis      # Redis mode + grouped CLI report
    python3 example_multiprocess.py --instant    # Instant stderr output
    python3 example_multiprocess.py --none       # No-op mode (no init, no profiler output)
"""

import json
import multiprocessing
import random
import sys
import time

import redis

from xray import Xray

REDIS_HOST = 'redis'
TASK_ID = f'example-{int(time.time())}'
INSTANT = '--instant' in sys.argv
NONE = '--none' in sys.argv
REDIS = '--redis' in sys.argv


def worker(worker_id: int, instant: bool = False, none: bool = False):
    """Simulates a Celery worker doing mixed work."""
    if none:
        pass
    elif instant:
        Xray.init_instant(thread_id=f'worker-{worker_id}')
    else:
        r = redis.Redis(host=REDIS_HOST)
        Xray.init(r, TASK_ID, thread_id=f'worker-{worker_id}')

    Xray.info('worker-start', {'pid': multiprocessing.current_process().pid})

    # Simulate DB query
    with Xray.i('DB::query', {'table': 'listings', 'state': 'FL'}) as span:
        time.sleep(random.uniform(0.01, 0.05))
        span.data({'rows': random.randint(10, 500)})

    # Simulate ES search
    with Xray.i('ES::search', {'index': 'listing'}) as span:
        time.sleep(random.uniform(0.02, 0.08))
        span.data({'hits': random.randint(0, 1000)})

    # Simulate API call with nested spans
    with Xray.i('API::enrich'):
        with Xray.i('API::geocode'):
            time.sleep(random.uniform(0.005, 0.02))
        with Xray.i('API::classify'):
            time.sleep(random.uniform(0.005, 0.015))

    # Decorator example
    @Xray.profile()
    def process_batch(n):
        time.sleep(random.uniform(0.01, 0.03))
        return n * 2

    process_batch(100)

    # Closure wrapper
    Xray.wrap(lambda: time.sleep(random.uniform(0.005, 0.01)), 'cache::warm')

    # AI call with request/response
    with Xray.i('AI::classify', {'request': {'text': 'Office space in Miami', 'model': 'gpt-4o-mini'}}) as span:
        time.sleep(random.uniform(0.05, 0.15))
        span.data({'response': {'category': 'office', 'confidence': 0.95, 'tokens': 142}})

    # Info points
    Xray.warning('slow-query', {'ms': 320})
    Xray.alert('connection-timeout', {'host': 'es-cluster', 'after_ms': 5000})

    Xray.info('worker-done')


if __name__ == '__main__':
    if not INSTANT and not NONE and not REDIS:
        print(__doc__.strip())
        sys.exit(0)

    mode = 'NONE (no-op)' if NONE else 'INSTANT (stderr)' if INSTANT else 'Redis'
    print(f'Mode: {mode} | Task ID: {TASK_ID}')

    if NONE:
        print('Running single worker with profiler OFF...\n')
        worker(0, none=True)
        print('\nDone. No profiler output is expected in this mode.')
    elif INSTANT:
        # Single process for instant — stderr output is sequential
        print('Running single worker in instant mode...\n')
        worker(0, instant=True)
        print('\nDone.')
    else:
        print(f'Spawning 3 workers...\n')
        processes = []
        for i in range(3):
            p = multiprocessing.Process(target=worker, args=(i, False))
            processes.append(p)
            p.start()
        for p in processes:
            p.join()
        print('All workers done.')
        # Connect profiler to Redis for report
        Xray.init(redis.Redis(host=REDIS_HOST), TASK_ID)

    # Print report + cleanup
    if Xray._redis:
        Xray.report()
        Xray._redis.delete(f'xray:{TASK_ID}')
