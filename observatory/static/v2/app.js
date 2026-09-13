/* Observatory v2 — live mesh map + packet stream, fed by /v2/api/state then Socket.IO deltas.
   Protocol-agnostic: only the neutral envelope fields are read (docs/OBSERVATORY_V2.md §2c).
   Vanilla JS, no build step. Relative URLs only — works on whatever host you opened it from. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  // API base: same-origin when Flask serves this page; a native shell (Capacitor) sets
  // window.WILDCAT_API_BASE (or the user stores v2.apiBase) to point at the Den.
  let API = "";
  try { API = (window.WILDCAT_API_BASE || localStorage.getItem("v2.apiBase") || "").replace(/\/+$/, ""); } catch (e) {}
  const A = (p) => API ? `${API}/v2/${p}` : p;
  const _fetch = window.fetch.bind(window);
  const fetch = (p, o) => _fetch(typeof p === "string" && (p.startsWith("api/") || p === "sw.js") ? A(p) : p, o);
  const now = () => Date.now() / 1000;
  const ONLINE = 15 * 60, WARM = 60 * 60, LINK_FADE = 3 * 3600;

  // ---------------------------------------------------------------- map
  const map = L.map("map", { zoomControl: true, attributionControl: true, preferCanvas: false, worldCopyJump: true })
    .setView([38.88, -84.62], 11);
  map.zoomControl.setPosition("bottomright");
  // Keyless OSM tiles, darkened in CSS (.dark-tiles) — no API key, no account, and the same
  // URL shape a Phase-3 offline tile cache can serve. (CARTO's dark basemap now needs a key.)
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, className: "dark-tiles",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  const linkLayer = L.layerGroup().addTo(map);
  const nodeLayer = L.layerGroup().addTo(map);
  const fxLayer = L.layerGroup().addTo(map);

  // ---------------------------------------------------------------- state
  const S = { myId: null, roster: {}, links: {}, markers: {}, lines: {}, filter: "all", times: [], fitted: false, selected: null };

  function ageClass(n) {
    if (n.id === S.myId) return "base";
    const lh = n.last_heard || 0, dt = now() - lh;
    return dt < ONLINE ? "on" : dt < WARM ? "warm" : "cold";
  }
  function pos(n) { const p = n && n.position; return p && p.lat != null && p.lon != null ? [p.lat, p.lon] : null; }
  function name(n) { return (n && (n.short_name || (n.id || "").slice(-4))) || "?"; }
  function ago(ts) {
    if (!ts) return "never";
    const d = Math.max(0, now() - ts);
    if (d < 60) return Math.floor(d) + "s ago";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }
  function snrClass(v) { return v == null ? "" : v >= 5 ? "good" : v >= -5 ? "ok" : "bad"; }

  // ---------------------------------------------------------------- nodes
  function icon(n) {
    const cls = ageClass(n);
    const html = `<div class="node ${cls}"><div class="core"></div><div class="lbl">${escape(name(n))}</div></div>`;
    const size = cls === "base" ? 22 : 14;
    return L.divIcon({ className: "", html, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
  }
  function upsertNode(n) {
    if (!n || !n.id) return;
    S.roster[n.id] = Object.assign(S.roster[n.id] || {}, n);
    const node = S.roster[n.id], p = pos(node);
    let m = S.markers[n.id];
    if (!p) { if (m) { nodeLayer.removeLayer(m); delete S.markers[n.id]; } return; }
    if (!m) {
      m = L.marker(p, { icon: icon(node), zIndexOffset: n.id === S.myId ? 1000 : 0 });
      m.bindTooltip(tip(node), { className: "node-tip", direction: "top", offset: [0, -10] });
      m.on("click", () => showCard(node.id));
      m.addTo(nodeLayer); S.markers[n.id] = m;
    } else {
      m.setLatLng(p); m.setIcon(icon(node)); m.setTooltipContent(tip(node));
    }
  }
  function tip(n) {
    const bits = [`<b>${escape(name(n))}</b>`];
    if (n.long_name) bits.push(escape(n.long_name));
    bits.push(`${escape(n.id)} · ${ago(n.last_heard)}` + (n.hops_away != null ? ` · ${n.hops_away} hop${n.hops_away === 1 ? "" : "s"}` : ""));
    return bits.join("<br>");
  }
  function refreshAges() { for (const id in S.markers) S.markers[id].setIcon(icon(S.roster[id])); }

  function flashNode(id) {
    const m = S.markers[id]; if (!m) return;
    const el = m.getElement(); const nd = el && el.querySelector(".node"); if (!nd) return;
    nd.classList.remove("flash"); void nd.offsetWidth; nd.classList.add("flash");
    const r = L.marker(m.getLatLng(), { icon: L.divIcon({ className: "", html: '<div class="ripple"></div>', iconSize: [10, 10], iconAnchor: [5, 5] }), interactive: false });
    r.addTo(fxLayer); setTimeout(() => fxLayer.removeLayer(r), 1200);
  }

  // ---------------------------------------------------------------- links + pulses
  function upsertLink(l) {
    S.links[l.key] = l;
    const a = pos(S.roster[l.a]), b = pos(S.roster[l.b]);
    if (!a || !b) return;
    const age = now() - (l.last || 0), op = Math.max(0.12, 0.85 - age / LINK_FADE);
    let line = S.lines[l.key];
    const cls = "link-line" + (l.kind === "neighbor" ? " neighbor" : "") + (age < 120 ? " hot" : "");
    if (!line) { line = L.polyline([a, b], { className: cls, weight: 2, opacity: op, interactive: false }); line.addTo(linkLayer); S.lines[l.key] = line; }
    else { line.setLatLngs([a, b]); line.setStyle({ opacity: op, className: cls }); const el = line.getElement(); if (el) el.setAttribute("class", cls + " leaflet-interactive"); }
    if (l.snr != null) line.bindTooltip(`${escape(name(S.roster[l.a]))} ↔ ${escape(name(S.roster[l.b]))} · SNR ${l.snr.toFixed(1)}`, { className: "node-tip", sticky: true });
  }
  function refreshLinks() { for (const k in S.links) upsertLink(S.links[k]); }

  function pulse(fromId, toId, multi) {
    const a = pos(S.roster[fromId]), b = pos(S.roster[toId]); if (!a || !b) return;
    const m = L.marker(a, { icon: L.divIcon({ className: "", html: `<div class="pulse${multi ? " multi" : ""}"></div>`, iconSize: [10, 10], iconAnchor: [5, 5] }), interactive: false, zIndexOffset: 2000 });
    m.addTo(fxLayer);
    const t0 = performance.now(), dur = multi ? 1600 : 1100;
    (function step(t) {
      const k = Math.min(1, (t - t0) / dur), e = k < .5 ? 2 * k * k : -1 + (4 - 2 * k) * k;
      m.setLatLng([a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e]);
      if (k < 1) requestAnimationFrame(step); else fxLayer.removeLayer(m);
    })(t0);
  }

  // ---------------------------------------------------------------- feed
  const list = $("feed-list");
  let addPacket = function (p, fresh) {
    const el = document.createElement("div");
    el.className = "pkt" + (fresh ? " fresh" : ""); el.dataset.kind = p.kind;
    const to = p.broadcast ? '<span class="to">→ all</span>' : p.to ? `<span class="to">→ ${escape(p.to_name || p.to.slice(-4))}</span>` : "";
    const snr = p.snr != null ? `<span class="snr ${snrClass(p.snr)}">${p.snr.toFixed(1)} dB</span>` : "";
    const rssi = p.rssi != null ? `<span>${p.rssi} dBm</span>` : "";
    const hops = p.hops != null ? `<span>${p.hops === 0 ? "direct" : p.hops + " hop" + (p.hops === 1 ? "" : "s")}</span>` : "";
    el.innerHTML = `<div class="bar"></div>
      <div><div class="who">${escape(p.from_name || p.from)} ${to} <span class="kind">${escape(p.kind)}</span></div><div class="sum">${escape(p.summary || "")}</div></div>
      <div class="meta"><span data-ts="${p.ts}">${ago(p.ts)}</span>${snr}${rssi}${hops}</div>`;
    el.hidden = !(S.filter === "all" || S.filter === p.kind);
    list.prepend(el);
    if (fresh) setTimeout(() => el.classList.remove("fresh"), 1500);
    while (list.children.length > 150) list.lastChild.remove();
  };
  $("filters").addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    S.filter = b.dataset.kind;
    for (const c of $("filters").children) c.classList.toggle("on", c === b);
    for (const el of list.children) el.hidden = !(S.filter === "all" || S.filter === el.dataset.kind);
  });
  function refreshTimes() { for (const el of list.querySelectorAll("[data-ts]")) el.textContent = ago(+el.dataset.ts); }

  // ---------------------------------------------------------------- stats + status
  function setStat(id, v, bump) { const el = $(id); if (el.textContent !== String(v)) { el.textContent = v; el.classList.remove("tick"); void el.offsetWidth; el.classList.add("tick"); if (bump) { const s = el.parentElement; s.classList.remove("bump"); void s.offsetWidth; s.classList.add("bump"); } } }
  const rateHist = [];   // per-minute counts for the tiny sparkline in the top bar
  function rateSpark() {
    const t = now(), cur = S.times.filter(x => x >= t - 60).length;
    if (!rateHist.length || t - rateHist[rateHist.length - 1].t >= 30) rateHist.push({ t, n: cur }); else rateHist[rateHist.length - 1].n = cur;
    while (rateHist.length > 24) rateHist.shift();
    const svg = $("s-rate-spark"); if (!svg || rateHist.length < 2) return;
    const max = Math.max(1, ...rateHist.map(r => r.n));
    svg.innerHTML = `<polyline points="${rateHist.map((r, i) => `${(i / (rateHist.length - 1) * 58 + 1).toFixed(1)},${(13 - r.n / max * 11).toFixed(1)}`).join(" ")}"/>`;
  }
  function refreshStats(stats) {
    const t = now(), hour = t - 3600, R = Object.values(S.roster);
    setStat("s-nodes", R.length);
    setStat("s-heard", R.filter(n => (n.last_heard || 0) >= hour).length);
    setStat("s-map", R.filter(n => pos(n)).length);
    S.times = S.times.filter(x => x >= t - 60);
    setStat("s-rate", stats && stats.per_min != null && !S.times.length ? Math.round(stats.per_min) : S.times.length, true);
    const me = S.roster[S.myId];
    setStat("s-util", me && me.channel_util != null ? me.channel_util.toFixed(1) + "%" : "–");
    rateSpark();
  }
  function setStatus(bus, meshd) {
    const dot = $("dot-bus"), txt = $("status-text"), st = meshd && meshd.state;
    dot.className = "dot " + (bus && st === "connected" ? "ok" : bus ? "warn" : "bad");
    txt.textContent = !bus ? "bus offline" : st === "connected" ? `radio ${meshd.radio || ""} · live` : `radio ${st || "?"}`;
  }

  // ---------------------------------------------------------------- node card
  const ARC = 2 * Math.PI * 27;
  function snrPct(v) { return v == null ? 0 : Math.max(0, Math.min(1, (v + 20) / 32)); }   // −20 dB → 0 … +12 dB → 1
  function sparkline(id, series, key, opts) {
    const svg = $(id); if (!svg) return;
    const pts = series.filter(s => s[key] != null);
    svg.innerHTML = "";
    if (pts.length < 2) { svg.innerHTML = `<text x="2" y="18" fill="#8a97a8" font-size="9">${pts.length ? "1 sample" : "no history"}</text>`; return; }
    const W = 120, H = 28, t0 = pts[0].ts, t1 = pts[pts.length - 1].ts || t0 + 1;
    let lo = Math.min(...pts.map(p => p[key])), hi = Math.max(...pts.map(p => p[key]));
    if (opts && opts.lo != null) lo = Math.min(lo, opts.lo); if (opts && opts.hi != null) hi = Math.max(hi, opts.hi);
    if (hi - lo < 1e-6) { hi = lo + 1; }
    const xy = pts.map(p => [((p.ts - t0) / Math.max(1, t1 - t0)) * (W - 4) + 2, H - 3 - ((p[key] - lo) / (hi - lo)) * (H - 6)]);
    const line = xy.map(c => c.map(v => v.toFixed(1)).join(",")).join(" ");
    const color = opts && opts.color ? opts.color : "#6fc3ff";
    svg.innerHTML = `<polygon class="area" points="${xy[0][0].toFixed(1)},${H} ${line} ${xy[xy.length - 1][0].toFixed(1)},${H}" style="fill:${color}22"/>`
      + `<polyline points="${line}" style="stroke:${color}"/>`
      + `<circle cx="${xy[xy.length - 1][0].toFixed(1)}" cy="${xy[xy.length - 1][1].toFixed(1)}" r="2" fill="${color}"/>`;
  }
  let cardReq = 0, cardLast = 0;
  function showCard(id, refetch) {
    const n = S.roster[id]; if (!n) return;
    const opening = S.selected !== id; S.selected = id;
    const cls = ageClass(n);
    $("card-short").textContent = name(n); $("card-long").textContent = n.long_name || name(n);
    $("card-id").textContent = n.id; $("card-proto").textContent = n.proto || "";
    $("card-pulse").className = "pulse-dot " + cls;
    $("c-heard").textContent = n.id === S.myId ? "this node · live" : ago(n.last_heard);
    $("c-hops").textContent = n.id === S.myId ? "" : n.hops_away != null ? (n.hops_away === 0 ? "· heard direct" : `· ${n.hops_away} hop${n.hops_away === 1 ? "" : "s"} away`) : "";
    const g = $("card-gauge"); g.className = "gauge " + cls;
    const arc = $("g-arc"), q = n.id === S.myId ? 1 : snrPct(n.snr);
    arc.style.strokeDashoffset = (ARC * (1 - q)).toFixed(1);
    arc.style.stroke = n.id === S.myId ? "var(--terracotta)" : snrColor(n.snr); arc.style.color = arc.style.stroke;
    $("c-snr").textContent = n.snr != null ? n.snr.toFixed(1) + " dB" : "–";
    $("c-rssi").textContent = n.rssi != null ? n.rssi + " dBm" : "–";
    const b = n.battery;
    $("c-batt").textContent = b != null ? (b > 100 ? "on power" : b + "%") + (n.voltage != null ? ` · ${n.voltage.toFixed(2)} V` : "") : "–";
    const bar = $("c-batt-bar"); bar.style.width = (b == null ? 0 : b > 100 ? 100 : b) + "%"; bar.className = b != null && b <= 100 ? (b < 15 ? "crit" : b < 35 ? "low" : "") : "";
    $("c-hw").textContent = n.hw || "–"; $("c-role").textContent = n.role ? n.role.replace("CLIENT_", "").replace("_", " ") : "–";
    $("c-util").textContent = n.channel_util != null ? n.channel_util.toFixed(1) + "%" : "–";
    const p = pos(n); $("c-pos").textContent = p ? `${p[0].toFixed(5)}, ${p[1].toFixed(5)}` + (n.position.alt != null ? ` · ${Math.round(n.position.alt)} m` : "") : "no position";
    $("c-center").hidden = !p;
    $("card").hidden = false;
    if (opening || refetch) {
      const t = performance.now(); if (!opening && t - cardLast < 4000) return; cardLast = t;
      const req = ++cardReq;
      fetch("api/node/" + encodeURIComponent(id) + "?hours=24").then(r => r.json()).then(d => {
        if (req !== cardReq || S.selected !== id) return;
        const sig = d.signal || [], tel = d.telemetry || [];
        sparkline("sp-snr", sig, "snr", { lo: -10, hi: 10, color: snrColor(sig.length ? sig[sig.length - 1].snr : null) });
        sparkline("sp-rssi", sig, "rssi", { lo: -125, hi: -70, color: "#6fc3ff" });
        const onBattery = tel.filter(x => x.battery != null && x.battery <= 100);
        if (onBattery.length) sparkline("sp-batt", onBattery, "battery", { lo: 0, hi: 100, color: "#58e39c" });
        else sparkline("sp-batt", tel.filter(x => x.voltage != null), "voltage", { color: "#f2c04e" });   // mains-powered: show voltage instead
        $("c-pkts").textContent = d.counts ? d.counts.packets_24h : "–";
        if (sig.length && n.rssi == null) { const last = sig[sig.length - 1]; if (last.rssi != null) $("c-rssi").textContent = last.rssi + " dBm"; }
      }).catch(() => {});
    }
  }
  $("card-close").onclick = () => { $("card").hidden = true; S.selected = null; };
  $("c-center").onclick = () => { const p = pos(S.roster[S.selected]); if (p) map.flyTo(p, Math.max(map.getZoom(), 13), { duration: .8 }); };

  // ---------------------------------------------------------------- fit
  function km(a, b) {
    const r = Math.PI / 180, la1 = a[0] * r, la2 = b[0] * r, dla = (b[0] - a[0]) * r, dlo = (b[1] - a[1]) * r;
    const h = Math.sin(dla / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dlo / 2) ** 2;
    return 6371 * 2 * Math.asin(Math.sqrt(h));
  }
  // Frame the LOCAL mesh: the base (or the densest node) plus everything within 40 km of it,
  // widening to 120 km if that's lonely. Far-flung nodes stay on the map, just off-screen.
  function fitOnce() {
    if (S.fitted) return;
    const R = Object.values(S.roster).filter(n => pos(n) && !(Math.abs(n.position.lat) < 0.5 && Math.abs(n.position.lon) < 0.5));
    if (!R.length) return;
    let centre = pos(S.roster[S.myId]);
    if (!centre) {   // no base fix: the node with the most neighbours within 40 km
      let best = null, bestN = -1;
      for (const n of R) { const c = R.filter(m => km(pos(n), pos(m)) <= 40).length; if (c > bestN) { bestN = c; best = n; } }
      centre = pos(best);
    }
    let pts = R.filter(n => km(centre, pos(n)) <= 40).map(pos);
    if (pts.length < 3) pts = R.filter(n => km(centre, pos(n)) <= 120).map(pos);
    if (pts.length < 2) { map.setView(centre, 13); S.fitted = true; return; }
    map.fitBounds(L.latLngBounds(pts).pad(0.2), { maxZoom: 14 });
    S.fitted = true;
  }

  // ---------------------------------------------------------------- apply
  function applySnapshot(s) {
    S.myId = s.my_id;
    for (const id in s.roster) upsertNode(s.roster[id]);
    for (const l of s.links || []) upsertLink(l);
    list.innerHTML = "";
    for (const p of (s.packets || [])) addPacket(p, false);
    if (typeof addExchange === "function") {
      catList.innerHTML = ""; catN = 0; $("cat-count").textContent = "";
      for (const p of (s.packets || [])) maybeExchange(p);
      for (const x of (s.brain || [])) addExchange(x, true);
    }
    setStatus(s.bus, s.meshd); refreshStats(s.stats); fitOnce();
  }
  function applyPacket(ev) {
    if (ev.my_id) S.myId = ev.my_id;
    S.times.push(now());
    if (ev.node && ev.packet && ev.packet.rssi != null) ev.node.rssi = ev.packet.rssi;
    upsertNode(ev.node);
    for (const l of ev.links || []) upsertLink(l);
    if (typeof TL !== "undefined" && document.body.classList.contains("replaying")) { TL.queuedLive.push(ev.packet); if (TL.events.length) TL.events.push(ev.packet); refreshStats(); return; }
    addPacket(ev.packet, true);
    if (typeof pingMessage === "function") { pingMessage(ev.packet); maybeExchange(ev.packet); }
    const p = ev.packet;
    flashNode(p.from);
    if (S.myId && p.from !== S.myId) {
      if (p.hops === 0) pulse(p.from, S.myId, false);
      else if (p.hops != null) pulse(p.from, S.myId, true);
    }
    if (S.selected === p.from) showCard(p.from, true);
    refreshStats();
  }

  // ---------------------------------------------------------------- coverage (measured, never interpolated)
  const cov = { on: false, direct: true, bins: true, points: [], layer: L.layerGroup(), hexLayer: L.layerGroup(), loaded: false, summary: null };
  const HEX_M = 150;   // hex "radius" in metres; ~260 m across — roughly a LoRa-meaningful cell in town
  function snrColor(v) { return v == null ? "#8a97a8" : v > 8 ? "#58e39c" : v > 3 ? "#b6e05a" : v > -3 ? "#f2c04e" : v > -8 ? "#ff9a5c" : "#ff6b6b"; }
  function covVisible(p) { return !(cov.direct && p.hops !== 0); }
  function drawPoint(p) {
    const relay = p.hops == null || p.hops > 0;
    const m = L.circleMarker([p.lat, p.lon], { radius: relay ? 4 : 5.5, color: snrColor(p.snr), weight: 1.5, fillColor: snrColor(p.snr),
      fillOpacity: relay ? 0.35 : (p.pos_age > 300 ? 0.55 : 0.9), className: "cov-pt" + (relay ? " relay" : ""), interactive: true });
    const who = S.roster[p.node_id] ? name(S.roster[p.node_id]) : p.node_id.slice(-4);
    m.bindTooltip(`<b>${escape(who)}</b> · ${p.snr != null ? p.snr.toFixed(1) + " dB" : "SNR ?"}${p.rssi != null ? " · " + p.rssi + " dBm" : ""}<br>`
      + `${p.hops === 0 ? "heard direct" : p.hops == null ? "hops unknown" : p.hops + " hop" + (p.hops === 1 ? "" : "s") + " (last-hop signal)"} · ${ago(p.ts)}`
      + `${p.pos_age ? `<br>fix ${Math.round(p.pos_age / 60)} min old` : ""}${p.source !== "live" ? " · " + escape(p.source) : ""}`, { className: "node-tip", direction: "top" });
    return m;
  }
  // pointy-top hex binning on a local metric plane around the base
  function hexKey(lat, lon, origin) {
    const R = 111320, x = (lon - origin[1]) * R * Math.cos(origin[0] * Math.PI / 180), y = (lat - origin[0]) * R;
    const q = (Math.sqrt(3) / 3 * x - y / 3) / HEX_M, r = (2 / 3 * y) / HEX_M;
    let rq = Math.round(q), rr = Math.round(r), rs = Math.round(-q - r);
    const dq = Math.abs(rq - q), dr = Math.abs(rr - r), ds = Math.abs(rs - (-q - r));
    if (dq > dr && dq > ds) rq = -rr - rs; else if (dr > ds) rr = -rq - rs;
    return [rq, rr];
  }
  function hexPolygon(q, r, origin) {
    const R = 111320, cx = HEX_M * Math.sqrt(3) * (q + r / 2), cy = HEX_M * 1.5 * r, pts = [];
    for (let i = 0; i < 6; i++) { const a = Math.PI / 180 * (60 * i - 30), x = cx + HEX_M * Math.cos(a), y = cy + HEX_M * Math.sin(a);
      pts.push([origin[0] + y / R, origin[1] + x / (R * Math.cos(origin[0] * Math.PI / 180))]); }
    return pts;
  }
  function drawBins() {
    cov.hexLayer.clearLayers();
    if (!cov.bins) return;
    const origin = pos(S.roster[S.myId]) || (cov.points[0] && [cov.points[0].lat, cov.points[0].lon]); if (!origin) return;
    const bins = new Map();
    for (const p of cov.points) { if (!covVisible(p) || p.snr == null) continue; const [q, r] = hexKey(p.lat, p.lon, origin); const k = q + "," + r;
      (bins.get(k) || bins.set(k, { q, r, snrs: [], nodes: new Set() }).get(k)).snrs.push(p.snr); bins.get(k).nodes.add(p.node_id); }
    for (const b of bins.values()) {
      if (b.snrs.length < 3) continue;                       // not enough samples to claim anything
      const sorted = b.snrs.slice().sort((a, c) => a - c), med = sorted[Math.floor(sorted.length / 2)], best = sorted[sorted.length - 1];
      L.polygon(hexPolygon(b.q, b.r, origin), { color: snrColor(med), weight: 1, fillColor: snrColor(med), fillOpacity: 0.28, className: "cov-hex", interactive: true })
        .bindTooltip(`<b>${b.snrs.length} samples</b> from ${b.nodes.size} node${b.nodes.size === 1 ? "" : "s"}<br>median ${med.toFixed(1)} dB · best ${best.toFixed(1)} dB`, { className: "node-tip", sticky: true })
        .addTo(cov.hexLayer);
    }
  }
  function drawCoverage() {
    cov.layer.clearLayers();
    for (const p of cov.points) if (covVisible(p)) drawPoint(p).addTo(cov.layer);
    drawBins(); covSummary();
  }
  function covSummary() {
    const el = $("cov-summary"), s = cov.summary; if (!s) return;
    const vis = cov.points.filter(covVisible).length;
    if (!s.count) { el.innerHTML = "<b>No measured points yet.</b> Points appear when a node with a fresh GPS fix is heard. Walk with GO (or run Range Test) and watch this fill in."; return; }
    const span = s.first_ts && s.last_ts ? `${new Date(s.first_ts * 1000).toLocaleDateString()} → ${ago(s.last_ts)}` : "";
    el.innerHTML = `<b>${vis}</b> shown of <b>${s.count}</b> measured · <b>${s.direct}</b> heard direct · ${s.nodes} node${s.nodes === 1 ? "" : "s"}<br>${span}<br><span style="opacity:.8">Dots are real receptions at the base; nothing between them is inferred.</span>`;
  }
  function loadCoverage() {
    fetch("api/coverage?hours=" + (24 * 90)).then(r => r.json()).then(d => { cov.points = d.points || []; cov.summary = d.summary; cov.loaded = true; drawCoverage(); })
      .catch(() => { $("cov-summary").textContent = "coverage API unavailable"; });
  }
  function setCoverage(on) {
    cov.on = on; $("cov-opts").hidden = !on;
    if (on) { cov.layer.addTo(map); cov.hexLayer.addTo(map); if (!cov.loaded) loadCoverage(); else drawCoverage(); }
    else { map.removeLayer(cov.layer); map.removeLayer(cov.hexLayer); }
    try { localStorage.setItem("v2.cov", on ? "1" : "0"); } catch (e) {}
  }
  $("cov-on").addEventListener("change", e => setCoverage(e.target.checked));
  $("cov-direct").addEventListener("change", e => { cov.direct = e.target.checked; drawCoverage(); });
  $("cov-bins").addEventListener("change", e => { cov.bins = e.target.checked; drawBins(); });
  function addRxPoint(p) {
    cov.points.unshift(p); if (cov.points.length > 5000) cov.points.pop();
    if (cov.summary) { cov.summary.count++; if (p.hops === 0) cov.summary.direct++; cov.summary.last_ts = p.ts; cov.summary.first_ts = cov.summary.first_ts || p.ts; }
    if (cov.on && covVisible(p)) { const m = drawPoint(p).addTo(cov.layer); const el = m.getElement && m.getElement(); if (el) el.style.transition = "r .3s"; drawBins(); covSummary(); }
  }
  try { if (localStorage.getItem("v2.cov") === "1") { $("cov-on").checked = true; setCoverage(true); } } catch (e) {}

  // ---------------------------------------------------------------- boot
  fetch("api/state").then(r => r.json()).then(applySnapshot).catch(() => setStatus(false, null));
  const sock = io(API ? API + "/v2" : "/v2", { transports: ["websocket", "polling"] });
  sock.on("snapshot", applySnapshot);
  sock.on("packet", applyPacket);
  sock.on("rxpoint", addRxPoint);
  sock.on("brain", (x) => { addExchange(x, true); chime(); });
  sock.on("roster", (r) => { S.myId = r.my_id || S.myId; for (const id in r.roster) upsertNode(r.roster[id]); refreshLinks(); refreshStats(); fitOnce(); });
  sock.on("status", (s) => { S.myId = s.my_id || S.myId; setStatus(s.bus, s.meshd); });
  sock.on("disconnect", () => setStatus(false, null));
  setInterval(() => { refreshAges(); refreshLinks(); refreshTimes(); refreshStats(); if (S.selected) showCard(S.selected); }, 15000);

  // ---------------------------------------------------------------- public view + share
  const PUBLIC = document.body.dataset.public === "yes";
  function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 2600); }
  $("share").addEventListener("click", async () => {
    const url = API ? API + "/v2/public" : new URL("public", location.href).href;
    try { await navigator.clipboard.writeText(url); toast("Public link copied: " + url); }
    catch (e) { prompt("Read-only public link:", url); }
  });

  // ---------------------------------------------------------------- chime on new messages (WebAudio, gesture-unlocked)
  let audioCtx = null, soundOn = false;
  try { soundOn = localStorage.getItem("v2.sound") === "1"; } catch (e) {}
  function renderSound() { const b = $("sound"); b.textContent = soundOn ? "🔔" : "🔕"; b.setAttribute("aria-pressed", soundOn ? "true" : "false"); b.title = soundOn ? "Chime on new messages (on)" : "Chime on new messages (off)"; }
  function chime() {
    if (!soundOn) return;
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume();
      const t = audioCtx.currentTime, g = audioCtx.createGain(); g.connect(audioCtx.destination);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.18, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
      [[880, 0], [1320, 0.09]].forEach(([f, d]) => { const o = audioCtx.createOscillator(); o.type = "sine"; o.frequency.value = f; o.connect(g); o.start(t + d); o.stop(t + 0.4); });
    } catch (e) {}
  }
  $("sound").addEventListener("click", () => { soundOn = !soundOn; try { localStorage.setItem("v2.sound", soundOn ? "1" : "0"); } catch (e) {} renderSound(); if (soundOn) chime(); });
  renderSound();
  function pingMessage(p) {
    if (p.kind !== "text" || p.from === S.myId) return;
    const b = $("sound"); b.classList.remove("flash"); void b.offsetWidth; b.classList.add("flash");
    chime();
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      try { new Notification(`${p.from_name || p.from}: ${p.text || ""}`.slice(0, 120), { tag: "wildcat-msg", silent: true }); } catch (e) {}
    }
  }

  // ---------------------------------------------------------------- Ask the Cat panel
  const catList = $("cat-list"); let catN = 0;
  function addExchange(x, isBrain) {
    const el = document.createElement("div"); el.className = "xch" + (isBrain ? " brain" : "");
    const who = `<div class="who"><span><b>${escape(x.node_name || x.from_name || "?")}</b> ${isBrain ? "asked the Cat" : "↔ the Den"}</span><span data-ts="${x.ts}">${ago(x.ts)}</span></div>`;
    if (isBrain) {
      const meta = [x.provider ? `<span class="prov">${escape(x.provider)}</span>` : "", x.latency_ms != null ? `${(x.latency_ms / 1000).toFixed(1)} s` : "", x.chunks != null ? `${x.chunks} pkt${x.chunks === 1 ? "" : "s"}` : "", x.rate_limited ? "rate-limited" : ""].filter(Boolean).join("<span>·</span>");
      el.innerHTML = who + `<div class="q">${escape(x.prompt)}</div><div class="a">${escape(x.reply)}</div><div class="meta">${meta}</div>`;
    } else {
      el.innerHTML = who + `<div class="${x.from === S.myId ? "a bbs" : "q"}">${escape(x.text || x.summary || "")}</div>`;
    }
    catList.prepend(el);
    while (catList.children.length > 80) catList.lastChild.remove();
    if (isBrain) { catN++; $("cat-count").textContent = catN; }
  }
  function maybeExchange(p) {   // DMs to/from the Den show as conversation, so the panel is useful before Phase 2
    if (p.kind !== "text" || p.broadcast || !S.myId) return;
    if (p.to === S.myId || p.from === S.myId) addExchange(p, false);
  }
  document.querySelector(".feed-tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".ftab"); if (!b) return;
    for (const t of document.querySelectorAll(".ftab")) t.classList.toggle("on", t === b);
    const cat = b.dataset.tab === "cat";
    $("cat").hidden = !cat; $("feed-list").hidden = cat; $("feed-empty").hidden = cat; $("filters").hidden = cat;
    if (feedEl.dataset.sheet === "peek") setSheet("half");
  });

  // ---------------------------------------------------------------- timeline + replay
  const TL = { hours: 6, events: [], buckets: 72, playing: false, speed: 60, pos: 1, timer: null, cursor: 0, lastFrame: 0, queuedLive: [] };
  const tlBadge = document.createElement("div"); tlBadge.className = "replay-badge"; tlBadge.textContent = "REPLAY"; document.body.appendChild(tlBadge);
  function tlLoad() {
    fetch("api/history?hours=" + TL.hours).then(r => r.json()).then(d => { TL.events = d.events || []; TL.since = d.since; tlDraw(); tlLabel(); }).catch(() => { $("tl-label").textContent = "history unavailable"; });
  }
  function tlDraw() {
    const bars = $("tl-bars"); bars.innerHTML = "";
    const t0 = TL.since, span = TL.hours * 3600, counts = new Array(TL.buckets).fill(0), texts = new Array(TL.buckets).fill(0);
    for (const e of TL.events) { const i = Math.min(TL.buckets - 1, Math.max(0, Math.floor((e.ts - t0) / span * TL.buckets))); counts[i]++; if (e.kind === "text") texts[i]++; }
    const max = Math.max(1, ...counts);
    counts.forEach((c, i) => { const b = document.createElement("i"); b.style.height = Math.max(1, Math.round(c / max * 26)) + "px"; if (texts[i]) b.className = "text"; b.title = `${c} event${c === 1 ? "" : "s"}${texts[i] ? ", " + texts[i] + " msg" : ""}`; bars.appendChild(b); });
  }
  function tlLabel(at) {
    const n = TL.events.length;
    $("tl-label").textContent = at != null ? new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) + ` · ${TL.speed}×`
      : `last ${TL.hours} h · ${n} event${n === 1 ? "" : "s"}` + (n ? ` · ${TL.events.filter(e => e.kind === "text").length} msgs` : "");
  }
  function tlEnterReplay(fromPos) {
    if (!TL.events.length) { toast("Nothing recorded yet to replay"); return; }
    TL.playing = true; document.body.classList.add("replaying"); $("tl-live").hidden = false; $("tl-play").textContent = "❚❚ Pause";
    const span = TL.hours * 3600; TL.clock = TL.since + span * fromPos;
    TL.cursor = TL.events.findIndex(e => e.ts >= TL.clock); if (TL.cursor < 0) TL.cursor = TL.events.length;
    if (fromPos === 0 && TL.events.length) TL.clock = TL.events[0].ts - 5;   // start where the recording starts
    list.innerHTML = ""; TL.lastFrame = performance.now();
    TL.timer = requestAnimationFrame(tlTick);
  }
  function tlTick(t) {
    if (!TL.playing) return;
    const dt = (t - TL.lastFrame) / 1000; TL.lastFrame = t;
    TL.clock += dt * TL.speed;
    // fast-forward through silence: if the next event is > 2 min of mesh time away, jump to just before it
    if (TL.cursor < TL.events.length && TL.events[TL.cursor].ts - TL.clock > 120) TL.clock = TL.events[TL.cursor].ts - 5;
    const span = TL.hours * 3600; TL.pos = Math.min(1, (TL.clock - TL.since) / span);
    $("tl-scrub").value = Math.round(TL.pos * 1000);
    let shown = 0;
    while (TL.cursor < TL.events.length && TL.events[TL.cursor].ts <= TL.clock && shown < 12) {
      const e = TL.events[TL.cursor++]; shown++;
      addPacket(e, true); flashNode(e.from);
      if (S.myId && e.from !== S.myId && e.kind === "text") pulse(e.from, S.myId, e.hops !== 0);
    }
    const bars = $("tl-bars").children; const cut = Math.floor(TL.pos * TL.buckets);
    for (let i = 0; i < bars.length; i++) bars[i].classList.toggle("past", i < cut);
    tlLabel(TL.clock);
    if (TL.pos >= 1) { tlExitReplay(); return; }
    TL.timer = requestAnimationFrame(tlTick);
  }
  function tlPause() { TL.playing = false; cancelAnimationFrame(TL.timer); $("tl-play").textContent = "▶ Resume"; }
  function tlExitReplay() {
    TL.playing = false; cancelAnimationFrame(TL.timer); document.body.classList.remove("replaying");
    $("tl-live").hidden = true; $("tl-play").textContent = "▶ Replay"; $("tl-scrub").value = 1000; TL.pos = 1;
    for (const b of $("tl-bars").children) b.classList.remove("past");
    list.innerHTML = ""; for (const p of TL.queuedLive.splice(-150)) addPacket(p, false); TL.queuedLive = [];
    fetch("api/state").then(r => r.json()).then(sn => { list.innerHTML = ""; for (const p of (sn.packets || [])) addPacket(p, false); }).catch(() => {});
    tlLabel();
  }
  $("tl-play").addEventListener("click", () => { if (document.body.classList.contains("replaying") && TL.playing) tlPause(); else if (document.body.classList.contains("replaying")) { TL.playing = true; TL.lastFrame = performance.now(); TL.timer = requestAnimationFrame(tlTick); $("tl-play").textContent = "❚❚ Pause"; } else tlEnterReplay(0); });
  $("tl-live").addEventListener("click", tlExitReplay);
  $("tl-scrub").addEventListener("input", (e) => { const pos = +e.target.value / 1000; if (pos >= 0.999 && !document.body.classList.contains("replaying")) return; if (document.body.classList.contains("replaying")) { cancelAnimationFrame(TL.timer); TL.playing = false; } tlEnterReplay(pos); });
  $("tl-speeds").addEventListener("click", (e) => { const b = e.target.closest(".chip"); if (!b) return; TL.speed = +b.dataset.speed; for (const c of $("tl-speeds").children) c.classList.toggle("on", c === b); tlLabel(document.body.classList.contains("replaying") ? TL.clock : null); });
  if (!PUBLIC && window.innerWidth > 760) { tlLoad(); setInterval(tlLoad, 5 * 60 * 1000); }

  // ---------------------------------------------------------------- health badge + home bar (polled)
  async function pollHealth() {
    try {
      const h = await (await fetch("api/health-report")).json();
      const n = h.counts.alerts, btn = $("alerts"), bar = $("health-bar");
      btn.hidden = !n; $("alerts-n").textContent = n; btn.classList.toggle("crit", h.level === "crit"); btn.onclick = () => { location.hash = "#/health"; };
      const nb = $("nav-alerts"); nb.hidden = !n; nb.textContent = n;
      const g = h.gauges || {};
      if (n && !document.body.classList.contains("viewing") && !PUBLIC && window.innerWidth > 760) {
        bar.hidden = false; bar.classList.toggle("crit", h.level === "crit");
        $("health-bar-text").innerHTML = `<b>${escape(h.alerts[0].text)}</b>${n > 1 ? ` <span style="color:var(--muted)">+${n - 1} more</span>` : ""}${g.channel_util != null ? ` <span style="color:var(--muted)">· util ${fmt1(g.channel_util, "%")}</span>` : ""}`;
      } else bar.hidden = true;
    } catch (e) {}
  }
  pollHealth(); setInterval(pollHealth, 60000);

  // ---------------------------------------------------------------- quick find (⌘K or /)
  const pal = $("palette"), palIn = $("palette-input"), palList = $("palette-list"); let palSel = 0, palItems = [];
  function palOpen() { pal.hidden = false; palIn.value = ""; palRender(""); setTimeout(() => palIn.focus(), 30); }
  function palClose() { pal.hidden = true; }
  function palRender(q) {
    q = q.trim().toLowerCase();
    const R = Object.values(S.roster);
    palItems = (q ? R.filter(n => [n.short_name, n.long_name, n.id, n.hw].some(v => v && String(v).toLowerCase().includes(q))) : R.slice().sort((a, b) => (b.last_heard || 0) - (a.last_heard || 0))).slice(0, 12);
    palSel = 0;
    palList.innerHTML = palItems.length ? palItems.map((n, i) => `<div class="pal${i === 0 ? " sel" : ""}" data-i="${i}">${dot(n)}<span class="n">${escape(name(n))}</span><span class="l">${escape(n.long_name || "")}</span><span class="d">${n.hops_away == null ? "" : n.hops_away === 0 ? "direct" : n.hops_away + " hops"} · ${ago(n.last_heard)}</span></div>`).join("") : '<div class="empty">no match</div>';
  }
  function palGo(i) { const n = palItems[i]; if (!n) return; palClose(); if (pos(n) && (location.hash === "#/" || !location.hash)) { map.flyTo(pos(n), 14, { duration: .7 }); showCard(n.id); } else location.hash = "#/node/" + encodeURIComponent(n.id); }
  palIn.addEventListener("input", () => palRender(palIn.value));
  palIn.addEventListener("keydown", (e) => { if (e.key === "Escape") palClose(); else if (e.key === "ArrowDown") { palSel = Math.min(palItems.length - 1, palSel + 1); } else if (e.key === "ArrowUp") { palSel = Math.max(0, palSel - 1); } else if (e.key === "Enter") { palGo(palSel); return; } else return;
    [...palList.children].forEach((el, i) => el.classList.toggle("sel", i === palSel)); });
  palList.addEventListener("click", (e) => { const el = e.target.closest(".pal"); if (el) palGo(+el.dataset.i); });
  pal.addEventListener("click", (e) => { if (e.target === pal) palClose(); });
  $("palette-btn").addEventListener("click", palOpen);
  document.addEventListener("keydown", (e) => { const typing = /input|textarea|select/i.test((e.target.tagName || "")); if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); pal.hidden ? palOpen() : palClose(); } else if (e.key === "/" && !typing) { e.preventDefault(); palOpen(); } else if (e.key === "Escape") { if (!pal.hidden) palClose(); else if (document.body.classList.contains("viewing")) location.hash = "#/"; else if (!$("card").hidden) $("card-close").click(); } });
  document.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("tr.row")) { e.preventDefault(); e.target.click(); } });
  new MutationObserver(() => { document.querySelectorAll("tr.row:not([tabindex])").forEach(tr => tr.setAttribute("tabindex", "0")); }).observe(viewBody, { childList: true, subtree: true });

  // ---------------------------------------------------------------- range rings around the base
  const ringLayer = L.layerGroup();
  function drawRings() {
    ringLayer.clearLayers(); const c = pos(S.roster[S.myId]); if (!c) return;
    for (const km of [1, 2, 5, 10]) {
      L.circle(c, { radius: km * 1000, className: "range-ring", weight: 1, interactive: false }).addTo(ringLayer);
      const lat = c[0] + (km * 1000) / 111320;
      L.marker([lat, c[1]], { icon: L.divIcon({ className: "ring-lbl", html: `${km} km`, iconSize: [40, 12], iconAnchor: [20, 6] }), interactive: false }).addTo(ringLayer);
    }
  }
  function setRings(on) { if (on) { drawRings(); ringLayer.addTo(map); } else map.removeLayer(ringLayer); try { localStorage.setItem("v2.rings", on ? "1" : "0"); } catch (e) {} }
  $("rings-on").addEventListener("change", e => setRings(e.target.checked));
  try { if (localStorage.getItem("v2.rings") === "1") { $("rings-on").checked = true; setTimeout(() => setRings(true), 1500); } } catch (e) {}

  // ---------------------------------------------------------------- label declutter by zoom
  function declutter() { const z = map.getZoom(); document.body.classList.toggle("z-far", z < 10); document.body.classList.toggle("z-mid", z >= 10 && z < 12); }
  map.on("zoomend", declutter); declutter();

  // ---------------------------------------------------------------- PWA: service worker, install, mobile sheet
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {}));
  }
  let deferredInstall = null;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferredInstall = e; $("install").hidden = false; });
  $("install").addEventListener("click", async () => { if (!deferredInstall) return; deferredInstall.prompt(); await deferredInstall.userChoice; deferredInstall = null; $("install").hidden = true; });
  window.addEventListener("appinstalled", () => { $("install").hidden = true; });
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  function dismissHint() { $("ios-hint").hidden = true; try { localStorage.setItem("v2.iosHint", "1"); } catch (e) {} }
  let hintSeen = false;
  try { hintSeen = localStorage.getItem("v2.iosHint") === "1"; } catch (e) {}
  if (isIOS && !standalone && !hintSeen) {
    setTimeout(() => { $("ios-hint").hidden = false; setTimeout(dismissHint, 15000); }, 4000);
  }
  $("ios-hint-close").addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); dismissHint(); });
  $("ios-hint-close").addEventListener("touchend", (e) => { e.preventDefault(); dismissHint(); }, { passive: false });

  const SHEET = ["peek", "half", "full"];
  const feedEl = $("feed");
  function setSheet(state) { feedEl.dataset.sheet = state; try { localStorage.setItem("v2.sheet", state); } catch (e) {} setTimeout(() => map.invalidateSize(), 340); }
  $("sheet-handle").addEventListener("click", () => { const i = SHEET.indexOf(feedEl.dataset.sheet); setSheet(SHEET[(i + 1) % SHEET.length]); });
  let touchY = null;
  feedEl.addEventListener("touchstart", (e) => { if (e.target.closest(".feed-head, .handle")) touchY = e.touches[0].clientY; }, { passive: true });
  feedEl.addEventListener("touchend", (e) => {
    if (touchY == null) return; const dy = e.changedTouches[0].clientY - touchY; touchY = null;
    const i = SHEET.indexOf(feedEl.dataset.sheet);
    if (dy < -40 && i < 2) setSheet(SHEET[i + 1]); else if (dy > 40 && i > 0) setSheet(SHEET[i - 1]);
  }, { passive: true });
  try { const saved = localStorage.getItem("v2.sheet"); if (saved && SHEET.includes(saved)) feedEl.dataset.sheet = saved; } catch (e) {}
  $("layers").addEventListener("click", (e) => {
    if (window.innerWidth > 760) return;
    if (!$("layers").classList.contains("open")) { e.preventDefault(); $("layers").classList.add("open"); }
  });
  document.addEventListener("click", (e) => { if (window.innerWidth <= 760 && !e.target.closest("#layers")) $("layers").classList.remove("open"); });
  const feedTitle = document.querySelector(".feed-title");
  const _addPacket = addPacket;
  addPacket = function (p, fresh) { _addPacket(p, fresh); feedTitle.dataset.count = list.children.length + " pkts"; };

  // ================================================================ views: hash router + sidebar/drawer
  const sidebar = $("sidebar"), viewEl = $("view"), viewBody = $("view-body"), viewTitle = $("view-title"), viewTools = $("view-tools");
  function openDrawer(on) { sidebar.classList.toggle("open", on); $("drawer-scrim").classList.toggle("open", on); }
  $("menu-btn").addEventListener("click", () => openDrawer(!sidebar.classList.contains("open")));
  $("drawer-scrim").addEventListener("click", () => openDrawer(false));
  sidebar.addEventListener("click", (e) => { if (e.target.closest("a")) openDrawer(false); });
  $("view-close").addEventListener("click", () => { location.hash = "#/"; });
  function fmtTs(ts) { return ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "–"; }
  function fmt1(v, unit) { return v == null ? "–" : (Math.round(v * 10) / 10) + (unit || ""); }
  function snrSpan(v) { return v == null ? "–" : `<span class="snr ${snrClass(v)}">${fmt1(v, " dB")}</span>`; }
  function dot(n) { return `<i class="nd ${ageClass(n)}"></i>`; }
  function hoursSel(cur, opts, cb) {
    const wrap = document.createElement("div"); wrap.className = "sel";
    for (const [h, label] of opts) { const b = document.createElement("button"); b.className = "chip" + (h === cur ? " on" : ""); b.textContent = label; b.onclick = () => cb(h); wrap.appendChild(b); }
    return wrap;
  }
  const H24 = [[24, "24h"], [168, "7d"], [720, "30d"]];

  // ---- tiny SVG charts (no CDN) ----
  function barChart(items, opts) {   // items: [{label, value, cls?}]
    const W = 600, H = 180, padL = 34, padB = 22, padT = 8, n = Math.max(1, items.length), max = Math.max(1, ...items.map(i => i.value));
    const bw = (W - padL - 6) / n, s = [`<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`];
    for (let g = 0; g <= 4; g++) { const y = padT + (H - padT - padB) * g / 4; s.push(`<line class="grid" x1="${padL}" x2="${W}" y1="${y}" y2="${y}"/><text x="${padL - 4}" y="${y + 3}" text-anchor="end">${Math.round(max * (1 - g / 4))}</text>`); }
    items.forEach((it, i) => { const h = (H - padT - padB) * it.value / max, x = padL + i * bw + 1, y = H - padB - h;
      s.push(`<rect class="bar ${it.cls || ""}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1, bw - 2).toFixed(1)}" height="${h.toFixed(1)}" rx="2"><title>${escape(it.label)}: ${it.value}</title></rect>`);
      if (n <= 32 || i % Math.ceil(n / 16) === 0) s.push(`<text x="${(x + bw / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">${escape(String(it.label))}</text>`); });
    return s.join("") + "</svg>";
  }
  function lineChart(pts, opts) {   // pts: [{x(label), y}], opts.lo/hi
    const W = 600, H = 180, padL = 36, padB = 22, padT = 8, n = pts.length;
    if (n < 2) return '<div class="empty">not enough data yet</div>';
    let lo = Math.min(...pts.map(p => p.y)), hi = Math.max(...pts.map(p => p.y));
    if (opts && opts.lo != null) lo = Math.min(lo, opts.lo); if (opts && opts.hi != null) hi = Math.max(hi, opts.hi); if (hi - lo < 1e-6) hi = lo + 1;
    const X = i => padL + (W - padL - 6) * i / (n - 1), Y = v => H - padB - (H - padT - padB) * (v - lo) / (hi - lo);
    const s = [`<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`];
    for (let g = 0; g <= 4; g++) { const v = hi - (hi - lo) * g / 4, y = Y(v); s.push(`<line class="grid" x1="${padL}" x2="${W}" y1="${y}" y2="${y}"/><text x="${padL - 4}" y="${y + 3}" text-anchor="end">${fmt1(v)}</text>`); }
    const line = pts.map((p, i) => `${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ");
    s.push(`<polygon class="area" points="${X(0).toFixed(1)},${H - padB} ${line} ${X(n - 1).toFixed(1)},${H - padB}"/><polyline points="${line}"/>`);
    pts.forEach((p, i) => { if (n <= 26 || i % Math.ceil(n / 12) === 0) s.push(`<text x="${X(i).toFixed(1)}" y="${H - 6}" text-anchor="middle">${escape(String(p.x))}</text>`); s.push(`<circle cx="${X(i).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="2.5" fill="#6fc3ff"><title>${escape(String(p.x))}: ${fmt1(p.y)}</title></circle>`); });
    return s.join("") + "</svg>";
  }
  function heatmap(rows, cells) {   // rows: [{key,label}], cells: {rowKey: {hour: count}}
    let max = 1; for (const r of rows) for (let h = 0; h < 24; h++) max = Math.max(max, (cells[r.key] || {})[h] || 0);
    const s = ['<div class="hm"><div></div>']; for (let h = 0; h < 24; h++) s.push(`<div class="hr">${h % 3 === 0 ? h : ""}</div>`);
    for (const r of rows) { s.push(`<div class="lab">${escape(r.label)}</div>`); for (let h = 0; h < 24; h++) { const v = (cells[r.key] || {})[h] || 0; s.push(`<div class="c" data-v="${v}" style="--p:${(v / max).toFixed(2)}" title="${escape(r.label)} · ${h}:00 · ${v} msg"></div>`); } }
    return s.join("") + "</div>";
  }
  function chName(c) { return c === 0 ? "LongFast (primary)" : c == null ? "?" : "Channel " + c; }

  // ---- table helper with sort + search ----
  function table(el, cols, rows, opts) {   // cols: [{key, label, num?, render?}]
    const st = { key: opts.sort || cols[0].key, asc: !!opts.asc, q: "" };
    function render() {
      const q = st.q.toLowerCase();
      let r = rows.filter(x => !q || cols.some(c => String(x[c.key] == null ? "" : x[c.key]).toLowerCase().includes(q)) || (opts.search && opts.search(x).toLowerCase().includes(q)));
      r.sort((a, b) => { const va = a[st.key], vb = b[st.key]; if (va == null && vb == null) return 0; if (va == null) return 1; if (vb == null) return -1; return (va < vb ? -1 : va > vb ? 1 : 0) * (st.asc ? 1 : -1); });
      el.innerHTML = `<div class="twrap"><table class="vt"><thead><tr>${cols.map(c => `<th data-k="${c.key}" class="${c.key === st.key ? "sorted" + (st.asc ? " asc" : "") : ""}">${c.label}</th>`).join("")}</tr></thead><tbody>${
        r.map(x => `<tr class="row" data-id="${escape(x.id || "")}">${cols.map(c => `<td class="${c.num ? "num" : ""}${c.dim ? " dim" : ""}">${c.render ? c.render(x) : escape(x[c.key] == null ? "–" : x[c.key])}</td>`).join("")}</tr>`).join("")
      }</tbody></table>${r.length ? "" : '<div class="empty">nothing matches</div>'}</div>`;
      el.querySelectorAll("th").forEach(th => th.onclick = () => { const k = th.dataset.k; if (st.key === k) st.asc = !st.asc; else { st.key = k; st.asc = false; } render(); });
      if (opts.onRow) el.querySelectorAll("tr.row").forEach(tr => tr.onclick = () => opts.onRow(tr.dataset.id));
    }
    render();
    return { search: (q) => { st.q = q; render(); } };
  }

  // ---- views ----
  const VIEWS = {
    nodes: {
      title: "Nodes",
      async render() {
        const d = await (await fetch("api/nodes")).json();
        const rows = d.nodes.map(n => ({ ...n, id: n.id, name: n.short_name || n.id.slice(-4), msgs: n.stats ? n.stats.message_count : 0,
          avg_snr: n.stats ? n.stats.avg_snr : null, best_snr: n.stats ? n.stats.best_snr : null, worst_snr: n.stats ? n.stats.worst_snr : null,
          avg_rssi: n.stats ? n.stats.avg_rssi : null, last: n.last_heard || (n.stats && n.stats.last_seen) || 0, hasPos: n.position ? 1 : 0 }));
        const t = now() - 3600, online = rows.filter(r => r.last >= t).length;
        viewBody.innerHTML = `<div class="kpis"><div class="kpi"><b>${rows.length}</b><span>nodes known</span></div><div class="kpi"><b>${online}</b><span>heard · 1h</span></div><div class="kpi"><b>${rows.filter(r => r.hasPos).length}</b><span>with GPS</span></div><div class="kpi"><b>${d.mesh.messages_24h}</b><span>msgs · 24h</span></div><div class="kpi"><b>${fmt1(d.mesh.avg_snr, " dB")}</b><span>avg SNR · 24h</span></div></div><div id="nodes-table"></div>`;
        const search = document.createElement("input"); search.className = "vsearch"; search.placeholder = "search name, id, hardware…";
        const exp = document.createElement("a"); exp.className = "link-btn"; exp.href = A("api/export/nodes.csv"); exp.textContent = "⇩ CSV";
        viewTools.replaceChildren(search, exp);
        const tb = table($("nodes-table"), [
          { key: "name", label: "Node", render: r => `${dot(r)}<b>${escape(r.name)}</b> <span class="dim" style="color:var(--muted)">${escape(r.long_name || "")}</span>` },
          { key: "id", label: "ID", dim: true }, { key: "hw", label: "Hardware", dim: true }, { key: "role", label: "Role", dim: true, render: r => escape(r.role ? r.role.replace("CLIENT_", "") : "–") },
          { key: "last", label: "Last heard", render: r => ago(r.last) }, { key: "hops_away", label: "Hops", num: true, render: r => r.hops_away == null ? "–" : r.hops_away === 0 ? "direct" : r.hops_away },
          { key: "snr", label: "SNR now", num: true, render: r => snrSpan(r.snr) }, { key: "avg_snr", label: "avg", num: true, render: r => snrSpan(r.avg_snr) },
          { key: "best_snr", label: "best", num: true, render: r => snrSpan(r.best_snr) }, { key: "worst_snr", label: "worst", num: true, render: r => snrSpan(r.worst_snr) },
          { key: "avg_rssi", label: "RSSI", num: true, render: r => r.avg_rssi == null ? "–" : r.avg_rssi + " dBm" },
          { key: "msgs", label: "Msgs", num: true }, { key: "battery", label: "Batt", num: true, render: r => r.battery == null ? "–" : r.battery > 100 ? "⚡" : r.battery + "%" },
        ], rows, { sort: "last", search: r => `${r.long_name || ""} ${r.hw || ""}`, onRow: id => { location.hash = "#/node/" + encodeURIComponent(id); } });
        search.oninput = () => tb.search(search.value);
      }
    },
    node: {
      title: "Node",
      async render(id) {
        const r = await fetch("api/node/" + encodeURIComponent(id) + "/full"); if (!r.ok) { viewBody.innerHTML = '<div class="empty">unknown node</div>'; return; }
        const d = await r.json(), n = d.node, st = d.stats || {}, rel = d.reliability || {};
        viewTitle.innerHTML = `${dot(n)}${escape(n.short_name || id.slice(-4))} <span style="color:var(--muted);font-weight:500">${escape(n.long_name || "")}</span>`;
        const center = document.createElement("button"); center.className = "link-btn"; center.textContent = "⌖ on map"; center.onclick = () => { location.hash = "#/"; setTimeout(() => { const p = pos(S.roster[id]); if (p) { map.flyTo(p, 14); showCard(id); } }, 50); };
        viewTools.replaceChildren(center);
        const sig = d.signal || [], tel = d.telemetry || [];
        const lastSig = sig.length ? sig[sig.length - 1] : {};
        const bands = rel.bands || {}, tot = Math.max(1, rel.messages || 0);
        viewBody.innerHTML = `
          <div class="kpis">
            <div class="kpi"><b>${ago(n.last_heard || st.last_seen)}</b><span>last heard</span></div>
            <div class="kpi"><b>${n.hops_away == null ? "–" : n.hops_away === 0 ? "direct" : n.hops_away}</b><span>hops away</span></div>
            <div class="kpi"><b>${fmt1(n.snr != null ? n.snr : lastSig.snr, " dB")}</b><span>SNR now</span></div>
            <div class="kpi"><b>${st.message_count || 0}</b><span>messages</span></div>
            <div class="kpi"><b>${rel.reliability_pct == null ? "–" : rel.reliability_pct + "%"}</b><span>good-signal share · 7d</span></div>
            <div class="kpi"><b>${n.battery == null ? "–" : n.battery > 100 ? "⚡ mains" : n.battery + "%"}</b><span>battery${n.voltage != null ? " · " + n.voltage.toFixed(2) + " V" : ""}</span></div>
          </div>
          <div class="vgrid">
            <div class="vcard wide"><h2>Signal quality · 7 days <span class="sel" style="color:var(--muted);text-transform:none;letter-spacing:0">${sig.length} samples</span></h2>${lineChart(sig.map(x => ({ x: new Date(x.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), y: x.snr })).filter(x => x.y != null), { lo: -10, hi: 10 })}</div>
            <div class="vcard"><h2>RSSI</h2>${lineChart(sig.map(x => ({ x: new Date(x.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), y: x.rssi })).filter(x => x.y != null), { lo: -125, hi: -70 })}</div>
            <div class="vcard"><h2>${tel.some(x => x.battery != null && x.battery <= 100) ? "Battery" : "Voltage"}</h2>${tel.some(x => x.battery != null && x.battery <= 100) ? lineChart(tel.filter(x => x.battery != null && x.battery <= 100).map(x => ({ x: new Date(x.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), y: x.battery })), { lo: 0, hi: 100 }) : lineChart(tel.filter(x => x.voltage != null).map(x => ({ x: new Date(x.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), y: x.voltage })), {})}</div>
            <div class="vcard"><h2>Connection quality · 7 days</h2>
              <div class="bands">${["excellent", "good", "fair", "poor", "unknown"].map(k => `<i class="${k}" style="width:${(100 * (bands[k] || 0) / tot).toFixed(1)}%" title="${k}: ${bands[k] || 0}"></i>`).join("")}</div>
              <div style="font-size:12px;color:var(--muted)">excellent ≥8 dB · good ≥3 · fair ≥−3 · poor below · ${rel.messages || 0} messages</div>
              <div style="margin-top:10px">${(rel.per_day || []).length ? barChart(rel.per_day.map(x => ({ label: x.day.slice(5), value: x.count }))) : '<div class="empty">no messages in 7 days</div>'}</div></div>
            <div class="vcard"><h2>Details</h2><table class="vt"><tbody>
              <tr><td class="dim">ID</td><td class="num">${escape(id)}</td></tr><tr><td class="dim">Protocol</td><td>${escape(n.proto || "–")}</td></tr>
              <tr><td class="dim">Hardware</td><td>${escape(n.hw || "–")}</td></tr><tr><td class="dim">Role</td><td>${escape(n.role || "–")}</td></tr>
              <tr><td class="dim">First seen</td><td>${fmtTs(st.first_seen)}</td></tr><tr><td class="dim">Best / worst SNR</td><td>${snrSpan(st.best_snr)} / ${snrSpan(st.worst_snr)}</td></tr>
              <tr><td class="dim">Avg RSSI</td><td class="num">${st.avg_rssi == null ? "–" : st.avg_rssi + " dBm"}</td></tr><tr><td class="dim">Position</td><td class="num">${n.position && n.position.lat != null ? n.position.lat.toFixed(5) + ", " + n.position.lon.toFixed(5) : "–"}</td></tr>
              <tr><td class="dim">Ch. util</td><td class="num">${fmt1(n.channel_util, "%")}</td></tr></tbody></table></div>
            <div class="vcard wide"><h2>Recent messages</h2>${(d.messages || []).length ? d.messages.map(m => `<div class="msgrow"><div class="m">${escape(m.text)}</div><div class="r">${ago(m.ts)}<br>${snrSpan(m.snr)} ${m.rssi != null ? m.rssi + " dBm" : ""}</div></div>`).join("") : '<div class="empty">no messages logged from this node</div>'}</div>
          </div>`;
      }
    },
    channels: {
      title: "Channels",
      hours: 24,
      async render() {
        const h = this.hours, d = await (await fetch("api/channels?hours=" + h)).json();
        viewTools.replaceChildren(hoursSel(h, H24, v => { VIEWS.channels.hours = v; route(); }));
        const cells = {}; for (const r of d.hourly) (cells[r.channel] = cells[r.channel] || {})[r.hour] = r.count;
        const chans = d.activity.map(a => a.channel);
        const total = d.activity.reduce((a, c) => a + c.count, 0);
        viewBody.innerHTML = `
          <div class="kpis"><div class="kpi"><b>${total}</b><span>messages · ${h}h</span></div><div class="kpi"><b>${chans.length}</b><span>active channels</span></div><div class="kpi"><b>${d.mesh.active_nodes}</b><span>nodes · 1h</span></div><div class="kpi"><b>${fmt1(d.mesh.avg_snr, " dB")}</b><span>avg SNR · 24h</span></div></div>
          <div class="vgrid">
            <div class="vcard"><h2>Channel activity</h2>${d.activity.length ? barChart(d.activity.map(a => ({ label: chName(a.channel).replace(" (primary)", ""), value: a.count, cls: a.channel === 0 ? "t" : "" }))) : '<div class="empty">no messages in this window</div>'}</div>
            <div class="vcard"><h2>Top senders</h2>${d.top_senders.length ? `<table class="vt"><thead><tr><th>Node</th><th>Msgs</th><th>avg SNR</th></tr></thead><tbody>${d.top_senders.map(t => `<tr class="row" data-id="${escape(t.id)}"><td><b>${escape(t.short_name || t.id.slice(-4))}</b></td><td class="num">${t.message_count}</td><td class="num">${snrSpan(t.avg_snr)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">nobody yet</div>'}</div>
            <div class="vcard wide"><h2>Activity heatmap · hour of day × channel</h2>${chans.length ? heatmap(chans.map(c => ({ key: c, label: chName(c).replace(" (primary)", "") })), cells) : '<div class="empty">no data</div>'}</div>
            <div class="vcard wide"><h2>Channel details</h2>${d.details.length ? `<table class="vt"><thead><tr><th>Channel</th><th>Messages</th><th>Senders</th><th>avg SNR</th><th>First</th><th>Last</th></tr></thead><tbody>${d.details.map(c => `<tr class="row" data-ch="${c.channel}"><td><b>${escape(chName(c.channel))}</b></td><td class="num">${c.message_count}</td><td class="num">${c.unique_senders}</td><td class="num">${snrSpan(c.avg_snr)}</td><td class="dim">${fmtTs(c.first_message)}</td><td class="dim">${ago(c.last_message)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">no data</div>'}</div>
          </div>`;
        viewBody.querySelectorAll("tr[data-id]").forEach(tr => tr.onclick = () => { location.hash = "#/node/" + encodeURIComponent(tr.dataset.id); });
        viewBody.querySelectorAll("tr[data-ch]").forEach(tr => tr.onclick = () => { location.hash = "#/channel/" + tr.dataset.ch; });
      }
    },
    channel: {
      title: "Channel", hours: 24,
      async render(ch) {
        const h = this.hours, d = await (await fetch(`api/channel/${ch}?hours=${h}`)).json();
        viewTitle.textContent = chName(+ch); viewTools.replaceChildren(hoursSel(h, H24, v => { VIEWS.channel.hours = v; route(); }));
        const det = d.details || {};
        viewBody.innerHTML = `<div class="kpis"><div class="kpi"><b>${d.messages.length}</b><span>broadcasts · ${h}h</span></div><div class="kpi"><b>${det.unique_senders || 0}</b><span>senders</span></div><div class="kpi"><b>${fmt1(det.avg_snr, " dB")}</b><span>avg SNR</span></div></div>
          <div class="vcard"><h2>Messages</h2>${d.messages.length ? d.messages.map(m => `<div class="msgrow"><div class="m"><b>${escape(m.short_name || m.sender_id.slice(-4))}</b>${escape(m.text)}</div><div class="r">${ago(m.ts)}<br>${snrSpan(m.snr)} ${m.rssi != null ? m.rssi + " dBm" : ""}</div></div>`).join("") : '<div class="empty">no broadcasts on this channel in the window</div>'}</div>`;
      }
    },
    messages: {
      title: "Messages", hours: 168, tab: "conversations", board: null,
      async render() {
        const self = VIEWS.messages, h = self.hours;
        const tabs = document.createElement("div"); tabs.className = "sel";
        for (const [k, label] of [["conversations", "Conversations"], ["bulletins", "Bulletins"], ["mail", "Mail"]]) {
          const b = document.createElement("button"); b.className = "chip" + (self.tab === k ? " on" : ""); b.textContent = label; b.onclick = () => { self.tab = k; route(); }; tabs.appendChild(b);
        }
        viewTools.replaceChildren(tabs);
        if (self.tab === "conversations") {
          viewTools.appendChild(hoursSel(h, H24, v => { self.hours = v; route(); }));
          const d = await (await fetch("api/messages?hours=" + h)).json();
          const me = d.my_id, threads = new Map();
          for (const m of d.messages) { const other = m.from_bbs ? m.to : m.sender_id; if (!other) continue; (threads.get(other) || threads.set(other, []).get(other)).push(m); }
          const rows = [...threads.entries()].sort((a, b) => b[1][0].ts - a[1][0].ts);
          viewBody.innerHTML = `<div class="kpis"><div class="kpi"><b>${d.messages.length}</b><span>direct messages · ${h}h</span></div><div class="kpi"><b>${threads.size}</b><span>nodes in conversation</span></div><div class="kpi"><b>${d.messages.filter(m => m.from_bbs).length}</b><span>replies from the Den</span></div></div>`
            + (rows.length ? rows.map(([nid, ms]) => { const nm = (S.roster[nid] && S.roster[nid].short_name) || (ms.find(m => !m.from_bbs) || {}).short_name || nid.slice(-4);
              return `<div class="vcard" style="margin-bottom:12px"><h2><span class="nd ${S.roster[nid] ? ageClass(S.roster[nid]) : ""}"></span>${escape(nm)} <span style="color:var(--muted);text-transform:none;letter-spacing:0;font-weight:500">${escape(nid)} · ${ms.length} messages · last ${ago(ms[0].ts)}</span><span class="sel"><a class="link-btn" href="#/node/${encodeURIComponent(nid)}">node →</a></span></h2>${
                ms.slice().reverse().map(m => `<div class="msgrow${m.from_bbs ? " den" : ""}"><div class="m"><b>${m.from_bbs ? "Den" : escape(m.short_name || nm)}</b>${escape(m.text)}</div><div class="r">${ago(m.ts)}${m.snr != null ? "<br>" + snrSpan(m.snr) : ""}</div></div>`).join("")}</div>`; }).join("")
              : '<div class="empty">no direct messages in this window — DM the Den from any node to start one</div>');
        } else if (self.tab === "bulletins") {
          const d = await (await fetch("api/bulletins" + (self.board ? "?board=" + encodeURIComponent(self.board) : ""))).json();
          const boards = document.createElement("div"); boards.className = "sel";
          for (const [k, label] of [[null, "All"], ...d.boards.map(b => [b.board, `${b.board} · ${b.count}`])]) { const c = document.createElement("button"); c.className = "chip" + ((self.board || null) === k ? " on" : ""); c.textContent = label; c.onclick = () => { self.board = k; route(); }; boards.appendChild(c); }
          viewTools.appendChild(boards);
          const urgent = (d.boards.find(b => b.board.toLowerCase() === "urgent") || {}).count || 0;
          viewBody.innerHTML = `<div class="kpis"><div class="kpi"><b>${d.boards.reduce((a, b) => a + b.count, 0)}</b><span>bulletins</span></div><div class="kpi"><b>${d.boards.length}</b><span>boards</span></div><div class="kpi"><b style="color:${urgent ? "#ff8a8a" : "inherit"}">${urgent}</b><span>urgent</span></div></div>`
            + (d.bulletins.length ? `<div class="vgrid">${d.bulletins.map(b => `<div class="vcard${b.board.toLowerCase() === "urgent" ? " urgent" : ""}"><h2>${escape(b.board)} <span style="margin-left:auto;text-transform:none;letter-spacing:0;font-weight:500">${escape(b.date)}</span></h2><div style="font-weight:700;margin-bottom:4px">${escape(b.subject)}</div><div style="font-size:13px;color:#c9d3df;white-space:pre-wrap">${escape(b.content)}</div><div style="margin-top:8px;color:var(--muted);font-size:11px">— ${escape(b.sender)}</div></div>`).join("")}</div>`
              : '<div class="empty">no bulletins yet — post one from the BBS: PB,,General,subject,text</div>');
        } else {
          const d = await (await fetch("api/bulletins")).json(), m = d.mail;
          viewBody.innerHTML = `<div class="kpis"><div class="kpi"><b>${m.total}</b><span>mail waiting</span></div><div class="kpi"><b>${m.recipients}</b><span>recipients</span></div></div>
            <div class="vcard"><h2>Mailboxes</h2><p style="margin:0 0 10px;color:var(--muted);font-size:12px">Mail is private between mesh users — the Den shows counts only, never contents. Users read theirs with CM on the BBS.</p>${
              m.per_recipient.length ? `<table class="vt"><thead><tr><th>Recipient</th><th>Waiting</th><th>Latest</th></tr></thead><tbody>${m.per_recipient.map(r => `<tr><td><b>${escape((S.roster[r.recipient] || {}).short_name || r.recipient)}</b> <span style="color:var(--muted)">${escape(r.recipient)}</span></td><td class="num">${r.waiting}</td><td class="dim">${escape(r.latest || "–")}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">no mail waiting</div>'}</div>`;
        }
      }
    },
    propagation: {
      title: "Propagation", days: 7,
      async render() {
        const self = VIEWS.propagation, d = await (await fetch("api/propagation?days=" + self.days)).json();
        viewTools.replaceChildren(hoursSel(self.days, [[1, "24h"], [7, "7d"], [30, "30d"]], v => { self.days = v; route(); }));
        const hourly = d.hourly || [], dist = d.distribution || [];
        const total = hourly.reduce((a, r) => a + r.message_count, 0);
        const best = hourly.length ? hourly.reduce((a, r) => r.avg_snr > a.avg_snr ? r : a) : null, worst = hourly.length ? hourly.reduce((a, r) => r.avg_snr < a.avg_snr ? r : a) : null;
        const byHour = new Array(24).fill(null); for (const r of hourly) byHour[r.hour] = r;
        const insights = [];
        if (best && worst && best !== worst) insights.push(`Best hour <b>${best.hour}:00</b> (avg ${fmt1(best.avg_snr, " dB")}), worst <b>${worst.hour}:00</b> (${fmt1(worst.avg_snr, " dB")}) — a ${fmt1(best.avg_snr - worst.avg_snr, " dB")} swing.`);
        if (dist.length) { const n = dist.reduce((a, r) => a + r.count, 0), good = dist.filter(r => r.snr >= 3).reduce((a, r) => a + r.count, 0); insights.push(`<b>${Math.round(100 * good / n)}%</b> of ${n} receptions were ≥3 dB SNR (comfortable margin).`); }
        if (hourly.length && hourly.length < 12) insights.push(`Only ${hourly.length} of 24 hours have data in this window — trends firm up as the Den runs longer.`);
        if (!hourly.length) insights.push("No SNR samples in this window yet.");
        const link = (r) => `<tr class="row" data-id="${escape(r.id)}"><td><b>${escape(r.short_name || r.id.slice(-4))}</b></td><td class="num">${snrSpan(r.snr)}</td><td class="num">${r.rssi != null ? r.rssi + " dBm" : "–"}</td><td class="dim">${ago(r.ts)}</td></tr>`;
        viewBody.innerHTML = `
          <div class="kpis"><div class="kpi"><b>${total}</b><span>receptions with SNR · ${self.days}d</span></div><div class="kpi"><b>${hourly.length ? fmt1(hourly.reduce((a, r) => a + r.avg_snr * r.message_count, 0) / Math.max(1, total), " dB") : "–"}</b><span>weighted avg SNR</span></div><div class="kpi"><b>${best ? best.hour + ":00" : "–"}</b><span>best hour</span></div><div class="kpi"><b>${worst ? worst.hour + ":00" : "–"}</b><span>worst hour</span></div></div>
          <div class="vgrid">
            <div class="vcard wide"><h2>Average SNR by hour of day <span class="sel" style="text-transform:none;letter-spacing:0;font-weight:500">local time · ${self.days} day${self.days === 1 ? "" : "s"}</span></h2>${hourly.length >= 2 ? lineChart(byHour.map((r, hh) => r ? { x: hh + ":00", y: r.avg_snr } : null).filter(Boolean), { lo: -10, hi: 10 }) : '<div class="empty">not enough hourly data yet</div>'}</div>
            <div class="vcard"><h2>SNR distribution</h2>${dist.length ? barChart(dist.map(r => ({ label: r.snr, value: r.count, cls: r.snr >= 3 ? "t" : "" }))) : '<div class="empty">no data</div>'}<div style="color:var(--muted);font-size:11px;margin-top:4px">dB buckets · terracotta = ≥3 dB</div></div>
            <div class="vcard"><h2>Messages per hour</h2>${hourly.length ? barChart(byHour.map((r, hh) => ({ label: hh, value: r ? r.message_count : 0 }))) : '<div class="empty">no data</div>'}</div>
            <div class="vcard"><h2>🏆 Best links</h2>${(d.best || []).length ? `<table class="vt"><thead><tr><th>Node</th><th>SNR</th><th>RSSI</th><th>When</th></tr></thead><tbody>${d.best.map(link).join("")}</tbody></table>` : '<div class="empty">none yet</div>'}</div>
            <div class="vcard"><h2>📉 Weakest links</h2>${(d.worst || []).length ? `<table class="vt"><thead><tr><th>Node</th><th>SNR</th><th>RSSI</th><th>When</th></tr></thead><tbody>${d.worst.map(link).join("")}</tbody></table>` : '<div class="empty">none yet</div>'}</div>
            <div class="vcard wide"><h2>💡 Insights</h2><ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.6">${insights.map(i => `<li>${i}</li>`).join("")}<li style="color:var(--muted)">SNR above ~5 dB is a solid LongFast link; below −7 dB packets start dropping. RSSI alone is misleading on LoRa — SNR is the number that matters.</li></ul></div>
          </div>`;
        viewBody.querySelectorAll("tr[data-id]").forEach(tr => tr.onclick = () => { location.hash = "#/node/" + encodeURIComponent(tr.dataset.id); });
      }
    },
    topology: {
      title: "Topology",
      async render() {
        const d = await (await fetch("api/topology")).json();
        const onMap = document.createElement("a"); onMap.className = "link-btn"; onMap.href = "#/"; onMap.textContent = "⌖ see links on the map"; viewTools.replaceChildren(onMap);
        // edges: NeighborInfo reports (DB, 7 d) ∪ live links (bridge: neighbor + direct-hop inference)
        const E = new Map();
        for (const e of d.edges || []) { const k = [e.a, e.b].sort().join("|"); E.set(k, { a: e.a, b: e.b, snr: e.snr, kind: "neighbor", ts: e.ts }); }
        for (const l of d.live || []) { const k = l.key; const cur = E.get(k); if (!cur) E.set(k, { a: l.a, b: l.b, snr: l.snr, kind: l.kind, ts: l.last }); else if (l.snr != null) cur.snr = l.snr; }
        const edges = [...E.values()], ids = new Set(); edges.forEach(e => { ids.add(e.a); ids.add(e.b); }); if (S.myId) ids.add(S.myId);
        const nodes = [...ids].map(id => ({ id, n: S.roster[id] || { id, short_name: id.slice(-4) } }));
        const reporters = new Set((d.edges || []).map(e => e.a)).size, neighborEdges = edges.filter(e => e.kind === "neighbor").length, directEdges = edges.length - neighborEdges;
        const density = nodes.length > 1 ? edges.length / (nodes.length * (nodes.length - 1) / 2) : 0;
        const online = nodes.filter(x => ageClass(x.n) === "on" || ageClass(x.n) === "base").length;
        // force layout (plain JS, ~120 iterations)
        const W = 900, H = 520, idx = new Map(nodes.map((x, i) => [x.id, i]));
        const P = nodes.map((x, i) => ({ x: W / 2 + Math.cos(i * 2.4) * (120 + i * 6), y: H / 2 + Math.sin(i * 2.4) * (90 + i * 4), vx: 0, vy: 0 }));
        if (S.myId && idx.has(S.myId)) { P[idx.get(S.myId)].x = W / 2; P[idx.get(S.myId)].y = H / 2; }
        for (let it = 0; it < 140; it++) {
          const k = 0.85 - it / 200;
          for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) { const dx = P[j].x - P[i].x, dy = P[j].y - P[i].y, d2 = Math.max(60, dx * dx + dy * dy), f = 5200 / d2; const fx = dx / Math.sqrt(d2) * f, fy = dy / Math.sqrt(d2) * f; P[i].vx -= fx; P[i].vy -= fy; P[j].vx += fx; P[j].vy += fy; }
          for (const e of edges) { const a = P[idx.get(e.a)], b = P[idx.get(e.b)]; if (!a || !b) continue; const dx = b.x - a.x, dy = b.y - a.y, dist = Math.sqrt(dx * dx + dy * dy) || 1, f = (dist - 130) * 0.02; a.vx += dx / dist * f; a.vy += dy / dist * f; b.vx -= dx / dist * f; b.vy -= dy / dist * f; }
          for (const q of P) { q.vx += (W / 2 - q.x) * 0.004; q.vy += (H / 2 - q.y) * 0.004; q.x += q.vx * k; q.y += q.vy * k; q.vx *= 0.6; q.vy *= 0.6; q.x = Math.max(30, Math.min(W - 30, q.x)); q.y = Math.max(24, Math.min(H - 24, q.y)); }
          if (S.myId && idx.has(S.myId)) { P[idx.get(S.myId)].x = W / 2; P[idx.get(S.myId)].y = H / 2; }
        }
        const col = (n) => ({ on: "#58e39c", warm: "#f2c04e", cold: "#5b6b80", base: "#e0704b" })[ageClass(n)];
        const svg = `<svg class="topo" viewBox="0 0 ${W} ${H}">${edges.map(e => { const a = P[idx.get(e.a)], b = P[idx.get(e.b)]; if (!a || !b) return ""; return `<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}" class="te ${e.kind}"><title>${escape(name(S.roster[e.a] || { id: e.a }))} ↔ ${escape(name(S.roster[e.b] || { id: e.b }))}${e.snr != null ? " · SNR " + e.snr.toFixed(1) : ""} · ${e.kind === "neighbor" ? "NeighborInfo" : "heard direct"}</title></line>`; }).join("")}${
          nodes.map((x, i) => `<g class="tn" data-id="${escape(x.id)}" transform="translate(${P[i].x.toFixed(1)},${P[i].y.toFixed(1)})"><circle r="${x.id === S.myId ? 11 : 7}" fill="${col(x.n)}"/><text y="${x.id === S.myId ? 24 : 19}" text-anchor="middle">${escape(name(x.n))}</text></g>`).join("")}</svg>`;
        viewBody.innerHTML = `
          <div class="kpis"><div class="kpi"><b>${nodes.length}</b><span>nodes in graph</span></div><div class="kpi"><b>${online}</b><span>online</span></div><div class="kpi"><b>${edges.length}</b><span>links</span></div><div class="kpi"><b>${(density * 100).toFixed(0)}%</b><span>density</span></div><div class="kpi"><b>${reporters}</b><span>NeighborInfo reporters · 7d</span></div></div>
          ${reporters === 0 ? `<div class="vcard" style="margin-bottom:12px;border-color:rgba(242,192,78,.4)"><h2>⚠ No NeighborInfo reports yet</h2><div style="font-size:13px;line-height:1.5">No node has sent a NeighborInfo packet in 7 days, so the ${directEdges} link${directEdges === 1 ? "" : "s"} below are <b>inferred</b>: packets the base heard with zero hops used (from ↔ base, real SNR). That's honest but base-centric. <b>Enable the NeighborInfo module on STAY</b> (and GO) — <code style="font:12px var(--mono)">meshtastic --set neighbor_info.enabled true --set neighbor_info.update_interval 900</code> — and the mesh's real who-hears-whom appears here and on the map, no Den changes needed.</div></div>` : ""}
          <div class="vcard wide" style="padding:6px">${nodes.length ? svg : '<div class="empty">no links yet — the base hasn\'t heard anyone directly</div>'}</div>
          <div style="display:flex;gap:14px;margin-top:8px;color:var(--muted);font-size:11px;flex-wrap:wrap"><span><i class="sw" style="background:#e0704b"></i> this node</span><span><i class="sw on"></i> online</span><span><i class="sw warm"></i> &lt; 1 h</span><span><i class="sw cold"></i> older</span><span><i class="sw link"></i> heard direct (inferred)</span><span><i class="sw link" style="background:#c78bff"></i> NeighborInfo</span></div>`;
        viewBody.querySelectorAll(".tn").forEach(g => g.addEventListener("click", () => { location.hash = "#/node/" + encodeURIComponent(g.dataset.id); }));
      }
    },
    admin: {
      title: "Admin", logType: "messages", timer: null,
      async render() {
        const self = VIEWS.admin, sv = await (await fetch("api/services")).json();
        const m = sv.meshd || {};
        const unitRow = (u, st) => `<tr><td><b>${escape(u)}</b></td><td><span class="nd ${st.ActiveState === "active" ? "on" : st.ActiveState === "failed" ? "bad" : ""}"></span>${escape(st.ActiveState || "?")} <span style="color:var(--muted)">${escape(st.SubState || "")}</span></td><td>${PUBLIC ? "" : `<button class="link-btn rs" data-unit="${escape(u)}">restart</button>`}</td></tr>`;
        viewBody.innerHTML = `
          <div class="kpis"><div class="kpi"><b><span class="nd ${sv.bus ? "on" : "bad"}"></span>${sv.bus ? "up" : "down"}</b><span>MQTT bus</span></div><div class="kpi"><b><span class="nd ${m.state === "connected" ? "on" : "warm"}"></span>${escape(m.state || "?")}</b><span>meshd · ${escape(m.radio || "")}</span></div><div class="kpi"><b>${sv.packets_seen}</b><span>packets since start</span></div><div class="kpi"><b>${Math.floor(sv.uptime / 3600)}h ${Math.floor(sv.uptime % 3600 / 60)}m</b><span>observatory uptime</span></div></div>
          <div class="vgrid">
            ${PUBLIC ? "" : `<div class="vcard"><h2>📢 Send to the mesh</h2><p style="margin:0 0 8px;color:var(--muted);font-size:12px">Goes out through meshd on <code style="font:12px var(--mono)">wildcat/tx</code>. Broadcasts reach everyone — keep it short and rare; every packet costs airtime.</p>
              <div style="display:flex;gap:6px;flex-wrap:wrap"><select class="vsearch" id="tx-to" style="min-width:120px"><option value="^all">Broadcast (all)</option>${Object.values(S.roster).filter(n => n.id !== S.myId && (now() - (n.last_heard || 0)) < 86400).sort((a, b) => (b.last_heard || 0) - (a.last_heard || 0)).slice(0, 40).map(n => `<option value="${escape(n.id)}">DM ${escape(name(n))}</option>`).join("")}</select>
              <input class="vsearch" id="tx-text" maxlength="200" placeholder="message (≤200 chars)" style="flex:1"><button class="link-btn" id="tx-send">Send</button></div><div id="tx-result" style="margin-top:6px;font-size:12px;color:var(--muted)"></div></div>`}
            <div class="vcard"><h2>📥 Exports</h2><div style="display:flex;gap:8px;flex-wrap:wrap"><a class="link-btn" href="${A("api/export/nodes.csv")}">nodes.csv</a><a class="link-btn" href="${A("api/export/messages.csv")}">messages.csv (last 1000)</a><a class="link-btn" href="${A("api/export/coverage.csv")}">coverage.csv</a><a class="link-btn" href="${A("api/state")}" target="_blank">state.json</a></div></div>
            <div class="vcard"><h2>🔧 Services</h2>${sv.systemd ? `<table class="vt"><tbody>${Object.entries(sv.units).map(([u, st]) => unitRow(u, st)).join("")}</tbody></table>` : `<div style="font-size:13px;color:var(--muted)">No systemd on this host (dev Mac) — processes are run by hand; see <code style="font:12px var(--mono)">logs/</code>. On the Pi this lists wildcat-meshd / bbs / telemetry / observatory / mosquitto with restart buttons.</div>`}</div>
            <div class="vcard wide"><h2>📜 Live logs <span class="sel" id="log-types"></span></h2><div class="twrap" id="log-table" style="max-height:340px"></div></div>
            <div class="vcard"><h2>⚙ BBS configuration <span class="sel" style="text-transform:none;letter-spacing:0;font-weight:500" id="cfg-src"></span></h2>
              <div id="cfg-form" style="display:grid;gap:8px;font-size:13px">
                <label>BBS name<input class="vsearch" id="cfg-name" style="width:100%"></label>
                <label>Sync with other BBS nodes <span style="color:var(--muted)">(ids, comma-separated)</span><input class="vsearch" id="cfg-sync" style="width:100%" placeholder="!17d7e4b7, !18e9f5a3"></label>
                <label>Urgent-board posters <span style="color:var(--muted)">(ids; blank = anyone)</span><input class="vsearch" id="cfg-allow" style="width:100%"></label>
                <label>Main menu letters<input class="vsearch" id="cfg-main" style="width:100%" placeholder="W, N, R, Q, G, B, U, X"></label>
                <label>BBS menu letters<input class="vsearch" id="cfg-bbs" style="width:100%"></label>
                <label>Utilities menu letters<input class="vsearch" id="cfg-util" style="width:100%"></label>
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${PUBLIC ? "" : '<button class="link-btn" id="cfg-save">Save to wildcat.toml (keeps a .bak)</button>'}<span id="cfg-status" style="color:var(--muted);font-size:12px"></span></div>
              </div>
              <details style="margin-top:8px"><summary style="cursor:pointer;color:var(--muted);font-size:12px">effective config (TOML, secrets redacted)</summary><pre id="cfg-toml" style="margin:6px 0 0;font:12px var(--mono);color:#c9d3df;white-space:pre-wrap;max-height:300px;overflow:auto"></pre></details></div>
            <div class="vcard"><h2>✏️ BBS content <span class="sel" id="content-tabs"></span></h2><textarea id="content-text" class="vsearch" style="width:100%;min-height:240px;font:12px var(--mono);resize:vertical" ${PUBLIC ? "readonly" : ""}></textarea><div style="display:flex;gap:8px;align-items:center;margin-top:6px">${PUBLIC ? "" : '<button class="link-btn" id="content-save">Save (keeps a .bak)</button>'}<span id="content-status" style="color:var(--muted);font-size:12px"></span></div></div>
          </div>`;
        // send
        const send = $("tx-send"); if (send) send.onclick = async () => { const text = $("tx-text").value.trim(); if (!text) return; send.disabled = true;
          try { const r = await fetch("api/tx", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ to: $("tx-to").value, text }) }); const j = await r.json();
            $("tx-result").textContent = r.ok ? `queued to meshd (id ${j.id}) — watch wildcat/tx/result in the feed` : "refused: " + (j.error || r.status); if (r.ok) $("tx-text").value = ""; } catch (e) { $("tx-result").textContent = "failed: " + e; } send.disabled = false; };
        viewBody.querySelectorAll(".rs").forEach(b => b.onclick = async () => { if (!confirm(`Restart ${b.dataset.unit}?`)) return; b.disabled = true; const r = await fetch("api/restart", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ unit: b.dataset.unit }) }); const j = await r.json(); toast(j.message || (r.ok ? "restarted" : "failed")); b.disabled = false; });
        // logs
        const types = [["messages", "Messages"], ["telemetry", "Telemetry"], ["positions", "Positions"], ["neighbors", "Neighbors"]];
        const lt = $("log-types"); for (const [k, label] of types) { const c = document.createElement("button"); c.className = "chip" + (self.logType === k ? " on" : ""); c.textContent = label; c.onclick = () => { self.logType = k; for (const x of lt.children) x.classList.toggle("on", x === c); loadLogs(); }; lt.appendChild(c); }
        async function loadLogs() {
          const d = await (await fetch(`api/logs?type=${self.logType}&limit=100`)).json(), rows = d.rows || [];
          const cols = { messages: ["ts", "short_name", "id", "channel", "text", "snr", "rssi"], telemetry: ["ts", "id", "battery", "voltage", "channel_util", "air_util_tx", "temperature"], positions: ["ts", "id", "lat", "lon", "alt", "sats"], neighbors: ["ts", "id", "neighbor", "snr"] }[self.logType];
          $("log-table").innerHTML = rows.length ? `<table class="vt"><thead><tr>${cols.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td class="${c === "ts" ? "dim" : typeof r[c] === "number" ? "num" : ""}">${c === "ts" ? ago(r[c]) : escape(r[c] == null ? "–" : (typeof r[c] === "number" && !Number.isInteger(r[c]) ? r[c].toFixed(2) : r[c]))}</td>`).join("")}</tr>`).join("")}</tbody></table>` : '<div class="empty">nothing logged yet</div>';
        }
        loadLogs(); clearInterval(self.timer); self.timer = setInterval(() => { if (location.hash.startsWith("#/admin")) loadLogs(); else clearInterval(self.timer); }, 5000);
        // config
        fetch("api/config").then(r => r.json()).then(c => {
          $("cfg-toml").textContent = c.toml; $("cfg-src").textContent = `${c.source} [${c.kind}]`;
          const b = c.bbs || {}, m = b.menu || {};
          $("cfg-name").value = b.name || ""; $("cfg-sync").value = (b.sync_nodes || []).join(", "); $("cfg-allow").value = (b.allowed_nodes || []).join(", ");
          $("cfg-main").value = (m.main || []).join(", "); $("cfg-bbs").value = (m.bbs || []).join(", "); $("cfg-util").value = (m.utilities || []).join(", ");
          if (!c.editable) { $("cfg-status").textContent = "not editable: the Den is running from the legacy INI — run `wildcat config migrate --write`"; for (const i of $("cfg-form").querySelectorAll("input,button")) i.disabled = true; }
        });
        const csv = (v) => v.split(",").map(x => x.trim()).filter(Boolean);
        const cfgSave = $("cfg-save"); if (cfgSave) cfgSave.onclick = async () => {
          cfgSave.disabled = true; $("cfg-status").textContent = "validating…";
          const body = { name: $("cfg-name").value, sync_nodes: csv($("cfg-sync").value), allowed_nodes: csv($("cfg-allow").value),
            menu: { main: csv($("cfg-main").value), bbs: csv($("cfg-bbs").value), utilities: csv($("cfg-util").value) } };
          try { const r = await fetch("api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); const j = await r.json();
            $("cfg-status").textContent = r.ok ? `saved to ${j.path} — ${j.note}` + (j.warnings.length ? ` · ${j.warnings.length} warning(s)` : "") : "rejected: " + j.error;
            if (r.ok) { fetch("api/config").then(x => x.json()).then(c => { $("cfg-toml").textContent = c.toml; }); } }
          catch (e) { $("cfg-status").textContent = "failed: " + e; }
          cfgSave.disabled = false; };
        // content
        let cur = "fortunes"; const ct = $("content-tabs");
        for (const [k, label] of [["fortunes", "fortunes.txt"], ["trivia", "trivia.txt"], ["messages", "messages.json"]]) { const c = document.createElement("button"); c.className = "chip" + (k === cur ? " on" : ""); c.textContent = label; c.onclick = () => { cur = k; for (const x of ct.children) x.classList.toggle("on", x === c); loadContent(); }; ct.appendChild(c); }
        async function loadContent() { const d = await (await fetch("api/content/" + cur)).json(); $("content-text").value = d.text || ""; $("content-status").textContent = d.exists ? d.path : `${d.path} (does not exist yet)`; }
        loadContent();
        const cs = $("content-save"); if (cs) cs.onclick = async () => { const r = await fetch("api/content/" + cur, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: $("content-text").value }) }); const j = await r.json(); $("content-status").textContent = r.ok ? `saved (${j.lines} lines, backup at .bak) — the BBS reads fortunes/trivia on each use; messages.json needs a BBS restart` : "error: " + j.error; };
      }
    },
    api: {
      title: "API",
      async render() {
        const base = new URL("api/", location.href).href;
        const eps = [
          ["GET", "api/state", "everything the map needs: roster, links, recent packets, stats, brain exchanges"],
          ["GET", "api/health", "liveness: bus, meshd state, node count"],
          ["GET", "api/nodes", "every known node + message statistics"],
          ["GET", "api/node/!9e766b18", "a node's 24 h signal/telemetry samples (card)"],
          ["GET", "api/node/!9e766b18/full", "node detail: stats, reliability bands, recent messages"],
          ["GET", "api/channels?hours=24", "activity, details, top senders, hour×channel heatmap"],
          ["GET", "api/channel/0?hours=24", "broadcasts on one channel"],
          ["GET", "api/messages?hours=168", "direct-message conversations with the Den"],
          ["GET", "api/bulletins?board=General", "BBS boards + privacy-safe mail counts"],
          ["GET", "api/propagation?days=7", "hourly SNR trend, best/worst links, distribution"],
          ["GET", "api/topology", "NeighborInfo edges (DB) + live inferred links"],
          ["GET", "api/coverage?hours=720", "measured rx points + summary"],
          ["GET", "api/history?hours=6", "recorded events for the timeline / replay"],
          ["GET", "api/logs?type=telemetry&limit=50", "raw recent rows: messages | telemetry | positions | neighbors"],
          ["GET", "api/dashboard", "mesh stats, 24 h channel activity, low battery, top senders"],
          ["GET", "api/services", "bus / meshd / systemd unit status"],
          ["GET", "api/config", "the resolved config: editable BBS subset + TOML (secrets redacted)"],
          ["POST", "api/config", '{"name", "sync_nodes", "allowed_nodes", "menu": {…}} → validates, rewrites wildcat.toml (.bak)'],
          ["GET", "api/health-report", "mesh health: gauges, packets/min buckets, alerts, quiet nodes, low battery"],
          ["GET", "api/export/nodes.csv", "CSV exports: nodes · messages · coverage"],
          ["POST", "api/tx", '{"to": "^all" | "!nodeid", "text": "…"} → queued on wildcat/tx'],
          ["POST", "api/content/fortunes", '{"text": "…"} → writes the BBS content file (keeps .bak)'],
          ["POST", "api/restart", '{"unit": "wildcat-bbs"} → systemd restart (Pi only)'],
        ];
        viewBody.innerHTML = `<div class="vcard" style="margin-bottom:12px"><h2>Base URL</h2><code style="font:13px var(--mono)">${escape(base)}</code><p style="margin:8px 0 0;color:var(--muted);font-size:12px">JSON everywhere. Read endpoints are open on the LAN (like v1). Live updates: Socket.IO namespace <code style="font:12px var(--mono)">/v2</code>, events <code style="font:12px var(--mono)">snapshot · packet · roster · status · rxpoint · brain</code>. Everything speaks the neutral envelope (docs/OBSERVATORY_V2.md §2c) — node ids are opaque strings, <code style="font:12px var(--mono)">proto</code> says which radio.</p></div>
          <div class="vcard"><h2>Endpoints</h2><table class="vt"><thead><tr><th></th><th>Path</th><th>What</th><th></th></tr></thead><tbody>${eps.map(([m, pth, what], i) => `<tr><td class="num">${m}</td><td class="num">${escape(pth)}</td><td style="white-space:normal">${escape(what)}</td><td>${m === "GET" ? `<button class="link-btn try" data-p="${escape(pth)}" data-i="${i}">try</button>` : ""}</td></tr><tr id="try-${i}" hidden><td colspan="4"><pre style="margin:0;font:11px var(--mono);max-height:220px;overflow:auto;white-space:pre-wrap;color:#c9d3df"></pre></td></tr>`).join("")}</tbody></table></div>
          <div class="vcard" style="margin-top:12px"><h2>Exports</h2><div style="font-size:13px"><code style="font:12px var(--mono)">GET api/export/nodes.csv · api/export/messages.csv · api/export/coverage.csv</code></div></div>`;
        viewBody.querySelectorAll(".try").forEach(b => b.onclick = async () => { const row = $("try-" + b.dataset.i), pre = row.querySelector("pre"); row.hidden = false; pre.textContent = "…"; try { const r = await fetch(b.dataset.p); const t = await r.text(); pre.textContent = t.length > 6000 ? t.slice(0, 6000) + "\n… (truncated)" : t; } catch (e) { pre.textContent = String(e); } });
      }
    },
    health: {
      title: "Mesh health",
      async render() {
        const h = await (await fetch("api/health-report")).json();
        const g = h.gauges || {}, arc = (v, max, color) => `<div class="gauge2"><svg viewBox="0 0 120 120"><circle class="t" cx="60" cy="60" r="50"/><circle class="a" cx="60" cy="60" r="50" style="stroke:${color};stroke-dashoffset:${(314 * (1 - Math.max(0, Math.min(1, (v || 0) / max)))).toFixed(1)}"/></svg><div class="v"><div><b>${v == null ? "–" : fmt1(v, "%")}</b></div></div></div>`;
        const utilColor = g.channel_util == null ? "#5b6b80" : g.channel_util >= 40 ? "#ff6b6b" : g.channel_util >= 25 ? "#f2c04e" : "#58e39c";
        const rate = h.rate || [], rateNow = rate.slice(-6).reduce((a, b) => a + b.n, 0) / 30;
        viewTools.replaceChildren();
        viewBody.innerHTML = `
          <div class="vgrid">
            <div class="vcard" style="text-align:center"><h2 style="justify-content:center">Mesh score</h2><div class="score ${h.level}">${h.score}</div><div style="color:var(--muted);font-size:12px;margin-top:4px">${h.counts.alerts ? `${h.counts.alerts} alert${h.counts.alerts === 1 ? "" : "s"}${h.counts.crit ? ` · ${h.counts.crit} critical` : ""}` : "all clear"}</div></div>
            <div class="vcard" style="text-align:center"><h2 style="justify-content:center">Channel utilization</h2>${arc(g.channel_util, 50, utilColor)}<div style="color:var(--muted);font-size:11px;margin-top:6px">the whole channel as this node hears it · keep under 25%</div></div>
            <div class="vcard" style="text-align:center"><h2 style="justify-content:center">Air-time TX</h2>${arc(g.air_util_tx, 20, g.air_util_tx != null && g.air_util_tx >= 10 ? "#f2c04e" : "#6fc3ff")}<div style="color:var(--muted);font-size:11px;margin-top:6px">how much this node itself transmits</div></div>
            <div class="vcard"><h2>Packets per minute · last 6 h</h2>${rate.length ? lineChart(rate.map((b, i) => ({ x: i % 12 === 0 ? new Date(b.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "", y: b.n / 5 })), { lo: 0 }) : '<div class="empty">no history</div>'}<div style="color:var(--muted);font-size:11px">now ≈ ${rateNow.toFixed(1)} / min (5-min buckets)</div></div>
            <div class="vcard wide"><h2>Alerts</h2>${h.alerts.length ? h.alerts.map(a => `<div class="alert ${a.level}"><span class="k">${escape(a.kind)}</span><span>${escape(a.text)}${a.node ? ` <a class="link-btn" style="margin-left:8px;padding:2px 7px" href="#/node/${encodeURIComponent(a.node)}">node</a>` : ""}</span></div>`).join("") : '<div class="empty">nothing to worry about right now</div>'}</div>
            <div class="vcard"><h2>Gone quiet</h2><div style="color:var(--muted);font-size:11px;margin-bottom:6px">regular nodes (≥3 pkts in the prior day) silent for 2 h+</div>${h.quiet.length ? `<table class="vt"><tbody>${h.quiet.map(q => `<tr class="row" data-id="${escape(q.id)}"><td><b>${escape(q.name)}</b></td><td class="num">${Math.floor(q.silent_for / 3600)}h ${Math.floor(q.silent_for % 3600 / 60)}m</td><td class="dim">was ${q.was}/day</td></tr>`).join("")}</tbody></table>` : '<div class="empty">everyone regular is still talking</div>'}</div>
            <div class="vcard"><h2>Low battery</h2>${h.low_battery.length ? `<table class="vt"><tbody>${h.low_battery.map(b => `<tr class="row" data-id="${escape(b.id)}"><td><b>${escape((S.roster[b.id] || {}).short_name || b.id.slice(-4))}</b></td><td class="num" style="color:${b.battery < 10 ? "#ff8a8a" : "var(--warm)"}">${b.battery}%</td><td class="num">${b.voltage != null ? b.voltage.toFixed(2) + " V" : ""}</td><td class="dim">${ago(b.ts)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">no node under 20%</div>'}</div>
          </div>`;
        viewBody.querySelectorAll("tr[data-id]").forEach(tr => tr.onclick = () => { location.hash = "#/node/" + encodeURIComponent(tr.dataset.id); });
      }
    },
    dashboard: {
      title: "Dashboard",
      async render() {
        const d = await (await fetch("api/dashboard")).json(), st = await (await fetch("api/state")).json();
        const t = now(), R = Object.values(st.roster || {}), active = R.filter(n => (n.last_heard || 0) >= t - 3600).sort((a, b) => (b.last_heard || 0) - (a.last_heard || 0));
        const recent = (st.packets || []).slice(-20).reverse();
        viewBody.innerHTML = `
          <div class="kpis"><div class="kpi"><b>${d.mesh.messages_24h}</b><span>messages · 24h</span></div><div class="kpi"><b>${fmt1(d.mesh.avg_snr, " dB")}</b><span>avg SNR · 24h</span></div><div class="kpi"><b>${active.length}</b><span>active nodes · 1h</span></div><div class="kpi"><b>${R.length}</b><span>nodes known</span></div><div class="kpi"><b>${st.stats ? Math.round(st.stats.per_min) : "–"}</b><span>packets / min</span></div></div>
          <div class="vgrid">
            <div class="vcard"><h2>📻 Recent activity</h2>${recent.length ? recent.map(p => `<div class="msgrow"><div class="m"><b>${escape(p.from_name || p.from)}</b><span style="color:var(--muted);font-size:11px;text-transform:uppercase;margin-right:6px">${escape(p.kind)}</span>${escape(p.summary || "")}</div><div class="r">${ago(p.ts)}${p.snr != null ? "<br>" + snrSpan(p.snr) : ""}</div></div>`).join("") : '<div class="empty">quiet</div>'}</div>
            <div class="vcard"><h2>🟢 Active nodes · 1h</h2>${active.length ? `<table class="vt"><tbody>${active.slice(0, 25).map(n => `<tr class="row" data-id="${escape(n.id)}"><td>${dot(n)}<b>${escape(name(n))}</b> <span style="color:var(--muted)">${escape(n.long_name || "")}</span></td><td class="num">${n.hops_away == null ? "" : n.hops_away === 0 ? "direct" : n.hops_away + " hops"}</td><td class="num">${snrSpan(n.snr)}</td><td class="dim">${ago(n.last_heard)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">nobody heard in the last hour</div>'}</div>
            <div class="vcard"><h2>📊 Channel activity · 24h</h2>${d.activity.length ? barChart(d.activity.map(a => ({ label: chName(a.channel).replace(" (primary)", ""), value: a.count, cls: a.channel === 0 ? "t" : "" }))) : '<div class="empty">no messages in 24 h</div>'}</div>
            <div class="vcard"><h2>🔋 Low battery</h2>${d.low_battery.length ? `<table class="vt"><tbody>${d.low_battery.map(b => `<tr class="row" data-id="${escape(b.id)}"><td><b>${escape((S.roster[b.id] || {}).short_name || b.id.slice(-4))}</b></td><td class="num" style="color:${b.battery < 10 ? "#ff8a8a" : "var(--warm)"}">${b.battery}%</td><td class="num">${b.voltage != null ? b.voltage.toFixed(2) + " V" : ""}</td><td class="dim">${ago(b.ts)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">no node under 20% — nice</div>'}</div>
            <div class="vcard"><h2>👥 Top senders · 24h</h2>${d.top.length ? `<table class="vt"><tbody>${d.top.map(x => `<tr class="row" data-id="${escape(x.id)}"><td><b>${escape(x.short_name || x.id.slice(-4))}</b></td><td class="num">${x.message_count}</td><td class="num">${snrSpan(x.avg_snr)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">no messages yet</div>'}</div>
          </div>`;
        viewBody.querySelectorAll("tr[data-id]").forEach(tr => tr.onclick = () => { location.hash = "#/node/" + encodeURIComponent(tr.dataset.id); });
      }
    },
  };
  function setNav(view) { sidebar.querySelectorAll("a[data-view]").forEach(a => a.classList.toggle("on", a.dataset.view === view)); }
  async function showView(name, arg) {
    const v = VIEWS[name]; if (!v) return;
    document.body.classList.add("viewing"); viewEl.hidden = false; setNav(name); $("health-bar").hidden = true;
    viewTitle.textContent = v.title; viewTools.replaceChildren();
    viewBody.innerHTML = '<div class="skel"><div class="row"><div class="k"></div><div class="k"></div><div class="k"></div><div class="k"></div></div><div class="k tall"></div><div class="row"><div class="k tall"></div><div class="k tall"></div></div></div>';
    viewBody.scrollTop = 0;
    if (v.soon) { viewBody.innerHTML = `<div class="vcard"><h2>${escape(v.title)}</h2><p style="margin:0 0 10px">Next increment: ${escape(v.soon)}.</p><a class="link-btn" href="/${name === "messages" ? "bbs-messages" : name}">Open in classic v1 →</a></div>`; return; }
    try { await v.render(arg); } catch (e) { viewBody.innerHTML = `<div class="empty">could not load: ${escape(e.message || e)}</div>`; }
  }
  function closeView() { document.body.classList.remove("viewing"); viewEl.hidden = true; setNav("home"); setTimeout(() => { map.invalidateSize(); pollHealth(); }, 50); }
  function route() {
    const h = location.hash || "#/", m = h.match(/^#\/([a-z]+)(?:\/(.+))?/);
    const name = m ? m[1] : "home", arg = m && m[2] ? decodeURIComponent(m[2]) : null;
    if (name === "home" || h === "#/") { closeView(); return; }
    if (name === "coverage") { closeView(); if (!$("cov-on").checked) { $("cov-on").checked = true; setCoverage(true); } setNav("coverage"); return; }
    if (name === "replay") { closeView(); setNav("replay"); if (!document.body.classList.contains("replaying")) tlEnterReplay(0); return; }
    if (name === "cat") { closeView(); setNav("cat"); document.querySelector('.ftab[data-tab="cat"]').click(); return; }
    showView(name, arg);
  }
  window.addEventListener("hashchange", route);
  route();

  function escape(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
})();
