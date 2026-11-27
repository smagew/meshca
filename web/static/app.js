/* meshca · map. Two modes over the same drawing:
   path — how packets travel to our observer (/api/graph): an arrow A -> B means B heard the packet from A and passed it on;
   talk — who talks to whom (/api/talk): a line joins the sender and the addressee, whatever way the packets went.
   Links are drawn on one canvas under the node markers; nodes are Leaflet markers with pie-chart icons. */
"use strict";

const COLOURS = { adverts: "#5b9cff", channel: "#5fd38d", direct: "#ffa24c", service: "#a9b3bf" };
const KIND_NAMES = { adverts: "объявления", channel: "каналы", direct: "личные сообщения", service: "служебные" };
const CRIT = "#ff4d5e", OBS = "#b980ff", UNKNOWN = "#8b95a3";

const MODES = {
  path: {
    text: "Как пакеты со всей сети доходят до нашего observer'а. Стрелка A → B: узел B услышал пакет от A и передал " +
          "его дальше. Кому был адресован сам пакет, здесь не показано — для этого второй режим.",
    unit: "пакетов",
    legend: `<ul>
      <li><b>Стрелка A → B</b> — B услышал пакеты от A и передал их дальше, к нам. Толще — больше пакетов.
        Цвет — каких больше: <b style="color:var(--adverts)">объявления</b>, <b style="color:var(--channel)">каналы</b>,
        <b style="color:var(--direct)">личные сообщения</b>, <b style="color:var(--service)">служебные</b>.</li>
      <li><b>Кружок</b> — узел. Размер — сколько чужих пакетов он передал дальше, доли цвета — каких.</li>
      <li><b class="gate">Фиолетовое кольцо</b> — ретранслятор, через который пакеты попадают к нашему observer'у.
        Поэтому к нему сходятся стрелки со всей сети: это не пакеты для него, а пакеты, которые он несёт к нам.</li>
      <li><b class="obs">Фиолетовая точка</b> — наш observer.</li>
      <li><b class="crit">Красное кольцо</b> — через этот узел идут все пакеты какого-то отправителя: без него
        отправитель перестанет до нас доходить.</li>
      <li><b>Пунктир</b> — узел не определён (известно только начало ключа) или его место примерное.</li>
    </ul>`,
  },
  talk: {
    text: "Кто кому пишет и кого опрашивает. Линия соединяет отправителя и получателя, каким бы путём ни шли пакеты. " +
          "Видны только разговоры, пакеты которых дошли до нашего observer'а.",
    unit: "сообщений",
    legend: `<ul>
      <li><b>Линия</b> — эти двое обменивались пакетами. Толще — чаще. Цвет — чем:
        <b style="color:var(--direct)">личные сообщения</b> — переписка людей;
        <b style="color:var(--service)">служебные</b> — приложение опрашивает ретранслятор или комнату, команды ей;
        <b style="color:var(--channel)">каналы</b> — ответы друг другу в общем чате (обращение @[имя] или быстрый ответ).</li>
      <li><b>Кружок</b> — устройство. Размер — сколько всего обменов. Телефоны обычно не сообщают координаты: они
        стоят у своего «домашнего» ретранслятора — того, через который их пакеты входят в сеть
        (кружок с <b>пунктирной</b> обводкой). Это район, а не точное место.</li>
      <li><b>Пунктирная линия</b> — один из собеседников определён неуверенно: в личных пакетах видны только
        первые два символа ключа.</li>
    </ul>`,
  },
};

const state = {
  mode: "path", raw: {}, data: null, nodes: new Map(), markers: new Map(),
  kinds: new Set(Object.keys(KIND_NAMES)), min: 5, risks: false,
  hover: null, selected: null, visibleEdges: [], visibleNodes: new Set(),
};

// ---------- map ----------
const map = L.map("map", { zoomControl: false, minZoom: 2, maxZoom: 16 })
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
const dominant = (mix) => Object.keys(mix).reduce((a, b) => ((mix[b] || 0) > (mix[a] || 0) ? b : a));
const shown = (mix) => [...state.kinds].reduce((s, k) => s + (mix[k] || 0), 0);
const when = (t) => new Date(t * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const nodeRadius = (n) => (n.kind === "observer" ? 9 : Math.min(30, 5 + 1.9 * Math.pow(Math.max(n.size, 1), 0.36)));
function mixLine(mix) {
  return Object.keys(KIND_NAMES).filter((k) => mix[k]).map((k) => `<span style="color:${COLOURS[k]}">${KIND_NAMES[k]} ${fmt(mix[k])}</span>`).join(" · ");
}

// ---------- both modes become one shape: nodes {id, label, kind, lat, lon, approx, size, mix, ...}, edges {source, target, mix, ...} ----------
function fromPath(d) {
  const gates = new Set(d.edges.filter((e) => e.target === "@observer").map((e) => e.source));
  const nodes = d.nodes.map((n) => ({ ...n, size: n.kind === "observer" ? n.heard : n.relayed, approx: n.placed === "neighbours", gate: gates.has(n.id) }));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = d.edges.map((e) => {
    const s = byId.get(e.source), t = byId.get(e.target);
    return { ...e, uncertain: s.kind === "unknown" || t.kind === "unknown" || s.approx || t.approx };
  });
  return { nodes, edges, arrows: true, unplaced: d.unplaced, computed_at: d.computed_at };
}
function fromTalk(d) {
  const mixOf = (l) => ({ direct: l.messages, service: l.service, channel: l.channel });
  const total = new Map();
  for (const l of d.links) for (const id of [l.a, l.b]) {
    const m = total.get(id) || { direct: 0, service: 0, channel: 0 };
    const x = mixOf(l); for (const k in x) m[k] += x[k];
    total.set(id, m);
  }
  const nodes = d.nodes.map((n) => ({ ...n, size: n.total, mix: total.get(n.id) || {}, critical: [], approx: n.placed === "home" }));
  const edges = d.links.map((l) => ({ ...l, source: l.a, target: l.b, mix: mixOf(l), packets: l.messages + l.service + l.channel }));
  return { nodes, edges, arrows: false, unplaced: d.unplaced };
}

// ---------- link canvas ----------
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
    const m = this._map; if (!m || !state.data) return;
    const size = m.getSize(), ratio = window.devicePixelRatio || 1, c = this._canvas;
    L.DomUtil.setPosition(c, m.containerPointToLayerPoint([0, 0]));
    c.width = size.x * ratio; c.height = size.y * ratio; c.style.width = size.x + "px"; c.style.height = size.y + "px";
    const ctx = c.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, size.x, size.y);
    this.geometry = [];
    const focus = state.hover || state.selected, arrows = state.data.arrows;
    const edges = [...state.visibleEdges].sort((a, b) => shown(a.mix) - shown(b.mix));   // thick ones on top
    for (const e of edges) {
      const s = state.nodes.get(e.source), t = state.nodes.get(e.target);
      if (!s || !t || s.lat == null || t.lat == null) continue;
      const p0 = m.latLngToContainerPoint([s.lat, s.lon]), p1 = m.latLngToContainerPoint([t.lat, t.lon]);
      const g = curve(p0, p1, nodeRadius(s) + 2, nodeRadius(t) + (arrows ? 4 : 2), arrows ? 0.16 : 0.08);
      if (!g) continue;
      const packets = shown(e.mix), width = Math.min(11, 0.8 + 1.25 * Math.log(1 + packets));
      const lit = !focus || e.source === focus || e.target === focus;
      const mix = {}; for (const k of state.kinds) mix[k] = e.mix[k] || 0;
      ctx.globalAlpha = lit ? (focus ? 0.95 : 0.75) : 0.06;
      ctx.strokeStyle = ctx.fillStyle = COLOURS[dominant(mix)];
      ctx.lineWidth = width; ctx.lineCap = "round";
      ctx.setLineDash(e.uncertain ? [Math.max(4, width * 1.6), Math.max(4, width * 1.4)] : []);
      ctx.beginPath(); ctx.moveTo(g.a.x, g.a.y); ctx.quadraticCurveTo(g.c.x, g.c.y, g.b.x, g.b.y); ctx.stroke();
      ctx.setLineDash([]);
      if (arrows) arrow(ctx, g, width);
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
function curve(p0, p1, r0, r1, bendShare) {
  const dx = p1.x - p0.x, dy = p1.y - p0.y, len = Math.hypot(dx, dy);
  if (len < r0 + r1 + 4) return null;
  // Bend to the left of the direction: A -> B and B -> A take opposite sides and never overlap.
  const nx = -dy / len, ny = dx / len, bend = Math.min(60, len * bendShare);
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
  const r = nodeRadius(n), size = 2 * r + 16, c = size / 2;
  let body;
  if (n.kind === "observer") {
    body = `<circle cx="${c}" cy="${c}" r="${r}" fill="${OBS}"/>`;
  } else {
    const parts = Object.keys(KIND_NAMES).map((k) => [k, n.mix[k] || 0]).filter(([, v]) => v > 0);
    const total = parts.reduce((s, [, v]) => s + v, 0);
    if (!total) body = `<circle cx="${c}" cy="${c}" r="${r}" fill="${UNKNOWN}" fill-opacity="0.6"/>`;
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
  const dashed = n.kind === "unknown" || n.approx;
  let rings = `<circle cx="${c}" cy="${c}" r="${r + 0.5}" fill="none" stroke="${dashed ? "#cfd6df" : "#0b0d10"}" stroke-width="1.5"${dashed ? ' stroke-dasharray="3 2"' : ""}/>`;
  if (n.gate) rings += `<circle cx="${c}" cy="${c}" r="${r + 3}" fill="none" stroke="${OBS}" stroke-width="2.5"/>`;
  if (n.critical && n.critical.length) rings += `<circle cx="${c}" cy="${c}" r="${r + (n.gate ? 6 : 3)}" fill="none" stroke="${CRIT}" stroke-width="2.5"/>`;
  return L.divIcon({ className: "node-icon", iconSize: [size, size], iconAnchor: [c, c], html: `<svg width="${size}" height="${size}">${body}${rings}</svg>` });
}

// ---------- drawing ----------
function applyFilters() {
  const d = state.data; if (!d) return;
  let edges = d.edges.filter((e) => shown(e.mix) >= state.min);
  if (state.mode === "path" && state.risks) {
    const crit = new Set(d.nodes.filter((n) => n.critical.length).map((n) => n.id));
    edges = edges.filter((e) => crit.has(e.source) || crit.has(e.target));
  }
  state.visibleEdges = edges;
  state.visibleNodes = new Set(edges.flatMap((e) => [e.source, e.target]));
  if (state.mode === "path" && state.risks) d.nodes.forEach((n) => n.critical.length && state.visibleNodes.add(n.id));
  for (const [id, marker] of state.markers) {
    const on = state.visibleNodes.has(id);
    if (on && !map.hasLayer(marker)) marker.addTo(map);
    if (!on && map.hasLayer(marker)) marker.remove();
  }
  updateLabels(); edgeLayer.redraw(); updateStats(); setFocus();
}

function neighboursOf(id) {
  const out = new Set();
  for (const e of state.visibleEdges) { if (e.source === id) out.add(e.target); if (e.target === id) out.add(e.source); }
  return out;
}

function updateLabels() {
  // Labels go to the most important nodes first and only where they fit; the rest appear on a closer zoom.
  // The focused node and its neighbours are always labelled.
  const focus = state.hover || state.selected;
  const must = new Set(focus ? [focus, ...neighboursOf(focus)] : []);
  const ranked = [...state.visibleNodes].map((id) => state.nodes.get(id))
    .filter((n) => n && n.lat != null)
    .sort((a, b) => must.has(b.id) - must.has(a.id) || (b.kind === "observer") - (a.kind === "observer")
      || (b.critical || []).length - (a.critical || []).length || b.size - a.size);
  const boxes = [], keep = new Set();
  for (const n of ranked) {
    const p = map.latLngToContainerPoint([n.lat, n.lon]), r = nodeRadius(n);
    const box = { x0: p.x - r, y0: p.y - Math.max(r, 8), x1: p.x + r + 6 + n.label.length * 6.6, y1: p.y + Math.max(r, 8) };
    const free = !boxes.some((b) => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0);
    if (free || must.has(n.id)) { keep.add(n.id); boxes.push(box); }
    else boxes.push({ x0: p.x - r, y0: p.y - r, x1: p.x + r, y1: p.y + r });
  }
  for (const [id, marker] of state.markers) {
    const want = keep.has(id) && map.hasLayer(marker);
    if (want && !marker.getTooltip()) {
      const n = state.nodes.get(id);
      marker.bindTooltip(esc(n.label), { permanent: true, direction: "right", className: "node-label", offset: [nodeRadius(n) + 4, 0] });
    } else if (!want && marker.getTooltip()) marker.unbindTooltip();
  }
}

function setFocus() {
  const focus = state.hover || state.selected;
  const lit = focus ? neighboursOf(focus) : new Set();
  if (focus) lit.add(focus);
  document.getElementById("map").classList.toggle("dim", !!focus);
  updateLabels();
  for (const [id, marker] of state.markers) {
    const el = marker.getElement(); if (el) el.classList.toggle("lit", lit.has(id));
    const tel = marker.getTooltip() && marker.getTooltip().getElement(); if (tel) tel.classList.toggle("lit", lit.has(id));
  }
  edgeLayer.redraw();
}

function updateStats() {
  const d = state.data, talk = state.mode === "talk";
  const amount = state.visibleEdges.reduce((s, e) => s + shown(e.mix), 0);
  const crit = d.nodes.filter((n) => (n.critical || []).length).length;
  document.getElementById("stats").innerHTML =
    `На карте <b>${fmt(state.visibleNodes.size)}</b> ${talk ? "собеседников" : "узлов"} и <b>${fmt(state.visibleEdges.length)}</b> ` +
    `${talk ? "пар" : "связей"} из ${fmt(d.edges.length)} · <b>${fmt(amount)}</b> ${talk ? "обменов" : "передач пакетов"}` +
    (talk ? "" : `<br>Критичных узлов: <b style="color:${CRIT}">${crit}</b>`) +
    (d.unplaced ? `<br>Не на карте (место неизвестно): ${d.unplaced}` : "") +
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
const ROLE_TEXT = {
  "ретранслятор": "Ретранслятор: принимает пакеты в эфире и передаёт их дальше, чтобы они дошли до тех, кого отправитель не слышит напрямую.",
  "клиент": "Устройство человека: телефон с приложением MeshCore или рация. С него пишут сообщения.",
  "сервер комнат": "Комната: общий чат на отдельном узле, хранит сообщения для тех, кто подключится позже.",
  "датчик": "Датчик: узел, который сам по себе передаёт показания.",
};
const KIND_TEXT = {
  adverts: "узел сообщает всем о себе · ADVERT",
  channel: "сообщения в общих чатах · GRP_TXT, GRP_DATA",
  direct: "переписка людей · TXT_MSG, ACK",
  service: "приложение опрашивает узел, команды · REQ, RESPONSE, ANON_REQ, PATH",
};
const plural = (n, one, few, many) => {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
};
const packetsWord = (n) => `${fmt(n)} ${plural(n, "пакет", "пакета", "пакетов")}`;
const periodText = () => ({ 1: "за сутки", 7: "за 7 дней", 30: "за 30 дней" }[document.getElementById("days").value]);

function mixTable(mix) {
  const total = Object.values(mix).reduce((s, v) => s + (v || 0), 0);
  if (!total) return "";
  return `<div class="mix">${Object.keys(KIND_NAMES).filter((k) => mix[k]).map((k) =>
    `<i style="background:${COLOURS[k]}"></i><span>${KIND_NAMES[k]} <span style="color:var(--muted)">— ${KIND_TEXT[k]}</span></span>` +
    `<span class="v">${fmt(mix[k])}</span><span class="pc">${Math.round((100 * mix[k]) / total)}%</span>`).join("")}</div>`;
}
function whoIs(n) {
  if (n.kind === "observer") return "Наш observer: приёмник, который слушает эфир и записывает всё, что слышит. Все данные этой карты — то, что услышал он.";
  if (n.kind === "unknown") return `Узел не определён: в пакетах видны только первые символы его ключа (${esc(n.label.replace("? ", ""))}), а под них подходит несколько узлов или ни одного известного.`;
  if (n.kind === "person") return "Известен только по имени, которое стоит в его сообщениях в каналах: своё объявление этот узел нам не присылал.";
  return ROLE_TEXT[n.role] || "Узел сети MeshCore.";
}

function nodeTip(n) {
  let h = `<div class="t">${esc(n.label)}</div><div class="k">${esc(n.role || { observer: "наш observer", unknown: "не определён", person: "известен по имени" }[n.kind] || "")}</div>`;
  h += `<p>${whoIs(n)}</p>`;
  if (state.mode === "path") {
    if (n.kind === "observer") {
      h += `<div class="num">Принял <b>${packetsWord(n.heard)}</b> ${periodText()}</div>`;
    } else {
      h += `<div class="num">Передал дальше <b>${packetsWord(n.relayed)}</b> ${periodText()}</div>` +
           `<p>Это чужие пакеты, которые прошли через него по пути к нам. Чем больше кружок, тем больше он передал.</p>`;
    }
    h += mixTable(n.mix);
    if (n.gate) h += `<div class="badge gate"><b>Через него пакеты попадают к нам.</b> Observer слышит сеть через этот ретранслятор, поэтому к нему сходятся стрелки со всей сети: он несёт к нам чужие пакеты, а не получает свои.</div>`;
    if (n.critical.length) {
      const names = n.critical.slice(0, 4).map(esc).join(", ") + (n.critical.length > 4 ? ` и ещё ${n.critical.length - 4}` : "");
      h += `<div class="badge crit"><b>Критичный узел.</b> Все пакеты этих отправителей идут через него; если он выключится, они перестанут до нас доходить: ${names}.</div>`;
    }
    if (n.approx) h += `<div class="badge note"><b>Место примерное.</b> Координат в его объявлении нет, поэтому он поставлен среди своих соседей.</div>`;
  } else {
    const partners = neighboursOfAll(n.id);
    h += `<div class="num"><b>${fmt(n.size)}</b> ${plural(n.size, "обмен", "обмена", "обменов")} с ${fmt(partners)} ${plural(partners, "собеседником", "собеседниками", "собеседниками")} ${periodText()}</div>`;
    h += mixTable(n.mix);
    if (n.placed === "home") h += `<div class="badge note"><b>Место примерное.</b> Своих координат нет. Поставлен у ретранслятора ${esc(n.home)}: через него его пакеты входят в сеть, значит, он где-то рядом.</div>`;
  }
  return h + `<div class="foot">Нажмите на кружок — подробности справа</div>`;
}

function neighboursOfAll(id) {
  const out = new Set();
  for (const e of state.data.edges) { if (e.source === id) out.add(e.target); if (e.target === id) out.add(e.source); }
  return out.size;
}

function edgeTip(e) {
  const s = state.nodes.get(e.source), t = state.nodes.get(e.target);
  if (state.mode === "path") {
    let h = `<div class="t">${esc(s.label)} → ${esc(t.label)}</div><div class="k">участок пути пакетов к нашему observer'у</div>`;
    h += `<div class="num"><b>${esc(t.label)}</b> услышал от <b>${esc(s.label)}</b> и передал дальше <b>${packetsWord(e.packets)}</b> ${periodText()}</div>`;
    h += mixTable(e.mix);
    h += `<p>Это дорога, по которой пакеты шли к нам, а не адресат: пакеты могли быть для кого угодно. Кто кому пишет — во втором режиме.</p>`;
    if (e.uncertain) h += `<div class="badge note"><b>Пунктир:</b> один из узлов не определён или стоит на карте примерно.</div>`;
    return h + `<div class="foot">Нажмите на линию — какие именно пакеты по ней шли и кому</div>`;
  }
  let h = `<div class="t">${esc(s.label)} ↔ ${esc(t.label)}</div><div class="k">обмен ${periodText()}, последний раз ${when(e.last)}</div><ul>`;
  if (e.mix.direct) h += `<li><b style="color:${COLOURS.direct}">${fmt(e.mix.direct)}</b> ${plural(e.mix.direct, "личное сообщение", "личных сообщения", "личных сообщений")} — переписка двух людей</li>`;
  if (e.mix.service) h += `<li><b style="color:${COLOURS.service}">${fmt(e.mix.service)}</b> ${plural(e.mix.service, "служебный пакет", "служебных пакета", "служебных пакетов")} — приложение одного опрашивает другого (вход, состояние, телеметрия) или отдаёт ему команды</li>`;
  if (e.mix.channel) h += `<li><b style="color:${COLOURS.channel}">${fmt(e.mix.channel)}</b> ${plural(e.mix.channel, "ответ", "ответа", "ответов")} друг другу в каналах ${e.channels.map(esc).join(", ")}: обращение @[имя] или ответ сразу после сообщения другого</li>`;
  h += `</ul><div class="num">${esc(s.label)} → ${esc(t.label)}: <b>${fmt(e.a_to_b)}</b> &nbsp;·&nbsp; ${esc(t.label)} → ${esc(s.label)}: <b>${fmt(e.b_to_a)}</b></div>`;
  if (e.uncertain) h += `<div class="badge note"><b>Пунктир: собеседник определён неуверенно.</b> В личных пакетах видны только первые два символа ключа, и по ним нельзя точно сказать, кто это.</div>`;
  return h + `<div class="foot">Нажмите на линию — список обменов по видам</div>`;
}

// ---------- help for the controls ----------
const HELP = {
  "mode-path": `<div class="t">Как пакеты доходят до нас</div><p>Дорога пакетов по сети к нашему observer'у: кто от кого их услышал и передал дальше.</p><p>Отвечает на вопросы: кто больше всех носит пакеты, какие связи главные, на ком держится связь с дальними частями сети.</p>`,
  "mode-talk": `<div class="t">Кто с кем общается</div><p>Линия соединяет того, кто отправил, и того, кому было адресовано, — каким бы путём ни шли пакеты.</p><p>Видно: кто с кем переписывается, кто чей ретранслятор опрашивает, кто кому отвечает в каналах.</p>`,
  days: `<div class="t">Период</div><p>За сколько последних дней собраны данные на карте. Расчёт обновляется раз в час.</p>`,
  adverts: `<div class="t" style="color:${COLOURS.adverts}">Объявления <code class="proto">ADVERT · 4</code></div><p>Узел время от времени сообщает всей сети о себе: «я есть», своё имя, роль и координаты. Так приложения узнают о соседях.</p><p>Это самый частый вид фона в сети.</p>`,
  channel: `<div class="t" style="color:${COLOURS.channel}">Каналы <code class="proto">GRP_TXT · 5</code> <code class="proto">GRP_DATA · 6</code></div><p>Сообщения в общих чатах: Public, #test и другие. Их получают все, кто подписан на канал.</p><p>В режиме «Кто с кем» — ответы людей друг другу в этих чатах.</p>`,
  direct: `<div class="t" style="color:${COLOURS.direct}">Личные сообщения <code class="proto">TXT_MSG · 2</code> <code class="proto">ACK · 3</code></div><p>Переписка от человека к человеку и подтверждения, что сообщение доставлено.</p><p>Сами тексты зашифрованы и нам не видны — только факт обмена.</p>`,
  service: `<div class="t" style="color:${COLOURS.service}">Служебные</div><p>Пакеты, которые шлют приложения, а не люди:</p><ul><li><code class="proto">REQ · 0</code> запрос к узлу: вход, состояние, телеметрия, соседи;</li><li><code class="proto">RESPONSE · 1</code> ответ узла на запрос;</li><li><code class="proto">ANON_REQ · 7</code> анонимный запрос: вход незнакомого устройства;</li><li><code class="proto">PATH · 8</code> сообщённый обратный путь: узел говорит отправителю, каким путём ему отвечать.</li></ul><p>В режиме «Кто с кем» сюда же попадают <code class="proto">TXT_MSG · 2</code> ретранслятору — это команды его удалённой консоли.</p>`,
  min: `<div class="t">Порог</div><p>Связи, по которым прошло меньше указанного числа, скрыты, чтобы карта не превращалась в паутину. Сдвиньте влево — увидите всё, вправо — только главные связи.</p>`,
  risks: `<div class="t" style="color:${CRIT}">Только узлы, без которых кто-то пропадёт</div><p>Оставляет на карте только критичные узлы и их связи.</p><p>Критичный узел — тот, через который до нас идут <b>все</b> пакеты какого-то отправителя. Если он выключится, этот отправитель перестанет до нас доходить.</p><p>Так видно, на ком держится связь с дальними частями сети и где не хватает обходных путей.</p>`,
  stats: `<div class="t">Что сейчас на карте</div><p>Сколько узлов и связей показано с учётом фильтров и сколько всего есть за период.</p><p>«Не на карте» — узлы, место которых неизвестно: нет ни своих координат, ни соседей с координатами.</p>`,
};
let helpEl = null;
document.addEventListener("mouseover", (ev) => {
  const el = ev.target.closest("[data-help]");
  if (el === helpEl) return;
  helpEl = el;
  if (!el) { if (!state.hover) tip.hidden = true; return; }
  const r = el.getBoundingClientRect(), panel = document.getElementById("panel").getBoundingClientRect();
  showTip(HELP[el.dataset.help] || "", Math.max(r.right, panel.right) - 4, r.top - 14);
});
document.addEventListener("click", (ev) => {
  const el = ev.target.closest(".help");          // on touch screens a tap on "?" shows the same text
  if (el) { ev.preventDefault(); const r = el.getBoundingClientRect(); showTip(HELP[el.dataset.help], r.right, r.bottom); }
});

map.on("mousemove", (ev) => {
  if (state.hover) return;
  const e = edgeLayer.edgeAt(ev.containerPoint);
  if (e) { showTip(edgeTip(e), ev.originalEvent.clientX, ev.originalEvent.clientY); map.getContainer().style.cursor = "pointer"; }
  else { tip.hidden = true; map.getContainer().style.cursor = ""; }
});
map.on("click", (ev) => {
  const e = edgeLayer.edgeAt(ev.containerPoint);
  if (e) openLink(e);
  else if (state.selected) closeCard();
});

// ---------- link card: what exactly a link is made of ----------
const TYPE_INFO = {
  0: ["service", "запрос к узлу", "приложение просит у ретранслятора или комнаты вход, состояние, телеметрию или соседей; что именно — зашифровано"],
  1: ["service", "ответ узла", "ответ ретранслятора или комнаты на запрос: состояние, телеметрия, подтверждение входа"],
  2: ["direct", "личное сообщение", "сообщение одного устройства другому; если получатель — ретранслятор, это команда его консоли"],
  3: ["direct", "подтверждение доставки", "получатель сообщает отправителю, что сообщение дошло"],
  4: ["adverts", "объявление", "узел сообщает всем: «я есть», имя, роль, координаты"],
  5: ["channel", "сообщение в канале", "текст в общем чате"],
  6: ["channel", "данные в канале", "не текст, а данные для всех подписчиков канала"],
  7: ["service", "анонимный запрос", "устройство, ещё не знакомое узлу, входит в комнату или на ретранслятор (обычно с паролем)"],
  8: ["service", "обратный путь", "узел сообщает отправителю, каким путём отвечать ему напрямую, без рассылки по всей сети"],
  9: ["service", "трассировка", "проверка пути: каждый ретранслятор по дороге добавляет уровень сигнала"],
  10: ["service", "часть составного пакета", "кусок большого сообщения, разбитого на несколько пакетов"],
  11: ["service", "управляющий пакет", "служебный обмен узлов, например поиск соседей"],
  15: ["service", "особый пакет", "пакет нестандартного формата"],
};
// Protocol names of the payload types, as MeshCore calls them.
const PROTO = { 0: "REQ", 1: "RESPONSE", 2: "TXT_MSG", 3: "ACK", 4: "ADVERT", 5: "GRP_TXT", 6: "GRP_DATA", 7: "ANON_REQ",
  8: "PATH", 9: "TRACE", 10: "MULTIPART", 11: "CONTROL", 15: "RAW_CUSTOM" };
const typeInfo = (t) => TYPE_INFO[t] || ["service", `тип ${t}`, ""];
const proto = (t) => `<code class="proto" title="Тип пакета в протоколе MeshCore: название и код">${PROTO[t] || "TYPE"} · ${t}</code>`;
const ROUTE_TEXT = { FLOOD: "рассылка по всей сети", TRANSPORT_FLOOD: "рассылка по сети с кодом региона", DIRECT: "по заранее известному пути", TRANSPORT_DIRECT: "по известному пути с кодом региона" };
function packetLine(p) {
  const bits = [`<span title="Хэш пакета: одинаков у всех копий одного пакета">#${esc(p.hash)}</span>`];
  if (p.route) bits.push(`<span title="${ROUTE_TEXT[p.route] || ""}">${esc(p.route)}</span>`);
  if (p.hops != null) bits.push(`<span title="Сколько ретрансляторов пакет прошёл до нас">${p.hops} ${plural(p.hops, "переход", "перехода", "переходов")}</span>`);
  if (p.bytes) bits.push(`<span title="Размер пакета в эфире">${p.bytes} байт</span>`);
  if (p.snr != null) bits.push(`<span title="Отношение сигнал/шум при приёме: чем больше, тем чище">SNR ${p.snr} дБ</span>`);
  if (p.rssi != null) bits.push(`<span title="Уровень сигнала при приёме: ближе к нулю — сильнее">RSSI ${p.rssi} дБм</span>`);
  if (p.receptions > 1) bits.push(`<span title="Сколько раз observer услышал этот пакет разными путями">принят ${p.receptions} раз</span>`);
  return `<div class="pkt">${bits.join(" · ")}</div>`;
}

function kindsTable(counts, info = typeInfo) {
  const total = counts.reduce((s, c) => s + c.n, 0) || 1;
  return `<table class="kinds-table">${counts.map((c) => {
    const [kind, name, text] = info(c.type);
    return `<tr class="ktype" data-type="${c.type}"><td><i class="dot" style="background:${COLOURS[kind]}"></i></td>` +
      `<td><b>${name}</b> ${proto(c.type)}<div class="meta">${text}</div></td><td class="n">${fmt(c.n)}<div class="meta">${Math.round((100 * c.n) / total)}%</div></td></tr>`;
  }).join("")}</table>`;
}

async function openLink(e) {
  state.selected = null; setFocus();
  const s = state.nodes.get(e.source), t = state.nodes.get(e.target);
  const card = document.getElementById("card"), body = document.getElementById("cardBody");
  card.hidden = false;
  const node = (n) => `<span class="link" data-id="${esc(n.id)}">${esc(n.label)}</span>`;
  const bindNodes = () => body.querySelectorAll(".link[data-id]").forEach((el) => el.addEventListener("click", () => select(el.dataset.id)));
  const filterable = (render) => {
    body.querySelectorAll(".ktype").forEach((row) => row.addEventListener("click", () => {
      const on = !row.classList.contains("sel");
      body.querySelectorAll(".ktype").forEach((r) => r.classList.remove("sel"));
      if (on) row.classList.add("sel");
      render(on ? row.dataset.type : null);
    }));
  };
  if (state.mode === "talk") {
    // Between a device and a repeater a text message is a command to the repeater's console.
    const repeater = [s, t].some((n) => n.role === "ретранслятор");
    const info = (type) => (repeater && +type === 2
      ? ["service", "команда ретранслятору", "текстовая команда его удалённой консоли (настройки, статус, перезапуск); текст зашифрован"]
      : +type === 5 ? ["channel", "ответ в канале", "обращение @[имя] или ответ сразу после сообщения другого"] : typeInfo(type));
    const counts = Object.entries(e.items.reduce((m, i) => ((m[i[1]] = (m[i[1]] || 0) + 1), m), {}))
      .map(([type, n]) => ({ type: +type, n })).sort((a, b) => b.n - a.n);
    body.innerHTML = `<h2>${node(s)} ↔ ${node(t)}</h2>
      <div class="meta">Что эти двое отправляли друг другу ${periodText()}. Нажмите на строку — останутся только такие пакеты.</div>
      <h3>Из чего состоит</h3>${kindsTable(counts, info)}
      <h3>Последние обмены</h3><div id="linkList"></div>`;
    const render = (type) => {
      const items = e.items.filter((i) => type == null || String(i[1]) === String(type)).slice(0, 80);
      document.getElementById("linkList").innerHTML = `<table>${items.map((i) => {
        const [from, to] = i[2] ? [s, t] : [t, s];
        return `<tr><td class="when">${when(i[0])}</td><td>${esc(from.label)} → ${esc(to.label)}<div class="meta">${info(i[1])[1]} ${proto(i[1])}${i[3] ? " · " + esc(i[3]) : ""}</div>${i[4] ? `<div class="pkt">#${esc(i[4])}</div>` : ""}</td></tr>`;
      }).join("")}</table>` + (e.items.length >= 150 ? `<div class="meta">показаны последние 150</div>` : "");
    };
    render(null); filterable(render); bindNodes();
    return;
  }
  body.innerHTML = `<h2>${node(s)} → ${node(t)}</h2>
    <div class="meta">${esc(t.label)} услышал эти пакеты от ${esc(s.label)} и передал дальше, к нам. Это дорога пакетов: адресованы они могли быть кому угодно — кому именно, видно в списке ниже.</div>
    <div class="meta">Загрузка…</div>`;
  bindNodes();
  let data;
  try {
    data = await (await fetch(`api/link?source=${encodeURIComponent(e.source)}&target=${encodeURIComponent(e.target)}&days=${document.getElementById("days").value}`)).json();
  } catch { body.querySelector(".meta:last-child").textContent = "Не удалось загрузить"; return; }
  const counts = data.kinds.map((k) => ({ type: k.type, n: k.packets }));
  body.innerHTML = `<h2>${node(s)} → ${node(t)}</h2>
    <div class="meta">${esc(t.label)} услышал эти пакеты от ${esc(s.label)} и передал дальше, к нам, ${periodText()}. Это дорога пакетов: адресованы они могли быть кому угодно — кому именно, видно в списке ниже. Нажмите на строку — останутся только такие пакеты.</div>
    <h3>Из чего состоит</h3>${kindsTable(counts)}
    <h3>Последние пакеты</h3><div id="linkList"></div>`;
  const render = (type) => {
    const rows = data.packets.filter((p) => type == null || String(p.type) === String(type)).slice(0, 80);
    document.getElementById("linkList").innerHTML = rows.length ? `<table>${rows.map((p) => {
      const [, name] = typeInfo(p.type);
      const who = p.sender ? `от <b>${esc(p.sender)}</b>` : "отправитель неизвестен";
      const to = p.addressee ? ` → ${p.type === 5 || p.type === 6 ? "в канал " : ""}<b>${esc(p.addressee)}</b>` : p.type === 4 ? " → всем" : "";
      const path = p.path && p.path.length ? `<div class="meta path">путь: ${p.path.map(esc).join(" → ")} → наш observer</div>` : "";
      return `<tr><td class="when">${new Date(p.at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</td>` +
        `<td>${name} ${proto(p.type)}<div class="meta">${who}${to}</div>${path}${packetLine(p)}</td></tr>`;
    }).join("")}</table>` + (data.packets.length >= 150 ? `<div class="meta">показаны последние 150</div>` : "") : `<div class="meta">Нет пакетов</div>`;
  };
  render(null); filterable(render); bindNodes();
}
map.on("zoomend", setFocus);

// ---------- node card ----------
function closeCard() { state.selected = null; document.getElementById("card").hidden = true; setFocus(); }
async function select(id) {
  state.selected = id; setFocus();
  const n = state.nodes.get(id), card = document.getElementById("card"), body = document.getElementById("cardBody");
  if (!n) return;
  card.hidden = false;
  const total = Object.values(n.mix).reduce((s, v) => s + v, 0) || 1;
  const bar = `<div class="bar">${Object.keys(KIND_NAMES).map((k) => `<i style="width:${(100 * (n.mix[k] || 0)) / total}%;background:${COLOURS[k]}"></i>`).join("")}</div>`;
  const rows = (list, other, value) => list.slice(0, 12).map((e) => {
    const o = state.nodes.get(e[other]);
    return `<tr><td class="link" data-id="${esc(e[other])}">${esc(o ? o.label : e[other])}</td><td class="n">${value(e)}</td></tr>`;
  }).join("") + (list.length > 12 ? `<tr><td colspan="2" class="meta">и ещё ${list.length - 12}</td></tr>` : "");
  const edges = state.data.edges;
  let html = `<h2>${esc(n.label)}</h2><div class="meta">${esc(n.role || { unknown: "не определён: известно только начало ключа", observer: "наш observer", person: "известен только по имени в сообщениях" }[n.kind] || "")}</div>`;
  if (state.mode === "path") {
    const incoming = edges.filter((e) => e.target === id).sort((a, b) => b.packets - a.packets);
    const outgoing = edges.filter((e) => e.source === id).sort((a, b) => b.packets - a.packets);
    html += `<div>${n.kind === "observer" ? `Принял <b>${fmt(n.heard)}</b> пакетов` : `Услышал и передал дальше <b>${fmt(n.relayed)}</b> пакетов`}</div>
      ${bar}<div class="meta">${mixLine(n.mix)}</div>
      ${n.gate ? `<p class="meta">Через этот ретранслятор пакеты попадают к нашему observer'у. Поэтому к нему сходится трафик со всей сети: это чужие пакеты, которые он несёт к нам, а не пакеты для него.</p>` : ""}
      ${n.critical.length ? `<h3 class="warn">Без него до нас не дойдут (${n.critical.length})</h3><div>${n.critical.map(esc).join(", ")}</div>` : ""}
      ${incoming.length ? `<h3>Слышит пакеты от</h3><table>${rows(incoming, "source", (e) => fmt(e.packets))}</table>` : ""}
      ${outgoing.length ? `<h3>Передаёт дальше</h3><table>${rows(outgoing, "target", (e) => fmt(e.packets))}</table>` : ""}`;
  } else {
    const partners = edges.filter((e) => e.source === id || e.target === id)
      .map((e) => ({ ...e, other: e.source === id ? e.target : e.source })).sort((a, b) => b.packets - a.packets);
    html += `<div>Обменов: <b>${fmt(n.size)}</b> с ${partners.length} собеседниками</div>${bar}<div class="meta">${mixLine(n.mix)}</div>
      ${n.placed === "home" ? `<p class="meta">Координат нет: на карте стоит у ретранслятора ${esc(n.home)}, через который его пакеты входят в сеть.</p>` : ""}
      ${n.lat == null ? `<p class="meta">Где находится, неизвестно: на карте его нет.</p>` : ""}
      <h3>С кем</h3><table>${rows(partners, "other", (e) => mixLine(e.mix))}</table>`;
  }
  body.innerHTML = html + `<div id="nodeMore" class="meta"></div>`;
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
    for (const r of more.rhythm || []) {
      const p = r.period_confirmed ? `период ${Math.round(r.period_s / 60)} мин` : r.judged ? "без явного периода" : "мало данных для ритма";
      lines.push(`Ритм (${esc(r.kind)}): ${fmt(r.events)} пакетов, ${p}`);
    }
    if (lines.length) document.getElementById("nodeMore").innerHTML = `<h3>Справочник</h3>${lines.join("<br>")}`;
  } catch { /* the card stays useful without these details */ }
}
document.getElementById("cardClose").addEventListener("click", closeCard);

// ---------- loading ----------
function showMode() {
  const m = MODES[state.mode];
  document.querySelectorAll(".modes button").forEach((b) => b.classList.toggle("on", b.dataset.mode === state.mode));
  document.querySelectorAll("[data-modes]").forEach((el) => (el.hidden = !el.dataset.modes.split(" ").includes(state.mode)));
  document.getElementById("modeText").textContent = m.text;
  document.getElementById("legend").innerHTML = m.legend;
  document.getElementById("minUnit").textContent = m.unit;
}

async function load(fit) {
  const days = document.getElementById("days").value, status = document.getElementById("status"), mode = state.mode;
  status.textContent = "Загрузка…";
  try {
    const res = await fetch(`api/${mode === "path" ? "graph" : "talk"}?days=${days}`);
    if (!res.ok) throw new Error((await res.json()).error || res.status);
    const raw = await res.json();
    if (mode !== state.mode) return;
    state.data = mode === "path" ? fromPath(raw) : fromTalk(raw);
    state.nodes = new Map(state.data.nodes.map((n) => [n.id, n]));
    for (const m of state.markers.values()) m.remove();
    state.markers = new Map();
    for (const n of state.data.nodes) {
      if (n.lat == null) continue;
      const marker = L.marker([n.lat, n.lon], { icon: pie(n), riseOnHover: true, zIndexOffset: n.kind === "observer" ? 1000 : Math.round(n.size / 10) });
      marker.on("mouseover", (ev) => { state.hover = n.id; setFocus(); showTip(nodeTip(n), ev.originalEvent.clientX, ev.originalEvent.clientY); });
      marker.on("mouseout", () => { state.hover = null; tip.hidden = true; setFocus(); });
      marker.on("click", () => select(n.id));
      state.markers.set(n.id, marker);
    }
    if (fit && !viewFromHash()) {
      const own = state.data.nodes.filter((n) => n.lat != null && !n.approx);
      if (own.length) map.fitBounds(L.latLngBounds(own.map((n) => [n.lat, n.lon])).pad(0.05));
    }
    status.textContent = "";
    applyFilters();
    if (state.selected && state.nodes.has(state.selected)) select(state.selected); else if (state.selected) closeCard();
  } catch (err) {
    status.textContent = "Не удалось загрузить данные: " + err.message;
  }
}

// The view is kept in the address (#zoom/lat/lon[/talk]), so a reload or a shared link opens the same place and mode.
function viewFromHash() {
  const m = location.hash.match(/^#(\d+)\/(-?[\d.]+)\/(-?[\d.]+)/);
  if (!m) return false;
  map.setView([+m[2], +m[3]], +m[1]);
  return true;
}
function saveView() {
  const c = map.getCenter();
  history.replaceState(null, "", `#${map.getZoom()}/${c.lat.toFixed(4)}/${c.lng.toFixed(4)}${state.mode === "talk" ? "/talk" : ""}`);
}
map.on("moveend", saveView);

document.querySelectorAll(".modes button").forEach((b) => b.addEventListener("click", () => {
  if (state.mode === b.dataset.mode) return;
  state.mode = b.dataset.mode; state.selected = null; document.getElementById("card").hidden = true;
  slider.value = DEFAULT_SLIDER[state.mode]; onSlider();
  showMode(); saveView(); load(false);
}));
document.getElementById("days").addEventListener("change", () => load(false));
document.querySelectorAll("[data-kind]").forEach((el) => el.addEventListener("change", () => {
  el.checked ? state.kinds.add(el.dataset.kind) : state.kinds.delete(el.dataset.kind); applyFilters();
}));
const slider = document.getElementById("min");
const DEFAULT_SLIDER = { path: 35, talk: 10 };   // about 11 packets per link; 2 exchanges per pair
const onSlider = () => { state.min = minFromSlider(+slider.value); document.getElementById("minOut").textContent = state.min; };
slider.addEventListener("input", () => { onSlider(); applyFilters(); });
document.getElementById("risks").addEventListener("change", (ev) => { state.risks = ev.target.checked; applyFilters(); });

if (location.hash.endsWith("/talk")) state.mode = "talk";
slider.value = DEFAULT_SLIDER[state.mode];
showMode(); onSlider(); load(true);
setInterval(() => load(false), 10 * 60 * 1000);   // the calculations run hourly; every 10 minutes is plenty
