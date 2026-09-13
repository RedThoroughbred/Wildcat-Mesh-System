# Phase 1 — "Stabilize the Den": step-by-step plan

Source: `WILDCAT_MESH_V2.md` §2.3 and the Phase 1 row of §5. Done-when: *pull
the Pi's power, plug it back in, walk away — every service is up within 2 min,
`wildcat doctor` is all green, a DM of `M` gets the menu, and the two known bugs
are structurally impossible.*

Branch: `v2/phase1-den`. Each step is a separate commit (or two) and leaves the
BBS runnable.

| # | Step | Status |
|---|------|--------|
| 1 | **One validated config.** `wildcat/` package: `paths.py`, `config.py` (dataclass schema + validator + TOML/INI loader + serializer), `legacy_ini.py`, `cli.py` (`wildcat config path/validate/show/migrate`). `config/wildcat.example.toml`. `pyproject.toml` (`pip install -e '.[den]'` → `wildcat` CLI). Tests. | ✅ done |
| 2 | **Fix the two bugs structurally + wire the existing code.** `bbs/config_init.py` builds its dict from `wildcat.config`; `command_handlers.py` reads menus/content lazily via absolute paths; `js8call_integration.py`, `telemetry_logger.py` (its stale hardcoded INI path is gone), `db_operations.py` (db path + busy_timeout + WAL) and `observatory/config.py` (now a shim over the same config) all read the one file; Observatory admin editor paths fixed (D-008). `setup.sh` writes `wildcat.toml`. | ✅ done |
| 3 | **`wildcat doctor`.** `wildcat/doctor.py`: config parses (with warnings) · Python version + every dep importable at the pinned version · DB opens, WAL on, tables present · radio reachable (TCP connect to host:4403 with timeout; serial device exists/permissions; if TCP fails, a bounded LAN scan for :4403 to find a stale IP) · Mosquitto reachable (only if `[mqtt].enabled`) · systemd units installed/enabled/active (when on Linux). Color/plain output, `--no-network`, exit 0/1/2. Tests for the pure checks with fakes. | ⏳ next |
| 4 | **Hardened systemd.** `deploy/systemd/wildcat-bbs.service`, `wildcat-telemetry.service`, `wildcat-observatory.service`, later `wildcat-meshd.service`; a `wildcat.target` that groups them. `After=network-online.target` (+ `mosquitto.service` where relevant), `Wants=network-online.target`, `Restart=on-failure`, `RestartSec`, `StartLimitIntervalSec=0`, `WorkingDirectory=<repo>`, `Environment=WILDCAT_CONFIG=…`, `ExecStartPre=…/wildcat config validate`, journald. `deploy/install.sh` (venv, `pip install -e '.[den]'`, `apt install mosquitto`, copy units with the real paths substituted, `systemctl enable`). Drop the `RuntimeMaxSec=300` self-kill once meshd owns the radio (it papers over the one-client-per-node problem). Nightly `sqlite3 .backup` timer. | ⏳ |
| 5 | **meshd + MQTT bus skeleton.** `wildcat/bus.py` (paho-mqtt wrapper: `publish(topic, dict)`, `subscribe(topic, fn)`, reconnect). `wildcat/meshd/` (owns the one radio connection via the existing `get_interface`; republishes each received packet as JSON on `wildcat/rx/<portnum-kind>` + `wildcat/nodes`; drains `wildcat/tx` with a single queue + 2 s pacing + `wantAck`; reconnects with backoff). Proof consumer: **telemetry** subscribes to `wildcat/rx/telemetry|position|neighborinfo|nodeinfo` instead of opening its own radio (it's the simplest and today it *fights the BBS for the socket*). BBS migration onto the bus (an `interface`-shaped adapter that publishes `sendText` to `wildcat/tx`) is designed + documented, wired only if time allows. Parser/queue tests with a fake interface; no hardware. | ⏳ |
| — | **Deploy + verify on the Pi.** Written up in `docs/DEPLOY_PI.md` at the end: exact commands, what must be observed on real hardware (power-cycle test, `wildcat doctor` green, `M` → menu). | ⏳ |

## What Seth must verify on the Pi (can't be tested from the Mac)

After steps 1–2 (this checkpoint):

1. `git fetch && git checkout v2/phase1-den && ./venv/bin/pip install -e '.[den]'`
2. `./venv/bin/wildcat config path` — shows the search order; expect it to pick up the existing `bbs/config.ini` as `[legacy-ini]`.
3. `./venv/bin/wildcat config validate` — expect `OK … [legacy-ini]` plus the migrate warning. Any `CONFIG ERROR` here is exactly the kind of thing that used to be a crash at runtime.
4. `./venv/bin/wildcat config migrate --write` → creates `config/wildcat.toml`; re-run `validate` → `[toml]`.
5. `cd /` (deliberately NOT inside bbs/) and `python3 /home/seth/Wildcat-Mesh-System/bbs/server.py` — the BBS must connect and answer `M` with the menu. This is the CWD bug, proven fixed.
6. Restart the three existing units (`sudo systemctl restart mesh-bbs telemetry-logger mesh-observatory`) and check `journalctl -u …` — all three now log which config file they loaded.
7. Observatory → Admin → BBS Config shows `config/wildcat.toml` (read-only) — not `/home/user/...`.
