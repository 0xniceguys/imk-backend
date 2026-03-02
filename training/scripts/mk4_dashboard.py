#!/usr/bin/env python3
"""
mk4_dashboard.py — Live Training Dashboard for MK4

Reads mk4_training_log.jsonl and mk4_training_stats.jsonl in real-time
and serves a beautiful live web dashboard at http://localhost:7860

Shows:
  - Episode reward over time (rolling avg + raw)
  - Win rate over time
  - Reward component breakdown (dealt, taken, spam, approach, survival)
  - Policy/value loss curves (when using MLP/LSTM agent)
  - Recent episode table

Usage:
    python3 training/scripts/mk4_dashboard.py
    # Then open: http://localhost:7860
"""
from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

N64_ROOT    = Path(__file__).resolve().parents[2]
TRAIN_LOG   = N64_ROOT / 'training/data/logs/mk4_training_log.jsonl'
STATS_LOG   = N64_ROOT / 'training/data/checkpoints/mk4_training_stats.jsonl'
PORT        = 7860


def read_jsonl(path: Path, max_lines: int = 2000) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text().strip().splitlines()
    return [json.loads(l) for l in lines[-max_lines:] if l.strip()]


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MK4 Training Monitor</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0a0a0f;
    --bg2: #12121a;
    --card: #1a1a2e;
    --border: #2a2a45;
    --accent: #e94560;
    --blue: #4FC3F7;
    --green: #00E5A0;
    --yellow: #FFD166;
    --purple: #BB86FC;
    --red: #ef476f;
    --text: #e0e0f0;
    --muted: #7070a0;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif;
         min-height: 100vh; padding: 24px; }
  h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.5px;
       background: linear-gradient(90deg, var(--accent), var(--purple));
       -webkit-background-clip: text; -webkit-text-fill-color: transparent;
       margin-bottom: 4px; }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
               padding: 20px; position: relative; overflow: hidden; }
  .stat-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
                        background: var(--accent-color, var(--accent)); }
  .stat-label { color: var(--muted); font-size: 0.75rem; text-transform: uppercase;
                letter-spacing: 1px; margin-bottom: 8px; }
  .stat-value { font-size: 2rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
  .stat-sub { color: var(--muted); font-size: 0.8rem; margin-top: 4px; }
  .charts { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 24px; }
  .charts2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
                padding: 20px; }
  .chart-title { font-size: 0.9rem; font-weight: 600; margin-bottom: 16px;
                  color: var(--text); letter-spacing: 0.3px; }
  .chart-wrap { position: relative; height: 200px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { color: var(--muted); text-align: left; padding: 8px 12px; font-weight: 600;
       text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.8px;
       border-bottom: 1px solid var(--border); }
  td { padding: 8px 12px; border-bottom: 1px solid #1f1f35; font-family: 'JetBrains Mono', monospace; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1f1f35; }
  .won { color: var(--green); } .lost { color: var(--red); }
  .pos { color: var(--green); } .neg { color: var(--red); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem;
           font-weight: 600; background: var(--green)22; color: var(--green); }
  .badge.red { background: var(--red)22; color: var(--red); }
  .live-dot { display: inline-block; width: 8px; height: 8px; background: var(--green);
               border-radius: 50%; margin-right: 6px;
               animation: pulse 1.5s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .header-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
  .refresh-info { color: var(--muted); font-size: 0.8rem; }
  .no-data { color: var(--muted); text-align: center; padding: 40px; font-size: 0.9rem; }
</style>
</head>
<body>
<div class="header-row">
  <div>
    <h1>⚔️ MK4 Training Monitor</h1>
    <div class="subtitle"><span class="live-dot"></span>Live — auto-refreshes every 3s</div>
  </div>
  <div class="refresh-info" id="lastUpdate">Loading...</div>
</div>

<div class="grid" id="statsGrid">
  <div class="stat-card" style="--accent-color: var(--green)">
    <div class="stat-label">Total Episodes</div>
    <div class="stat-value" id="totalEp">—</div>
    <div class="stat-sub" id="epSub">waiting for data</div>
  </div>
  <div class="stat-card" style="--accent-color: var(--yellow)">
    <div class="stat-label">Win Rate</div>
    <div class="stat-value" id="winRate">—</div>
    <div class="stat-sub" id="winSub">last 50 eps</div>
  </div>
  <div class="stat-card" style="--accent-color: var(--blue)">
    <div class="stat-label">Avg Reward (50)</div>
    <div class="stat-value" id="avgReward">—</div>
    <div class="stat-sub" id="rewardSub">rolling window</div>
  </div>
  <div class="stat-card" style="--accent-color: var(--purple)">
    <div class="stat-label">Gradient Updates</div>
    <div class="stat-value" id="gradUpdates">—</div>
    <div class="stat-sub">policy + value</div>
  </div>
</div>

<div class="charts">
  <div class="chart-card">
    <div class="chart-title">Episode Reward — raw + rolling avg (50)</div>
    <div class="chart-wrap"><canvas id="rewardChart"></canvas></div>
  </div>
  <div class="chart-card">
    <div class="chart-title">Win Rate % over time</div>
    <div class="chart-wrap"><canvas id="winChart"></canvas></div>
  </div>
</div>

<div class="charts2">
  <div class="chart-card">
    <div class="chart-title">Reward Components (per episode avg)</div>
    <div class="chart-wrap"><canvas id="termChart"></canvas></div>
  </div>
  <div class="chart-card">
    <div class="chart-title">Policy Loss + Entropy (LSTM/MLP)</div>
    <div class="chart-wrap"><canvas id="lossChart"></canvas></div>
  </div>
</div>

<div class="chart-card">
  <div class="chart-title">Recent Episodes</div>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>Ep</th><th>Steps</th><th>Reward</th>
      <th>Dealt</th><th>Taken</th><th>Spam</th><th>Approach</th>
      <th>Won</th><th>Win%</th><th>Avg50</th>
    </tr></thead>
    <tbody id="epTable"><tr><td colspan="10" class="no-data">No data yet — run mk4_train.py to start training</td></tr></tbody>
  </table>
  </div>
</div>

<script>
const CHART_OPTS = (color, label, fill=false) => ({
  label, borderColor: color, borderWidth: 2,
  backgroundColor: fill ? color + '22' : 'transparent',
  pointRadius: 0, tension: 0.3, fill
});

let rewardChart, winChart, termChart, lossChart;

function mkChart(id, datasets, yLabel='', min=undefined) {
  const ctx = document.getElementById(id).getContext('2d');
  return new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#7070a0', boxWidth: 12, font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: '#454570', maxTicksLimit: 8 },
             grid: { color: '#1f1f35' } },
        y: { ticks: { color: '#7070a0' }, grid: { color: '#1f1f35' },
             title: { display: !!yLabel, text: yLabel, color: '#7070a0' },
             ...(min !== undefined ? { min } : {}) }
      }
    }
  });
}

window.addEventListener('load', () => {
  rewardChart = mkChart('rewardChart', [
    { ...CHART_OPTS('#4FC3F7', 'Raw reward'), data: [] },
    { ...CHART_OPTS('#00E5A0', 'Avg-50', true), data: [] },
  ]);
  winChart = mkChart('winChart', [
    { ...CHART_OPTS('#FFD166', 'Win %', true), data: [] },
  ], '%', 0);
  termChart = mkChart('termChart', [
    { ...CHART_OPTS('#ef476f', 'Dealt'), data: [] },
    { ...CHART_OPTS('#e94560', 'Taken'), data: [] },
    { ...CHART_OPTS('#FF6B35', 'Spam'), data: [] },
    { ...CHART_OPTS('#4FC3F7', 'Approach'), data: [] },
  ]);
  lossChart = mkChart('lossChart', [
    { ...CHART_OPTS('#BB86FC', 'Policy Loss'), data: [] },
    { ...CHART_OPTS('#4FC3F7', 'Entropy'), data: [] },
  ]);
  refresh();
  setInterval(refresh, 3000);
});

async function refresh() {
  try {
    const res = await fetch('/data');
    const d   = await res.json();
    update(d);
    document.getElementById('lastUpdate').textContent =
      'Updated ' + new Date().toLocaleTimeString();
  } catch(e) { console.error(e); }
}

function fmt(v, decimals=1) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  return (n >= 0 ? '+' : '') + n.toFixed(decimals);
}

function update(d) {
  const eps = d.episodes || [];
  const stats = d.stats || [];

  // Stat cards
  if (eps.length) {
    const last = eps[eps.length-1];
    document.getElementById('totalEp').textContent = eps.length;
    document.getElementById('winRate').textContent = (last.win_rate || 0).toFixed(1) + '%';
    document.getElementById('avgReward').textContent = fmt(last.avg50);
    const wins = eps.filter(e => e.won).length;
    document.getElementById('winSub').textContent = `${wins} wins total`;
    document.getElementById('epSub').textContent = `latest: ep ${last.episode}`;
  }
  if (stats.length) {
    document.getElementById('gradUpdates').textContent = stats[stats.length-1].episode || '—';
  }

  const labels = eps.map(e => e.episode);

  // Reward chart
  rewardChart.data.labels = labels;
  rewardChart.data.datasets[0].data = eps.map(e => e.reward);
  rewardChart.data.datasets[1].data = eps.map(e => e.avg50);
  rewardChart.update('none');

  // Win chart
  winChart.data.labels = labels;
  winChart.data.datasets[0].data = eps.map(e => e.win_rate);
  winChart.update('none');

  // Terms chart
  termChart.data.labels = labels;
  termChart.data.datasets[0].data = eps.map(e => e.r_dealt || 0);
  termChart.data.datasets[1].data = eps.map(e => e.r_taken || 0);
  termChart.data.datasets[2].data = eps.map(e => e.r_spam || 0);
  termChart.data.datasets[3].data = eps.map(e => e.r_approach || 0);
  termChart.update('none');

  // Loss chart
  if (stats.length) {
    const slabels = stats.map(s => s.episode);
    lossChart.data.labels = slabels;
    lossChart.data.datasets[0].data = stats.map(s => s.policy_loss);
    lossChart.data.datasets[1].data = stats.map(s => s.entropy);
    lossChart.update('none');
  }

  // Table — last 20 eps (newest first)
  const tbody = document.getElementById('epTable');
  const recent = [...eps].reverse().slice(0, 20);
  if (!recent.length) return;
  tbody.innerHTML = recent.map(e => `
    <tr>
      <td>${e.episode}</td>
      <td>${e.steps || '—'}</td>
      <td class="${(e.reward||0)>=0?'pos':'neg'}">${fmt(e.reward)}</td>
      <td class="pos">${fmt(e.r_dealt||0)}</td>
      <td class="neg">${fmt(e.r_taken||0)}</td>
      <td class="neg">${fmt(e.r_spam||0)}</td>
      <td class="pos">${fmt(e.r_approach||0,2)}</td>
      <td class="${e.won?'won':'lost'}">${e.won ? '✓' : '✗'}</td>
      <td>${(e.win_rate||0).toFixed(1)}%</td>
      <td class="${(e.avg50||0)>=0?'pos':'neg'}">${fmt(e.avg50)}</td>
    </tr>`).join('');
}
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass  # silence access logs

    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML.encode())

        elif self.path == '/data':
            episodes = read_jsonl(TRAIN_LOG)
            stats    = read_jsonl(STATS_LOG)
            payload  = json.dumps({'episodes': episodes, 'stats': stats}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(payload)

        else:
            self.send_response(404)
            self.end_headers()


def main():
    print(f'[dashboard] Training Monitor starting at http://localhost:{PORT}')
    print(f'[dashboard] Reading: {TRAIN_LOG}')
    print(f'[dashboard] Press Ctrl+C to stop.')
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[dashboard] Stopped.')


if __name__ == '__main__':
    main()
