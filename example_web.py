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

from xray import Xray

app = Flask(__name__)
r = redis.Redis(host='redis')


class SearchContext:
    pass


# --- Middleware: auto-profile every request ---

@app.before_request
def start_profiler():
    if request.path in ('/_profiler', '/_profiler/json', '/worker'):
        request.environ['xray_attach_profiler'] = False
        Xray.init(False)  # explicit no-op init for requests that should not attach profiler UI
        return
    request.environ['xray_attach_profiler'] = True
    task_id = f'web-{uuid4().hex[:8]}'
    request.environ['profiler_task_id'] = task_id
    Xray.init(r, task_id)


@app.after_request
def attach_profiler(response):
    if not request.environ.get('xray_attach_profiler', True):
        return response
    return Xray.attach_profiler(
        response,
        endpoint='/_profiler',
        delay_ms=int(request.environ.get('profiler_delay_ms', 0)),
        wait_iframes=bool(request.environ.get('profiler_wait_iframes', False)),
    )


# --- Profiler report endpoint ---

@app.route('/_profiler')
def profiler_view():
    task_id = request.args.get('k', '')
    if not task_id:
        return 'Missing ?k= parameter', 400
    Xray._redis = r
    html = Xray.html_report(task_id)
    return Response(html, content_type='text/html; charset=utf-8')


@app.route('/_profiler/json')
def profiler_json():
    task_id = request.args.get('k', '')
    if not task_id:
        return jsonify({'error': 'Missing ?k= parameter'}), 400
    Xray._redis = r
    return jsonify(Xray.json(task_id))


# --- Simulated operations ---

def sim_db_query(table, where=None):
    with Xray.i('DB::query', {'table': table, 'where': where}) as span:
        time.sleep(random.uniform(0.01, 0.04))
        rows = random.randint(5, 200)
        span.data({'rows': rows})
        return [{'id': i} for i in range(rows)]


def sim_es_search(index, query):
    with Xray.i('ES::search', {'index': index, 'query': query, 'context': SearchContext()}) as span:
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
    with Xray.i('AI::classify', {'request': req}) as span:
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
        Xray.info('cache-hit', {'key': key})
    else:
        Xray.info('cache-miss', {'key': key})
    return hit


# --- Routes ---

@app.route('/')
def index():
    with Xray.i('page::index'):
        Xray.info('request-start', {'ip': request.remote_addr, 'ua': request.user_agent.string[:60]})

        sim_cache_lookup('page:index')
        listings = sim_db_query('listings', 'state=FL')
        results = sim_es_search('listing', 'miami office')

        with Xray.i('API::enrich', {'listing_id': 12228396, 'source': 'resites'}):
            with Xray.i('API::geocode', {'address': '553 G St, Chula Vista, CA 91910', 'provider': 'google'}) as geo:
                time.sleep(random.uniform(0.005, 0.02))
                geo.data({'lat': 32.6401, 'lon': -117.0842, 'confidence': 0.98, 'cached': False})
            with Xray.i('API::classify', {'property_type': 'Commercial Sale', 'sqft': 5900, 'price': 1250000, 'categories': ['restaurant', 'retail']}) as cls:
                time.sleep(random.uniform(0.005, 0.015))
                cls.data({'result': 'restaurant', 'confidence': 0.87, 'is_business': True})

        classification = sim_ai_classify('Office space in Miami')

        Xray.warning('slow-query', {'ms': 320})
        Xray.alert('connection-timeout', {'host': 'es-cluster', 'after_ms': 5000})

        with Xray.i('render::template'):
            time.sleep(random.uniform(0.005, 0.015))

        Xray.info('request-done')

    task_id = request.environ['profiler_task_id']

    return f'''<!DOCTYPE html>
<html>
<head><title>Xray Web Demo</title></head>
<body style="font-family: -apple-system, sans-serif; padding: 20px 40px; background: #f5f5f5; margin-bottom: 60vh; max-width: 800px; line-height: 1.6">
    <h1>📊 Xray — Web Demo</h1>

    <p>Welcome! This page is <b>auto-profiled</b>. Every database query, API call, and cache lookup
    is tracked and timed. Look at the <b>panel at the bottom</b> of the screen — that's the
    execution trace of this very page.</p>

    <div style="background:#fff; padding:16px 20px; border-radius:8px; border-left:4px solid #4fc3f7; margin:16px 0">
        <b>🔍 What happened on this page:</b>
        <ul style="margin:8px 0">
            <li>🗄 <b>DB query</b> fetched {len(listings)} listings from Florida</li>
            <li>🔎 <b>Elasticsearch</b> found {results['hits']} matching results</li>
            <li>🌍 <b>Geocoding + Classification</b> enriched a sample property</li>
            <li>🤖 <b>AI classify</b> analyzed "Office space in Miami" with request/response</li>
            <li>⚠️ A simulated <b>slow query warning</b> and ‼️ <b>timeout alert</b></li>
        </ul>
    </div>

    <p>Each operation shows its <b>duration</b>, <b>memory usage</b>, <b>nested children</b>,
    and <b>typed parameters</b> (strings in green, numbers in teal, booleans in blue).
    Long values are truncated with a <code>[+]</code> expand button.</p>

    <h3>🚀 More to explore</h3>
    <ul>
        <li>📡 <a href="/api/search?q=miami+office">/api/search?q=miami+office</a> — JSON API
            <span style="color:#888">(profiler key in <code>X-Profiler-Key</code> response header)</span></li>
        <li>👥 <a href="/threaded">/threaded</a> — multi-worker demo
            <span style="color:#888">(two iframe workers share the same profiler task-id)</span></li>
    </ul>

    <p style="color:#999; font-size:13px; margin-top:24px">
        ↓ Scroll down or click the red bar to see the profiler panel.
        Click <b>open ↗</b> to view the report in a standalone page.
    </p>
</body>
</html>'''


@app.route('/threaded')
def threaded():
    with Xray.i('page::threaded'):
        Xray.info('request-start', {'ip': request.remote_addr})
        sim_cache_lookup('page:threaded')
        listings = sim_db_query('listings', 'state=NY')
        results = sim_es_search('listing', 'boston warehouse')

    task_id = request.environ['profiler_task_id']
    request.environ['profiler_wait_iframes'] = True  # load profiler after all iframes done

    return f'''<!DOCTYPE html>
<html>
<head><title>Xray — Multi-Worker</title></head>
<body style="font-family: sans-serif; padding: 20px; background: #f5f5f5; margin-bottom: 60vh">
    <h1>📊 Multi-Worker Example</h1>
    <p>Two background workers share the same profiler task-id. Panel loads after 4s.</p>
    <ul>
        <li>DB query returned {len(listings)} rows</li>
        <li>ES search returned {results['hits']} hits</li>
    </ul>

    <h3>Background workers (task-id: {task_id})</h3>
    <div style="display:flex;gap:12px">
        <iframe src="/worker?task_id={task_id}&name=enricher" style="width:200px;height:30px;border:1px solid #ddd;border-radius:4px"></iframe>
        <iframe src="/worker?task_id={task_id}&name=classifier" style="width:200px;height:30px;border:1px solid #ddd;border-radius:4px"></iframe>
    </div>

    <p><a href="/">← back to single-process</a></p>
</body>
</html>'''


@app.route('/api/search')
def api_search():
    q = request.args.get('q', 'commercial real estate')

    with Xray.i('api::search', {'query': q}):
        sim_cache_lookup(f'search:{q}')
        results = sim_es_search('listing', q)

        with Xray.i('enrich'):
            for i in range(min(3, results['hits'])):
                sim_db_query('listing_details', f'id={i}')

        if random.random() > 0.5:
            classification = sim_ai_classify(q)
        else:
            classification = None

        Xray.warning('slow-upstream', {'latency_ms': random.randint(80, 300)})

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

    Xray.init(r, task_id, thread_id=worker_name)

    with Xray.i(f'{worker_name}::run'):
        # Simulate worker doing its own DB + API work
        sim_db_query('worker_queue', f'worker={worker_name}')

        with Xray.i(f'{worker_name}::process'):
            time.sleep(random.uniform(0.3, 0.8))

            if worker_name == 'enricher':
                with Xray.i('Geo::batch_geocode', {'count': 25}) as span:
                    time.sleep(random.uniform(0.2, 0.5))
                    span.data({'resolved': 23, 'failed': 2})
                Xray.info('enricher-checkpoint', {'processed': 25})

            elif worker_name == 'classifier':
                sim_ai_classify('Warehouse with loading dock in Boston industrial district near I-93')
                sim_es_search('listing', 'similar:warehouse:boston')
                Xray.warning('model-fallback', {'primary': 'gpt-4o', 'fallback': 'gpt-4o-mini', 'reason': 'rate-limited'})

        Xray.info(f'{worker_name}::done')

    Xray.finish()
    return f'<html><body style="font:11px monospace;color:#888">✓ {worker_name} done</body></html>'


if __name__ == '__main__':
    print('Xray Web Demo')
    print('  http://localhost:5000/')
    print('  http://localhost:5000/api/search?q=miami+office')
    print()
    app.run(host='0.0.0.0', port=5000, debug=True)
