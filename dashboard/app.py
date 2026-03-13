#!/usr/bin/env python3
# dashboard/app.py
"""
Policy Trend Agent — Web Dashboard
A lightweight Flask app that reads from the SQLite (or Postgres) database
and renders a filterable, searchable policy intelligence dashboard.

Usage:
  pip install flask
  cd policy-agent
  python dashboard/app.py
  → Open http://localhost:5000
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import yaml
from flask import Flask, render_template_string, request, jsonify
from storage.database import PolicyDatabase

app = Flask(__name__)


def get_db():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "config.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    db_cfg = config.get("database", {})
    pg_dsn = db_cfg.get("postgres")
    if pg_dsn:
        from storage.postgres_database import PolicyDatabasePG
        return PolicyDatabasePG(pg_dsn)
    return PolicyDatabase(db_cfg.get("path", "storage/policy_agent.db"))


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Policy Intelligence Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Fraunces:ital,wght@0,300;0,600;1,300&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:       #0d0f12;
    --surface:  #14171c;
    --border:   #1e2329;
    --muted:    #3d4450;
    --text-dim: #6b7585;
    --text:     #c8cdd8;
    --text-hi:  #eceef2;
    --accent:   #4f7cff;
    --urgent:   #f05252;
    --notable:  #e3a53a;
    --monitor:  #4b5565;
    --sg:       #3ecf8e;
    --au:       #4f7cff;
    --uk:       #a78bfa;
    --eu:       #f9a826;
    --asean:    #e879a0;
    --global:   #60c5ba;
    --online:   #4f7cff;
    --ai:       #a78bfa;
    --tech:     #3ecf8e;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    min-height: 100vh;
  }

  /* ── Layout ── */
  .layout { display: grid; grid-template-columns: 220px 1fr; min-height: 100vh; }

  /* ── Sidebar ── */
  .sidebar {
    background: var(--surface);
    border-right: 1px solid var(--border);
    padding: 28px 20px;
    display: flex;
    flex-direction: column;
    gap: 28px;
    position: sticky;
    top: 0;
    height: 100vh;
    overflow-y: auto;
  }
  .logo {
    font-family: 'Fraunces', serif;
    font-size: 18px;
    font-weight: 600;
    color: var(--text-hi);
    letter-spacing: -0.3px;
    line-height: 1.3;
  }
  .logo span { color: var(--accent); font-style: italic; }
  .logo-sub { font-size: 10px; color: var(--text-dim); letter-spacing: 1.5px; text-transform: uppercase; margin-top: 4px; }
  .filter-group { display: flex; flex-direction: column; gap: 6px; }
  .filter-label { font-size: 9px; letter-spacing: 2px; text-transform: uppercase; color: var(--text-dim); margin-bottom: 2px; }
  .chip-row { display: flex; flex-wrap: wrap; gap: 5px; }
  .chip {
    padding: 4px 10px;
    border-radius: 3px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-dim);
    font-family: 'DM Mono', monospace;
    font-size: 10px;
    cursor: pointer;
    transition: all 0.15s;
    letter-spacing: 0.5px;
  }
  .chip:hover { border-color: var(--muted); color: var(--text); }
  .chip.active { background: var(--accent); border-color: var(--accent); color: #fff; }
  .chip.jur-sg.active  { background: var(--sg);    border-color: var(--sg); }
  .chip.jur-au.active  { background: var(--au);    border-color: var(--au); }
  .chip.jur-uk.active  { background: var(--uk);    border-color: var(--uk); }
  .chip.jur-eu.active  { background: var(--eu);    border-color: var(--eu); }
  .chip.jur-asean.active { background: var(--asean); border-color: var(--asean); }

  .days-select {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    padding: 6px 10px;
    border-radius: 3px;
    width: 100%;
    cursor: pointer;
  }
  .days-select:focus { outline: none; border-color: var(--accent); }

  .stat-row { display: flex; flex-direction: column; gap: 8px; }
  .stat-item { display: flex; justify-content: space-between; align-items: center; }
  .stat-key { font-size: 10px; color: var(--text-dim); }
  .stat-val { font-size: 13px; color: var(--text-hi); font-weight: 500; }

  /* ── Main ── */
  .main { padding: 28px 32px; display: flex; flex-direction: column; gap: 20px; }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .search-wrap { flex: 1; max-width: 440px; position: relative; }
  .search-input {
    width: 100%;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    padding: 9px 14px 9px 36px;
    border-radius: 4px;
    transition: border-color 0.15s;
  }
  .search-input:focus { outline: none; border-color: var(--accent); }
  .search-input::placeholder { color: var(--muted); }
  .search-icon {
    position: absolute;
    left: 12px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--muted);
    font-size: 13px;
    pointer-events: none;
  }
  .result-count { color: var(--text-dim); font-size: 11px; white-space: nowrap; }

  /* ── Items grid ── */
  .items-grid { display: flex; flex-direction: column; gap: 1px; }

  .item-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 16px 18px;
    display: grid;
    grid-template-columns: 3px 1fr auto;
    gap: 0 14px;
    align-items: start;
    transition: border-color 0.15s;
    cursor: pointer;
    text-decoration: none;
  }
  .item-card:hover { border-color: var(--muted); }
  .item-card:hover .item-title { color: var(--accent); }

  .urgency-bar { width: 3px; border-radius: 2px; align-self: stretch; }
  .urgency-bar.urgent  { background: var(--urgent); }
  .urgency-bar.notable { background: var(--notable); }
  .urgency-bar.monitoring { background: var(--monitor); }

  .item-body { min-width: 0; }
  .item-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
    flex-wrap: wrap;
  }
  .badge {
    font-size: 9px;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    padding: 2px 7px;
    border-radius: 2px;
    font-weight: 500;
    border: 1px solid transparent;
  }
  .badge-jur-sg    { color: var(--sg);    border-color: var(--sg);    background: color-mix(in srgb, var(--sg) 10%, transparent); }
  .badge-jur-au    { color: var(--au);    border-color: var(--au);    background: color-mix(in srgb, var(--au) 10%, transparent); }
  .badge-jur-uk    { color: var(--uk);    border-color: var(--uk);    background: color-mix(in srgb, var(--uk) 10%, transparent); }
  .badge-jur-eu    { color: var(--eu);    border-color: var(--eu);    background: color-mix(in srgb, var(--eu) 10%, transparent); }
  .badge-jur-asean { color: var(--asean); border-color: var(--asean); background: color-mix(in srgb, var(--asean) 10%, transparent); }
  .badge-jur-global{ color: var(--global);border-color: var(--global);background: color-mix(in srgb, var(--global) 10%, transparent); }
  .badge-dom-online_safety    { color: var(--online); border-color: var(--muted); background: transparent; }
  .badge-dom-ai_safety        { color: var(--ai);     border-color: var(--muted); background: transparent; }
  .badge-dom-tech_governance  { color: var(--tech);   border-color: var(--muted); background: transparent; }
  .badge-urgency-urgent       { color: var(--urgent); border-color: var(--urgent); background: color-mix(in srgb, var(--urgent) 10%, transparent); }
  .badge-urgency-notable      { color: var(--notable);border-color: var(--notable);background: color-mix(in srgb, var(--notable) 10%, transparent); }

  .item-date { font-size: 10px; color: var(--text-dim); margin-left: auto; }

  .item-title {
    font-family: 'Fraunces', serif;
    font-weight: 600;
    font-size: 15px;
    color: var(--text-hi);
    line-height: 1.35;
    margin-bottom: 6px;
    transition: color 0.15s;
    text-decoration: none;
  }
  .item-summary {
    font-size: 12px;
    color: var(--text-dim);
    line-height: 1.6;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .item-implications {
    margin-top: 8px;
    font-size: 11px;
    color: var(--text-dim);
    font-style: italic;
    border-left: 2px solid var(--muted);
    padding-left: 10px;
  }

  .item-score {
    font-size: 11px;
    color: var(--text-dim);
    text-align: right;
    white-space: nowrap;
    padding-top: 2px;
  }
  .score-hi { color: var(--notable); }
  .score-lo { color: var(--monitor); }

  /* ── Empty state ── */
  .empty {
    text-align: center;
    padding: 80px 20px;
    color: var(--text-dim);
    font-family: 'Fraunces', serif;
    font-style: italic;
    font-size: 16px;
  }

  /* ── Loading ── */
  .loading { text-align: center; padding: 60px; color: var(--text-dim); }

  /* ── Synthesis panel ── */
  .synthesis-panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 20px 22px;
  }
  .synthesis-header {
    font-size: 9px;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-bottom: 12px;
  }
  .synthesis-body {
    font-size: 12px;
    color: var(--text);
    line-height: 1.7;
    white-space: pre-wrap;
    max-height: 300px;
    overflow-y: auto;
  }
  .synthesis-body h2 {
    font-family: 'Fraunces', serif;
    font-size: 14px;
    font-weight: 600;
    color: var(--text-hi);
    margin: 14px 0 6px;
  }
  .synthesis-body h2:first-child { margin-top: 0; }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--muted); border-radius: 2px; }
</style>
</head>
<body>
<div class="layout">

  <!-- ── SIDEBAR ── -->
  <aside class="sidebar">
    <div>
      <div class="logo">Policy<br><span>Intelligence</span></div>
      <div class="logo-sub">Trend Monitor</div>
    </div>

    <div class="filter-group">
      <div class="filter-label">Jurisdiction</div>
      <div class="chip-row" id="jur-chips">
        <button class="chip jur-all active" data-jur="">All</button>
        <button class="chip jur-sg" data-jur="sg">SG</button>
        <button class="chip jur-au" data-jur="au">AU</button>
        <button class="chip jur-uk" data-jur="uk">UK</button>
        <button class="chip jur-eu" data-jur="eu">EU</button>
        <button class="chip jur-asean" data-jur="asean">ASEAN</button>
      </div>
    </div>

    <div class="filter-group">
      <div class="filter-label">Domain</div>
      <div class="chip-row" id="dom-chips">
        <button class="chip active" data-dom="">All</button>
        <button class="chip" data-dom="online_safety">Online</button>
        <button class="chip" data-dom="ai_safety">AI</button>
        <button class="chip" data-dom="tech_governance">Gov</button>
      </div>
    </div>

    <div class="filter-group">
      <div class="filter-label">Urgency</div>
      <div class="chip-row" id="urg-chips">
        <button class="chip active" data-urg="">All</button>
        <button class="chip" data-urg="urgent">Urgent</button>
        <button class="chip" data-urg="notable">Notable</button>
        <button class="chip" data-urg="monitoring">Monitor</button>
      </div>
    </div>

    <div class="filter-group">
      <div class="filter-label">Time Window</div>
      <select class="days-select" id="days-select">
        <option value="7">Last 7 days</option>
        <option value="14">Last 14 days</option>
        <option value="30" selected>Last 30 days</option>
        <option value="90">Last 90 days</option>
      </select>
    </div>

    <div class="filter-group">
      <div class="filter-label">Overview</div>
      <div class="stat-row" id="stats">
        <div class="stat-item"><span class="stat-key">Total</span><span class="stat-val" id="stat-total">—</span></div>
        <div class="stat-item"><span class="stat-key">Urgent</span><span class="stat-val" id="stat-urgent">—</span></div>
        <div class="stat-item"><span class="stat-key">Notable</span><span class="stat-val" id="stat-notable">—</span></div>
      </div>
    </div>
  </aside>

  <!-- ── MAIN ── -->
  <main class="main">
    <div class="topbar">
      <div class="search-wrap">
        <span class="search-icon">⌕</span>
        <input class="search-input" id="search" type="text" placeholder="Search titles and summaries…">
      </div>
      <div class="result-count" id="result-count"></div>
    </div>

    <div id="synthesis-container"></div>

    <div class="items-grid" id="items-container">
      <div class="loading">Loading…</div>
    </div>
  </main>
</div>

<script>
const state = { jur: '', dom: '', urg: '', days: 30, search: '' };

function domainLabel(d) {
  return { online_safety:'Online Safety', ai_safety:'AI Safety', tech_governance:'Tech Gov', other:'Other' }[d] || d;
}
function jurLabel(j) {
  return { sg:'🇸🇬 SG', au:'🇦🇺 AU', uk:'🇬🇧 UK', eu:'🇪🇺 EU', asean:'🌏 ASEAN', global:'🌐 Global' }[j] || j.toUpperCase();
}
function scoreClass(s) { return s >= 8 ? 'score-hi' : s <= 5 ? 'score-lo' : ''; }

function renderItem(item) {
  const urgencyLabel = item.urgency !== 'monitoring'
    ? `<span class="badge badge-urgency-${item.urgency}">${item.urgency}</span>`
    : '';
  const date = item.published
    ? new Date(item.published).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric'})
    : item.created_at
      ? new Date(item.created_at).toLocaleDateString('en-GB', {day:'numeric',month:'short'})
      : '';

  return `<a class="item-card" href="${item.url}" target="_blank" rel="noopener">
    <div class="urgency-bar ${item.urgency}"></div>
    <div class="item-body">
      <div class="item-meta">
        <span class="badge badge-jur-${item.jurisdiction}">${jurLabel(item.jurisdiction)}</span>
        <span class="badge badge-dom-${item.domain}">${domainLabel(item.domain)}</span>
        ${urgencyLabel}
        <span class="item-date">${date}</span>
      </div>
      <div class="item-title">${item.title}</div>
      ${item.summary ? `<div class="item-summary">${item.summary}</div>` : ''}
      ${item.implications ? `<div class="item-implications">⚡ ${item.implications}</div>` : ''}
    </div>
    <div class="item-score ${scoreClass(item.relevance_score)}">${item.relevance_score}/10</div>
  </a>`;
}

async function load() {
  const params = new URLSearchParams({
    days: state.days,
    ...(state.jur  && { jurisdiction: state.jur }),
    ...(state.dom  && { domain: state.dom }),
    ...(state.urg  && { urgency: state.urg }),
    ...(state.search && { search: state.search }),
  });

  const [itemsRes, synthesisRes] = await Promise.all([
    fetch('/api/items?' + params),
    fetch('/api/synthesis?' + params),
  ]);

  const items = await itemsRes.json();
  const { synthesis } = await synthesisRes.json();

  // Stats
  document.getElementById('stat-total').textContent = items.length;
  document.getElementById('stat-urgent').textContent = items.filter(i => i.urgency==='urgent').length;
  document.getElementById('stat-notable').textContent = items.filter(i => i.urgency==='notable').length;
  document.getElementById('result-count').textContent = `${items.length} item${items.length!==1?'s':''}`;

  // Synthesis
  const sc = document.getElementById('synthesis-container');
  if (synthesis && !state.search) {
    const formatted = synthesis
      .replace(/## (.+)/g, '<h2>$1</h2>')
      .replace(/\n/g, '\n');
    sc.innerHTML = `<div class="synthesis-panel"><div class="synthesis-header">Trend Synthesis</div><div class="synthesis-body">${formatted}</div></div>`;
  } else {
    sc.innerHTML = '';
  }

  // Items
  const container = document.getElementById('items-container');
  if (!items.length) {
    container.innerHTML = '<div class="empty">No items match the current filters.</div>';
    return;
  }
  container.innerHTML = items.map(renderItem).join('');
}

// Chips
document.querySelectorAll('#jur-chips .chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('#jur-chips .chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    state.jur = chip.dataset.jur;
    load();
  });
});
document.querySelectorAll('#dom-chips .chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('#dom-chips .chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    state.dom = chip.dataset.dom;
    load();
  });
});
document.querySelectorAll('#urg-chips .chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('#urg-chips .chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    state.urg = chip.dataset.urg;
    load();
  });
});
document.getElementById('days-select').addEventListener('change', e => {
  state.days = parseInt(e.target.value);
  load();
});

// Search (debounced)
let searchTimer;
document.getElementById('search').addEventListener('input', e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.search = e.target.value.trim(); load(); }, 350);
});

load();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/items")
def api_items():
    db = get_db()
    try:
        items = db.query_items(
            jurisdiction=request.args.get("jurisdiction") or None,
            domain=request.args.get("domain") or None,
            urgency=request.args.get("urgency") or None,
            days=int(request.args.get("days", 30)),
            limit=200,
        )
        result = [dict(row) for row in items]

        # Client-side search fallback (SQLite doesn't have FTS via this adapter)
        q = request.args.get("search", "").lower()
        if q:
            result = [
                r for r in result
                if q in (r.get("title") or "").lower()
                or q in (r.get("summary") or "").lower()
            ]

        return jsonify(result)
    finally:
        db.close()


@app.route("/api/synthesis")
def api_synthesis():
    """Return the most recent digest synthesis."""
    db = get_db()
    try:
        conn = db.conn
        row = conn.execute(
            "SELECT synthesis FROM digests ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        synthesis = row["synthesis"] if row else None
        return jsonify({"synthesis": synthesis})
    except Exception:
        return jsonify({"synthesis": None})
    finally:
        db.close()


if __name__ == "__main__":
    print("\n  Policy Intelligence Dashboard")
    print("  → http://localhost:5000\n")
    app.run(debug=True, port=5000)
