# Decision log — Wildcat Mesh v2 ("The Den")

Append-only. One entry per decision that someone might later ask "why?" about.
Format: **D-NNN · date · title** → context / decision / consequences.
The design this implements is `WILDCAT_MESH_V2.md` (the v2 plan, 2026-09-13).

---

## D-001 · 2026-09-13 · One config file, TOML, validated with plain dataclasses

**Context.** v1 had four sources of truth: `bbs/config.ini` (read three separate
times by three modules, each with its own defaults or none), `observatory/config.py`
(hardcoded IP, port, secret, `DEBUG = True`), a hardcoded absolute INI path in
`telemetry_logger.py` that pointed at a directory that no longer exists
(`/home/seth/Wildcat-TC2-BBS`), and `/home/user/...` paths in the Observatory's
admin editor (wrong user). Two production crashes came straight from this:
`configparser.read('config.ini')` silently reading nothing unless the CWD was
`bbs/`, and `config['menu']` raising a bare `KeyError`.

**Decision.** `wildcat/config.py` is the only config reader. Format is TOML
(`config/wildcat.toml`), schema is one `@dataclass` per table, validation is a
hand-written pass that collects *every* problem and reports them together with
the table + key name. No pydantic: it would add a compiled dependency to a Pi
install, and the schema is ~10 tables of scalars. `tomllib` is stdlib on 3.11+;
`tomli` is pulled in only on 3.9/3.10 (the Pi's Bullseye Python).

**Consequences.** Every service must call `wildcat.config.load()` at startup
(or `get_config()` lazily). Adding a key = add a dataclass field with a default
+ one `_take_*` line + a line in `wildcat.example.toml`. The old INI keeps
working (D-003) so this is not a flag day.

## D-002 · 2026-09-13 · Paths resolve from the package location, never the CWD

**Context.** The "only runs from inside bbs/" bug.

**Decision.** `wildcat/paths.py` derives the repo root from `wildcat/__file__`.
Config search order: `--config` → `$WILDCAT_CONFIG` → `<repo>/config/wildcat.toml`
→ `/etc/wildcat/wildcat.toml` → legacy `<repo>/bbs/config.ini`. An explicit
`--config`/env path that doesn't exist is an error (never a silent fall-through).
Relative paths *inside* the config resolve against the repo root (or, for a
non-checkout install, the directory above the config file). BBS content files
(`messages.json`, `fortunes.txt`, `trivia.txt`) are opened via
`cfg.bbs.content_dir`, the DB via `cfg.database.path`.

**Consequences.** `WorkingDirectory=` in systemd units becomes belt-and-braces,
not load-bearing. `db_admin.py` and the JS8Call `js8call.db` still use bare
relative names — they are standalone/optional tools and are noted, not fixed,
in Phase 1.

## D-003 · 2026-09-13 · Legacy INI is auto-migrated in memory; `wildcat config migrate --write` retires it

**Context.** Seth's Pi has a working `bbs/config.ini`. Phase 1 must not break
the running BBS on the first `git pull`.

**Decision.** If no `wildcat.toml` exists but `bbs/config.ini` does, the loader
maps the INI through `wildcat/legacy_ini.py` into the *same* validator and logs
a one-line warning telling you to migrate. `wildcat config migrate --write`
produces `config/wildcat.toml` from it and verifies the round-trip. Once the
TOML exists it wins; the INI is ignored (delete it whenever).

**Consequences.** Two on-disk formats, one schema, one validator — the INI path
is ~80 lines and can be deleted after Phase 1 is deployed.

## D-004 · 2026-09-13 · Nothing reads config at import time

**Context.** `command_handlers.py` read the INI and `messages.json` at module
import, so config was consumed before `server.py` had even parsed `--config`,
and a bad file exploded inside an `import` line.

**Decision.** Config and content are read lazily (`get_config()`, a lazy
`MESSAGES` proxy, `_menu("main")`) or explicitly in `main()`. Entry points
catch `ConfigError`, print it, and exit 2.

**Consequences.** `python -c "import command_handlers"` is now side-effect-free,
which is also what makes the BBS unit-testable without a radio.

## D-005 · 2026-09-13 · Menu defaults are Seth's menu, not upstream TC²'s

**Context.** Upstream `example_config.ini` lists `Q, B, U, X`; every Wildcat
install (setup.sh, the Observatory editor's defaults) uses
`W, N, R, Q, G, B, U, X` because Weather/Network/Resources/Games are the point
of this fork.

**Decision.** `DEFAULT_MAIN_MENU = W N R Q G B U X`, bbs `M B C J X`, utilities
`S F W X`. A menu-less config gets those. Letters are normalized (case, spaces);
unknown letters warn (the menu builder ignores them) rather than fail.

## D-006 · 2026-09-13 · `[observatory].debug` defaults to false

**Context.** `observatory/config.py` shipped `DEBUG = True`, which turns on the
Werkzeug debugger — arbitrary code execution for anyone on the LAN (or the
ngrok share link in `share-observatory.sh`).

**Decision.** Default false; setting it true is allowed but logs a loud warning
at boot. Same rule Repot follows on the Mac mini.

## D-007 · 2026-09-13 · Config keys for Phase 2/5 (`[brain]`, `[mqtt]`) are defined now

**Context.** The design doc shows the full `wildcat.toml`; adding tables later
would mean a second migration for users.

**Decision.** `[mqtt]` and `[brain]` are parsed and validated from day one with
`enabled = false` defaults and are simply unused until their phase lands. The
doc's example error (`[brain].max_chunks must be 1–5`) is a real test.

## D-008 · 2026-09-13 · The Observatory's web config editor goes read-only for TOML

**Context.** `/admin/bbs-config` rewrote `config.ini` with `configparser` (at a
hardcoded wrong path). Pointing an INI writer at a TOML file would corrupt it.

**Decision.** The page now shows the *active* config (whichever file the
loader picked) and stays editable only while that file is the legacy INI. With
`wildcat.toml` active it renders read-only with instructions to edit the file
on the Pi and restart; the save route refuses with a clear message. A proper
TOML editor is Observatory-v2 work (Phase 3), not Phase 1.

## D-009 · 2026-09-13 · Keep `shared/bulletins.db` as the DB name

**Context.** The design doc says `shared/wildcat.db`.

**Decision.** Renaming the live database is a data migration with zero
functional benefit in Phase 1; `[database].path` is configurable, so whoever
wants `wildcat.db` sets it. Revisit when the schema module (`wildcat/db.py`)
lands with real migrations.

## D-010 · 2026-09-13 · `wildcat doctor` probes are injectable; the LAN scan is bounded

**Decision.** Every check takes its probe (`probe_tcp`, `find_serial_ports`,
`systemctl_show`, `import_version`) as a parameter with the real one as the
default, so the report logic is unit-tested with no hardware and the Mac's
suite never sweeps anyone's LAN. The stale-IP finder sweeps only the node's
own private /24 (254 × 0.25 s in 64 threads ≈ 2 s), never loopback/public
space. The doctor cannot detect "another process already holds the node's
socket" (a TCP connect still succeeds) — the systemd check covers that case.

## D-011 · 2026-09-13 · systemd: `on-failure` + never-give-up + exit-on-connection-lost, not `RuntimeMaxSec`

**Context.** The v1 unit killed the BBS every 5 minutes (`RuntimeMaxSec=300`)
because a dropped node connection left the process idling on a dead socket,
and the default `StartLimitBurst` would have given up after five fast failures
— e.g. during a power cut where the node boots slower than the Pi.

**Decision.** `Restart=on-failure`, `RestartSec=10`, `StartLimitIntervalSec=0`;
`RestartPreventExitStatus=2` so a config error (exit 2) stays down and visible
instead of restart-looping; `ExecStartPre=wildcat config validate` so the
reason lands in `journalctl`; `server.py`/`telemetry_logger.py` subscribe to
`meshtastic.connection.lost` and exit 3 so the restart actually happens.
`KillSignal=SIGINT` because the v1 code closes the radio on KeyboardInterrupt.
A `wildcat.target` with `PartOf=` gives one handle for the lot. `WatchdogSec`
was skipped (needs sd_notify plumbing; not the failure mode we've seen).

## D-012 · 2026-09-13 · Bus = Mosquitto + JSON envelopes + retained snapshots

**Decision.** Topics `wildcat/rx/<kind>`, `wildcat/tx`, `wildcat/tx/result`,
retained `wildcat/nodes` and `wildcat/meshd/status` (+ a `dead` last-will).
Payloads are JSON with the ORIGINAL meshtastic packet embedded losslessly
(`bytes` → `{"__bytes_b64__"}`), so legacy consumers get the exact dict they
were written for, and new consumers get flat convenience fields. Retained
snapshots mean a consumer that starts late (or restarts) is immediately
correct without asking meshd anything. QoS 1 everywhere; no downlink from
anywhere but `wildcat/tx` (bridging is Phase 6, off by default).

## D-013 · 2026-09-13 · The BBS moves onto the bus via an interface-shaped adapter, not a rewrite

**Context.** The BBS is ~1300 lines of handlers written against the meshtastic
interface object (`nodes`, `myInfo`, `sendText`, `getMyNodeInfo`, plus
attributes it hangs on the object).

**Decision.** `wildcat/busiface.py` implements exactly that surface over the
bus. `bbs/server.py` picks it when `[bbs].source = "bus"`. Result: zero handler
changes, one config flag to switch (and to roll back), and an end-to-end test
that drives a real DM through the real handlers with a fake radio. The v1
2 s/chunk pacing in `utils.send_message` stays (meshd paces again; harmless).

## D-014 · 2026-09-13 · Telemetry is the proof consumer; `source = "radio"` stays the default

**Decision.** The logger is the simplest consumer and the one that actually
fought the BBS for the socket in v1, so it is the first thing that must work
over the bus on the Pi. Both consumers default to `"radio"` (v1 behavior, zero
risk on `git pull`); the bus is opt-in until it has run on real hardware.

## D-015 · 2026-09-13 · `[mqtt].enabled` and `source` must agree — enforced by the validator

**Context.** `enabled = true` starts meshd, which takes the node's single API
slot; a consumer still on `"radio"` would then fight it — the exact v1 disease.

**Decision.** `wildcat config validate` errors on any half-state: mqtt on with
a `"radio"` consumer, or a `"bus"` consumer with mqtt off. meshd itself exits 0
when mqtt is off so the unit is inert in v1 mode.

## D-016 · 2026-09-13 · TX chunking default is v1's raw 200-char slice

**Decision.** `wildcat/tx` requests with `text` are sliced raw at
`[meshd].max_chunk_chars` (200), byte-for-byte what `utils.send_message` did,
so menus render identically over the bus. Word-boundary, numbered chunking
(`chunk_words`) exists for the brain, which sends pre-split `chunks`. Both are
tested; neither is a behavior change for the BBS.

## D-017 · 2026-09-13 · Backups via `wildcat db backup` (sqlite backup API), not `sqlite3 .backup`

**Decision.** Same effect, no dependency on the `sqlite3` CLI being installed
on the Pi, and the copy is `quick_check`ed before old ones are pruned. Nightly
02:30 via a `Persistent=true` timer so a Pi that was off at 02:30 still runs it
after boot. Off-box copies (USB / Mac over Tailscale) are a `rsync` of
`backups/` — left to a one-liner in DEPLOY_PI when Tailscale is set up.

## D-018 · 2026-09-13 · Observatory v2 is a route (`/v2`) on the existing service, fed only by the bus

**Context.** The v1 dashboard is live and useful; a rewrite-in-place would break
it for weeks. The v2 page must never touch the radio.

**Decision.** `wildcat/observatory/bridge.py` subscribes to `wildcat/rx/+`,
`wildcat/nodes`, `wildcat/meshd/status`, folds them into an in-memory `State`
(roster, links, packet ring, counters), and pushes deltas over a Socket.IO
namespace `/v2` on the SAME Flask-SocketIO app; `/v2/api/state` gives the first
paint. v1 routes are untouched. When `[mqtt].enabled = false` the page renders
from the database with a "bus offline" badge.

## D-019 · 2026-09-13 · The neutral envelope is ADDITIVE and versioned (`v: 1`)

**Decision.** `wildcat/meshd/packets.py` now emits the protocol-neutral fields
(`proto`, string `from`/`to`, `rx{snr,rssi,hops,time}`, one payload block per
kind) alongside the meshtastic extras (`portnum`, `packet`, `from_num`…). The
UI reads only the neutral fields; `BusInterface` reads only `packet`. A
MeshCore adapter implements the neutral part and is done. `NEIGHBORINFO_APP`'s
kind was renamed `neighbors` (neutral name) before anything depended on it.
`wildcat/nodes` gained a neutral `roster` next to the native `nodes` map.

## D-020 · 2026-09-13 · Links: NeighborInfo when available, direct-hop inference always

**Decision.** A packet with `rx.hops == 0` (hopStart == hopLimit) was heard
straight from its sender, so `from ↔ base` is drawn as a real RF link with the
measured SNR — a live topology with zero node configuration. `neighbors`
payloads (the honest topology, needs the module enabled) outrank inference for
the same pair. Links fade over 3 h and expire after 6 h.

## D-021 · 2026-09-13 · Map tiles: keyless OSM darkened in CSS

**Context.** CARTO's dark basemap now requires an API key ("API KEY REQUIRED"
tiles rendered on first load).

**Decision.** Plain `tile.openstreetmap.org` tiles with a CSS invert/hue/
saturate filter (`.dark-tiles`). No key, no account, and the same `{z}/{x}/{y}`
shape a Phase-3 offline cache serves. Framing is distance-based (base + nodes
within 40 km, widening to 120 km) so MQTT-fed nodes 300 km away don't zoom the
map out to the whole world.
