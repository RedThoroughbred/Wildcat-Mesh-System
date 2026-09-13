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
  function showCard(id) {
    const n = S.roster[id]; if (!n) return; S.selected = id;
    $("card-short").textContent = name(n); $("card-ring").className = "ring " + ageClass(n);
    $("card-long").textContent = n.long_name || name(n); $("card-id").textContent = n.id; $("card-proto").textContent = n.proto || "";
    $("c-heard").textContent = ago(n.last_heard);
    $("c-hops").textContent = n.id === S.myId ? "this node" : n.hops_away != null ? (n.hops_away === 0 ? "direct" : n.hops_away) : "–";
    $("c-snr").textContent = n.snr != null ? n.snr.toFixed(1) + " dB" : "–";
    $("c-batt").textContent = n.battery != null ? (n.battery > 100 ? "on power" : n.battery + "%") + (n.voltage != null ? ` · ${n.voltage.toFixed(2)} V` : "") : "–";
    $("c-hw").textContent = n.hw || "–"; $("c-role").textContent = n.role || "–";
    const p = pos(n); $("c-pos").textContent = p ? `${p[0].toFixed(5)}, ${p[1].toFixed(5)}` + (n.position.alt != null ? ` · ${Math.round(n.position.alt)} m` : "") : "no position";
    $("card").hidden = false;
  }
  $("card-close").onclick = () => { $("card").hidden = true; S.selected = null; };

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
    upsertNode(ev.node);
    for (const l of ev.links || []) upsertLink(l);
    addPacket(ev.packet, true);
    const p = ev.packet;
    flashNode(p.from);
    if (S.myId && p.from !== S.myId) {
      if (p.hops === 0) pulse(p.from, S.myId, false);
      else if (p.hops != null) pulse(p.from, S.myId, true);
    }
    if (S.selected === p.from) showCard(p.from);
    refreshStats();
  }

  // ---------------------------------------------------------------- boot
  fetch("api/state").then(r => r.json()).then(applySnapshot).catch(() => setStatus(false, null));
  const sock = io("/v2", { transports: ["websocket", "polling"] });
  sock.on("snapshot", applySnapshot);
  sock.on("packet", applyPacket);
  sock.on("roster", (r) => { S.myId = r.my_id || S.myId; for (const id in r.roster) upsertNode(r.roster[id]); refreshLinks(); refreshStats(); fitOnce(); });
  sock.on("status", (s) => { S.myId = s.my_id || S.myId; setStatus(s.bus, s.meshd); });
  sock.on("disconnect", () => setStatus(false, null));
  setInterval(() => { refreshAges(); refreshLinks(); refreshTimes(); refreshStats(); }, 15000);

  function escape(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
})();
