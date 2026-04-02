"""
HTML renderer for Python Xray.

Renders profiler entries as a styled HTML table (Call Tree format).
Two modes:
  - render() — full HTML report (standalone or embeddable)
  - snippet() — JS snippet to inject into page (async fetch)
"""

import json


def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _fmt_val(v) -> str:
    """Format a value with type-specific coloring."""
    if v is None:
        return '<span class="v-null">null</span>'
    if isinstance(v, bool):
        return f'<span class="v-bool">{"true" if v else "false"}</span>'
    if isinstance(v, int):
        return f'<span class="v-int">{v}</span>'
    if isinstance(v, float):
        return f'<span class="v-int">{v}</span>'
    if isinstance(v, str):
        if len(v) > 80:
            short = _esc(v[:80])
            full = _esc(v[80:])
            return f'<span class="v-str">{short}<span class="v-ellipsis" title="{full}">…</span></span>'
        return f'<span class="v-str">{_esc(v)}</span>'
    # array, dict — render as JSON
    return f'<span class="v-json">{_esc(json.dumps(v, separators=(", ", ": "), default=str))}</span>'


def _fmt_data(data: dict) -> str:
    """Format a data dict with typed key:value pairs."""
    if not data:
        return ''
    parts = []
    for k, v in data.items():
        parts.append(f'<b>{_esc(k)}</b>: {_fmt_val(v)}')
    return ', '.join(parts)


_TRUNC_ID = 0

def _truncatable(content: str, max_len: int = 200) -> str:
    """Wrap long content in a truncatable span with expand button."""
    global _TRUNC_ID
    # Check plain text length (strip HTML tags for length check)
    import re
    plain_len = len(re.sub(r'<[^>]+>', '', content))
    if plain_len <= max_len:
        return content
    _TRUNC_ID += 1
    trunc_id = f'trunc-{_TRUNC_ID}'
    return (f'<span class="truncated" id="{trunc_id}">{content}</span>'
            f' <span class="expand-btn" data-target="{trunc_id}">▸</span>')


def _time_class(ms: float, total_ms: float) -> str:
    pct = (ms / total_ms * 100) if total_ms else 0
    if pct > 30 or ms > 200:
        return 'time-red'
    if pct > 10 or ms > 100:
        return 'time-yellow'
    return ''


def _merged_duration_ms(spans: list) -> float:
    """Total covered time across overlapping spans, in ms."""
    intervals = sorted((e['start'], e['end']) for e in spans if e.get('end'))
    if not intervals:
        return 0.0
    merged = []
    cur_start, cur_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))
    return sum((end - start) * 1000 for start, end in merged)


def _fmt_metric(value: float) -> str:
    """Render a 1-decimal metric with a smaller fractional part."""
    whole, frac = f'{value:.1f}'.split('.')
    return f'{whole}<span class="metric-frac">.{frac}</span>'


CSS = '''
<style>
.profiler-report {
    font-family: 'SF Mono', 'Menlo', 'Consolas', monospace;
    font-size: 12px;
    background: #fff;
    border-top: 3px solid #f88;
    margin: 8px 0;
    overflow-x: auto;
}
.profiler-report h3 {
    text-align: left;
    margin: 6px 0;
    padding: 4px 6px;
    font-size: 13px;
    color: #333;
    background: #eef6ff;
    border-left: 3px solid #b7d7f5;
}
.profiler-report table {
    width: 100%;
    border-collapse: collapse;
}
.profiler-report th {
    background: #f5f5f5;
    border-bottom: 2px solid #ccc;
    padding: 3px 6px;
    text-align: left;
    font-size: 11px;
    color: #666;
}
.profiler-report th.r { text-align: right; }
.profiler-report td {
    padding: 2px 6px;
    border-bottom: 1px solid #eee;
    vertical-align: top;
    white-space: nowrap;
}
.profiler-report td.r { text-align: right; }
.profiler-report tr:hover { background: #f8f8f0; }
.profiler-report .block { font-weight: bold; color: #333; }
.profiler-report .params { color: #888; font-weight: normal; max-width: 700px; word-break: break-all; white-space: normal; }
.profiler-report .metric-frac { font-size: 8px; vertical-align: 1px; }
.profiler-report .truncated { display: inline; }
.profiler-report .truncated:not(.expanded) { display: -webkit-inline-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; max-width: 100%; vertical-align: top; word-break: break-all; }
.profiler-report .truncated.expanded { word-break: break-all; }
.profiler-report .expand-btn { color: #4fc3f7; cursor: pointer; font-size: 11px; user-select: none; }
.profiler-report .v-str { color: #6a9955; font-size: 11px; }
.profiler-report .v-ellipsis { color: #e88; cursor: help; font-weight: bold; }
.profiler-report .v-int { color: #098658; }
.profiler-report .v-bool { color: #569cd6; }
.profiler-report .v-null { color: #aaa; font-style: italic; }
.profiler-report .v-json { color: #888; font-size: 11px; }
.profiler-report .start-col { color: #aaa; cursor: help; font-size: 10px; }
.profiler-report .indent { color: #ccc; }
.profiler-report .time-red { background: #fcc; }
.profiler-report .time-yellow { background: #ffc; }
.profiler-report .warn-row { color: #b8860b; }
.profiler-report .warn-row .block { color: #b8860b; }
.profiler-report .alert-row { color: #c00; font-weight: bold; }
.profiler-report .alert-row .block { color: #c00; }
.profiler-report .info-row { color: #999; }
.profiler-report .info-row .block { color: #888; font-weight: normal; }
.profiler-report .event-badge { display: inline-block; padding: 0 5px; border-radius: 3px; color: #fff; font-size: 10px; line-height: 1.4; margin-right: 4px; vertical-align: 1px; }
.profiler-report .event-badge.warn { background: #f90; }
.profiler-report .event-badge.alert { background: #c00; }
.profiler-report .thread-sep td { background: #e8e8ff; font-weight: bold; padding: 4px 6px; }
.profiler-report .coverage-wrap { margin: 2px 0 1px; }
.profiler-report .coverage-thread { margin: 1px 0; }
.profiler-report .coverage-row { display: flex; align-items: center; gap: 4px; margin: 0; }
.profiler-report .coverage-track { position: relative; flex: 1; height: 6px; border: 1px solid #ddd; background: #fafafa; border-radius: 999px; overflow: hidden; }
.profiler-report .coverage-fill { position: absolute; top: 0; bottom: 0; border-radius: 999px; min-width: 2px; opacity: 0.95; }
.profiler-report .coverage-meta { width: 38px; text-align: right; color: #999; font-size: 9px; line-height: 1; }
.profiler-report .coverage-meta.blank { color: transparent; user-select: none; }
</style>
'''


def render(entries: list, task_id: str = '') -> str:
    global _TRUNC_ID
    if not entries:
        return '<div class="profiler-report"><h3>No profiler data.</h3></div>'

    # Sort by start time (spans pushed to Redis on exit, not entry)
    entries.sort(key=lambda e: e.get('start') or 0)

    threads = {}
    for e in entries:
        threads.setdefault(e.get('thread_id', '?'), []).append(e)

    spans = [e for e in entries if e['type'] == 'span' and e.get('end')]
    first_start = min((e.get('start') or 9e12) for e in entries)
    last_end = max((e.get('end') or 0) for e in spans) if spans else first_start
    total_ms = (last_end - first_start) * 1000

    html = CSS
    html += '<div class="profiler-report">\n'
    html += '<table>\n'
    multi = len(threads) > 1
    # Assign colors to workers
    worker_colors = ['#fff', '#f0f4ff', '#fff8f0', '#f0fff4', '#fff0f8', '#f8f0ff', '#f0ffff', '#fffff0']
    worker_bg = {}
    for i, tid in enumerate(sorted(threads)):
        worker_bg[tid] = worker_colors[i % len(worker_colors)]

    cols = 5
    html += '<tr><th class="r" title="% from start">%%</th><th>Block</th><th>Params</th><th class="r"><small>Mem(MB)</small></th><th class="r">Time(ms)</th><th class="r" title="% when finished">%%</th></tr>\n'

    for tid in sorted(threads):
        thread_entries = threads[tid]
        thread_spans = [e for e in thread_entries if e['type'] == 'span' and e.get('end')]
        # Use root span (depth=0) duration as wall-clock time, fallback to max end - min start
        root_spans = [e for e in thread_spans if e.get('depth', 0) == 0]
        if root_spans:
            thread_total = (root_spans[0]['end'] - root_spans[0]['start']) * 1000
        else:
            t_start = min((e['start'] for e in thread_spans), default=0)
            t_end = max((e['end'] for e in thread_spans), default=0)
            thread_total = (t_end - t_start) * 1000 if t_start else 0

        # Thread header — always shown, uses root span data
        root = root_spans[0] if root_spans else None
        thread_start_offset = ((root['start'] - first_start) * 1000) if root else 0
        thread_start_pct = (thread_start_offset / (total_ms or 1)) * 100
        thread_end_offset = ((root['end'] - first_start) * 1000) if root and root.get('end') else thread_start_offset
        thread_end_pct = (thread_end_offset / (total_ms or 1)) * 100
        thread_mem = root.get('mem_kb', 0) if root else 0
        thread_mem_str = _fmt_metric(thread_mem / 1024) if thread_mem else ''
        tcls = _time_class(thread_total, total_ms)
        bg = f' style="background:{worker_bg[tid]}"' if multi else ''
        html += f'<tr class="thread-sep"{bg}>'
        html += f'<td class="r start-col" title="{thread_start_offset:,.1f}ms from start">{_fmt_metric(thread_start_pct)}</td>'
        html += f'<td><b>{_esc(tid)}</b></td>'
        html += f'<td class="params">{len(thread_entries)} entries</td>'
        html += f'<td class="r">{thread_mem_str}</td>'
        html += f'<td class="r {tcls}">{_fmt_metric(thread_total)}</td>'
        html += f'<td class="r start-col" title="{thread_end_offset:,.1f}ms from start">{_fmt_metric(thread_end_pct)}</td>'
        html += f'</tr>\n'

        for e in thread_entries:
            # Skip root span (depth=0) — its data is shown in thread header
            if e.get('depth', 0) == 0 and e['type'] == 'span':
                continue
            depth = e.get('depth', 0) - 1 if e.get('depth', 0) > 0 else 0  # shift depth since root is hidden
            indent = '<span class="indent">' + '│ ' * depth + '</span>' if depth else ''
            data = e.get('data') or {}
            start_offset = ((e.get('start') or first_start) - first_start) * 1000

            if e['type'] == 'span' and e.get('end'):
                ms = (e['end'] - e['start']) * 1000
                pct = (ms / total_ms * 100) if total_ms else 0
                tcls = _time_class(ms, total_ms)

                # Params: all data in one cell, request/response expandable
                inline = {k: v for k, v in data.items() if k not in ('request', 'response')}
                params = _fmt_data(inline)
                for k in ('request', 'response'):
                    if k in data:
                        val = _fmt_data(data[k]) if isinstance(data[k], dict) else _fmt_val(data[k])
                        _TRUNC_ID += 1
                        t_id = f'trunc-{_TRUNC_ID}'
                        prefix = '<br>' if params else ''
                        params += (f'{prefix}<span class="expand-btn" data-target="{t_id}">[+]</span> '
                                   f'<b>{k}:</b> <span class="truncated" id="{t_id}">{val}</span>')

                mem_kb = e.get('mem_kb') or 0
                mem_mb = _fmt_metric(mem_kb / 1024) if mem_kb else ''

                end_offset = (e['end'] - first_start) * 1000
                start_pct = (start_offset / (total_ms or 1)) * 100
                end_pct = (end_offset / (total_ms or 1)) * 100

                bg = f' style="background:{worker_bg[tid]}"' if multi else ''

                html += f'<tr{bg}>'
                html += f'<td class="r start-col" title="{start_offset:,.1f}ms from start">{_fmt_metric(start_pct)}</td>'
                html += f'<td>{indent}<span class="block">{_esc(e["name"])}</span></td>'
                html += f'<td class="params">{params}</td>'
                html += f'<td class="r">{mem_mb}</td>'
                html += f'<td class="r {tcls}">{_fmt_metric(ms)}</td>'
                html += f'<td class="r start-col" title="{end_offset:,.1f}ms from start">{_fmt_metric(end_pct)}</td>'
                html += f'</tr>\n'

            elif e['type'] == 'warning':
                start_pct = (start_offset / (total_ms or 1)) * 100
                params = _truncatable(_fmt_data(data)) if data else ''
                rest = 2  # remaining cols after %% and Block
                bg = f' style="background:{worker_bg[tid]}"' if multi else ''
                html += f'<tr class="warn-row"{bg}>'
                html += f'<td class="r start-col" title="{start_offset:,.1f}ms from start">{_fmt_metric(start_pct)}</td>'
                html += f'<td>{indent}<span class="event-badge warn">⚠</span><span class="block">{_esc(e["name"])}</span></td>'
                html += f'<td class="params">{params}</td>'
                html += f'<td colspan="{rest}"></td></tr>\n'

            elif e['type'] == 'alert':
                start_pct = (start_offset / (total_ms or 1)) * 100
                params = _truncatable(_fmt_data(data)) if data else ''
                rest = 2
                bg = f' style="background:{worker_bg[tid]}"' if multi else ''
                html += f'<tr class="alert-row"{bg}>'
                html += f'<td class="r start-col" title="{start_offset:,.1f}ms from start">{_fmt_metric(start_pct)}</td>'
                html += f'<td>{indent}<span class="event-badge alert">‼</span><span class="block">{_esc(e["name"])}</span></td>'
                html += f'<td class="params">{params}</td>'
                html += f'<td colspan="{rest}"></td></tr>\n'

            else:  # info
                start_pct = (start_offset / (total_ms or 1)) * 100
                params = _truncatable(_fmt_data(data)) if data else ''
                rest = 2
                bg = f' style="background:{worker_bg[tid]}"' if multi else ''
                html += f'<tr class="info-row"{bg}>'
                html += f'<td class="r start-col" title="{start_offset:,.1f}ms from start">{_fmt_metric(start_pct)}</td>'
                html += f'<td>{indent}· <span class="block">{_esc(e["name"])}</span></td>'
                html += f'<td class="params">{params}</td>'
                html += f'<td colspan="{rest}"></td></tr>\n'

    html += '</table>\n'

    # Top 5 slowest
    non_root = [e for e in spans if e.get('depth', 0) > 0]
    top = sorted(non_root, key=lambda e: e['end'] - e['start'], reverse=True)[:5]
    if top:
        html += '<h3>🔥 Top 5 slowest</h3>\n<table>\n'
        html += '<tr><th>Block</th><th class="r">Time(ms)</th>'
        if multi:
            html += '<th>Worker</th>'
        html += '</tr>\n'
        for e in top:
            ms = (e['end'] - e['start']) * 1000
            tcls = _time_class(ms, total_ms)
            html += f'<tr><td class="block">{_esc(e["name"])}</td>'
            html += f'<td class="r {tcls}">{_fmt_metric(ms)}</td>'
            if multi:
                html += f'<td class="params">{_esc(e.get("thread_id", "?"))}</td>'
            html += f'</tr>\n'
        html += '</table>\n'

    html += '<h3>▦ Coverage</h3>\n<div class="coverage-wrap">\n'
    coverage_palette = ['#4fc3f7', '#81c784', '#ffb74d', '#e57373', '#ba68c8', '#ffd54f', '#4db6ac', '#90a4ae']
    for tid in sorted(threads):
        thread_spans = [
            e for e in threads[tid]
            if e['type'] == 'span' and e.get('end') and e.get('depth', 0) > 0
        ]
        if not thread_spans:
            continue
        thread_busy_ms = _merged_duration_ms(thread_spans)
        coverage_pct = (thread_busy_ms / (total_ms or 1)) * 100
        max_depth = max(e.get('depth', 0) for e in thread_spans)
        html += '<div class="coverage-thread">\n'
        for depth in range(1, max_depth + 1):
            level_spans = [e for e in thread_spans if e.get('depth', 0) == depth]
            if not level_spans:
                continue
            html += '<div class="coverage-row">'
            title = f'{tid}: {thread_busy_ms:.1f}ms profiled busy time ({coverage_pct:.1f}% of total)' if depth == 1 else f'{tid}: nested spans depth {depth}'
            html += f'<div class="coverage-track" title="{title}">'
            for i, e in enumerate(level_spans):
                left = (((e['start'] - first_start) * 1000) / (total_ms or 1)) * 100
                width = (((e['end'] - e['start']) * 1000) / (total_ms or 1)) * 100
                color = coverage_palette[(depth + i) % len(coverage_palette)]
                label = _esc(e["name"])
                ms = (e['end'] - e['start']) * 1000
                html += f'<span class="coverage-fill" title="{label} ({ms:.1f}ms)" style="left:{left:.3f}%;width:max({width:.3f}%, 2px);background:{color}"></span>'
            html += '</div>'
            meta_class = 'coverage-meta' if depth == 1 else 'coverage-meta blank'
            meta_value = f'{coverage_pct:.1f}%' if depth == 1 else '&nbsp;'
            html += f'<div class="{meta_class}">{meta_value}</div>'
            html += '</div>\n'
        html += '</div>\n'
    html += '</div>\n'

    html += '</div>\n'
    return html


def render_from_redis(task_id: str, redis_client) -> str:
    entries = [json.loads(e) for e in redis_client.lrange(f'xray:{task_id}', 0, -1)]
    return render(entries, task_id)


def _snippet_load_strategy(delay_ms: int, wait_iframes: bool) -> str:
    if wait_iframes:
        return '''var iframes = document.querySelectorAll("iframe");
    var pending = iframes.length;
    if (pending === 0) { loadProfiler(); }
    else {
        iframes.forEach(function(f) {
            f.addEventListener("load", function() {
                pending--;
                if (pending <= 0) loadProfiler();
            });
        });
        setTimeout(loadProfiler, 10000);
    }'''
    if delay_ms:
        return f'setTimeout(loadProfiler, {delay_ms});'
    return 'loadProfiler();'


def snippet(task_id: str, endpoint: str = '/_profiler', delay_ms: int = 0, wait_iframes: bool = False, elapsed_ms: float = 0, warnings: int = 0, alerts: int = 0) -> str:
    if elapsed_ms > 1000:
        time_str = f' | <span style="background:#ff0;color:#c00;padding:0 4px;border-radius:2px">{elapsed_ms:.0f}ms</span>'
    elif elapsed_ms:
        time_str = f' | <span style="color:#000">{elapsed_ms:.0f}ms</span>'
    else:
        time_str = ''
    badges = ''
    if alerts:
        badges += f' <span style="background:#c00;color:#fff;padding:0 5px;border-radius:3px">‼ {alerts}</span>'
    if warnings:
        badges += f' <span style="background:#f90;color:#fff;padding:0 5px;border-radius:3px">⚠ {warnings}</span>'
    return f'''
<div id="profiler-container" style="position:fixed;bottom:0;left:0;right:0;max-height:50vh;overflow:auto;z-index:99999;box-shadow:0 -2px 10px rgba(0,0,0,0.3)">
    <div id="profiler-bar" style="background:#f88;padding:2px 8px;font:bold 12px monospace;color:#fff;cursor:pointer;text-align:right">
        📊 Xray: {task_id}{time_str}{badges}
        <a href="{endpoint}?k={task_id}" target="_blank" style="color:#fff;margin-left:8px">open ↗</a>
    </div>
    <div id="profiler-body"></div>
</div>
<script>
(function() {{
    document.getElementById("profiler-bar").addEventListener("click", function(e) {{
        if (e.target.tagName === "A") return;
        var b = document.getElementById("profiler-body");
        b.style.display = b.style.display === "none" ? "block" : "none";
    }});
    function loadProfiler() {{
        fetch("{endpoint}?k={task_id}")
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{ document.getElementById("profiler-body").innerHTML = html; }});
    }}
    {_snippet_load_strategy(delay_ms, wait_iframes)}
    document.addEventListener("click", function(e) {{
        if (e.target.classList.contains("expand-btn")) {{
            var t = document.getElementById(e.target.getAttribute("data-target"));
            if (t) {{
                t.classList.toggle("expanded");
                var exp = t.classList.contains("expanded");
                var txt = e.target.textContent;
                if (txt === "[+]" || txt === "[-]") e.target.textContent = exp ? "[-]" : "[+]";
                else e.target.textContent = exp ? "\\u25be" : "\\u25b8";
            }}
        }}
    }});
}})();
</script>
'''
