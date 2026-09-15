# CLAUDE.md — Wildcat Companion (the fork)

*Copy this file to the root of the fork as `CLAUDE.md`. It is written for Claude
(Fable) building the app, with Seth as the operator and first user. It says what
the app is, what must not change, how to build it in order, and how to know each
step is done. Written 2026-09-14 from the Den codebase this fork grows out of.*

---

## What this is

**Wildcat Companion** is a phone-first mesh app that talks to a **radio in your
pocket** (Meshtastic or MeshCore, over Bluetooth) **and/or** to a **Den** (the
Wildcat Mesh server: `wildcat/`, `observatory/`, MQTT bus, SQLite) over the
network — and shows both through the one UI the Den already has: the live map,
the feed, node cards, coverage, conversations, RF-vs-internet, SOS, the digest.

The thing it does that neither the Meshtastic app nor the MeshCore app does:
**one app, several radios/networks, one at a time, with the same screens.** A
person with a Meshtastic node on the NKY mesh and a MeshCore node on a different
mesh switches between them like accounts; the history for each stays separate;
the Den view is a third "account" that needs no radio at all. Later: the
companion uploads what its radio heard to a Den (coverage from every member's
phone) and receives store-and-forward mail when it comes back in range.

It is not "a nicer Meshtastic app". It beats them by being **one place** for the
whole mesh life — the radio in your hand, the base station on the hill, the
community — not by re-implementing every setting screen those apps have. Start
narrow (see phases) and let the settings screens stay in the vendor apps for as
long as that is honest.

## The parent project (read these first)

- `docs/OBSERVATORY_V2.md` — the vision, the **neutral envelope** (§2c), the
  Capacitor path (§6), the MeshCore exploration (§7). The envelope is the seam
  this fork stands on: every UI panel consumes `{v, proto, kind, from, to,
  broadcast, channel, rx{time,snr,rssi,hops,via_mqtt}, received_at, <payload>}`
  and knows nothing about radios. **Do not fork the UI away from the envelope.**
- `docs/DECISIONS.md` — D-001 … D-027. In particular D-022 (SQLite writers
  commit-or-rollback-and-close), D-024 (Bobcat guardrails), D-025 (SOS is text),
  D-027 (RF vs MQTT evidence, never mistaken for radio truth).
- `docs/IDEAS.md` — the ambitions this fork is the vehicle for.
- `wildcat/observatory/bridge.py` — `State` is the roster/links/feed/evidence
  model in Python. **This fork ports `State` to JavaScript** (`src/state.js`) so
  it runs on the phone with no server; the Python one stays the reference and
  the parity fixture (`tests/state_fixtures.json`) keeps them identical.
- `observatory/static/v2/app.js` / `app.css` / `templates/v2/index.html` — the UI.
  Fork it as-is; the first commit of the fork is "the Den UI, unchanged, wrapped
  in Capacitor". Every later change to shared UI should be upstreamable.

## Non-negotiables

1. **Protocol-agnostic in the model, honest in the UI.** Node ids are opaque
   strings; `proto` says which radio. The UI may *style* by proto (a badge), never
   *branch* by it. A MeshCore packet and a Meshtastic packet are the same envelope.
2. **Airtime is the scarce resource.** Every send path has a byte cap, a rate
   limit, and shows its cost. No feature may auto-transmit without the person
   knowing (the Den's SOS is two taps; the responder is off by default; keep that
   standard).
3. **Offline first, then sync.** The phone app must be fully useful with no
   internet and no Den: map (cached tiles for the home county), feed, nodes,
   conversations, coverage from its own radio. Den sync is additive.
4. **One codebase, no build step for the web target.** Vanilla JS + Leaflet +
   Socket.IO, exactly like the parent. Capacitor wraps `www/`; native code is
   limited to plugins (BLE, notifications, filesystem, keychain). If a build step
   ever becomes necessary, it must still produce a plain `www/` the Den can serve.
5. **Per-account isolation.** Each account (radio or Den) has its own SQLite
   database, its own settings, its own credentials. Switching accounts is a
   full model swap — nothing leaks, and deleting an account deletes its data.
6. **Nothing that needs the Den reachable from the open internet.** Tailscale or
   LAN, with the operator token. The companion talks to a Den through the same
   `/v2/api` + Socket.IO the browser uses.
7. **Tests green, commit per piece, no secrets in the repo, v1 of nothing touched.**

## Architecture

```
                 ┌──────────────── UI (forked app.js, app.css, index.html) ────────────────┐
                 │  map · feed · nodes · conversations · coverage · SOS · digest · settings │
                 └───────────────▲────────────────────────────────────▲─────────────────────┘
                                 │ envelope events (packet, roster, status, tx, brain, sos)  │ writes: send(), sos()
                 ┌───────────────┴────────────────────────────────────┴─────────────────────┐
                 │                          Account (one active at a time)                  │
                 │   state.js (JS twin of State) · db.js (per-account SQLite) · settings     │
                 └───────▲──────────────────────▲──────────────────────────▲────────────────┘
                         │                      │                          │
              ┌──────────┴─────────┐  ┌─────────┴──────────┐   ┌───────────┴───────────┐
              │ transports/den.js  │  │ transports/         │   │ transports/            │
              │ Socket.IO + /v2/api│  │ meshtastic-ble.js   │   │ meshcore-ble.js        │
              │ (a Den is an       │  │ @meshtastic/core +  │   │ MeshCore companion     │
              │  account too)      │  │ Capacitor BLE       │   │ protocol over BLE      │
              └────────────────────┘  └─────────────────────┘   └────────────────────────┘
```

- **A transport** is a small object: `connect()`, `disconnect()`, `send(req)`
  (the same `wildcat/tx` request shape), and it emits **envelopes** (`rx/*`),
  **roster** snapshots, and **status** — the exact payloads the Den's bus carries.
  `transports/den.js` is trivial (it forwards the socket). The radio transports
  translate vendor packets → envelopes; that translation is the *only* place
  vendor formats are allowed (mirror `wildcat/meshd/packets.py` for Meshtastic).
- **The account** owns one transport, one `State`, one database. The UI is bound
  to the active account; switching accounts tears the binding down and rebuilds
  it (same as a page reload, without the reload).
- **Sync (later)**: an account with a radio can also be *linked* to a Den; on
  reconnect it uploads its RX points and text history as envelopes (`POST
  /v2/api/ingest`, to be built on the Den side), and pulls store-and-forward mail.

## Build order (each phase is shippable; do not start the next before the last is verified on a device)

**Phase 0 — the shell (1–2 weeks).** Capacitor iOS project; `www/` = the Den
UI unchanged; the "Den account" transport; a settings screen with Den URL +
operator token (keychain); push notifications for SOS and DMs via the Den (needs
the Den's HTTPS through Tailscale and a `POST /v2/api/push/subscribe` the parent
will provide); home-screen install parity. **Done when:** Seth uses the app on
his iPhone over Tailscale for a week and prefers it to the browser.

**Phase 1 — the JS State (1 week).** Port `State` to `src/state.js` with the
parity fixture; per-account SQLite via `@capacitor-community/sqlite` (sql.js in
the browser for dev, exactly the Repot pattern); history, coverage points and
node_transport persisted locally. **Done when:** the Den account works with the
network off, from local history.

**Phase 2 — Meshtastic over BLE (3–4 weeks).** `@meshtastic/core` (or the
protobufs directly) + `@capacitor-community/bluetooth-le`; pairing flow; the
packet → envelope translator; sends through the radio; the app now works with
NO Den. Prototype first in desktop Chrome with Web Bluetooth against Seth's GO
node — it settles in a weekend whether the seam holds. **Done when:** GO in
the pocket, phone in airplane mode, map/feed/conversations live from the radio;
a DM sent from the app arrives at the Den's BBS.

**Phase 3 — accounts (1 week).** The account switcher; isolation tests (switch,
delete, re-add); each account's own database and settings; a Den-linked radio
account.

**Phase 4 — MeshCore over BLE (research 1 week, build 3–4 weeks).** Do the
research in `docs/MESHCORE.md` first: the companion protocol, what a packet
exposes, how ids/channels/paths map to the envelope, what "neighbors" means
there. Then the transport. **Done when:** the same conversations screen shows a
MeshCore channel with no UI change.

**Phase 5 — sync (2 weeks).** Upload envelopes to a linked Den; Den-side
`ingest` with dedup and provenance (`source: "companion:<device>"`) so
crowd-sourced coverage is honest about who measured what; store-and-forward
delivery on reappearance.

## Working rules for Claude

- Before touching a screen, read the parent's version and ask whether the
  change is upstreamable. If yes, make it in the parent first (or in a form
  that merges cleanly); the fork should track the parent's UI for as long as
  possible.
- Every transport ships with a **replay fixture**: a captured stream of vendor
  packets and the envelopes they must produce. Tests run without hardware.
- Device verification is not optional: Phase acceptance is on an iPhone with a
  real radio, and the result goes in `docs/DEVICE_LOG.md` with the date, the
  firmware versions, and what failed.
- Keep a `docs/DECISIONS.md` in the fork from day one (start numbering at
  F-001) and record why, not just what.
- When something is unclear about a vendor protocol, do the research and write
  it down before coding; never invent packet semantics. Flag anything
  unconfirmed as such.
- Airtime: any new send path gets the byte cap, the rate limit, and the cost
  note in the same commit as the feature.
- Do not add a build step, a UI framework, a paid map provider, or an analytics
  SDK. Do not store tokens outside the keychain. Do not ship a screen that only
  works online.

## What "beats both" concretely means (the bar for each phase)

| The vendor apps | Wildcat Companion |
|---|---|
| one radio protocol each | Meshtastic and MeshCore, same screens, switchable |
| the node list is a list | the live map, links, coverage, RF-vs-internet honesty |
| messages are a chat | conversations **plus** the mesh's story: digest, replay, health |
| no base station concept | a Den account: the community's hub, the operator's tools |
| your phone hears only what your radio hears | linked to a Den, you also see what the hill hears — and the hill sees what you heard |
| settings-heavy | opinionated: the three things you need are one tap; the rest stays in the vendor app until it is honest to move it |

## Names and places

- Parent repo: `RedThoroughbred/Wildcat-Mesh-System`, branch `v2/phase1-den`
  (the Den, the Observatory, Bobcat). This fork: `RedThoroughbred/wildcat-companion`
  (suggested). Bundle id suggestion: `com.wildcatmesh.companion`.
- Ask Seth before: enabling push in production, publishing to TestFlight,
  anything that transmits automatically, anything that costs money per use.
