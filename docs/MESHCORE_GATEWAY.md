# MeshCore gateway — integration plan

*Listen on a second radio. Say exactly what was heard, and how. Never speak for anyone.*

*Research + plan, 2026-09-20. This is the research OBSERVATORY_V2 §7 said had to
happen before MeshCore became "any type of plan". It is a plan for a **listener**.
It is still gated on the consent question in §9 — nothing here transmits on OKI
Mesh's network by default.*

> Library facts below were read from source on 2026-09-20 (`meshcore` 2.3.14 on
> PyPI, `meshcore-dev/meshcore_py` @ main; AMMB @ main). MeshCore *on-air*
> semantics I could not confirm from code are marked **[verify on bench]**.
> Never invent packet semantics.

---

## 0. Read this first — where this doc sits

The brief for this work described a repo with `CLAUDE.md / ARCHITECTURE.md /
GAMES.md / HARDWARE.md / ANTICHEAT.md` and a `/gateways · /core · /identity ·
/arena · /observatory` tree, with a `MeshEvent` type. **That tree and those names
are not on disk anywhere** (not in `wildcat-mesh-v2`, `wildcat-games`, any branch,
or GitHub). The multi-protocol `/gateways` structure is **aspirational**. What is
implemented today:

| Brief's name | What actually exists |
|---|---|
| `/gateways/meshtastic` | `wildcat/meshd/` — one process owns the radio, publishes to the bus |
| `MeshEvent` bus | MQTT bus + the **neutral envelope** (`wildcat/rx/<kind>`, OBSERVATORY_V2 §2c, D-019; v2 adds `rx.via_mqtt`, D-027) |
| `/arena` | the referee in `wildcat-games/ARCHITECTURE.md` §7 (designed, not built) |
| `/observatory` | `observatory/` + `wildcat/observatory/` (built) |
| ANTICHEAT trust tiers | proof tiers A–D in `wildcat-games/GAMES.md` §2.2 + D-027 |
| `/gateways/meshcore` | nothing. §7 of OBSERVATORY_V2 calls it `meshcored`; games ARCHITECTURE calls it `cored` |

So this plan specifies the gateway against **the `MeshEvent` shape from the brief**
and gives the exact projection onto **the envelope that is running today** (§5.3).
If `MeshEvent` lands as a real type, §5.1 is the contract; until then the gateway
publishes the envelope and carries the extra MeshEvent fields additively (D-019
allows that). Either way the code is the same ~400 lines.

Working name here: **`meshcored`** (`wildcat/meshcored/` today →
`gateways/meshcore/` if the tree is reorganized).

## 1. The rules this gateway is built around

1. **Bridge at the application layer only.** A MeshCore radio and a Meshtastic
   radio never share firmware, a port, or a process. One process per radio.
2. **No cross-network chat relay. Ever.** `meshcored` has no code path from any
   other protocol's traffic to its transmitter — enforced by construction (§6),
   not by config.
3. **Gateways emit events only.** `meshcored` publishes; it does not rule, score,
   reply, store, or decide. It subscribes to *nothing* on the bus in Phase 1.
4. **Pi 3.** One asyncio process, USB serial, no BLE, no web server, no new
   database (§8).
5. **Append-only.** The gateway's only disk artifact is an optional JSONL capture
   file, append-only, which doubles as the replay fixture format.
6. **Honest provenance.** RF numbers describe the *link our radio actually heard*,
   which on MeshCore is often the last repeater, not the author (§5.2). Say so.

## 2. Ecosystem survey (awesome-meshcore, 2026-09-20)

Source: `github.com/samuk/awesome-meshcore` → *Software Libraries*, *Maps and
Diagnostics*, *Simulation*, *BBS*, *Bots*. What matters for a Python gateway:

| Project | What it is | Health (GitHub/PyPI, 2026-09-20) | Verdict |
|---|---|---|---|
| **`meshcore` / meshcore_py** (`meshcore-dev/meshcore_py`) | Async Python client for the **companion radio protocol** over serial / BLE / TCP. Event-subscription API. | 114★, 54 forks, 8 open issues, **pushed yesterday**; **105 PyPI releases since 2025-03**, five in the last week (2.3.10→2.3.14); MIT; py≥3.10; deps: `bleak`, `pyserial-asyncio-fast`, `pycryptodome`, `pycayennelpp` | **The pick.** |
| `pymc_core` (now `openhop-dev/openhop_core`) | Pure-Python MeshCore **node stack** driving an SX126x over SPI — the Pi *is* the radio (repeater daemon `pyMC_Repeater` is built on it) | 95★, 18 open issues; last PyPI release 1.0.12 on 2026-05-22; mid-rename | Wrong layer for us: it *implements* MeshCore rather than *listening through* a stock node. Interesting later (§10) because the Den's Pi already has an SX1262 HAT — but that HAT belongs to `meshtasticd`. |
| `meshcore-cli` (`meshcore-dev/meshcore-cli`) | CLI on top of meshcore_py | 199★, pushed yesterday | Bench tool. Use it for `wildcat doctor`-style manual checks and for capturing fixtures. Not a library. |
| `meshcore-ha` (`meshcore-dev/meshcore-ha`) | Home Assistant integration, on meshcore_py | 260★, active | Proof the lib survives a long-running daemon on Pi-class hardware. Good reference for repeater status/telemetry polling cadence. |
| RemoteTerm (`jkingsman/Remote-Terminal-for-MeshCore`) | Self-hosted base-station companion: MQTT, community observation, HA, bots | 316★; last push 2026-07 | Closest cousin to the Den. Reference for observer-style packet capture. Too big to embed. |
| `meshcore_c`, MeshCoreKmp, `meshcore_client` (Dart), meshcore.js | Same protocol, other languages | — | Relevant to the *app* (games ARCHITECTURE §10 shape 1), not this gateway. |
| `meshcore-decoder` (TS), Wireshark dissector, CoreScope | On-air packet decoders | — | Cross-check our §5 parsing against these when writing fixtures. |
| **Akita AMMB** (`AkitaEngineering/Akita-Meshtastic-Meshcore-Bridge`) | Bidirectional Meshtastic⇄MeshCore/MQTT **message relay** | 106★, pushed 2026-09-13, GPL-3.0 | **Reference only** — see §7. Its purpose is the thing our rules forbid. |

### Why `meshcore` (meshcore_py)

- **It is the project's own client.** Lives in the `meshcore-dev` org beside the
  firmware; `meshcore-cli` and `meshcore-ha` are built on it, so protocol changes
  land here first (the v3 message frames with SNR, path-hash modes, flood scopes
  are all already parsed).
- **Release cadence is the best in the ecosystem** — 105 releases in 18 months.
  That cuts both ways: **pin it exactly** (`meshcore==2.3.14`), like
  `meshtastic==2.7.4` is pinned, and bump on purpose with fixtures re-run.
- **It exposes the one thing a gateway needs and nothing else does cleanly:**
  `EventType.RX_LOG_DATA` — every packet the radio demodulates, with SNR, RSSI,
  the raw on-air bytes, and a parsed header (route type, payload type, path).
  That is the physics. Text events alone would not give us RSSI or a path.
- **Even AMMB depends on it** (`meshcore>=2.2.0` in its requirements) for its
  async path — its hand-rolled decoder is the legacy path.
- Async-native, serial-first, auto-reconnect built in, MIT (compatible with the
  Den's GPL-3.0-only).

Costs, honestly: py≥3.10 (the Den declares ≥3.9 — Pi OS Bookworm ships 3.11, so
fine on the Pi; make `meshcore` an **optional extra**, `.[meshcore]`, so the rest
of the Den still installs on 3.9); `bleak` comes along as a hard dependency even
though we never open BLE; event payload keys are inconsistent (`SNR`/`RSSI`
upper-case on message events, `snr`/`rssi` lower-case on RX log, `pubkey_prefix`
vs `pubkey_pre`) — normalize in exactly one function and cover it with fixtures.

## 3. Hardware and placement

- **A second radio** flashed with MeshCore **companion (USB)** firmware on its own
  port — the spare Heltec V3 is fine. Not the Station G2 (that is `meshd`'s), not
  the Waveshare HAT (that is `meshtasticd`'s).
- **USB serial, 115200.** Not BLE: the Pi 3's Bluetooth shares a UART and an
  antenna with Wi-Fi, BlueZ/dbus costs RAM, and a node serves one BLE client — keep
  it free for a phone. TCP (`create_tcp`) is the fallback for a Wi-Fi companion
  mounted somewhere better than the Pi.
- Stable device path via udev (`/dev/serial/by-id/…`), never `ttyUSB0` — two
  CP210x/CH34x boards on one Pi *will* swap.
- Radio settings are **OKI Mesh's**, read from them, not guessed. `SELF_INFO`
  reports `radio_freq / radio_bw / radio_sf / radio_cr`; the gateway logs them at
  connect and `wildcat doctor` shows them.
- **Companion, not repeater, firmware.** Repeater firmware has a text CLI, not the
  companion protocol; and a Den that *repeats* on someone else's mesh is a
  different conversation. A companion does not repeat **[verify: `DEVICE_INFO.repeat`
  is false after flashing; alarm in doctor if true]**.

## 4. What the gateway subscribes to

```python
mc = await MeshCore.create_serial(port, 115200, auto_reconnect=True,
                                  max_reconnect_attempts=<large>)   # [verify semantics of the cap]
await mc.start_auto_message_fetching()      # drains queued DMs/channel msgs on MESSAGES_WAITING
mc.subscribe(EventType.RX_LOG_DATA, on_rx_log)   # …and the rest of the table below
```

| Signal | meshcore_py event | Why we want it | Costs airtime? |
|---|---|---|---|
| **Every packet heard on RF** | `RX_LOG_DATA` → `snr`, `rssi`, `raw_hex`, `route_typename` (`FLOOD`/`DIRECT`/`TC_*`), `payload_typename` (`ADVERT`, `GRP_TXT`, `TXT_MSG`, `ACK`, `PATH`, `TRACE`, `REQ`, `RESPONSE`, …), `path_len`, `path_hash_size`, `path`, `pkt_hash` | **The primary feed.** Coverage points, link inference, airtime picture, and the only source of RSSI. Repeater traffic is visible here whether or not it's addressed to us. | No — passive |
| **Adverts (repeaters, room servers, clients, sensors)** | `RX_LOG_DATA` with `payload_type == ADVERT` → `adv_key` (32-byte pubkey), `adv_type` (`CLI`/`REP`/`ROOM`/`SENS`), `adv_name`, `adv_lat`/`adv_lon`, `adv_timestamp`, `signature`; plus `NEW_CONTACT` / `ADVERTISEMENT` for the firmware's own view | The roster. **This is how repeaters and room servers become nodes on the map.** Adverts are Ed25519-signed — verify the signature in the gateway (pycryptodome is already installed) and stamp `identity_verified` **[verify signed-message layout against firmware]** | No |
| **Channel (group) text** | `CHANNEL_MSG_RECV` → `channel_idx`, `text`, `sender_timestamp`, `path_len`, `SNR`; the lib joins it to the RX log by message hash and adds `RSSI`, `path`, `recv_time` when `decrypt_channels` is on and the channel key is known | Public-channel feed; slow open-mesh game traffic | No |
| **Direct messages to the Den's MeshCore identity** | `CONTACT_MSG_RECV` → `pubkey_prefix` (6 bytes), `text`, `txt_type`, `sender_timestamp`, `path_len`, `SNR`, optional `signature` | **`WG` game moves.** A DM is decrypted with an ECDH secret, so the sender is *authenticated* — channel text is not (§5.2) | The firmware ACKs DMs on its own; that is the protocol, not us |
| **Path learning** | `PATH_UPDATE`, `ADVERT_PATH` | Link topology without asking anyone | No |
| **Repeater health** *(opt-in)* | `req_status_sync(contact)` → `STATUS_RESPONSE`: `bat`, `noise_floor`, `last_rssi`, `last_snr`, `airtime`, `rx_airtime`, `uptime`, `nb_recv/nb_sent`, flood/direct counters, dups | Uptime + noise-floor data for the Observatory and for Sensor Steward / Repeater Rally | **Yes** — a request and a response across the mesh. Off by default; budgeted; needs the repeater owner's OK (many need a guest login) |
| **Repeater neighbours** *(opt-in)* | `req_neighbours_sync` / `fetch_all_neighbours` → `[{pubkey, secs_ago, snr}]` | The MeshCore analogue of NeighborInfo → `kind: neighbors` | **Yes** — same rules |
| **Sensor telemetry** *(opt-in)* | `req_telemetry_sync` → Cayenne-LPP list | Sensor Steward | **Yes** |
| **Room server posts** *(opt-in)* | `send_login_sync(room, guest_pwd)` then posts arrive as `CONTACT_MSG_RECV` from the room's key with `txt_type` = signed and a 4-byte author prefix in `signature` | Room servers are MeshCore's BBS; bulletins are useful signal | **Yes** — login + sync traffic. **Off until the room's operator agrees.** Note the RF metrics on a room post describe *room → us*, never *author → us* |
| **Traces** *(bench only)* | `send_trace` → `TRACE_DATA` with per-hop SNR | Range-card surveys, never in steady state | **Yes** |
| Link state | `CONNECTED` / `DISCONNECTED`, `SELF_INFO`, `DEVICE_INFO` | `wildcat/meshcored/status` (retained) + last-will, exactly like `meshd/status` | No |

Steady state is the first five rows: **purely passive.** Everything that
transmits is a named, default-off option with its own airtime budget.

## 5. Normalization — MeshCore → `MeshEvent`

### 5.1 The shape

```jsonc
{
  "v": 2,
  "protocol": "meshcore",
  "source_node": ["meshcore", "a1b2c3…64 hex"],     // (protocol, native_id). native_id = full pubkey when known,
                                                    // else the 12-hex prefix; null for channel text (see 5.2)
  "source_id_kind": "pubkey" | "prefix6" | "claimed_name" | null,
  "identity_verified": true,                        // advert signature checked, or DM decrypted with that key
  "gateway_node": ["meshcore", "<our SELF_INFO.public_key>"],
  "gateway": "den:home",                            // = games ARCHITECTURE `heard_by`
  "kind": "text" | "nodeinfo" | "position" | "telemetry" | "neighbors" | "routing" | "encrypted" | "other",
  "packet_id": "9F2A11C4",                          // pkt_hash — for dedupe across multiple receptions
  "received_at": 1790000000.4,                      // gateway clock, when the frame left the serial port
  "sender_time": 1790000000,                        // what the sender claims; never used for ordering
  "rf": {
    "rssi": -97, "snr": 6.25,                       // OUR radio's reception of the LAST HOP
    "hop_count": 2,                                 // FLOOD: path_len. DIRECT: null (unknowable — 5.2)
    "path": ["a3", "7f"],                           // repeater hashes, in order travelled; last entry is who we heard
    "path_hash_size": 1,
    "route": "FLOOD",
    "last_hop": "7f"                                // null when hop_count == 0 (we heard the author)
  },
  "trust": "direct_rf",                             // 5.2
  "raw": { "hex": "<lossless on-air bytes from RX_LOG_DATA.raw_hex>",
           "lib": { …the meshcore_py payload, JSON-safe… } },
  // exactly one payload block, by kind — same blocks as the running envelope:
  "text": "WG ZAP T3 9F2A", "channel": "#nky-info",
  "nodeinfo": { "id": "…", "long_name": "Tower Hill RPT", "role": "REP" },
  "position": { "lat": 38.88, "lon": -84.62 }
}
```

### 5.2 Concept map

| MeshCore concept | MeshEvent field | Notes — the parts that bite |
|---|---|---|
| 32-byte Ed25519 public key | `source_node = ("meshcore", hex)` | The only real identity. Meshtastic ids are `!xxxxxxxx`; these are 64 hex — they can never collide. Games use `mc:<prefix>` for display only. |
| 6-byte `pubkey_prefix` on DMs | `source_node` via `get_contact_by_key_prefix()`; else the prefix with `source_id_kind: "prefix6"` | Resolve against the contact table the lib maintains. Never widen a prefix into a key we haven't seen in a verified advert. |
| 1–3 byte **path hashes** | `rf.path[]` only | These are *not* ids — a 1-byte hash collides constantly. Map to a repeater only when exactly one verified `REP` advert matches, and mark the link `inferred`. |
| Channel (group) text | `kind: text`, `channel: <name>`, **`source_node: null`**, `source_id_kind: "claimed_name"` | A MeshCore channel message carries **no sender key** — just `"Name: text"` by convention, encrypted with a shared secret. Anyone with the key can type any name. So: channel text can *never* be attributed for scoring. `WG` moves must be **DMs to the Den's MeshCore identity.** |
| Advert | `kind: nodeinfo` (+ a `position` event when `adv_lat/lon` present) | `adv_type` → `role` (`CLI/REP/ROOM/SENS`). Position is *claimed by the advertiser* — fine for a map, never a C-tier proof by itself. |
| `path_len` on a **FLOOD** packet | `rf.hop_count` | Each repeater appends its hash, so `path_len == 0` ⇒ **we heard the author's own transmitter.** This is MeshCore's "zero hops". |
| `path_len` on a **DIRECT** packet | `rf.hop_count: null` | Direct routing *consumes* the path as it goes — it arrives short regardless of distance **[verify on bench]**. Unknown is `null`, never `0`. D-027's rule: never guessed. |
| SNR / RSSI | `rf.snr`, `rf.rssi` | **Belong to the last hop → us.** Only when `hop_count == 0` do they describe the author. The Observatory must credit a coverage point to `last_hop`'s location when there is one, to the author's otherwise. |
| RSSI on DMs | join | The DM event carries SNR only. Correlate to the `RX_LOG_DATA` `TXT_MSG` frame in the preceding ~2 s whose src-hash byte equals the first byte of `pubkey_prefix` **[verify TXT_MSG header layout]**. No match → `rssi: null`. |
| `pkt_hash` | `packet_id` | A flood is often heard several times via different repeaters. **Emit every reception** (each is coverage evidence) with the same `packet_id`; consumers dedupe. Bounded LRU in the gateway only to suppress serial-level duplicates. |
| Status / neighbours / telemetry *responses* | `kind: telemetry` / `neighbors`, `trust: "reported"` | The numbers were measured by a **remote** node and told to us. `rf` still describes how *the response packet* reached us. |
| Room server post | `kind: text`, `source_node` = author prefix (4-byte, `prefix4`), `via_node` = room key, `trust: "reported"` | Store-and-forward by design; hours old is normal. |
| Undecryptable payloads (others' DMs, unknown channels) | `kind: encrypted`, header + `rf` only | We log that a packet existed and how well we heard it. We do not try. |

**`trust` values this gateway can emit:**

| `trust` | Meaning | Can it support an A-tier (rf-witnessed) proof? |
|---|---|---|
| `direct_rf` | Our radio demodulated this packet (it has an `RX_LOG_DATA` reception with SNR/RSSI) | **Only if also** `rf.route ∈ FLOOD*`, `rf.hop_count == 0`, and `identity_verified`. The arena's proof builder owns that rule (games ARCHITECTURE §7: *tier rules are per-protocol, behind one interface*); the gateway just refuses to blur the inputs. |
| `reported` | A remote node told us (status, neighbours, telemetry, room posts) | No. B-tier at best, and only with a signature. |
| *(never)* `internet` | MeshCore's analogue of `via_mqtt` is observer/LetsMesh MQTT feeds. **`meshcored` does not ingest them.** If that is ever wanted it is a *different* gateway with `trust: internet`, so the two can't be confused. | Never. |

*(These three names are mine — align them with ANTICHEAT.md's when that doc exists.)*

### 5.3 Projection onto the envelope that runs today

| MeshEvent | Envelope (`wildcat/rx/<kind>`) |
|---|---|
| `protocol` | `proto: "meshcore"` |
| `source_node[1]` | `from` (string; `null` for channel text) — `to` = our key for DMs, `null` otherwise; `broadcast` = not a DM |
| `channel` | `channel` = the channel **name** (OBSERVATORY_V2 §2c already says so) |
| `rf.rssi / snr / hop_count` | `rx.rssi / rx.snr / rx.hops` |
| — | `rx.via_mqtt: false` always (this gateway only has a radio) |
| `rf.path`, `route`, `last_hop`, `path_hash_size` | `rx.path`, `rx.route`, `rx.last_hop`, `rx.path_hash_size` — additive (D-019) |
| `gateway` / `gateway_node` | `heard_by` / `heard_by_node` — the field games ARCHITECTURE §6 already asked for |
| `trust`, `identity_verified`, `source_id_kind`, `packet_id` | same names, additive |
| `raw` | `packet` (the "native packet, lossless" slot) |
| roster | contribution to retained `wildcat/nodes`, merged by id, `proto: "meshcore"`, `role` from `adv_type` |

Nothing in the Observatory, BBS or brain has to change to *tolerate* these
events; they key on `proto` + string ids already. One UI rule is new and is not
optional: **a point with `rx.last_hop` set is a link to that repeater, not to the
author** — without it the coverage map lies on MeshCore.

## 6. Shape of the code

```
wildcat/meshcored/            (→ gateways/meshcore/)
  __init__.py
  daemon.py      MeshcoreDaemon: connect, subscribe, status/last-will, reconnect, pump
  events.py      normalize(lib_event, ctx) -> (kind, envelope)   ← the ONLY place lib key names appear
  rxjoin.py      small time-windowed join: RX_LOG_DATA ⇄ DM/channel events (RSSI, path)
  roster.py      verified adverts -> roster entries; prefix/hash resolution
  polls.py       opt-in, budgeted status/neighbour/telemetry/room polling (Phase 3; default off)
  capture.py     append-only JSONL of raw lib events (fixtures + replay)
```

- Mirrors `wildcat/meshd/`: injected `open_radio` and `bus` so `MemoryBus` +
  a fake `MeshCore` drive the tests (`test_meshd_daemon.py` is the template).
  `wildcat meshcored --dry-run` replays a capture file through `normalize()`.
- **asyncio inside, the Den's `Bus` outside.** `MqttBus.publish` is thread-safe
  paho; call it straight from the event callbacks. One loop, no threads of ours.
- Config is one table, absent = service not installed:

  ```toml
  [meshcore]
  port = "/dev/serial/by-id/usb-…"     # or tcp = "host:4000"
  gateway = "den:home"
  decrypt_channels = ["Public"]        # names only; keys come from the radio's own channel table
  capture = "logs/meshcored.jsonl"     # optional, append-only, size-rotated
  polls = false                        # Phase 3; anything true here transmits
  ```
- `deploy/systemd/wildcat-meshcored.service`, part of `wildcat.target`,
  `MemoryMax=96M`, `Restart=always`, same hardening as `wildcat-meshd.service`.
- `wildcat doctor` grows a MeshCore section: port present and distinct from
  meshd's, firmware `ver`, `repeat == false`, radio params, contacts count, last
  RX age.

**No-relay, by construction (the test, not the promise):**

1. `meshcored` makes **zero** bus subscriptions in Phases 1–2. A test asserts
   `bus._subs == []` after `start()` — the twin of `test_router_invariants.py`.
2. It does **not** consume `wildcat/tx`. There is no `send_msg` /
   `send_chan_msg` call anywhere in the package; a test greps the package for
   them and fails the build if one appears outside `polls.py`'s login/status calls.
3. `meshd` and `meshcored` share no imports except `wildcat.bus`, `wildcat.config`.
4. If the referee ever needs to *answer* a MeshCore player (`WG OK …`), that is a
   **new, separately-decided component** with its own TX queue, airtime brake and
   OKI Mesh's consent — and it would send only the referee's own rulings to the
   player who asked. It is not this gateway and it is not a relay. **Ask Seth
   before building it.**

## 7. Akita AMMB — what to borrow, what to leave

AMMB is competent code for a purpose we have ruled out. Read it as a field guide
to the companion serial link, not as a base.

| | From AMMB | Do what |
|---|---|---|
| **Borrow** | Companion **USB framing** (`protocol.py::MeshcoreCompanionProtocol`): radio→app `0x3E` + len(LE16) + payload, app→radio `0x3C` + …; resync by hunting the start byte; treat length > 1024 as a false sync and drop one byte | Knowledge for `wildcat doctor`'s "is this really a companion radio?" probe and for reading raw serial captures. **Not** a runtime parser — meshcore_py owns that. |
| **Borrow** | **On-air packet parse** (`observer_mqtt.py::parse_raw_packet`): header → route (bits 0–1), payload type (bits 2–5), version (6–7); 4-byte transport code on `TC_*` routes; path byte = hop count (low 6) + hash size (high 2, +1); payload ≤ 184 | An independent second reading of the same bytes meshcore_py parses. Use it to write **cross-check fixtures** for `events.py`. |
| **Borrow** | **Packet hash** = SHA-256(payload_type ‖ payload)[:8] (the observer/LetsMesh convention) | Emit it as `packet_id_observer` beside meshcore_py's 4-byte `pkt_hash` so our captures can be compared with community observer tools. |
| **Borrow** | Channel key facts: Public key `8b3387e9…cd72`; `#name` rooms derive key = SHA-256("#name")[:16]; AES-ECB + 2-byte truncated HMAC | Only to *label* which channel a `GRP_TXT` belongs to. Decryption itself stays in meshcore_py (`decrypt_channels`). |
| **Borrow** | Bounded `SeenHashCache` (OrderedDict LRU, 512) | Serial-level duplicate suppression in `rxjoin.py`. |
| **Borrow** | "Queue subscriptions until connected" + wrap every handler so one bad event can't kill the loop (`meshcore_async_handler.py`) | Same pattern in `daemon.py`; the Den's bus already does the second half. |
| **Borrow** | `preflight.py` + `tests/test_integration_no_hardware.py` discipline | Already our habit (`doctor`, `MemoryBus`). Keep it. |
| **Avoid** | **The relay itself** — `_encode_companion_from_meshtastic` (prepends `"sender: "` and re-sends Meshtastic text as a MeshCore channel message), `encode_group_text_packet`, `meshtastic_item_to_observer_packet`, the bidirectional queues in `bridge.py` | **This is the forbidden behaviour**: it doubles airtime, impersonates speakers on a network they never joined, and breaks both communities' norms. Nothing like it exists in `meshcored`, and §6's tests keep it that way. |
| **Avoid** | The ~600-line hand-rolled companion **decoder** (`protocol.py::decode`, contact/self-info byte layouts) | Duplicates meshcore_py and will drift with every firmware bump. One parser. |
| **Avoid** | Runtime weight: FastAPI + uvicorn + Textual TUI + `cryptography` + two MQTT clients + threads *and* asyncio variants | Not on a Pi 3 beside meshd, the BBS, the Observatory and Mosquitto. |
| **Avoid** | External-MQTT bridging and the REST control API | The Den's rule: nothing reachable from outside; and an API that can inject messages is a relay with extra steps. |
| **Avoid** | Flattening identity to a display name | We keep the key, the prefix, *and* how sure we are. |

Licence note: AMMB is GPL-3.0; the Den is GPL-3.0-only — compatible if a snippet
were ever copied, with attribution. The plan is to copy **knowledge, not code**.

## 8. Pi 3 budget

| Concern | Plan |
|---|---|
| RAM (1 GB, shared with Mosquitto, meshd, BBS, Observatory, meshtasticd, maybe rnsd) | One Python process, target < 40 MB RSS, `MemoryMax=96M`. No web server, no TUI, no second MQTT client. |
| CPU | MeshCore traffic is a few packets a minute; one Ed25519 verify per advert and one AES block or two per channel message are noise on an A53. |
| **Database** | **None added.** The gateway persists nothing. The Observatory already persists what it shows, from the bus. The JSONL capture is a log file, not a store. *If* MeshCore contacts/paths ever need their own tables, that is an **ask-Seth-first** change. |
| SD wear | Capture off by default in production; when on, size-rotated, append-only, never rewritten. |
| USB | Two serial radios + optional RNode on a Pi 3's single USB 2 bus is fine at 115200; give the hub real power. by-id paths only. |
| Install | `pip install -e '.[den,meshcore]'`; piwheels has `pycryptodome` and `bleak`'s deps for armv7/aarch64 **[verify on the actual Pi 3 image]**. |
| Failure | Radio unplugged → `meshcored/status: disconnected` (retained) via last-will; reconnect with backoff; never takes meshd down with it (separate unit, separate port). |

## 9. Consent gate (unchanged, restated)

OBSERVATORY_V2 §7 and games ARCHITECTURE §10 both say it: before the Den listens
on, or transmits to, OKI Mesh's network — **ask.** A passive companion on the
public channel is what every MeshCore phone already is, so Phase 1 is the smallest
possible ask; say what is logged (headers, signal, public-channel text, adverts),
what is not (no DMs but our own; nothing undecryptable is attacked), and that
nothing is relayed anywhere. Polls, room logins and any future reply path are each
their own ask.

## 10. Phases

| Phase | What | Transmits? | Effort |
|---|---|---|---|
| **0 · Bench** | Flash the spare Heltec (companion USB). `meshcore-cli` on the Mac: confirm `SELF_INFO`, watch `RX_LOG_DATA`, capture 24 h to JSONL. Settle every **[verify]** above; write `docs/MESHCORE_DEVICE_LOG.md`. | adverts only (set to manual) | **S** |
| **1 · Listener** | `meshcored`: RX log + adverts + channel text → envelopes; roster merge; status topic; doctor; fixtures from Phase 0; invariant tests. Observatory shows ● MeshCore nodes and last-hop links. | No | **M** |
| **2 · Game intake** | DMs to the Den's MeshCore key → `kind: text` with authenticated `source_node`; RX-join for RSSI; `WG LINK` works over MeshCore. Proof-builder rule for MeshCore written *in the arena*, fed by `trust` + `rf`. | ACKs only (firmware) | **S–M** |
| **3 · Opt-in polls** | Status / neighbours / telemetry for repeaters whose owners said yes; room server read-only login where welcomed. Budgeted, `trust: reported`. | Yes — budgeted | **M** |
| **Later, separately decided** | Referee reply path on MeshCore; a `pymc_core` SPI listener (no second MCU) if the HAT ever frees up; an observer-MQTT gateway with `trust: internet`. | — | — |

## 11. Open questions for Seth

1. **`MeshEvent` vs the envelope** — is the five-doc `/gateways` design (from the
   phone conversation) meant to replace `wildcat/rx/*`, or is `MeshEvent` just the
   envelope's next version? This plan works either way; §5.3 is the hinge.
2. Name: `meshcored` (Den docs) or `cored` (games docs)? Pick one.
3. Does the Den get its **own MeshCore identity** that players DM, and what is it
   called on OKI Mesh's network? (It will appear in everyone's contact list.)
4. Who at OKI Mesh do we ask, and do we bring them this doc?
5. Where do the docs live — do `wildcat-games`' docs and the Den's merge into the
   one repo the brief describes?

---

**Sources.** `github.com/samuk/awesome-meshcore` · `github.com/meshcore-dev/meshcore_py`
(read: `events.py`, `reader.py`, `meshcore_parser.py`, `parsing.py`, `commands/*`) ·
`pypi.org/project/meshcore` · `github.com/AkitaEngineering/Akita-Meshtastic-Meshcore-Bridge`
(read: `protocol.py`, `observer_mqtt.py`, `meshcore_handler.py`,
`meshcore_async_handler.py`, `requirements.txt`) · `github.com/openhop-dev/openhop_core` ·
`github.com/meshcore-dev/meshcore-cli` · `github.com/meshcore-dev/meshcore-ha` ·
`github.com/jkingsman/Remote-Terminal-for-MeshCore`.
