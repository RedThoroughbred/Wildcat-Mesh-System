/* Observatory v2 — live mesh map + packet stream, fed by /v2/api/state then Socket.IO deltas.
   Protocol-agnostic: only the neutral envelope fields are read (docs/OBSERVATORY_V2.md §2c).
   Vanilla JS, no build step. Relative URLs only — works on whatever host you opened it from. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
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
  function addPacket(p, fresh) {
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
  }
  $("filters").addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    S.filter = b.dataset.kind;
    for (const c of $("filters").children) c.classList.toggle("on", c === b);
    for (const el of list.children) el.hidden = !(S.filter === "all" || S.filter === el.dataset.kind);
  });
  function refreshTimes() { for (const el of list.querySelectorAll("[data-ts]")) el.textContent = ago(+el.dataset.ts); }

  // ---------------------------------------------------------------- stats + status
  function setStat(id, v, bump) { const el = $(id); if (el.textContent !== String(v)) { el.textContent = v; if (bump) { const s = el.parentElement; s.classList.remove("bump"); void s.offsetWidth; s.classList.add("bump"); } } }
  function refreshStats(stats) {
    const t = now(), hour = t - 3600, R = Object.values(S.roster);
    setStat("s-nodes", R.length);
    setStat("s-heard", R.filter(n => (n.last_heard || 0) >= hour).length);
    setStat("s-map", R.filter(n => pos(n)).length);
    S.times = S.times.filter(x => x >= t - 60);
    setStat("s-rate", stats && stats.per_min != null && !S.times.length ? Math.round(stats.per_min) : S.times.length, true);
    const me = S.roster[S.myId];
    setStat("s-util", me && me.channel_util != null ? me.channel_util.toFixed(1) + "%" : "–");
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
    setStatus(s.bus, s.meshd); refreshStats(s.stats); fitOnce();
  }
  function applyPacket(ev) {
    if (ev.my_id) S.myId = ev.my_id;
    S.times.push(now());
    if (ev.node && ev.packet && ev.packet.rssi != null) ev.node.rssi = ev.packet.rssi;
    upsertNode(ev.node);
    for (const l of ev.links || []) upsertLink(l);
    addPacket(ev.packet, true);
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
  const sock = io("/v2", { transports: ["websocket", "polling"] });
  sock.on("snapshot", applySnapshot);
  sock.on("packet", applyPacket);
  sock.on("rxpoint", addRxPoint);
  sock.on("roster", (r) => { S.myId = r.my_id || S.myId; for (const id in r.roster) upsertNode(r.roster[id]); refreshLinks(); refreshStats(); fitOnce(); });
  sock.on("status", (s) => { S.myId = s.my_id || S.myId; setStatus(s.bus, s.meshd); });
  sock.on("disconnect", () => setStatus(false, null));
  setInterval(() => { refreshAges(); refreshLinks(); refreshTimes(); refreshStats(); if (S.selected) showCard(S.selected); }, 15000);

  function escape(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
})();
