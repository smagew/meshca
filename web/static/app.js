/* meshca · map of links. Edges are drawn on one canvas under the node markers; nodes are Leaflet markers with
   pie-chart icons. Everything is computed from /api/graph; /api/node adds what the directory knows about a node. */
"use strict";

const COLOURS = { adverts: "#5b9cff", channel: "#5fd38d", direct: "#ffa24c" };
const KIND_NAMES = { adverts: "объявления", channel: "каналы", direct: "личные" };
const CRIT = "#ff4d5e", OBS = "#b980ff", UNKNOWN = "#8b95a3";

const state = {
  data: null, nodes: new Map(), markers: new Map(),
  kinds: new Set(["adverts", "channel", "direct"]), min: 5, risks: false,
  hover: null, selected: null, visibleEdges: [], visibleNodes: new Set(),
};

// ---------- map ----------
const map = L.map("map", { zoomControl: false, preferCanvas: false, worldCopyJump: false, minZoom: 2, maxZoom: 16 })
  .setView([20, 0], 3);   // replaced by the extent of the nodes once the data arrives
L.control.zoom({ position: "bottomright" }).addTo(map);
// OpenStreetMap's own tiles, darkened in CSS (class "base") so the coloured links stand out.
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, className: "base",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">участники OpenStreetMap</a>',
}).addTo(map);

// ---------- helpers ----------
const fmt = (n) => Number(n || 0).toLocaleString("ru-RU");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const minFromSlider = (v) => (v <= 0 ? 1 : Math.max(1, Math.round(Math.pow(10, (v / 100) * 3))));
const dominant = (mix) => Object.keys(KIND_NAMES).reduce((a, b) => ((mix[b] || 0) > (mix[a] || 0) ? b : a), "adverts");
// The observer is drawn small: it stands where its neighbours are and must not hide them.
const nodeRadius = (n) => (n.kind === "observer" ? 9 : Math.min(30, 5 + 1.9 * Math.pow(Math.max(n.relayed, 1), 0.36)));
function shown(mix) { return [...state.kinds].reduce((s, k) => s + (mix[k] || 0), 0); }

// ---------- edge canvas layer ----------
const EdgeLayer = L.Layer.extend({
  onAdd(m) {
    this._canvas = L.DomUtil.create("canvas", "edge-canvas");
    Object.assign(this._canvas.style, { position: "absolute", pointerEvents: "none" });
    m.getPanes().overlayPane.appendChild(this._canvas);
    m.on("moveend zoomend resize viewreset", this.redraw, this);
    m.on("zoomstart", () => (this._canvas.style.visibility = "hidden"));
    m.on("zoomend", () => (this._canvas.style.visibility = "visible"));
    this.redraw();
  },
  onRemove(m) { this._canvas.remove(); m.off("moveend zoomend resize viewreset", this.redraw, this); },
  redraw() {
    const m = this._map; if (!m) return;
    const size = m.getSize(), ratio = window.devicePixelRatio || 1, c = this._canvas;
    L.DomUtil.setPosition(c, m.containerPointToLayerPoint([0, 0]));
    c.width = size.x * ratio; c.height = size.y * ratio; c.style.width = size.x + "px"; c.style.height = size.y + "px";
    const ctx = c.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, size.x, size.y);
    this.geometry = [];
    const focus = state.hover || state.selected;
    // Thin first, so thick links stay on top.
    const edges = [...state.visibleEdges].sort((a, b) => shown(a.mix) - shown(b.mix));
    for (const e of edges) {
      const s = state.nodes.get(e.source), t = state.nodes.get(e.target);
      if (!s || !t || s.lat == null || t.lat == null) continue;
      const p0 = m.latLngToContainerPoint([s.lat, s.lon]), p1 = m.latLngToContainerPoint([t.lat, t.lon]);
      const g = curve(p0, p1, nodeRadius(s) + 2, nodeRadius(t) + 4);
      if (!g) continue;
      const packets = shown(e.mix), width = Math.min(11, 0.8 + 1.25 * Math.log(1 + packets));
      const lit = !focus || e.source === focus || e.target === focus;
      const uncertain = s.kind === "unknown" || t.kind === "unknown" || s.placed === "neighbours" || t.placed === "neighbours";
      ctx.globalAlpha = lit ? (focus ? 0.95 : 0.72) : 0.06;
      ctx.strokeStyle = ctx.fillStyle = COLOURS[dominant(e.mix)];
      ctx.lineWidth = width; ctx.lineCap = "round";
      ctx.setLineDash(uncertain ? [Math.max(4, width * 1.6), Math.max(4, width * 1.4)] : []);
      ctx.beginPath(); ctx.moveTo(g.a.x, g.a.y); ctx.quadraticCurveTo(g.c.x, g.c.y, g.b.x, g.b.y); ctx.stroke();
      ctx.setLineDash([]);
      arrow(ctx, g, width);
      this.geometry.push({ e, pts: sample(g, 16), width });
    }
    ctx.globalAlpha = 1;
  },
  edgeAt(point) {
    let best = null, bestD = 7;
    for (const item of this.geometry || []) {
      for (let i = 1; i < item.pts.length; i++) {
        const d = segDist(point, item.pts[i - 1], item.pts[i]) - item.width / 2;
        if (d < bestD) { bestD = d; best = item.e; }
      }
    }
    return best;
  },
});

function curve(p0, p1, r0, r1) {
  const dx = p1.x - p0.x, dy = p1.y - p0.y, len = Math.hypot(dx, dy);
  if (len < r0 + r1 + 4) return null;
  // Bend to the left of the direction: A->B and B->A take opposite sides and never overlap.
  const nx = -dy / len, ny = dx / len, bend = Math.min(60, len * 0.16);
  const c = { x: (p0.x + p1.x) / 2 + nx * bend, y: (p0.y + p1.y) / 2 + ny * bend };
  const trim = (p, q, r) => { const l = Math.hypot(q.x - p.x, q.y - p.y); return { x: p.x + ((q.x - p.x) * r) / l, y: p.y + ((q.y - p.y) * r) / l }; };
  return { a: trim(p0, c, r0), b: trim(p1, c, r1), c };
}
function at(g, t) {
  const u = 1 - t;
  return { x: u * u * g.a.x + 2 * u * t * g.c.x + t * t * g.b.x, y: u * u * g.a.y + 2 * u * t * g.c.y + t * t * g.b.y };
}
function sample(g, n) { return Array.from({ length: n + 1 }, (_, i) => at(g, i / n)); }
function arrow(ctx, g, width) {
  const t = 0.58, p = at(g, t), q = at(g, t + 0.02), ang = Math.atan2(q.y - p.y, q.x - p.x), s = 4 + width * 1.5;
  ctx.beginPath();
  ctx.moveTo(p.x + Math.cos(ang) * s, p.y + Math.sin(ang) * s);
  ctx.lineTo(p.x + Math.cos(ang + 2.5) * s, p.y + Math.sin(ang + 2.5) * s);
  ctx.lineTo(p.x + Math.cos(ang - 2.5) * s, p.y + Math.sin(ang - 2.5) * s);
  ctx.closePath(); ctx.fill();
}
function segDist(p, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2)) : 0;
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

const edgeLayer = new EdgeLayer().addTo(map);

// ---------- node icons ----------
function pie(n) {
  const r = nodeRadius(n), size = 2 * r + 10, c = size / 2;
  let body;
  if (n.kind === "observer") {
    body = `<circle cx="${c}" cy="${c}" r="${r}" fill="${OBS}" fill-opacity="0.9"/>`;
  } else {
    const parts = Object.keys(KIND_NAMES).map((k) => [k, n.mix[k] || 0]).filter(([, v]) => v > 0);
    const total = parts.reduce((s, [, v]) => s + v, 0);
    if (!total) body = `<circle cx="${c}" cy="${c}" r="${r}" fill="${UNKNOWN}" fill-opacity="0.55"/>`;
    else if (parts.length === 1) body = `<circle cx="${c}" cy="${c}" r="${r}" fill="${COLOURS[parts[0][0]]}"/>`;
    else {
      let a0 = -Math.PI / 2; body = "";
      for (const [k, v] of parts) {
        const a1 = a0 + (2 * Math.PI * v) / total, large = a1 - a0 > Math.PI ? 1 : 0;
        body += `<path d="M${c},${c} L${c + r * Math.cos(a0)},${c + r * Math.sin(a0)} A${r},${r} 0 ${large} 1 ${c + r * Math.cos(a1)},${c + r * Math.sin(a1)} Z" fill="${COLOURS[k]}"/>`;
        a0 = a1;
      }
    }
  }
  const dashed = n.kind === "unknown" || n.placed === "neighbours";
  const ring = n.critical.length
    ? `<circle cx="${c}" cy="${c}" r="${r + 3}" fill="none" stroke="${CRIT}" stroke-width="3"/>`
    : `<circle cx="${c}" cy="${c}" r="${r + 0.5}" fill="none" stroke="#0b0d10" stroke-width="1.5" ${dashed ? 'stroke-dasharray="3 2" stroke="#cfd6df"' : ""}/>`;
  return L.divIcon({ className: "node-icon", iconSize: [size, size], iconAnchor: [c, c], html: `<svg width="${size}" height="${size}">${body}${ring}</svg>` });
}

// ---------- drawing ----------
function applyFilters() {
  const d = state.data; if (!d) return;
  let edges = d.edges.filter((e) => shown(e.mix) >= state.min);
  if (state.risks) {
    const crit = new Set(d.nodes.filter((n) => n.critical.length).map((n) => n.id));
    edges = edges.filter((e) => crit.has(e.source) || crit.has(e.target));
  }
  state.visibleEdges = edges;
  state.visibleNodes = new Set(edges.flatMap((e) => [e.source, e.target]));
  if (state.risks) d.nodes.forEach((n) => n.critical.length && state.visibleNodes.add(n.id));
  for (const [id, marker] of state.markers) {
    const on = state.visibleNodes.has(id) && state.nodes.get(id).lat != null;
    if (on && !map.hasLayer(marker)) marker.addTo(map);
    if (!on && map.hasLayer(marker)) marker.remove();
  }
  updateLabels(); edgeLayer.redraw(); updateStats();
}

function updateLabels() {
  // Labels go to the most important nodes first and only where they fit: a label that would overlap one already
  // placed waits for a closer zoom. The focused node and its neighbours are always labelled.
  const focus = state.hover || state.selected;
  const must = new Set(focus ? [focus, ...neighboursOf(focus)] : []);
  const ranked = [...state.visibleNodes].map((id) => state.nodes.get(id))
    .filter((n) => n && n.lat != null)
    .sort((a, b) => must.has(b.id) - must.has(a.id) || (b.kind === "observer") - (a.kind === "observer")
      || b.critical.length - a.critical.length || b.relayed - a.relayed);
  const boxes = [], keep = new Set();
  for (const n of ranked) {
    const p = map.latLngToContainerPoint([n.lat, n.lon]), r = nodeRadius(n);
    const box = { x0: p.x - r, y0: p.y - Math.max(r, 8), x1: p.x + r + 6 + n.label.length * 6.6, y1: p.y + Math.max(r, 8) };
    const free = !boxes.some((b) => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0);
    if (free || must.has(n.id)) { keep.add(n.id); boxes.push(box); }
    else boxes.push({ x0: p.x - r, y0: p.y - r, x1: p.x + r, y1: p.y + r });   // its circle still takes room
  }
  for (const [id, marker] of state.markers) {
    const want = keep.has(id) && map.hasLayer(marker);
    if (want && !marker.getTooltip()) {
      marker.bindTooltip(esc(state.nodes.get(id).label), { permanent: true, direction: "right", className: "node-label", offset: [nodeRadius(state.nodes.get(id)) + 2, 0] });
    } else if (!want && marker.getTooltip()) marker.unbindTooltip();
  }
}

function neighboursOf(id) {
  const out = new Set();
  for (const e of state.visibleEdges) { if (e.source === id) out.add(e.target); if (e.target === id) out.add(e.source); }
  return out;
}

function setFocus() {
  const focus = state.hover || state.selected;
  const lit = focus ? neighboursOf(focus) : new Set();
  if (focus) lit.add(focus);
  document.getElementById("map").classList.toggle("dim", !!focus);
  for (const [id, marker] of state.markers) {
    const el = marker.getElement(); if (el) el.classList.toggle("lit", lit.has(id));
    const tip = marker.getTooltip(); const tel = tip && tip.getElement(); if (tel) tel.classList.toggle("lit", lit.has(id));
  }
  updateLabels(); setFocusLabels(lit); edgeLayer.redraw();
}
function setFocusLabels(lit) {
  for (const [id, marker] of state.markers) {
    const tel = marker.getTooltip() && marker.getTooltip().getElement(); if (tel) tel.classList.toggle("lit", lit.has(id));
  }
}

function updateStats() {
  const d = state.data;
  const placed = d.nodes.filter((n) => n.lat != null).length;
  const crit = d.nodes.filter((n) => n.critical.length).length;
  const packets = state.visibleEdges.reduce((s, e) => s + shown(e.mix), 0);
  document.getElementById("stats").innerHTML =
    `На карте <b>${fmt(state.visibleNodes.size)}</b> узлов и <b>${fmt(state.visibleEdges.length)}</b> связей из ${fmt(d.edges.length)}` +
    ` · <b>${fmt(packets)}</b> прохождений пакетов` +
    `<br>Критичных узлов: <b style="color:${CRIT}">${crit}</b>` +
    (d.unplaced ? ` · без места на карте: ${d.unplaced}` : "") +
    (placed ? "" : "<br>Нет узлов с координатами") +
    (d.computed_at ? `<br>Расчёт: ${new Date(d.computed_at).toLocaleString("ru-RU")}` : "");
}

// ---------- tooltips ----------
const tip = document.getElementById("tip");
function showTip(html, x, y) {
  tip.innerHTML = html; tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(window.innerWidth - w - 8, x + 14) + "px";
  tip.style.top = Math.min(window.innerHeight - h - 8, y + 14) + "px";
}
function mixLine(mix) {
  return Object.keys(KIND_NAMES).filter((k) => mix[k]).map((k) => `<span style="color:${COLOURS[k]}">${KIND_NAMES[k]} ${fmt(mix[k])}</span>`).join(" · ");
}
function nodeTip(n) {
  const what = n.kind === "observer" ? `принял ${fmt(n.heard)}` : `передал дальше ${fmt(n.relayed)}`;
  return `<b>${esc(n.label)}</b>${n.role ? ` · ${esc(n.role)}` : ""}<br>${what} пакетов<br>${mixLine(n.mix)}` +
    (n.critical.length ? `<br><span style="color:${CRIT}">критичный: без него не дойдут ${n.critical.length}</span>` : "") +
    (n.placed === "neighbours" ? "<br><i>место примерное: среди соседей</i>" : "");
}
map.on("mousemove", (ev) => {
  if (state.hover) return;
  const e = edgeLayer.edgeAt(ev.containerPoint);
  if (e) {
    const s = state.nodes.get(e.source), t = state.nodes.get(e.target);
    showTip(`<b>${esc(s.label)}</b> → <b>${esc(t.label)}</b><br>${fmt(e.packets)} пакетов<br>${mixLine(e.mix)}`, ev.originalEvent.clientX, ev.originalEvent.clientY);
    map.getContainer().style.cursor = "pointer";
  } else { tip.hidden = true; map.getContainer().style.cursor = ""; }
});
map.on("click", (ev) => {
  const e = edgeLayer.edgeAt(ev.containerPoint);
  if (e) select(state.selected === e.source ? e.target : e.source);
  else if (state.selected) { state.selected = null; document.getElementById("card").hidden = true; setFocus(); }
});
map.on("zoomend", () => { updateLabels(); setFocusLabels(new Set()); setFocus(); });

// ---------- node card ----------
async function select(id) {
  state.selected = id; setFocus();
  const n = state.nodes.get(id), card = document.getElementById("card"), body = document.getElementById("cardBody");
  card.hidden = false;
  const incoming = state.data.edges.filter((e) => e.target === id).sort((a, b) => b.packets - a.packets);
  const outgoing = state.data.edges.filter((e) => e.source === id).sort((a, b) => b.packets - a.packets);
  const total = Object.values(n.mix).reduce((s, v) => s + v, 0) || 1;
  const bar = `<div class="bar">${Object.keys(KIND_NAMES).map((k) => `<i style="width:${(100 * (n.mix[k] || 0)) / total}%;background:${COLOURS[k]}"></i>`).join("")}</div>`;
  const rows = (list, key) => list.slice(0, 10).map((e) => {
    const other = state.nodes.get(e[key]);
    return `<tr><td class="link" data-id="${esc(e[key])}">${esc(other ? other.label : e[key])}</td><td class="n">${fmt(e.packets)}</td></tr>`;
  }).join("") + (list.length > 10 ? `<tr><td colspan="2" class="meta">и ещё ${list.length - 10}</td></tr>` : "");
  body.innerHTML = `<h2>${esc(n.label)}</h2>
    <div class="meta">${esc(n.role || (n.kind === "unknown" ? "узел не определён: известно только начало ключа" : n.kind === "observer" ? "наш observer" : ""))}</div>
    <div>${n.kind === "observer" ? `Принял <b>${fmt(n.heard)}</b>` : `Передал дальше <b>${fmt(n.relayed)}</b>`} пакетов</div>
    ${bar}<div class="meta">${mixLine(n.mix)}</div>
    ${n.critical.length ? `<h3 class="warn">Критичный узел</h3><div>Без него перестанут доходить до нас (${n.critical.length}): ${n.critical.map(esc).join(", ")}</div>` : ""}
    ${incoming.length ? `<h3>Получает от</h3><table>${rows(incoming, "source")}</table>` : ""}
    ${outgoing.length ? `<h3>Передаёт</h3><table>${rows(outgoing, "target")}</table>` : ""}
    <div id="nodeMore" class="meta"></div>`;
  body.querySelectorAll(".link").forEach((el) => el.addEventListener("click", () => {
    const other = state.nodes.get(el.dataset.id);
    if (other && other.lat != null) map.panTo([other.lat, other.lon]);
    select(el.dataset.id);
  }));
  if (n.kind !== "node") return;
  try {
    const more = await (await fetch(`api/node?id=${encodeURIComponent(id)}`)).json();
    if (state.selected !== id) return;
    const lines = [];
    if (more.key) lines.push(`Ключ: <code>${esc(more.key.slice(0, 16))}…</code>`);
    if (more.first_seen) lines.push(`Впервые слышен: ${new Date(more.first_seen).toLocaleString("ru-RU")}`);
    if (more.last_seen) lines.push(`Последнее объявление: ${new Date(more.last_seen).toLocaleString("ru-RU")}`);
    if (more.adverts) lines.push(`Объявлений получено: ${fmt(more.adverts)}`);
    if (n.placed === "neighbours") lines.push("Координат в объявлении нет: место на карте примерное, среди соседей.");
    for (const r of more.rhythm || []) {
      const p = r.period_confirmed ? `период ${Math.round(r.period_s / 60)} мин` : r.judged ? "без явного периода" : "мало данных для ритма";
      lines.push(`Ритм (${esc(r.kind)}): ${fmt(r.events)} пакетов, ${p}`);
    }
    document.getElementById("nodeMore").innerHTML = `<h3>Справочник</h3>${lines.join("<br>")}`;
  } catch { /* the card stays useful without these details */ }
}
document.getElementById("cardClose").addEventListener("click", () => {
  state.selected = null; document.getElementById("card").hidden = true; setFocus();
});

// ---------- loading ----------
async function load() {
  const days = document.getElementById("days").value, status = document.getElementById("status");
  status.textContent = "Загрузка…";
  try {
    const res = await fetch(`api/graph?days=${days}`);
    if (!res.ok) throw new Error((await res.json()).error || res.status);
    const data = await res.json();
    const first = !state.data;
    state.data = data; state.nodes = new Map(data.nodes.map((n) => [n.id, n]));
    for (const m of state.markers.values()) m.remove();
    state.markers = new Map();
    for (const n of data.nodes) {
      if (n.lat == null) continue;
      const marker = L.marker([n.lat, n.lon], { icon: pie(n), riseOnHover: true, zIndexOffset: n.kind === "observer" ? 1000 : Math.round(n.relayed / 10) });
      marker.on("mouseover", (ev) => { state.hover = n.id; setFocus(); showTip(nodeTip(n), ev.originalEvent.clientX, ev.originalEvent.clientY); });
      marker.on("mouseout", () => { state.hover = null; tip.hidden = true; setFocus(); });
      marker.on("click", () => select(n.id));
      state.markers.set(n.id, marker);
    }
    if (first && !viewFromHash()) {
      const own = data.nodes.filter((n) => n.placed === "own");
      if (own.length) map.fitBounds(L.latLngBounds(own.map((n) => [n.lat, n.lon])).pad(0.05));
    }
    status.textContent = "";
    applyFilters();
    if (state.selected && state.nodes.has(state.selected)) select(state.selected);
  } catch (err) {
    status.textContent = "Не удалось загрузить данные: " + err.message;
  }
}

// The view is kept in the address (#zoom/lat/lon), so a reload or a shared link opens the same place.
function viewFromHash() {
  const m = location.hash.match(/^#(\d+)\/(-?[\d.]+)\/(-?[\d.]+)$/);
  if (!m) return false;
  map.setView([+m[2], +m[3]], +m[1]);
  return true;
}
map.on("moveend", () => {
  updateLabels(); setFocusLabels(new Set());
  const c = map.getCenter();
  history.replaceState(null, "", `#${map.getZoom()}/${c.lat.toFixed(4)}/${c.lng.toFixed(4)}`);
});

document.getElementById("days").addEventListener("change", load);
document.querySelectorAll("[data-kind]").forEach((el) => el.addEventListener("change", () => {
  el.checked ? state.kinds.add(el.dataset.kind) : state.kinds.delete(el.dataset.kind); applyFilters();
}));
const slider = document.getElementById("min");
const onSlider = () => { state.min = minFromSlider(+slider.value); document.getElementById("minOut").textContent = state.min; };
slider.addEventListener("input", () => { onSlider(); applyFilters(); });
document.getElementById("risks").addEventListener("change", (ev) => { state.risks = ev.target.checked; applyFilters(); });
onSlider();
load();
setInterval(load, 10 * 60 * 1000);   // the calculation runs hourly; a refresh every 10 minutes is plenty
