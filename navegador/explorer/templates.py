# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
HTML template for the Navegador Graph Explorer.

A fully self-contained, single-file HTML page with:
- Force-directed graph visualisation via inline canvas + JS physics
- Search box that queries /api/search
- Node click → detail panel via /api/node/<name>
- Stats bar via /api/stats
- Zero external dependencies (no CDN, no frameworks)
"""

# Colour palette per node label ─────────────────────────────────────────────
NODE_COLORS = {
    "Function": "#4e9af1",
    "Method": "#6cb4f5",
    "Class": "#f4a93b",
    "File": "#a8d9a7",
    "Module": "#82c9a0",
    "Repository": "#e67e22",
    "Variable": "#c39bd3",
    "Import": "#a9cce3",
    "Decorator": "#f1948a",
    "Domain": "#f7dc6f",
    "Concept": "#f9e79f",
    "Rule": "#f0b27a",
    "Decision": "#f8c471",
    "WikiPage": "#d2b4de",
    "Person": "#fadbd8",
    "default": "#aaaaaa",
}

_COLORS_JS = "\n".join(
    f"    '{label}': '{color}'," for label, color in NODE_COLORS.items()
)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Navegador Graph Explorer</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}
  #header {{
    background: #16213e;
    border-bottom: 1px solid #0f3460;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
  }}
  #header h1 {{
    font-size: 1.1rem;
    color: #4e9af1;
    letter-spacing: 0.05em;
    white-space: nowrap;
  }}
  #search-box {{
    flex: 1;
    max-width: 400px;
    background: #0f3460;
    border: 1px solid #4e9af1;
    border-radius: 6px;
    padding: 6px 12px;
    color: #e0e0e0;
    font-size: 0.9rem;
    outline: none;
  }}
  #search-box::placeholder {{ color: #5a6a8a; }}
  #search-box:focus {{ border-color: #6cb4f5; box-shadow: 0 0 0 2px rgba(78,154,241,0.2); }}
  #stats-bar {{
    font-size: 0.75rem;
    color: #7a8aaa;
    white-space: nowrap;
  }}
  #main {{
    display: flex;
    flex: 1;
    overflow: hidden;
  }}
  #canvas-wrap {{
    flex: 1;
    position: relative;
    overflow: hidden;
  }}
  #graph-canvas {{
    display: block;
    width: 100%;
    height: 100%;
    cursor: grab;
  }}
  #graph-canvas:active {{ cursor: grabbing; }}
  #sidebar {{
    width: 300px;
    background: #16213e;
    border-left: 1px solid #0f3460;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    overflow: hidden;
  }}
  #sidebar-title {{
    padding: 10px 14px;
    font-size: 0.8rem;
    color: #7a8aaa;
    border-bottom: 1px solid #0f3460;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }}
  #search-results {{
    border-bottom: 1px solid #0f3460;
    max-height: 220px;
    overflow-y: auto;
  }}
  .search-result {{
    padding: 8px 14px;
    cursor: pointer;
    border-bottom: 1px solid #0d1b35;
    transition: background 0.15s;
  }}
  .search-result:hover {{ background: #0f3460; }}
  .search-result .sr-name {{ font-size: 0.85rem; font-weight: 600; color: #c8d8f0; }}
  .search-result .sr-meta {{ font-size: 0.72rem; color: #5a6a8a; margin-top: 2px; }}
  #detail-panel {{
    flex: 1;
    padding: 14px;
    overflow-y: auto;
    font-size: 0.82rem;
  }}
  #detail-panel h2 {{
    font-size: 1rem;
    color: #4e9af1;
    margin-bottom: 8px;
    word-break: break-all;
  }}
  .detail-label {{
    color: #5a6a8a;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-top: 10px;
    margin-bottom: 3px;
  }}
  .detail-value {{
    color: #c8d8f0;
    word-break: break-word;
  }}
  .badge {{
    display: inline-block;
    padding: 2px 7px;
    border-radius: 10px;
    font-size: 0.7rem;
    font-weight: 600;
    margin-right: 4px;
    margin-bottom: 4px;
  }}
  .neighbor-item {{
    background: #0f3460;
    border-radius: 4px;
    padding: 4px 8px;
    margin: 3px 0;
    cursor: pointer;
    transition: background 0.15s;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .neighbor-item:hover {{ background: #1a4a80; }}
  .neighbor-name {{ color: #c8d8f0; font-size: 0.8rem; }}
  .neighbor-type {{ color: #5a6a8a; font-size: 0.7rem; }}
  #empty-hint {{
    color: #3a4a6a;
    text-align: center;
    margin-top: 40px;
    font-size: 0.85rem;
    line-height: 1.6;
  }}
  #loading {{
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: #4e9af1;
    font-size: 0.9rem;
    pointer-events: none;
  }}
  ::-webkit-scrollbar {{ width: 5px; }}
  ::-webkit-scrollbar-track {{ background: #0d1b35; }}
  ::-webkit-scrollbar-thumb {{ background: #0f3460; border-radius: 3px; }}
</style>
</head>
<body>
<div id="header">
  <h1>navegador</h1>
  <input id="search-box" type="text" placeholder="Search nodes..." autocomplete="off">
  <div id="stats-bar">Loading…</div>
</div>
<div id="main">
  <div id="canvas-wrap">
    <canvas id="graph-canvas"></canvas>
    <div id="loading">Loading graph…</div>
  </div>
  <div id="sidebar">
    <div id="sidebar-title">Explorer</div>
    <div id="search-results"></div>
    <div id="detail-panel">
      <div id="empty-hint">Click a node<br>or search above<br>to see details.</div>
    </div>
  </div>
</div>
<script>
(function() {
'use strict';

// ── Colour palette ─────────────────────────────────────────────────────────
const NODE_COLORS = {{
{colors}
}};
function nodeColor(label) {{
  return NODE_COLORS[label] || NODE_COLORS['default'];
}}

// ── State ──────────────────────────────────────────────────────────────────
let nodes = [];      // {{id, label, name, x, y, vx, vy, ...props}}
let edges = [];      // {{source_id, target_id, type}}
let nodeById = {{}};  // id → node

let selectedNode = null;
let hoveredNode = null;

// Camera
let camX = 0, camY = 0, camScale = 1;
let isDragging = false, dragStartX = 0, dragStartY = 0, camStartX = 0, camStartY = 0;
let isDraggingNode = false, dragNode = null, dragNodeOffX = 0, dragNodeOffY = 0;

// Physics
let physicsRunning = true;
const REPEL = 8000;
const ATTRACT = 0.04;
const EDGE_LEN = 120;
const DAMPING = 0.85;
const MAX_VEL = 12;

// ── Canvas setup ────────────────────────────────────────────────────────────
const canvas = document.getElementById('graph-canvas');
const ctx = canvas.getContext('2d');
const wrap = document.getElementById('canvas-wrap');
const loading = document.getElementById('loading');

function resize() {{
  canvas.width = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
}}
window.addEventListener('resize', resize);
resize();

// ── Fetch graph data ────────────────────────────────────────────────────────
async function loadGraph() {{
  try {{
    const data = await fetch('/api/graph').then(r => r.json());
    initGraph(data.nodes || [], data.edges || []);
    loading.style.display = 'none';
    loadStats();
  }} catch(e) {{
    loading.textContent = 'Error loading graph.';
  }}
}}

function initGraph(rawNodes, rawEdges) {{
  const cx = canvas.width / 2, cy = canvas.height / 2;
  nodeById = {{}};
  nodes = rawNodes.map((n, i) => {{
    const angle = (i / Math.max(rawNodes.length, 1)) * 2 * Math.PI;
    const r = Math.min(cx, cy) * 0.6;
    const node = {{
      id: n.id,
      label: n.label || 'default',
      name: n.name || n.id,
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
      vx: 0, vy: 0,
      props: n.props || {{}},
    }};
    nodeById[n.id] = node;
    return node;
  }});
  edges = rawEdges.map(e => ({{
    source_id: e.source,
    target_id: e.target,
    type: e.type || '',
  }}));
}}

async function loadStats() {{
  try {{
    const s = await fetch('/api/stats').then(r => r.json());
    const bar = document.getElementById('stats-bar');
    bar.textContent = `${{s.nodes}} nodes · ${{s.edges}} edges`;
  }} catch(_) {{}}
}}

// ── Physics simulation ──────────────────────────────────────────────────────
function tick() {{
  if (!physicsRunning || nodes.length === 0) return;

  const cx = canvas.width / 2, cy = canvas.height / 2;

  // Repulsion between all pairs (Barnes-Hut approximation skipped for simplicity)
  for (let i = 0; i < nodes.length; i++) {{
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {{
      const b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      const dist2 = dx*dx + dy*dy + 0.1;
      const dist = Math.sqrt(dist2);
      const force = REPEL / dist2;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      a.vx -= fx; a.vy -= fy;
      b.vx += fx; b.vy += fy;
    }}
  }}

  // Edge spring attraction
  for (const e of edges) {{
    const a = nodeById[e.source_id], b = nodeById[e.target_id];
    if (!a || !b) continue;
    const dx = b.x - a.x, dy = b.y - a.y;
    const dist = Math.sqrt(dx*dx + dy*dy) || 1;
    const force = (dist - EDGE_LEN) * ATTRACT;
    const fx = (dx / dist) * force;
    const fy = (dy / dist) * force;
    a.vx += fx; a.vy += fy;
    b.vx -= fx; b.vy -= fy;
  }}

  // Weak centering pull
  for (const n of nodes) {{
    n.vx += (cx - n.x) * 0.0005;
    n.vy += (cy - n.y) * 0.0005;
  }}

  // Integrate
  for (const n of nodes) {{
    if (n === dragNode) continue;
    n.vx = Math.max(-MAX_VEL, Math.min(MAX_VEL, n.vx * DAMPING));
    n.vy = Math.max(-MAX_VEL, Math.min(MAX_VEL, n.vy * DAMPING));
    n.x += n.vx;
    n.y += n.vy;
  }}
}}

// ── Render ──────────────────────────────────────────────────────────────────
function draw() {{
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.save();
  ctx.translate(camX, camY);
  ctx.scale(camScale, camScale);

  // Edges
  ctx.lineWidth = 1 / camScale;
  for (const e of edges) {{
    const a = nodeById[e.source_id], b = nodeById[e.target_id];
    if (!a || !b) continue;
    const isHighlighted = selectedNode && (a === selectedNode || b === selectedNode);
    ctx.globalAlpha = isHighlighted ? 0.9 : 0.25;
    ctx.strokeStyle = isHighlighted ? '#4e9af1' : '#3a5070';
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();

    // Arrowhead
    if (isHighlighted) {{
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx*dx+dy*dy) || 1;
      const r = 8;
      const tx = b.x - (dx/dist)*r, ty = b.y - (dy/dist)*r;
      const angle = Math.atan2(dy, dx);
      ctx.globalAlpha = 0.7;
      ctx.fillStyle = '#4e9af1';
      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(tx - 8*Math.cos(angle-0.4), ty - 8*Math.sin(angle-0.4));
      ctx.lineTo(tx - 8*Math.cos(angle+0.4), ty - 8*Math.sin(angle+0.4));
      ctx.closePath();
      ctx.fill();
    }}
  }}
  ctx.globalAlpha = 1;

  // Edge labels on highlighted edges
  if (selectedNode) {{
    ctx.font = `${{Math.max(9, 10/camScale)}}px sans-serif`;
    ctx.fillStyle = '#5a8ab8';
    for (const e of edges) {{
      const a = nodeById[e.source_id], b = nodeById[e.target_id];
      if (!a || !b) continue;
      if (a !== selectedNode && b !== selectedNode) continue;
      if (!e.type) continue;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      ctx.fillText(e.type, mx, my);
    }}
  }}

  // Nodes
  const nodeR = 8;
  for (const n of nodes) {{
    const isSelected = n === selectedNode;
    const isHovered = n === hoveredNode;
    const color = nodeColor(n.label);

    ctx.beginPath();
    ctx.arc(n.x, n.y, nodeR + (isSelected ? 3 : isHovered ? 1 : 0), 0, 2*Math.PI);
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.9;
    ctx.fill();
    if (isSelected || isHovered) {{
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2 / camScale;
      ctx.stroke();
    }}
    ctx.globalAlpha = 1;

    // Label
    const labelThreshold = 0.4;
    if (camScale > labelThreshold || isSelected || isHovered) {{
      const fontSize = Math.max(8, 11 / camScale);
      ctx.font = `${{isSelected ? 'bold ' : ''}}${{fontSize}}px sans-serif`;
      ctx.fillStyle = '#e0e8ff';
      ctx.globalAlpha = Math.min(1, (camScale - labelThreshold + 0.1) * 3);
      if (isSelected || isHovered) ctx.globalAlpha = 1;
      ctx.fillText(n.name, n.x + nodeR + 2, n.y + 4);
      ctx.globalAlpha = 1;
    }}
  }}

  ctx.restore();
}}

function loop() {{
  tick();
  draw();
  requestAnimationFrame(loop);
}}

// ── Hit testing ─────────────────────────────────────────────────────────────
function screenToWorld(sx, sy) {{
  return {{ x: (sx - camX) / camScale, y: (sy - camY) / camScale }};
}}

function nodeAtScreen(sx, sy) {{
  const w = screenToWorld(sx, sy);
  const nodeR = 11;
  for (let i = nodes.length - 1; i >= 0; i--) {{
    const n = nodes[i];
    const dx = n.x - w.x, dy = n.y - w.y;
    if (dx*dx + dy*dy <= nodeR*nodeR) return n;
  }}
  return null;
}}

// ── Mouse / touch events ────────────────────────────────────────────────────
canvas.addEventListener('mousedown', e => {{
  const hit = nodeAtScreen(e.offsetX, e.offsetY);
  if (hit) {{
    isDraggingNode = true;
    dragNode = hit;
    physicsRunning = true;
    const w = screenToWorld(e.offsetX, e.offsetY);
    dragNodeOffX = hit.x - w.x;
    dragNodeOffY = hit.y - w.y;
  }} else {{
    isDragging = true;
    dragStartX = e.offsetX; dragStartY = e.offsetY;
    camStartX = camX; camStartY = camY;
  }}
}});

canvas.addEventListener('mousemove', e => {{
  if (isDraggingNode && dragNode) {{
    const w = screenToWorld(e.offsetX, e.offsetY);
    dragNode.x = w.x + dragNodeOffX;
    dragNode.y = w.y + dragNodeOffY;
    dragNode.vx = 0; dragNode.vy = 0;
  }} else if (isDragging) {{
    camX = camStartX + (e.offsetX - dragStartX);
    camY = camStartY + (e.offsetY - dragStartY);
  }} else {{
    hoveredNode = nodeAtScreen(e.offsetX, e.offsetY);
    canvas.style.cursor = hoveredNode ? 'pointer' : 'grab';
  }}
}});

canvas.addEventListener('mouseup', e => {{
  if (isDraggingNode && dragNode) {{
    const wasDragged = Math.abs(dragNode.vx) < 0.5 && Math.abs(dragNode.vy) < 0.5;
    if (wasDragged) selectNode(dragNode);
  }} else if (!isDragging || (Math.abs(e.offsetX - dragStartX) < 4 && Math.abs(e.offsetY - dragStartY) < 4)) {{
    const hit = nodeAtScreen(e.offsetX, e.offsetY);
    if (hit) selectNode(hit);
  }}
  isDragging = false;
  isDraggingNode = false;
  dragNode = null;
}});

canvas.addEventListener('wheel', e => {{
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.1 : 0.9;
  const mx = e.offsetX, my = e.offsetY;
  camX = mx - (mx - camX) * factor;
  camY = my - (my - camY) * factor;
  camScale = Math.max(0.05, Math.min(10, camScale * factor));
}}, {{ passive: false }});

// ── Node selection ──────────────────────────────────────────────────────────
async function selectNode(node) {{
  selectedNode = node;
  try {{
    const data = await fetch('/api/node/' + encodeURIComponent(node.name)).then(r => r.json());
    renderDetail(data);
  }} catch(e) {{
    renderDetail({{ name: node.name, label: node.label, props: node.props, neighbors: [] }});
  }}
}}

function renderDetail(data) {{
  const panel = document.getElementById('detail-panel');
  const label = data.label || '';
  const color = nodeColor(label);
  let html = `<h2>${{data.name}}</h2>`;
  html += `<div class="detail-label">Type</div>`;
  html += `<span class="badge" style="background:${{color}}22;color:${{color}};border:1px solid ${{color}}44">${{label}}</span>`;

  const props = data.props || {{}};
  const skip = new Set(['name']);
  const order = ['file_path', 'line_start', 'line_end', 'signature', 'docstring',
                  'description', 'status', 'domain', 'rationale', 'url'];
  const shown = new Set();

  for (const key of order) {{
    if (props[key] !== undefined && props[key] !== null && props[key] !== '') {{
      html += `<div class="detail-label">${{key.replace(/_/g,' ')}}</div>`;
      html += `<div class="detail-value">${{escHtml(String(props[key]))}}</div>`;
      shown.add(key);
    }}
  }}
  for (const [key, val] of Object.entries(props)) {{
    if (skip.has(key) || shown.has(key) || val === null || val === undefined || val === '') continue;
    html += `<div class="detail-label">${{key.replace(/_/g,' ')}}</div>`;
    html += `<div class="detail-value">${{escHtml(String(val))}}</div>`;
  }}

  const neighbors = data.neighbors || [];
  if (neighbors.length > 0) {{
    html += `<div class="detail-label">Neighbors (${{neighbors.length}})</div>`;
    for (const nb of neighbors.slice(0, 50)) {{
      html += `<div class="neighbor-item" onclick="jumpToNode(${{JSON.stringify(nb.name)}})">
        <span class="neighbor-name">${{escHtml(nb.name)}}</span>
        <span class="neighbor-type">${{escHtml(nb.label || '')}} · ${{escHtml(nb.rel || '')}}</span>
      </div>`;
    }}
    if (neighbors.length > 50) {{
      html += `<div style="color:#5a6a8a;font-size:0.75rem;margin-top:4px">and ${{neighbors.length - 50}} more…</div>`;
    }}
  }}

  panel.innerHTML = html;
}}

function escHtml(s) {{
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

// ── Jump to node by name ────────────────────────────────────────────────────
function jumpToNode(name) {{
  const n = nodes.find(x => x.name === name);
  if (!n) return;
  selectedNode = n;
  // Pan to node
  camX = canvas.width/2 - n.x * camScale;
  camY = canvas.height/2 - n.y * camScale;
  selectNode(n);
}}
window.jumpToNode = jumpToNode;

// ── Search ──────────────────────────────────────────────────────────────────
let searchTimer = null;
const searchBox = document.getElementById('search-box');
const searchResults = document.getElementById('search-results');

searchBox.addEventListener('input', () => {{
  clearTimeout(searchTimer);
  searchTimer = setTimeout(doSearch, 200);
}});

async function doSearch() {{
  const q = searchBox.value.trim();
  if (!q) {{ searchResults.innerHTML = ''; return; }}
  try {{
    const results = await fetch('/api/search?q=' + encodeURIComponent(q)).then(r => r.json());
    renderSearchResults(results.nodes || []);
  }} catch(_) {{}}
}}

function renderSearchResults(results) {{
  if (!results.length) {{
    searchResults.innerHTML = '<div style="padding:8px 14px;color:#5a6a8a;font-size:0.8rem">No results</div>';
    return;
  }}
  searchResults.innerHTML = results.slice(0, 20).map(r => `
    <div class="search-result" onclick="jumpToNode(${{JSON.stringify(r.name)}})">
      <div class="sr-name">${{escHtml(r.name)}}</div>
      <div class="sr-meta">${{escHtml(r.label || '')}} · ${{escHtml(r.file_path || r.domain || '')}}</div>
    </div>
  `).join('');
}}

// ── Boot ────────────────────────────────────────────────────────────────────
loadGraph().then(() => loop());
}());
</script>
</body>
</html>"""

# Inject the colour map into the template
HTML_TEMPLATE = HTML_TEMPLATE.replace("{colors}", _COLORS_JS)
