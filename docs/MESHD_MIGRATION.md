# meshd + the MQTT bus — what's built, how it fits, how to migrate

Phase 1 step 5 of `WILDCAT_MESH_V2.md` (§2.1). Status as of 2026-09-13:
**built and unit-tested end-to-end with a fake radio; not yet run against real
hardware** — see `docs/DEPLOY_PI.md` for the switch-over procedure and what to
verify on the Pi.

## The shape

```
                 ┌────────────── the Pi ──────────────────────────────────────────┐
 LoRa ◄──► node ◄─tcp/serial─► wildcat-meshd ──► Mosquitto ──┬──► wildcat-bbs        (source = "bus")
                               (the ONLY radio client)       ├──► wildcat-telemetry  (source = "bus")
                                    ▲                        ├──► brain (Phase 2)
                                    └── wildcat/tx ◄─────────┴──  anything else: mosquitto_sub -t 'wildcat/#'
```

A Meshtastic node accepts **one** API client. In v1 the BBS and the telemetry
logger each opened their own connection and fought (the reason the legacy unit
killed the BBS every 5 minutes). In v2 `meshd` is the one client; everyone else
is an MQTT subscriber that can crash, restart, or not exist without touching
the radio.

## Topic tree (prefix from `[mqtt].topic_prefix`, default `wildcat`)

| Topic | Who | Retained | Payload |
|---|---|---|---|
| `wildcat/rx/text` `…/position` `…/telemetry` `…/neighborinfo` `…/nodeinfo` `…/routing` `…/traceroute` `…/rangetest` `…/other` `…/encrypted` | meshd → all | no | **envelope** (below) |
| `wildcat/nodes` | meshd → all | **yes** | `{ts, myNodeNum, count, nodes: <interface.nodes, JSON-safe>}` every `[meshd].nodes_publish_interval` s and on connect |
| `wildcat/meshd/status` | meshd → all | **yes** | `{state: starting\|connected\|disconnected\|stopped\|dead, ts, myNodeNum, radio, rxCount, txSent, txQueued, error?}` — `dead` is the MQTT last-will (process died) |
| `wildcat/tx` | anyone → meshd | no | **tx request** (below) |
| `wildcat/tx/result` | meshd → all | no | `{id, index, count, ok, packetId?, to?, error?}` one per chunk (or one with `ok:false` for a rejected request) |

Everything is JSON. QoS 1. Nothing on the bus is ever re-published to the radio
unless it arrived on `wildcat/tx` (there is no downlink from the outside world;
§2.5 bridging is a *separate*, off-by-default Phase 6 stanza).

**Envelope** (`wildcat/rx/*`):

```json
{"kind": "text", "portnum": "TEXT_MESSAGE_APP",
 "from": 2658765568, "fromId": "!9e766b18", "to": 2657904408, "toId": "!9e69a9d8", "broadcast": false,
 "channel": 0, "id": 12345, "rxTime": 1700000000, "rxSnr": 5.5, "rxRssi": -86, "hopLimit": 3, "hopStart": 3,
 "receivedAt": 1700000001.5,
 "text": "M",
 "packet": { "...the original meshtastic packet dict, bytes as {\"__bytes_b64__\": \"…\"}..." }}
```

`packet` is the *whole* original packet, losslessly (`wildcat.meshd.packets.restore`
inverts it), so consumers written against the meshtastic library — the BBS's
`on_receive`, the telemetry `log_*` functions — get exactly what they always got.
The top-level fields are a convenience for new consumers (the brain, a coverage
logger: `rxSnr`/`rxRssi` + the GO node's position is the §2.4 heatmap).

**TX request** (`wildcat/tx`):

```json
{"to": "!9e766b18",           // node num, "!hex" id, or "^all"
 "text": "…",                 // split RAW at [meshd].max_chunk_chars (200) — v1-identical
 "chunks": ["1/2 …", "2/2 …"],// OR pre-split (the brain uses wildcat.meshd.txqueue.chunk_words)
 "channel": 0,                // default [radio].channel_index
 "wantAck": true,             // default true for DMs, false for broadcast
 "priority": 5,               // 0 (urgent) … 9; default 5; FIFO within a priority
 "id": "bbs-…"}               // echoed on wildcat/tx/result
```

meshd drains the queue **one chunk per `[meshd].tx_pacing_seconds`** (2 s, same
as v1's `send_message`), priority first, then FIFO; a request with a bad
destination/channel/oversized chunk is rejected on `tx/result` and never touches
the air; the queue caps at 500 chunks.

## Reconnect behaviour

meshd opens the radio with exponential backoff (`[meshd].reconnect_min_seconds`
→ `…max_seconds`), publishes `status: disconnected {error}` on every failure,
and on `meshtastic.connection.lost` closes the dead interface and reconnects in
the same loop — the consumers see the retained status flip and simply wait.
The MQTT side reconnects via paho's own loop (1→60 s backoff), and
subscriptions are re-applied on every reconnect. systemd is the outer safety
net (`Restart=on-failure`).

## Per-service status

| Service | v2 mechanism | Status |
|---|---|---|
| **meshd** | `wildcat meshd` → `wildcat/meshd/daemon.py` (radio ↔ bus), `txqueue.py`, `packets.py`, `radio.py` | built; 9 daemon tests + queue/packet tests with a fake radio. **Unverified on hardware.** |
| **telemetry** (the proof consumer) | `bbs/telemetry_logger.py` with `[telemetry].source = "bus"`: subscribes `rx/+` through `BusInterface`, feeds the unchanged `log_*` functions | wired; the same code path is exercised by the BusInterface tests |
| **bbs** | `bbs/server.py` with `[bbs].source = "bus"`: `wildcat/busiface.py` presents `nodes` / `myInfo` / `getMyNodeInfo()` / `sendText()` backed by the bus, so the 1300 lines of handlers are untouched | wired; **end-to-end test**: a DM `M` goes fake-radio → meshd → bus → real `message_processing` → bus → meshd → fake-radio `sendText(menu)` and lands in `message_logs` |
| **observatory** | still reads the shared DB (no radio, never needed one) | unchanged; Phase 3 can subscribe to `wildcat/rx/#` for live pushes instead of the 5 s poll |
| **brain** (Phase 2) | subscribe `rx/text`, publish `tx` with `chunks` from `chunk_words(…, numbered=True, max_chunks=…)`; read `nodes` for the admin allowlist and `telemetry` envelopes for `channel_utilization` (the airtime brake) | not started — the bus is exactly the interface it needs |

The adapter approach (`BusInterface`) was chosen over rewriting the BBS's
handlers because it keeps the v1 code path byte-for-byte (menus, sync
messages, logging) and lets `source = "radio"` remain the default until the
bus has proven itself on the Pi. Retire the adapter when/if the BBS gets a
real rewrite; until then it's ~120 lines that turned a three-week refactor into
one config flag.

## Switching a running Pi to the bus

Config-driven, reversible, one restart:

1. `./deploy/install.sh --mosquitto` (installs + enables the broker; the units are already there).
2. In `config/wildcat.toml`: `[mqtt] enabled = true`, `[bbs] source = "bus"`, `[telemetry] source = "bus"`.
   `wildcat config validate` **refuses** any half-way state — `[mqtt].enabled = true`
   with a `"radio"` consumer means two processes on one socket, and a `"bus"`
   consumer without mqtt has nothing to listen to.
3. `sudo systemctl restart wildcat.target`. Order: mosquitto → meshd (connects
   the radio, publishes retained `status` + `nodes`) → bbs/telemetry (wait up to
   120 s for `status: connected`, then subscribe).
4. Watch it: `mosquitto_sub -v -t 'wildcat/#'` on the Pi (or over Tailscale from the Mac).

Rollback: `enabled = false`, both sources back to `"radio"`, restart. meshd
then exits 0 on start ("nothing to do") and the target stays green.

## Not built (deliberately)

- **Bridging to `mqtt.meshtastic.org` / Cincy Mesh** — Phase 6, separate Mosquitto bridge stanza, downlink off.
- **`WatchdogSec`/sd_notify** — would need `sdnotify`; `Restart=on-failure` + exit-on-lost covers the real failure modes seen so far.
- **A TX ACK tracker** — `tx/result` carries the packet id; correlating `ROUTING_APP` acks to it is the brain's Phase 2 job (retry once, then log partial).
