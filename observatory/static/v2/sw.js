/* Wildcat Mesh — Observatory v2 service worker.
   - App shell (page, css, js, vendor, icons): network-first with cache fallback → always fresh
     online, still opens offline.
   - /v2/api/state + /v2/api/coverage: network-first, cached copy when offline → the map
     renders the last-known mesh with no connection at all.
   - Map tiles (any host with /{z}/{x}/{y}.png): cache-first with a capped LRU-ish cache, so
     everywhere you've looked at works offline (Phase 3 pre-caches a box; this is the seed).
   - Everything else (socket.io, other APIs): network only.
   Bump VERSION to drop old caches. */
const VERSION = "v2-2026-09-13-1";
const SHELL = `wildcat-shell-${VERSION}`;
const DATA = `wildcat-data-${VERSION}`;
const TILES = "wildcat-tiles-v1";
const TILE_MAX = 1500;
const SHELL_URLS = [
  "/v2/", "/static/v2/app.css", "/static/v2/app.js", "/static/v2/manifest.webmanifest",
  "/static/vendor/leaflet/leaflet.css", "/static/vendor/leaflet/leaflet.js", "/static/vendor/socket.io.min.js",
  "/static/v2/icons/icon-192.png", "/static/v2/icons/icon-512.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_URLS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== DATA && k !== TILES).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

const isTile = (u) => /\/\d+\/\d+\/\d+(@2x)?\.png$/.test(u.pathname);
const isShell = (u) => u.origin === self.location.origin && (u.pathname === "/v2/" || u.pathname.startsWith("/static/"));
const isData = (u) => u.origin === self.location.origin && (u.pathname === "/v2/api/state" || u.pathname.startsWith("/v2/api/coverage"));

async function networkFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(req);
    if (res && (res.ok || res.type === "opaque")) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req, { ignoreSearch: req.url.includes("/v2/?") });
    if (hit) return hit;
    throw err;
  }
}
async function tileCacheFirst(req) {
  const cache = await caches.open(TILES);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req, { mode: "no-cors" });
  cache.put(req, res.clone()).then(() => trim(cache, TILE_MAX)).catch(() => {});
  return res;
}
async function trim(cache, max) {
  const keys = await cache.keys();
  if (keys.length <= max) return;
  for (const k of keys.slice(0, keys.length - max)) await cache.delete(k);
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const u = new URL(req.url);
  if (u.pathname.startsWith("/socket.io/")) return;
  if (isTile(u)) { e.respondWith(tileCacheFirst(req)); return; }
  if (isData(u)) { e.respondWith(networkFirst(req, DATA)); return; }
  if (isShell(u)) { e.respondWith(networkFirst(req, SHELL)); return; }
});
