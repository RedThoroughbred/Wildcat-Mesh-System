# Deploy + verify on the Pi (Phase 1)

Everything in Phase 1 was written and unit-tested on a Mac **with no radio**.
This is the list of what only the Pi can prove. Work top to bottom; each block
is a rollback point.

Assumptions: repo at `/home/seth/Wildcat-Mesh-System`, user `seth`, the node at
`192.168.86.37` over TCP, the v1 units (`mesh-bbs`, `telemetry-logger`,
`mesh-observatory`) currently running.

> **About "DM `M` gets the menu":** in this fork a bare `M` opens the *Mail*
> menu (`main_menu_handlers["m"]`), and the *main* menu (Weather / Network /
> Resources / Quote / Games / Bulletins / Utilities) comes back for `X` or any
> text the BBS doesn't recognize. That's v1 behavior, unchanged; the checks
> below say "menu" meaning either. (Mapping `M` to the main menu is a
> one-line change in `bbs/message_processing.py` if you want it.)

## 0. Before touching anything

```bash
cd /home/seth/Wildcat-Mesh-System
cp shared/bulletins.db /home/seth/bulletins-pre-v2.db          # belt and braces
git fetch && git checkout v2/phase1-den
```

## 1. Config layer (steps 1–2) — no service changes yet

```bash
./venv/bin/pip install -e '.[den]'
./venv/bin/wildcat config path          # expect: [FOUND] …/bbs/config.ini  (legacy bbs/config.ini)
./venv/bin/wildcat config validate      # expect: OK … [legacy-ini]  + the "run migrate" warning
./venv/bin/wildcat config migrate --write
./venv/bin/wildcat config validate      # expect: OK …/config/wildcat.toml [toml]
./venv/bin/wildcat config show          # eyeball [radio].host, [bbs.menu]
```

**Verify the CWD bug is dead:**
```bash
cd / && /home/seth/Wildcat-Mesh-System/venv/bin/python3 /home/seth/Wildcat-Mesh-System/bbs/server.py
```
(stop the v1 unit first: `sudo systemctl stop mesh-bbs`). It must print
`Config: …/config/wildcat.toml [toml]`, connect, and a DM of `M` from GO must
return the menu with Weather/Network/Resources/Games. Ctrl-C, `sudo systemctl start mesh-bbs`.

**Verify the KeyError bug is dead:** delete the whole `[bbs.menu]` table from
`wildcat.toml`, run `wildcat config validate` (OK), run the server the same way,
DM `M` → the stock menu. Put the table back (or don't; it's the same menu).

## 2. Doctor (step 3)

```bash
./venv/bin/wildcat doctor
```
Expected on a healthy Pi: python ✔ · config ✔ · deps ✔ · database ✔ ×2 (+ "last
packet logged Nm ago") · content ✔ · radio ✔ `tcp 192.168.86.37:4403 reachable`
· mqtt – (disabled) · systemd: legacy units ✔ + a ⚠ "no wildcat-* units".

Deliberately break it: change `[radio].host` to `192.168.86.99` → radio ✖ and,
if the node is on, the LAN scan names `192.168.86.37`. Put it back.

Note: while `mesh-bbs` holds the node's TCP session, the radio probe still
succeeds (it's a plain connect), so doctor can't tell you "someone else has the
socket" — that's what the systemd check is for.

## 3. systemd (step 4)

```bash
./deploy/install.sh              # renders units, DISABLES the legacy three, enables wildcat.target
systemctl list-units 'wildcat*'  # meshd: inactive (exited 0 — mqtt disabled), brain: inactive (exited 0 — needs the bus), bbs/telemetry/observatory: active
journalctl -u wildcat-bbs -n 30  # "Config: …wildcat.toml [toml]", "TC²-BBS is running on tcp interface"
./venv/bin/wildcat doctor        # systemd: wildcat-* ✔, no legacy ✖
```

**The Phase 1 done-when, part 1 — power cycle:** pull the Pi's power, plug it
back in, walk away. After 2 minutes: `systemctl status wildcat.target` green,
`journalctl -b -u wildcat-bbs` shows it waited for network-online then
connected (if the node boots slower than the Pi you'll see a few
"connect failed … retry" lines — that's `Restart=on-failure` + `RestartSec=10`
doing its job), and a DM of `M` gets the menu with nobody at a keyboard.

**Connection-loss restart:** reboot the *node* (or `meshtastic --host … --reboot`).
`journalctl -u wildcat-bbs -f` must show "Radio connection lost — exiting",
exit code 3, and a restart that reconnects. (v1 needed the 5-minute self-kill for this.)

**Backup timer:** `sudo systemctl start wildcat-backup.service && ls backups/`
→ one `bulletins-YYYYMMDD-HHMMSS.db`; `systemctl list-timers wildcat-backup*`.

## 4. meshd + bus (step 5) — the new thing; do this last, on a quiet evening

```bash
./deploy/install.sh --mosquitto
```
Edit `config/wildcat.toml`: `[mqtt] enabled = true`, `[bbs] source = "bus"`,
`[telemetry] source = "bus"`. `wildcat config validate` (it rejects half-states).

```bash
sudo systemctl restart wildcat.target
journalctl -u wildcat-meshd -f          # "MQTT connected", "connecting to node over tcp", status connected
mosquitto_sub -v -t 'wildcat/#'         # retained status + nodes appear immediately; rx/* as the mesh talks
journalctl -u wildcat-brain -n 5        # "Bobcat responder off (prefix '?' …)" — it stays off until you flip it in the dashboard
```

What to prove, in order:
1. `wildcat/meshd/status` says `connected` with your node num; `wildcat/nodes` lists the mesh.
2. `journalctl -u wildcat-bbs` says "running on the MQTT bus"; `wildcat-telemetry` says "from the bus".
3. **DM `M` from GO → menu comes back.** Watch it cross the bus: `rx/text` (your M), `tx` (the BBS's reply),
   `tx/result {ok: true}`. That is the whole architecture working.
4. Position/telemetry rows keep landing in `telemetry_logs` / `position_logs` (Observatory still updates).
5. Reboot the node: meshd logs "connection lost", backoff-reconnects, `status` flips disconnected → connected;
   **the BBS and telemetry do not restart** (they just wait). DM `M` again → menu.
6. `sudo systemctl kill -s KILL wildcat-meshd`: `status` becomes `dead` (last will) within ~30 s,
   systemd restarts meshd, status → connected, BBS keeps working. (No more 5-minute self-kill anywhere.)
7. `wildcat doctor`: mqtt ✔, all wildcat-* ✔.
8. Power-cycle again (done-when, part 2): everything up within 2 min, `M` → menu.

If anything in 1–3 fails: set `enabled = false` and both sources to `"radio"`,
`sudo systemctl restart wildcat.target` — you're back on the v1 path with the v2
units. Paste the `journalctl -u wildcat-meshd` lines.

## Things that were assumed, not verified (flag if wrong)

- The Pi's Python is 3.9 or 3.11 (code is 3.9-safe; `tomli` is pulled in automatically below 3.11).
- `meshtastic==2.7.4` publishes `meshtastic.connection.lost` on TCP drop (it does in 2.3+; the exit-3
  restart relies on it — if the BBS ever idles on a dead socket, that's the assumption to check).
- `interface.nodes` values are plain dicts (they are in 2.7; anything odd is stringified by `json_safe`).
- The v1 admin editor (`/admin/bbs-config`) shows the TOML path read-only; the content editor still works.
