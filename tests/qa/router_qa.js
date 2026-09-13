// Browser-side router QA. Paste into the console on /v2 (or run via Playwright evaluate):
//   await (async () => { <this file> })()
// Every line must start with OK. Exercises: view ↔ view ↔ detail ↔ home, action routes,
// replay exit on navigation, compose hidden across views, Esc/× close, same-hash clicks.
(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const $ = id => document.getElementById(id);
  const st = () => ({ hash: location.hash, view: !$("view").hidden, viewing: document.body.classList.contains("viewing"),
    title: $("view-title").textContent.trim().slice(0, 18), nav: [...document.querySelectorAll(".sidebar a.on")].map(a => a.dataset.view).join(","),
    card: !$("card").hidden, compose: !$("compose").hidden && getComputedStyle($("compose")).display !== "none", replaying: document.body.classList.contains("replaying"),
    body: $("view-body").innerText.trim().slice(0, 24).replace(/\s+/g, " ") });
  const out = []; const check = (label, cond, s) => out.push(`${cond ? "OK " : "BAD"} ${label} :: ${JSON.stringify(s)}`);
  const go = async (h, ms = 900) => { location.hash = h; await wait(ms); return st(); };
  let s;
  s = await go("#/nodes"); check("nodes view", s.view && s.nav === "nodes" && s.title === "Nodes", s);
  s = await go("#/node/!716c668c"); check("node detail highlights Nodes", s.view && s.nav === "nodes", s);
  s = await go("#/channel/0"); check("channel detail highlights Channels", s.view && s.nav === "channels", s);
  s = await go("#/"); check("home closes view", !s.view && !s.viewing && s.nav === "home", s);
  location.hash = "#/nodes"; await wait(20); location.hash = "#/channels"; await wait(2500); s = st();
  check("race nodes→channels shows channels", s.title === "Channels" && s.body.startsWith("64") || s.body.includes("MESSAGES"), s);
  for (const [h, name] of [["#/dashboard", "Dashboard"], ["#/messages", "Messages"], ["#/propagation", "Propagation"], ["#/topology", "Topology"], ["#/health", "Mesh health"], ["#/admin", "Admin"], ["#/api", "API"]]) {
    s = await go(h); check(`${name} view`, s.view && s.title === name && s.nav === h.slice(2), s);
  }
  s = await go("#/coverage"); check("coverage action rewrites URL to #/", s.hash === "#/" && !s.view && $("cov-on").checked, s);
  s = await go("#/replay", 1200); check("replay action rewrites URL, replaying on home", s.hash === "#/" && !s.view && s.replaying, s);
  s = await go("#/nodes"); check("opening a view exits replay", s.view && !s.replaying, s);
  s = await go("#/cat"); check("cat action rewrites URL", s.hash === "#/" && !s.view && !$("cat").hidden, s);
  document.querySelector('.ftab[data-tab="live"]').click();
  const m = document.querySelector(".leaflet-marker-icon .node:not(.base)"); if (m) { m.closest(".leaflet-marker-icon").click(); await wait(300); }
  s = st(); check("map card opens on home", s.card, s);
  s = await go("#/nodes"); check("view closes the card", s.view && !s.card, s);
  s = await go("#/"); document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await wait(200); s = st(); check("home after Esc", !s.view, s);
  s = await go("#/channels"); document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await wait(500); s = st(); check("Esc from a view goes home", !s.view && s.hash === "#/", s);
  s = await go("#/health"); $("view-close").click(); await wait(500); s = st(); check("× goes home", !s.view && s.hash === "#/", s);
  $("compose-btn").click(); await wait(200); s = await go("#/nodes"); check("compose hidden while viewing", s.view && !s.compose, s);
  s = await go("#/"); await wait(200); s = st(); check("compose back on home", s.compose, s); $("compose-close").click();
  s = await go("#/nodes"); document.querySelector(".sidebar a[data-view=nodes]").click(); await wait(400); s = st(); check("re-click same view is a no-op", s.view && s.title === "Nodes", s);
  s = await go("#/replay", 1200); document.querySelector(".sidebar a[data-view=home]").click(); await wait(400); s = st(); check("Live map click while replaying exits replay", !s.replaying && s.hash === "#/", s);
  s = await go("#/nope"); check("unknown route recovers to #/", s.hash === "#/" && !s.view, s);
  return out.join("\n");
})();
