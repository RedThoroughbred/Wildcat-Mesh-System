# Observatory v2 — a mesh command center anyone can run

*Vision + build strategy, 2026-09-13. Builds on The Den (Phase 1): one radio
owner, one MQTT bus, one config. Nothing here talks to a radio.*

## 1. The pitch in one paragraph

A single web page that makes a LoRa mesh **visible**: nodes glowing on a dark
map, links drawn between who actually hears whom, packets pulsing along those
links as they cross the bus, a coverage heatmap built from your own walks, and a
live feed of everything on the air — in real time, on a phone, with no account,
no cloud, and one command to install on a Pi or a Mac. It is **protocol-agnostic
by construction**: the page never sees Meshtastic. It sees a neutral packet
envelope on `wildcat/rx/*`, so a MeshCore radio (or a second Meshtastic node, or
a replayed log) plugs in by publishing the same envelope.

## 2. Three promises

### (a) Stunning
- **Dark "mission control" glassmorphism.** Map is the room; everything else
  floats over it in frosted panels. One accent (Wildcat terracotta) for *you*,
  cool tones for the mesh, warm for alerts. No dashboard chrome, no tables on
  the front page.
- **Motion that means something.** A packet is a pulse travelling the link it
  arrived on. A node's ring breathes when it's online and fades as it goes
  quiet. Links brighten with use. The feed slides, never jumps.
- **Zero build step.** Vanilla JS + vendored Leaflet + Socket.IO, served by
  the Flask app that's already there. Same philosophy as the BBS: edit a file,
  reload.
- **Mobile-gorgeous.** The map fills the phone; the feed becomes a bottom
  sheet; node cards are thumb-sized. It's the screen you open on the hill.

### (b) Easy for anyone to run
- **One command.** `./deploy/install.sh` (Pi) or `./setup.sh` (Mac/Linux dev)
  → venv, deps, `wildcat.toml`, units, `wildcat doctor`. Observatory v2 is a
  route on the existing service — nothing new to install.
- **Zero hardcoded anything.** Host/port/db/radio/broker come from
  `wildcat.toml`; the map centres on your base node's own GPS fix (or the
  centroid of what it hears); the page uses relative URLs so it works on
  whatever IP/hostname you opened it at. Cross-machine viewers need no config.
- **Works with no internet** except map tiles (Phase 3 caches a tile box for
  your area; until then the map degrades to a dark grid with links still drawn).
- **Degrades honestly.** No broker? The page renders from the database with a
  "bus offline" badge. No positions? Nodes sit in a ring around the base.
- **README-first.** `wildcat doctor` tells you what's wrong before you open a
  browser.

### (c) Protocol-agnostic — the neutral envelope

The bus contract (docs/MESHD_MIGRATION.md) already says every consumer reads
`wildcat/rx/<kind>`. v2 pins down the **neutral fields** every producer must
fill, whatever the radio:

```jsonc
// wildcat/rx/<kind>   kind ∈ text | position | telemetry | neighbors | nodeinfo | routing | other | encrypted
{
  "v": 1,                          // envelope version
  "proto": "meshtastic",           // "meshcore" | "replay" | …
  "kind": "text",
  "from": "!9e766b18",             // STRING node id, proto-native form
  "to": "!716c668c",               // or null
  "broadcast": false,
  "channel": 0,                    // proto channel index/name; null if n/a
  "rx": { "time": 1700000000, "snr": 5.5, "rssi": -86, "hops": 0 },   // hops USED to reach us; null if unknown
  "received_at": 1700000001.5,     // when meshd saw it
  // exactly one payload block, by kind:
  "text": "M",
  "position":  { "lat": 38.88, "lon": -84.62, "alt": 278, "sats": 7, "time": … },
  "telemetry": { "battery": 101, "voltage": 4.32, "channel_util": 4.4, "air_util_tx": 0.9,
                 "uptime": 1234, "temperature": 21.5, "humidity": 40, "pressure": 1013 },
  "neighbors": [ { "id": "!a0388880", "snr": 7.25 } ],
  "nodeinfo":  { "id": "!…", "short_name": "STAY", "long_name": "…", "hw": "STATION_G2", "role": "CLIENT_BASE" },
  // proto-specific extras (consumers written against the native lib use these; the UI never does):
  "portnum": "TEXT_MESSAGE_APP", "packet": { …the native packet, lossless… }
}
```

and the retained roster:

```jsonc
// wildcat/nodes  (retained)
{ "ts": …, "myNodeNum": …, "my_id": "!9e766b18", "count": 200,
  "roster": { "!a0388880": { "id": "!a0388880", "proto": "meshtastic", "short_name": "STAY", "long_name": "…",
                             "hw": "STATION_G2", "role": "CLIENT_BASE", "last_heard": 1700000000,
                             "hops_away": 0, "snr": 7.25,
                             "position": { "lat": …, "lon": …, "alt": … },
                             "battery": 100, "voltage": 4.1, "channel_util": 3.2, "air_util_tx": 0.5 } },
  "nodes": { …the native interface.nodes map (meshtastic only; BusInterface needs it)… } }
```

Rules: neutral fields are **required** (null when unknown), ids are opaque
strings, units are SI-ish and explicit (°C, %, V, m), timestamps are Unix
seconds. Anything proto-specific lives beside them, never instead of them.
`wildcat/tx` is already neutral (`to`, `text`/`chunks`, `channel`, `priority`).

**What a MeshCore → bus adapter needs** (Phase 6-ish, sketched so nothing here
paints it into a corner):
1. A MeshCore companion/repeater radio on its own port (MeshCore and Meshtastic
   are different firmware — one node can't do both).
2. A Python client for the MeshCore companion serial protocol (the community
   `meshcore` / `meshcore_py` packages speak it) publishing the envelope above
   with `proto: "meshcore"`, node ids as MeshCore public-key prefixes, `channel`
   as the channel *name* (`#nky-info`), `rx.hops` from the path length, and
   `neighbors` from repeater path info. Its `wildcat/nodes` contribution is
   merged by id (ids from different protos never collide — different formats).
3. A second `wildcat/tx` consumer keyed by `proto` (or a `proto` field on the
   request) so the BBS/brain can answer on the network the question came from.
   The TX queue and pacing are per-radio.
4. Nothing on the UI side. It keys everything on `proto` + string ids already.
   Node markers get a proto glyph; the feed gets a proto chip; done.

The same shape makes a **replay adapter** trivial (`wildcat replay file.jsonl`
republishing saved envelopes at speed) — that's how the UI gets developed on a
laptop with no radio, and how Seth's 0.6-mile walk renders today.

## 3. What actually fills a gap (honest read)

| Area | What exists | Gap v2 fills |
|---|---|---|
| **Node list / map** | Meshtastic apps, meshtastic.org web client, MeshMap/Liam's map (MQTT-scraped, regional), Meshtastic-web dashboards | Those show *positions*. None show *links* (who hears whom, from NeighborInfo + direct-hop inference) or *traffic in motion*. Nobody animates. |
| **Coverage** | Range Test module writes CSV on the node; people plot it by hand; a few hex-map scripts | **Automatic** heatmap from the base's own RX (SNR/RSSI + the mobile node's last fix), accumulated across walks/drives, re-rendered after every antenna change. This is the killer feature and it's cheap because meshd already sees every packet. |
| **Monitoring** | Node telemetry in apps; Grafana stacks via MQTT for the ambitious | A monitoring page that a non-ops person can read: battery/SNR sparklines, "last heard" pulses, low-voltage trends for the solar repeater. |
| **AI on the mesh** | A handful of hand-rolled bots (GPT relays) with no rate limits or airtime discipline | Phase 2's `brain` + an **AI conversation view** here (who asked what, which provider answered, how many chunks, rate-limit hits). Airtime-safe by design. |
| **Cross-protocol** | Nothing sanctioned; Meshtastic and MeshCore communities are separate | One *dashboard* for both (not a relay between them — see WILDCAT_MESH_V2 §2.5). Watching both meshes on one map is genuinely new. |
| **Self-hosting** | Most dashboards assume MQTT-to-cloud + Docker + Grafana | Pi + one command + no cloud. |

What v2 does **not** try to be: a Meshtastic client (send DMs, configure nodes
— the apps are great at that), a public regional map (MeshMap exists), or a
Grafana replacement for people who already run Grafana.

## 4. Architecture (what's being built)

```
wildcat/rx/*, wildcat/nodes, wildcat/meshd/status  (MQTT, from meshd / any adapter)
        │
        ▼
wildcat/observatory/bridge.py   ── in-memory State: roster · links · packet ring · counters
        │  (paho thread)                 · REST /v2/api/state  (first paint)
        │                                · Socket.IO events: packet · roster · link · status  (live)
        ▼
observatory/app.py  (existing Flask + Socket.IO; v1 pages untouched)  ──►  /v2  (blueprint)
        │
        ▼
observatory/static/v2/app.js + app.css   — vanilla JS, vendored Leaflet + Socket.IO, dark tiles
```

- The bridge holds state so a page can render **instantly** from `/v2/api/state`
  and then only apply deltas. Restarting the Observatory rebuilds the state from
  the retained `wildcat/nodes` + the DB (positions/telemetry history).
- **Links** come from two sources: `neighbors` payloads (the honest topology,
  needs the NeighborInfo module on) and **direct-hop inference** — a packet
  with `rx.hops == 0` was heard straight from its sender, so `from ↔ base` is a
  real RF link with a real SNR. That gives a live topology today, with zero
  node config.
- **Coverage points** = every envelope with `rx.snr` from a node whose latest
  position is known → `(lat, lon, snr, rssi, hops, ts, from)`. Stored in a new
  `rx_points` table (bridge writes it), rendered as hex bins colored by best SNR.

## 5. Build order (each step is viewable at /v2)

| # | Increment | Viewable as |
|---|---|---|
| 1 | **Live map + packet stream** (this checkpoint): nodes from the roster with GPS, online glow by age, base ringed; links from direct-hop inference + NeighborInfo; **pulses** animate sender → base on each packet; glass top bar with live counters; live feed panel with kind icons, SNR/RSSI/hops; node tap → card. | `/v2` |
| 2 | **Coverage heatmap**: `rx_points` table + `/v2/api/coverage` + hex-bin layer with a toggle; Range Test packets tagged; import the 0.6-mile walk from `message_logs`/`position_logs` history so it renders on day one. | `/v2` layer toggle |
| 3 | **Node cards done properly**: signal ring, battery + SNR/RSSI sparklines from `telemetry_logs`, last-heard pulse, hops-away; a nodes drawer with search. | `/v2` |
| 4 | **Mesh health strip + alerts**: channel util / air-time TX gauges from the base's telemetry, packets/min history, low-battery + gone-quiet alerts. | `/v2` |
| 5 | **PWA** (manifest + service worker + offline shell + tile cache) and the **mobile pass** — shipped; **cache-a-box** for tiles and a **replay adapter** follow. | everywhere |
| 6 | **AI conversation view** when `brain` lands (Phase 2). | `/v2/brain` |

v1 pages stay exactly as they are until v2 covers them; then `/` redirects.

## 5b. v1 superset — every classic option, reimagined (in progress)

Seth: "I miss all the options from v1." v2 stays map-first but now carries a
navigation shell (glass sidebar ≥1180 px, icon rail down to 761 px, slide-in
drawer on phones) and hash-routed **views** over the dimmed map, each reading
the same tables v1 reads through `wildcat/observatory/queries.py` (v1's SQL,
ported and tested; v1 itself untouched at `/`).

| v1 page | v2 view | Status |
|---|---|---|
| Dashboard | Home (live map + feed + KPIs) and `#/dashboard` | ✅ |
| Map | Home | ✅ (+ links, pulses, coverage) |
| Nodes table (search/sort/CSV) | `#/nodes` | ✅ |
| Node detail (SNR/RSSI history, reliability, recent messages) | `#/node/<id>` | ✅ |
| Channels (activity, top senders, heatmap, details, 24h/7d/30d) | `#/channels` | ✅ |
| Channel detail | `#/channel/<n>` | ✅ |
| BBS Messages (DM conversations) — plus Bulletins by board and privacy-safe Mail counts (new) | `#/messages` | ✅ |
| Propagation (hourly SNR, best/worst, distribution, insights) | `#/propagation` | ✅ |
| Topology (neighbour graph + stats) | `#/topology` | ✅ (honest NeighborInfo note; map draws the same links) |
| Admin (broadcast/DM via the bus, exports, live logs, services + restart, BBS **config editor**, content editors) | `#/admin` | ✅ (read-only in `/v2/public`) |
| API docs (with try-it) | `#/api` | ✅ |
| CSV exports | `/v2/api/export/{nodes,messages,coverage}.csv` | ✅ (v2-native) |
| Dashboard cards | `#/dashboard` | ✅ |
| Mesh health + alerts (new) | `#/health` + top-bar badge | ✅ |

v2 links nowhere else: no "open in v1" anywhere. v1 stays served at `/` only
until Seth retires it.

## 6. Roadmap (a) — Mobile: PWA now, native later

**Now (shipped in this branch): an installable PWA.** `/v2` carries a web-app
manifest (`/v2/manifest.webmanifest`, standalone display, Wildcat icons incl. a
maskable one), a service worker (`/v2/sw.js`, scope `/v2/`) and iOS/Android
home-screen metadata. What that buys, with no app store and no build step:

- **Home-screen app** on iOS (Share → Add to Home Screen; the page shows a
  one-time hint) and Android/desktop Chrome (an "Install" chip appears when the
  browser offers it). Runs full-screen, dark status bar, safe-area aware.
- **Offline shell**: the page, styles, scripts, vendored Leaflet/Socket.IO and
  icons are served network-first with a cache fallback, so the app opens with
  no connection. `/v2/api/state` and `/v2/api/coverage` are cached the same way,
  so it opens showing the **last-known mesh** — the field-station case.
- **Offline tiles, seeded by use**: every map tile you've looked at is cached
  (capped LRU, 1500 tiles ≈ 25 MB). Phase 3 adds a one-time "cache this box"
  action for the home area.
- **Mobile layout**: the feed is a bottom sheet with peek / half / full states
  (tap the handle or swipe), the layers panel collapses to a button, the stat
  strip scrolls, the node card sits above the sheet. Verified at 375 × 812.

**Built native-ready from now on (Seth, 2026-09-13: "build it in a way to become a
Capacitor iOS app, and eventually Android").** Constraints the codebase now honors so
that wrapping is a packaging task, not a rewrite:

- The page is a **static shell**: one HTML file + `static/v2/app.{js,css}` + vendored
  Leaflet/Socket.IO. Nothing it needs is server-rendered except the asset version stamp.
- **Configurable API base.** Every request goes through one helper; same-origin when
  Flask serves the page, else `window.WILDCAT_API_BASE` (set by a 3-line `config.js`
  in the native bundle) or a stored `v2.apiBase`. Socket.IO connects to the same base.
- **CORS on `/v2/api/*`** so a `capacitor://localhost` origin can call the Den.
- **No cookies, no sessions, no server-side state in the page.** Public vs operator is
  a body class; a native shell can flip it the same way.
- Touch-first mobile layout already exists (sheet, drawer, safe areas).
- A `build-www.sh` for this repo (mirror `observatory/templates/v2/index.html` →
  `www/index.html` with a static `config.js`, plus `static/`) is the whole bridge to
  `npx cap add ios`; Repot's pipeline is the template. Flutter/Dart is the other road
  Seth mentioned — that would consume the same API and Socket.IO events, so nothing
  here forecloses it.

**Limits of the PWA path** (honest): iOS gives PWAs no background execution
and no push without the user adding it to the home screen (and even then push
is iOS 16.4+ only); no Bluetooth/serial to a *phone-attached* radio; the
"install" affordance is a browser convention people don't always know. For a
community dashboard fed by a Den on the network, none of that matters — the
phone is a viewer.

**Later — native iOS/Android, what it would take.** The cheapest credible path
is the one Repot uses: **Capacitor** wrapping this exact page (same HTML/JS,
no rewrite), plus native plugins only where the web can't reach:

1. *Push notifications* — "GO just came into range", "Lantern battery low",
   "someone asked the Cat" — needs APNs/FCM and a tiny push relay in the Den
   (the bridge already knows every event; ~200 lines + a device-token table).
2. *Local radio* — talk to a phone-attached node over BLE (Meshtastic's BLE
   API) so the app can be a **Den-less viewer** in the field: the same neutral
   envelope, produced on-device. This is the piece that makes the app useful
   with no infrastructure at all, and it's also the biggest lift (BLE plugin,
   protobuf decode on device).
3. *Background location* for automatic coverage walks (the phone as the GO node).
4. App Store/Play plumbing: bundle ids, signing, review — a week of yak, well
   understood from Repot.

Order: (1) is a weekend and pays immediately; (2) is the real product; (3)–(4)
follow (2). Nothing in the UI changes shape for any of it — that's the point of
building the PWA first.

## 7. Exploration (not a plan) — could one Den watch Meshtastic *and* MeshCore?

**Status: an idea, deliberately unfleshed.** Seth's call (2026-09-13): merging
MeshCore "needs true fleshing out and research before it becomes any type of
plan for architecture." So nothing below is committed, scheduled, or designed
against; it is kept only so the *neutral envelope* decision (§2c) isn't
accidentally undone. What would have to be researched first, honestly:

- **The protocol itself.** MeshCore's companion-radio serial API, its packet
  types, path/route semantics, and how its node identity (public keys) maps
  to anything a viewer can name. Which Python client, if any, is maintained.
- **Whether a second radio is even wanted.** It's a different firmware on a
  dedicated board; that's hardware, placement, and airtime on a network Seth
  isn't part of yet.
- **Community etiquette and consent.** OKI Mesh's norms for observers,
  gateways, and bots; what they consider a bulletin vs spam; whether a Den
  listening on their network is welcome at all.
- **Licensing/legal.** Same Part 15 rules, but MeshCore's encryption defaults
  and channel conventions differ.
- **Value.** What Seth (and NKY) would actually get from one map of both that
  they don't get from two apps.

Only if those come back positive does an architecture conversation start. For
the record, the reason the *envelope* stays neutral anyway: it costs nothing
now and keeps that door unlocked. The sketch below is the only thing that was
ever drawn, kept for context:

```
Meshtastic node ──serial/tcp──► meshd            ──┐
                                                   ├──► wildcat/rx/*  (neutral envelope, proto tag)
MeshCore companion ──serial──► meshcored (Phase 6) ──┘        │
                                                   wildcat/nodes  (roster merged by id; ids never collide)
                                                              │
                                              observatory v2 ─┴─ one map, one feed, one coverage layer
                                              bbs / brain ───── reply on wildcat/tx with {"proto": …}
```

1. **Second radio, second owner.** A MeshCore-flashed node (the spare Heltec, or
   a dedicated board) on its own port; `wildcat meshcored` owns it exactly like
   meshd owns 6b18 — one process per radio, both publishing the same envelope
   with `proto: "meshcore"`. Config is one more table: `[meshcore] port = …`.
2. **Roster merge.** MeshCore node ids are public-key prefixes; Meshtastic's are
   `!xxxxxxxx`. The bridge already merges `wildcat/nodes` by string id, so both
   populations coexist in one roster. The UI colors/glyphs markers by `proto`
   (one CSS rule) and the feed shows a proto chip (already rendered when present).
3. **Links and coverage** need nothing: MeshCore path info → `neighbors`;
   received SNR/RSSI + a fresh fix → the same `rx_points` table. One coverage
   map shows where *each* network reaches, filterable by proto.
4. **Replies go back the right way.** `wildcat/tx` gains an optional `proto`
   (default: the proto of the packet being answered); each radio owner drains
   only its own. The BBS and the brain don't change — the adapter stamps it.
5. **Etiquette stays enforced by construction.** Nothing crosses from one
   network's `rx` to the other's `tx` unless a human builds an explicit gateway
   (the one-way bulletin experiment in §2.5), and the UI stays read-only.

If it ever happens, the payoff would be one URL showing both meshes live. But
"~300 lines" estimates and Phase-6 slots are withdrawn until the research above
is done — see WILDCAT_MESH_V2 §2.5, which already says no cross-network relay
without both communities' say-so.

## 8. Non-goals / guard rails
- No writes to the radio from the UI in v2.1 (read-only command center; sending
  is the BBS's/brain's job through `wildcat/tx`). A "send" affordance is a later,
  admin-gated feature.
- No cloud, no accounts, no analytics. LAN + Tailscale.
- No framework/bundler. If a feature needs React, it's the wrong feature for this page.
