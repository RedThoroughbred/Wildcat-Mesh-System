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
