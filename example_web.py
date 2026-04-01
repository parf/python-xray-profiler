#!/usr/bin/env python3
"""
Web profiler example — Flask app with auto-profiling.

Endpoints:
  GET /                  — HTML page with profiled operations + embedded profiler
  GET /api/search?q=...  — JSON API (profiler key in X-Profiler-Key header)
  GET /_profiler?k=KEY   — standalone HTML profiler report

Usage:
  pip3 install flask
  python3 example_web.py
  # Open http://localhost:5000/
"""

import random
import time
from uuid import uuid4

import redis
from flask import Flask, Response, request, jsonify

from profiler import Profiler
from profiler_html import render_from_redis, snippet

app = Flask(__name__)
r = redis.Redis(host='redis')


# --- Middleware: auto-profile every request ---

@app.before_request
def start_profiler():
    if request.path in ('/_profiler', '/worker'):
        return  # profiler endpoint + worker iframes handle their own init
    task_id = f'web-{uuid4().hex[:8]}'
    request.environ['profiler_task_id'] = task_id
    Profiler.init(r, task_id, context={'method': request.method, 'path': request.path})


@app.after_request
def attach_profiler(response):
    task_id = request.environ.get('profiler_task_id')
    if not task_id:
        return response

    Profiler.finish()  # close root span (before response sent; atexit is fallback)

    # HTML responses: inject profiler panel
    if response.content_type and response.content_type.startswith('text/html'):
        delay = int(request.environ.get('profiler_delay_ms', 0))
        html = snippet(task_id, delay_ms=delay)
        response.data = response.data.replace(b'</body>', html.encode() + b'</body>')

    # JSON/API responses: add profiler key as header
    response.headers['X-Profiler-Key'] = task_id
    response.headers['X-Profiler-URL'] = f'/_profiler?k={task_id}'
    return response


# --- Profiler report endpoint ---

@app.route('/_profiler')
def profiler_view():
    task_id = request.args.get('k', '')
    if not task_id:
        return 'Missing ?k= parameter', 400
    html = render_from_redis(task_id, r)
    return Response(html, content_type='text/html')


# --- Simulated operations ---

def sim_db_query(table, where=None):
    with Profiler.i('DB::query', {'table': table, 'where': where}) as span:
        time.sleep(random.uniform(0.01, 0.04))
        rows = random.randint(5, 200)
        span.data({'rows': rows})
        return [{'id': i} for i in range(rows)]


def sim_es_search(index, query):
    with Profiler.i('ES::search', {'index': index, 'query': query}) as span:
        time.sleep(random.uniform(0.02, 0.06))
        hits = random.randint(0, 500)
        span.data({'hits': hits})
        return {'hits': hits}


def sim_ai_classify(text):
    req = {
        'text': text,
        'model': 'gpt-4o-mini',
        'system_prompt': 'You are a commercial real estate classifier. Analyze the property description and return category, subcategory, confidence score, key features, and suggested tags.',
        'temperature': 0.3,
        'max_tokens': 512,
    }
    with Profiler.i('AI::classify', {'request': req}) as span:
        time.sleep(random.uniform(0.05, 0.15))
        resp = {
            'category': 'office',
            'subcategory': 'Class A Office Space',
            'confidence': 0.92,
            'tokens': {'prompt': 87, 'completion': 142, 'total': 229},
            'features': ['high-rise', 'downtown', 'parking', 'elevator', 'reception', 'conference-rooms'],
            'tags': ['premium', 'professional', 'CBD', 'transit-accessible', 'recently-renovated'],
            'description': 'Modern Class A office space in the heart of Miami financial district with panoramic views, 24/7 security, and premium amenities including fitness center and rooftop terrace.',
        }
        span.data({'response': resp})
        return resp


def sim_cache_lookup(key):
    hit = random.random() > 0.3
    if hit:
        Profiler.info('cache-hit', {'key': key})
    else:
        Profiler.info('cache-miss', {'key': key})
    return hit


# --- Routes ---

@app.route('/')
def index():
    with Profiler.i('page::index'):
        Profiler.info('request-start', {'ip': request.remote_addr, 'ua': request.user_agent.string[:60]})

        sim_cache_lookup('page:index')
        listings = sim_db_query('listings', 'state=FL')
        results = sim_es_search('listing', 'miami office')

        with Profiler.i('API::enrich', {'listing_id': 12228396, 'source': 'resites'}):
            with Profiler.i('API::geocode', {'address': '553 G St, Chula Vista, CA 91910', 'provider': 'google'}) as geo:
                time.sleep(random.uniform(0.005, 0.02))
                geo.data({'lat': 32.6401, 'lon': -117.0842, 'confidence': 0.98, 'cached': False})
            with Profiler.i('API::classify', {'property_type': 'Commercial Sale', 'sqft': 5900, 'price': 1250000, 'categories': ['restaurant', 'retail']}) as cls:
                time.sleep(random.uniform(0.005, 0.015))
                cls.data({'result': 'restaurant', 'confidence': 0.87, 'is_business': True})

        classification = sim_ai_classify('Office space in Miami')

        Profiler.warning('slow-query', {'ms': 320})
        Profiler.alert('connection-timeout', {'host': 'es-cluster', 'after_ms': 5000})

        with Profiler.i('render::template'):
            time.sleep(random.uniform(0.005, 0.015))

        Profiler.info('request-done')

    task_id = request.environ['profiler_task_id']
    request.environ['profiler_delay_ms'] = 4000  # wait for iframe workers

    return f'''<!DOCTYPE html>
<html>
<head><title>Profiler Web Example</title></head>
<body style="font-family: sans-serif; padding: 20px; background: #f5f5f5; margin-bottom: 60vh">
    <h1>📊 Profiler Web Example</h1>
    <p>This page has auto-profiling. Two background workers run in iframes below.</p>
    <p>Profiler panel loads after 4s to capture all workers.</p>
    <ul>
        <li>DB query returned {len(listings)} rows</li>
        <li>ES search returned {results['hits']} hits</li>
    </ul>

    <h3>Background workers (shared task-id: {task_id})</h3>
    <div style="display:flex;gap:12px">
        <iframe src="/worker?task_id={task_id}&name=enricher" style="width:200px;height:30px;border:1px solid #ddd;border-radius:4px"></iframe>
        <iframe src="/worker?task_id={task_id}&name=classifier" style="width:200px;height:30px;border:1px solid #ddd;border-radius:4px"></iframe>
    </div>

    <h3>Try also:</h3>
    <ul>
        <li><a href="/api/search?q=miami+office">/api/search?q=miami+office</a> — JSON API (check X-Profiler-Key header)</li>
        <li><a href="/api/search?q=boston+warehouse">/api/search?q=boston+warehouse</a> — another search</li>
    </ul>
</body>
</html>'''


@app.route('/api/search')
def api_search():
    q = request.args.get('q', 'commercial real estate')

    with Profiler.i('api::search', {'query': q}):
        sim_cache_lookup(f'search:{q}')
        results = sim_es_search('listing', q)

        with Profiler.i('enrich'):
            for i in range(min(3, results['hits'])):
                sim_db_query('listing_details', f'id={i}')

        if random.random() > 0.5:
            classification = sim_ai_classify(q)
        else:
            classification = None

        Profiler.warning('slow-upstream', {'latency_ms': random.randint(80, 300)})

    return jsonify({
        'query': q,
        'hits': results['hits'],
        'classification': classification,
        'profiler': request.environ.get('profiler_task_id'),
    })


@app.route('/worker')
def worker_iframe():
    """Simulates a background worker (called via iframe with shared task_id)."""
    task_id = request.args.get('task_id', '')
    worker_name = request.args.get('name', 'worker')
    if not task_id:
        return 'Missing task_id', 400

    Profiler.init(r, task_id, thread_id=worker_name, context={'worker': worker_name})

    with Profiler.i(f'{worker_name}::run'):
        # Simulate worker doing its own DB + API work
        sim_db_query('worker_queue', f'worker={worker_name}')

        with Profiler.i(f'{worker_name}::process'):
            time.sleep(random.uniform(0.3, 0.8))

            if worker_name == 'enricher':
                with Profiler.i('Geo::batch_geocode', {'count': 25}) as span:
                    time.sleep(random.uniform(0.2, 0.5))
                    span.data({'resolved': 23, 'failed': 2})
                Profiler.info('enricher-checkpoint', {'processed': 25})

            elif worker_name == 'classifier':
                sim_ai_classify('Warehouse with loading dock in Boston industrial district near I-93')
                sim_es_search('listing', 'similar:warehouse:boston')
                Profiler.warning('model-fallback', {'primary': 'gpt-4o', 'fallback': 'gpt-4o-mini', 'reason': 'rate-limited'})

        Profiler.info(f'{worker_name}::done')

    return f'<html><body style="font:11px monospace;color:#888">✓ {worker_name} done</body></html>'


if __name__ == '__main__':
    print('Profiler Web Example')
    print('  http://localhost:5000/')
    print('  http://localhost:5000/api/search?q=miami+office')
    print()
    app.run(host='0.0.0.0', port=5000, debug=True)
