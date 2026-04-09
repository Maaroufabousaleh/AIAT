"""LLM Gateway Observability Dashboard — embedded HTML/JS single-page app.

Serves a rich interactive dashboard with:
- Real-time request timeline (line chart)
- Per-model health gauges
- Latency histograms
- Rate-limit utilisation bars
- Routing score radar chart
- Audit event stream
- Token/cost accumulators
- Auto-refresh every 5 seconds

All data is fetched from the JSON API endpoints created by
``routes_observability.py``.  The HTML is self-contained (no build
step) and loads Chart.js from CDN.
"""

from __future__ import annotations

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LLM Gateway — Observability Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f1117; --bg2: #161b22; --bg3: #1c2333;
    --fg: #e6edf3; --fg2: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --orange: #db6d28; --purple: #bc8cff; --border: #30363d;
    --radius: 10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--fg); line-height: 1.5; }
  .header { background: var(--bg2); border-bottom: 1px solid var(--border);
            padding: 14px 24px; display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 18px; font-weight: 600; }
  .header .status { margin-left: auto; font-size: 13px; color: var(--fg2); }
  .header .dot { width: 8px; height: 8px; border-radius: 50%;
                 display: inline-block; margin-right: 6px; }
  .dot.live { background: var(--green); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

  /* Layout */
  .grid { display: grid; gap: 16px; padding: 20px 24px;
          grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
  .card { background: var(--bg2); border: 1px solid var(--border);
          border-radius: var(--radius); padding: 16px; overflow: hidden; }
  .card.wide { grid-column: span 2; }
  .card.full { grid-column: 1 / -1; }
  .card h2 { font-size: 14px; font-weight: 600; margin-bottom: 10px;
             color: var(--accent); text-transform: uppercase; letter-spacing: .5px; }
  .card h3 { font-size: 13px; color: var(--fg2); margin-bottom: 6px; }

  /* KPI strip */
  .kpi-strip { display: flex; gap: 12px; flex-wrap: wrap; }
  .kpi { background: var(--bg3); border-radius: 8px; padding: 12px 16px;
         flex: 1; min-width: 140px; text-align: center; }
  .kpi .value { font-size: 26px; font-weight: 700; }
  .kpi .label { font-size: 11px; color: var(--fg2); text-transform: uppercase; letter-spacing: .6px; }
  .kpi.green .value { color: var(--green); }
  .kpi.yellow .value { color: var(--yellow); }
  .kpi.red .value { color: var(--red); }
  .kpi.accent .value { color: var(--accent); }
  .kpi.purple .value { color: var(--purple); }

  /* Tables */
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--border); }
  th { color: var(--fg2); font-weight: 600; font-size: 11px; text-transform: uppercase; }
  td { font-variant-numeric: tabular-nums; }
  tr:hover td { background: var(--bg3); }

  /* Health badge */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
           font-size: 11px; font-weight: 600; }
  .badge.good { background: rgba(63,185,80,.15); color: var(--green); }
  .badge.warn { background: rgba(210,153,34,.15); color: var(--yellow); }
  .badge.bad  { background: rgba(248,81,73,.15); color: var(--red); }

  /* Progress bars */
  .bar-bg { background: var(--bg3); border-radius: 4px; height: 8px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 4px; transition: width .5s ease; }
  .bar-fill.green { background: var(--green); }
  .bar-fill.yellow { background: var(--yellow); }
  .bar-fill.red { background: var(--red); }

  /* Event log */
  .event-log { max-height: 260px; overflow-y: auto; font-size: 12px; }
  .event-log::-webkit-scrollbar { width: 6px; }
  .event-log::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  .evt { padding: 5px 8px; border-bottom: 1px solid var(--border);
         display: grid; grid-template-columns: 80px 1fr 70px 60px 70px; gap: 6px; align-items: center; }
  .evt .time { color: var(--fg2); }
  .evt .model { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .evt .status-ok { color: var(--green); }
  .evt .status-err { color: var(--red); }
  .evt .status-rl { color: var(--yellow); }

  /* Chart containers */
  .chart-container { position: relative; width: 100%; }
  .chart-container canvas { width: 100% !important; }

  /* Tabs */
  .tabs { display: flex; gap: 1px; margin-bottom: 12px; }
  .tab { padding: 6px 14px; font-size: 12px; background: var(--bg3);
         border: none; color: var(--fg2); cursor: pointer; }
  .tab:first-child { border-radius: 6px 0 0 6px; }
  .tab:last-child { border-radius: 0 6px 6px 0; }
  .tab.active { background: var(--accent); color: #fff; }

  /* Controls */
  .controls { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
  select { background: var(--bg3); color: var(--fg); border: 1px solid var(--border);
           border-radius: 6px; padding: 4px 8px; font-size: 12px; }

  /* Loading */
  .loading { text-align: center; padding: 40px; color: var(--fg2); }

  /* Responsive */
  @media (max-width: 768px) {
    .grid { grid-template-columns: 1fr; }
    .card.wide { grid-column: span 1; }
  }
</style>
</head>
<body>
<div class="header">
  <h1>&#x1F50D; LLM Gateway Observability</h1>
  <div class="status"><span class="dot live"></span>Live — auto-refresh <select id="refreshInterval">
    <option value="5">5s</option><option value="10">10s</option>
    <option value="30">30s</option><option value="0">Off</option>
  </select></div>
</div>

<div class="grid">
  <!-- KPI row -->
  <div class="card full" id="kpi-card">
    <div class="kpi-strip" id="kpi-strip">
      <div class="kpi accent"><div class="value" id="kpi-requests">—</div><div class="label">Total Requests</div></div>
      <div class="kpi green"><div class="value" id="kpi-tokens">—</div><div class="label">Total Tokens</div></div>
      <div class="kpi purple"><div class="value" id="kpi-cost">—</div><div class="label">Total Cost</div></div>
      <div class="kpi yellow"><div class="value" id="kpi-errors">—</div><div class="label">Errors</div></div>
      <div class="kpi accent"><div class="value" id="kpi-models">—</div><div class="label">Models</div></div>
      <div class="kpi green"><div class="value" id="kpi-health">—</div><div class="label">Avg Health</div></div>
    </div>
  </div>

  <!-- Request timeline chart -->
  <div class="card wide">
    <h2>Request Timeline</h2>
    <div class="controls">
      <select id="timeline-window">
        <option value="minute">1 Minute</option>
        <option value="hour" selected>1 Hour</option>
        <option value="day">24 Hours</option>
      </select>
      <select id="timeline-model">
        <option value="__all__">All Models</option>
      </select>
    </div>
    <div class="chart-container" style="height:220px"><canvas id="timeline-chart"></canvas></div>
  </div>

  <!-- Status breakdown donut -->
  <div class="card">
    <h2>Status Breakdown</h2>
    <div class="chart-container" style="height:220px"><canvas id="status-chart"></canvas></div>
  </div>

  <!-- Model Health Table -->
  <div class="card wide">
    <h2>Model Health &amp; Performance</h2>
    <div style="max-height:300px; overflow-y:auto;">
      <table id="health-table">
        <thead><tr><th>Model</th><th>Health</th><th>Requests</th><th>Errors</th>
               <th>Avg Latency</th><th>p95 Latency</th><th>Tokens</th><th>Cost</th><th>TPS</th></tr></thead>
        <tbody id="health-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Latency histogram -->
  <div class="card">
    <h2>Latency Distribution</h2>
    <div class="controls">
      <select id="hist-model"></select>
    </div>
    <div class="chart-container" style="height:200px"><canvas id="latency-chart"></canvas></div>
  </div>

  <!-- Rate Limits -->
  <div class="card wide">
    <h2>Rate Limit Utilisation</h2>
    <div class="controls">
      <select id="rl-model"></select>
    </div>
    <div id="rl-grid" style="display:grid; grid-template-columns:repeat(3,1fr); gap:10px;"></div>
  </div>

  <!-- Routing Scores -->
  <div class="card">
    <h2>Routing Scores</h2>
    <div class="chart-container" style="height:260px"><canvas id="routing-chart"></canvas></div>
  </div>

  <!-- Token/Cost time series -->
  <div class="card wide">
    <h2>Token &amp; Cost Trends</h2>
    <div class="chart-container" style="height:200px"><canvas id="token-chart"></canvas></div>
  </div>

  <!-- Audit Event Stream -->
  <div class="card full">
    <h2>Recent Audit Events</h2>
    <div class="evt" style="font-weight:600; color:var(--fg2); font-size:11px;">
      <span>TIME</span><span>MODEL</span><span>STATUS</span><span>TOKENS</span><span>LATENCY</span>
    </div>
    <div class="event-log" id="event-log"></div>
  </div>
</div>

<script>
// ── globals ──────────────────────────────────────────────────────────
const BASE = window.location.pathname.replace(/\/ui\/?$/, '');
let refreshTimer = null;
let charts = {};

// ── helpers ──────────────────────────────────────────────────────────
async function api(path) {
  const r = await fetch(BASE + path);
  return r.ok ? r.json() : null;
}
function fmt(n, d=0) {
  if (n == null) return '—';
  if (n >= 1e9) return (n/1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return d > 0 ? n.toFixed(d) : String(n);
}
function fmtCost(n) { return n == null ? '—' : '$' + n.toFixed(6); }
function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
function healthBadge(h) {
  if (h >= 0.8) return `<span class="badge good">${(h*100).toFixed(0)}%</span>`;
  if (h >= 0.5) return `<span class="badge warn">${(h*100).toFixed(0)}%</span>`;
  return `<span class="badge bad">${(h*100).toFixed(0)}%</span>`;
}
function barColor(pct) { return pct > 0.85 ? 'red' : pct > 0.6 ? 'yellow' : 'green'; }

function createOrUpdate(id, type, data, options) {
  if (charts[id]) {
    charts[id].data = data;
    charts[id].options = { ...charts[id].options, ...options };
    charts[id].update('none');
  } else {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    charts[id] = new Chart(ctx, { type, data, options });
  }
}

// Theme defaults for Chart.js
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#30363d';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif';
Chart.defaults.font.size = 11;

// ── data loaders ─────────────────────────────────────────────────────

async function loadKPIs() {
  const s = await api('/audit/summary');
  if (!s) return;
  document.getElementById('kpi-requests').textContent = fmt(s.total_requests);
  document.getElementById('kpi-tokens').textContent = fmt(s.total_tokens);
  document.getElementById('kpi-cost').textContent = fmtCost(s.total_cost_usd);
  document.getElementById('kpi-errors').textContent = fmt(s.total_errors);
  const modelCount = Object.keys(s.per_model || {}).length;
  document.getElementById('kpi-models').textContent = modelCount;
}

async function loadHealth() {
  const d = await api('/metrics');
  if (!d || !d.models) return;

  // Update avg health KPI
  const models = Object.keys(d.models);
  const healthScores = models.map(m => d.models[m].health_score ?? 1);
  const avgHealth = healthScores.length ? healthScores.reduce((a,b)=>a+b,0)/healthScores.length : 1;
  document.getElementById('kpi-health').textContent = (avgHealth*100).toFixed(0) + '%';

  // Update model selector dropdowns
  for (const sel of ['timeline-model', 'hist-model', 'rl-model']) {
    const el = document.getElementById(sel);
    const cur = el.value;
    const hasAll = sel === 'timeline-model';
    el.innerHTML = hasAll ? '<option value="__all__">All Models</option>' : '';
    models.forEach(m => { el.innerHTML += `<option value="${m}">${m}</option>`; });
    if (cur && [...el.options].some(o => o.value === cur)) el.value = cur;
  }

  // Health table
  const tbody = document.getElementById('health-tbody');
  let html = '';
  for (const m of models) {
    const hourStats = d.models[m].hour || {};
    const h = d.models[m].health_score ?? 1;
    html += `<tr>
      <td title="${m}" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${m}</td>
      <td>${healthBadge(h)}</td>
      <td>${fmt(hourStats.requests)}</td>
      <td>${fmt(hourStats.errors)}</td>
      <td>${(hourStats.latency?.avg || 0).toFixed(3)}s</td>
      <td>${(hourStats.latency?.p95 || 0).toFixed(3)}s</td>
      <td>${fmt(hourStats.tokens?.total || 0)}</td>
      <td>${fmtCost(hourStats.cost_usd)}</td>
      <td>${fmt(hourStats.tokens_per_second, 1)}</td>
    </tr>`;
  }
  tbody.innerHTML = html || '<tr><td colspan="9" style="text-align:center;color:var(--fg2)">No data yet</td></tr>';
}

async function loadTimeline() {
  const window = document.getElementById('timeline-window').value;
  const model = document.getElementById('timeline-model').value;
  const path = model === '__all__'
    ? `/metrics/time-series?window=${window}`
    : `/metrics/time-series/${model}?window=${window}`;
  const series = await api(path);
  if (!series) return;

  const labels = series.map(p => fmtTime(p.t));
  createOrUpdate('timeline-chart', 'line', {
    labels,
    datasets: [
      { label: 'Requests', data: series.map(p => p.requests),
        borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,.1)',
        fill: true, tension: 0.3, borderWidth: 2, pointRadius: 0 },
      { label: 'Errors', data: series.map(p => p.errors),
        borderColor: '#f85149', backgroundColor: 'rgba(248,81,73,.1)',
        fill: true, tension: 0.3, borderWidth: 2, pointRadius: 0 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    scales: {
      x: { display: true, ticks: { maxTicksLimit: 8 } },
      y: { display: true, beginAtZero: true }
    },
    plugins: { legend: { position: 'top', labels: { boxWidth: 12 } } }
  });
}

async function loadStatusBreakdown() {
  const data = await api('/audit/status-breakdown');
  if (!data) return;

  const labels = Object.keys(data);
  const values = Object.values(data);
  const colors = labels.map(l => {
    if (l === 'success') return '#3fb950';
    if (l === 'error') return '#f85149';
    if (l === 'rate_limited') return '#d29922';
    if (l === 'timeout') return '#db6d28';
    return '#8b949e';
  });

  createOrUpdate('status-chart', 'doughnut', {
    labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }]
  }, {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { position: 'right', labels: { boxWidth: 10 } } },
    cutout: '65%',
  });
}

async function loadLatencyHist() {
  const model = document.getElementById('hist-model').value;
  if (!model) return;
  const data = await api(`/metrics/${model}/latency-histogram`);
  if (!data || !data.bins) return;

  const labels = data.bins.map(b => b.toFixed(3) + 's');
  createOrUpdate('latency-chart', 'bar', {
    labels,
    datasets: [{ label: 'Count', data: data.counts,
                 backgroundColor: 'rgba(188,140,255,.6)', borderRadius: 3 }]
  }, {
    responsive: true, maintainAspectRatio: false,
    scales: { x: { display: true, ticks: { maxTicksLimit: 10, font: { size: 10 } } },
              y: { beginAtZero: true } },
    plugins: { legend: { display: false } }
  });
}

async function loadRateLimits() {
  const model = document.getElementById('rl-model').value;
  if (!model) return;
  const data = await api(`/rate-limits/${model}`);
  if (!data) return;

  const grid = document.getElementById('rl-grid');
  let html = '';
  const usage = data.current_usage?.dimensions || {};
  for (const [dim, info] of Object.entries(usage)) {
    const util = info.utilisation || 0;
    const est = info.estimated_limit;
    const doc = info.documented_limit;
    const pct = Math.min(util * 100, 100);
    const c = barColor(util);
    html += `<div style="padding:8px;">
      <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
        <span style="font-weight:600;text-transform:uppercase;font-size:12px;">${dim}</span>
        <span style="font-size:11px;color:var(--fg2)">${info.current}/${est ?? doc ?? '?'}</span>
      </div>
      <div class="bar-bg"><div class="bar-fill ${c}" style="width:${pct}%"></div></div>
      <div style="font-size:10px;color:var(--fg2);margin-top:3px;">
        ${util != null ? (util*100).toFixed(1)+'%' : 'N/A'} used
        · conf ${(info.confidence*100).toFixed(0)}%
        ${doc ? '· doc '+fmt(doc) : ''}
      </div>
    </div>`;
  }
  grid.innerHTML = html || '<div style="color:var(--fg2)">No rate limit data</div>';
}

async function loadRouting() {
  const data = await api('/routing');
  if (!data || !data.ranking) return;

  const top = data.ranking.slice(0, 8);
  const labels = top.map(r => r.model.split('/').pop());
  createOrUpdate('routing-chart', 'bar', {
    labels,
    datasets: [
      { label: 'Health', data: top.map(r => r.health_score), backgroundColor: '#3fb950' },
      { label: 'Headroom', data: top.map(r => r.headroom_score), backgroundColor: '#58a6ff' },
      { label: 'Cost', data: top.map(r => r.cost_score), backgroundColor: '#bc8cff' },
      { label: 'Latency', data: top.map(r => r.latency_score), backgroundColor: '#d29922' },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    indexAxis: 'y',
    scales: { x: { stacked: true, max: 1 }, y: { stacked: true } },
    plugins: { legend: { position: 'top', labels: { boxWidth: 10, font: { size: 10 } } } }
  });
}

async function loadTokenChart() {
  const series = await api('/metrics/time-series?window=hour');
  if (!series) return;

  const labels = series.map(p => fmtTime(p.t));
  createOrUpdate('token-chart', 'line', {
    labels,
    datasets: [
      { label: 'Tokens', data: series.map(p => p.tokens), yAxisID: 'y',
        borderColor: '#3fb950', backgroundColor: 'rgba(63,185,80,.1)',
        fill: true, tension: 0.3, borderWidth: 2, pointRadius: 0 },
      { label: 'Cost ($)', data: series.map(p => p.cost_usd), yAxisID: 'y1',
        borderColor: '#bc8cff', borderWidth: 2, tension: 0.3, pointRadius: 0,
        borderDash: [4,4] },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    scales: {
      x: { ticks: { maxTicksLimit: 8 } },
      y: { position: 'left', beginAtZero: true, title: { display: true, text: 'Tokens' } },
      y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false },
            title: { display: true, text: 'Cost ($)' } },
    },
    plugins: { legend: { position: 'top', labels: { boxWidth: 12 } } }
  });
}

async function loadEvents() {
  const events = await api('/audit/events?last_n=50');
  if (!events) return;
  const log = document.getElementById('event-log');
  let html = '';
  for (const e of events) {
    const statusCls = e.status === 'success' ? 'status-ok'
                    : e.status === 'rate_limited' ? 'status-rl' : 'status-err';
    html += `<div class="evt">
      <span class="time">${fmtTime(e.timestamp)}</span>
      <span class="model" title="${e.resolved_model || e.model}">${e.resolved_model || e.model}</span>
      <span class="${statusCls}">${e.status}</span>
      <span>${fmt(e.total_tokens)}</span>
      <span>${e.latency_s.toFixed(3)}s</span>
    </div>`;
  }
  log.innerHTML = html || '<div style="padding:16px;text-align:center;color:var(--fg2)">No events yet</div>';
}

// ── refresh cycle ────────────────────────────────────────────────────

async function refreshAll() {
  await Promise.all([
    loadKPIs(), loadHealth(), loadTimeline(), loadStatusBreakdown(),
    loadLatencyHist(), loadRateLimits(), loadRouting(), loadTokenChart(),
    loadEvents(),
  ]);
}

function startRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  const secs = parseInt(document.getElementById('refreshInterval').value);
  if (secs > 0) refreshTimer = setInterval(refreshAll, secs * 1000);
}

document.getElementById('refreshInterval').addEventListener('change', startRefresh);
document.getElementById('timeline-window').addEventListener('change', loadTimeline);
document.getElementById('timeline-model').addEventListener('change', loadTimeline);
document.getElementById('hist-model').addEventListener('change', loadLatencyHist);
document.getElementById('rl-model').addEventListener('change', loadRateLimits);

// Initial load
refreshAll().then(startRefresh);
</script>
</body>
</html>"""
