"""wildcat.config — the ONE validated config layer for The Den.

    from wildcat.config import get_config
    cfg = get_config()          # lazy singleton, loaded on first use
    cfg.radio.host              # typed, validated, defaulted

Resolution order (see ``wildcat.paths.config_search_paths``):

    1. an explicit path (``load("/path")`` / ``--config``)
    2. ``$WILDCAT_CONFIG``
    3. ``<repo>/config/wildcat.toml``
    4. ``/etc/wildcat/wildcat.toml``
    5. legacy ``<repo>/bbs/config.ini`` — read through the migrator, with a
       warning telling you to run ``wildcat config migrate --write``.

Design rules (docs/DECISIONS.md D-001..D-004):

* **Fail loudly, early, and specifically.** A missing file lists every path
  tried. A bad value says ``config/wildcat.toml: [brain].max_chunks must be
  between 1 and 5 (got 9)`` and the process exits at boot — never a
  ``KeyError`` from deep inside a menu handler.
* **Every key has a default.** Omit ``[bbs.menu]`` entirely and you get the
  stock Wildcat menu.
* **No module reads config at import time.** Services call ``get_config()``
  inside their entry point (after parsing ``--config``); library code calls it
  lazily in the function that needs it.
* **Relative paths in the file resolve against the config file's directory,
  never the CWD.**

Only ``dataclasses`` + ``tomllib`` (``tomli`` on Python < 3.11) — no pydantic,
so this imports in ~1 ms on a Pi and works with a half-broken venv.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ImportError:  # Python 3.9 / 3.10 on the Pi (Bullseye)
    import tomli as tomllib  # type: ignore[no-redef]

from . import paths

log = logging.getLogger("wildcat.config")

RADIO_TYPES = ("tcp", "serial")
SOURCES = ("radio", "bus")
MESHTASTIC_TCP_PORT = 4403

# The stock Wildcat menus. These are what setup.sh has always written and what
# the Observatory editor advertises as defaults — NOT upstream TC²'s shorter
# "Q, B, U, X", because Seth's live BBS has Weather/Network/Resources/Games.
DEFAULT_MAIN_MENU = ["W", "N", "R", "Q", "G", "B", "U", "X"]
DEFAULT_BBS_MENU = ["M", "B", "C", "J", "X"]
DEFAULT_UTILITIES_MENU = ["S", "F", "W", "X"]

# Letters build_menu() in bbs/command_handlers.py knows how to render.
KNOWN_MENU_LETTERS = set("WNQRBUXMCJSFG")

_NODE_ID_RE = re.compile(r"^![0-9a-fA-F]{8}$")


class ConfigError(Exception):
    """Raised for a missing file or any validation failure.

    ``str(err)`` is the complete, human-readable report — print it and exit.
    """


# --------------------------------------------------------------------------
# Schema — one dataclass per TOML table. Field defaults ARE the defaults.
# --------------------------------------------------------------------------

@dataclass
class RadioConfig:
    """``[radio]`` — how The Den reaches its Meshtastic node."""
    type: str = "tcp"                  # "tcp" | "serial"
    host: Optional[str] = None         # tcp: node IP / hostname (required)
    port: Optional[str] = None         # serial: device path; None = auto-detect
    tcp_port: int = MESHTASTIC_TCP_PORT
    channel_index: int = 0             # 0..7
    hop_limit: int = 3                 # 1..7; leave at 3, raising it hurts everyone


@dataclass
class MqttConfig:
    """``[mqtt]`` — the local Mosquitto bus (Phase 1 step 5). Off until meshd lands."""
    enabled: bool = False
    host: str = "localhost"
    port: int = 1883
    topic_prefix: str = "wildcat"
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class MeshdConfig:
    """``[meshd]`` — the radio owner (Phase 1 step 5). Active only when [mqtt].enabled."""
    nodes_publish_interval: int = 60      # seconds between retained wildcat/nodes snapshots
    tx_pacing_seconds: float = 2.0        # min gap between chunks on air (v1 BBS used 2 s)
    max_chunk_chars: int = 200            # raw-slice size; ≤ ~230-byte LoRa payload
    reconnect_min_seconds: int = 5
    reconnect_max_seconds: int = 120


@dataclass
class TelemetryConfig:
    """``[telemetry]`` — where the logger gets its packets."""
    source: str = "radio"                 # "radio" (opens its own connection, v1) | "bus" (via meshd)


@dataclass
class MenuConfig:
    """``[bbs.menu]`` — which letters each BBS menu shows. Omit the table for stock menus."""
    main: List[str] = field(default_factory=lambda: list(DEFAULT_MAIN_MENU))
    bbs: List[str] = field(default_factory=lambda: list(DEFAULT_BBS_MENU))
    utilities: List[str] = field(default_factory=lambda: list(DEFAULT_UTILITIES_MENU))


@dataclass
class JS8CallConfig:
    """``[bbs.js8call]`` — optional JS8Call bridge. Enabled only when host AND port are set."""
    host: Optional[str] = None
    port: Optional[int] = None
    db_file: str = "js8call.db"
    groups: List[str] = field(default_factory=list)
    store_messages: bool = True
    urgent_groups: List[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.port)


@dataclass
class BBSConfig:
    """``[bbs]`` — the TC² menu bot."""
    name: str = "Wildcat TC² BBS — Northern Kentucky Mesh"
    source: str = "radio"                 # "radio" (owns the connection, v1) | "bus" (via meshd)
    sync_nodes: List[str] = field(default_factory=list)      # was [sync] bbs_nodes
    allowed_nodes: List[str] = field(default_factory=list)   # was [allow_list] allowed_nodes (Urgent board)
    content_dir: Path = field(default_factory=lambda: paths.repo_root() / "bbs")  # messages.json, fortunes.txt, trivia.txt
    weather_api_key: str = ""          # OpenWeatherMap; "" keeps the legacy built-in key for now
    menu: MenuConfig = field(default_factory=MenuConfig)
    js8call: JS8CallConfig = field(default_factory=JS8CallConfig)


@dataclass
class DatabaseConfig:
    """``[database]`` — the one shared SQLite file."""
    path: Path = field(default_factory=lambda: paths.repo_root() / "shared" / "bulletins.db")
    busy_timeout_ms: int = 5000        # several writers, one SQLite: wait, don't "database is locked"
    wal: bool = True


@dataclass
class ObservatoryConfig:
    """``[observatory]`` — the Flask/WebSocket dashboard."""
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False                # the Werkzeug debugger is an RCE on the LAN; opt-in only
    secret_key: str = "wildcat-mesh-observatory-2025"
    refresh_interval: int = 5
    max_recent_messages: int = 20
    active_node_threshold: int = 3600
    bbs_node_id: str = "!9e766b18"     # which node's DMs count as "BBS responses" in the BBS view


@dataclass
class BrainConfig:
    """``[brain]`` — Ask the Cat (Phase 2). Parsed + validated now so the file format is stable."""
    enabled: bool = False
    trigger_prefix: str = "?"
    persona: str = "Bobcat"
    max_reply_chars: int = 320
    max_chunks: int = 3
    per_node_per_hour: int = 6
    per_node_per_day: int = 30
    global_per_hour: int = 40
    max_channel_util_pct: int = 20
    providers: List[str] = field(default_factory=lambda: ["anthropic", "ollama", "canned"])
    admin_nodes: List[str] = field(default_factory=list)
    # the local `claude` CLI (Part A analyst + Part B responder's first provider)
    cli_model: str = "claude-sonnet-5"          # the responder uses this; the analyst may override per call
    analyst_model: str = "claude-sonnet-5"
    cli_timeout: int = 120                      # seconds per CLI call
    analyst_enabled: bool = True                # the operator console (read-only DB, LAN-trust like admin)
    ollama_url: str = ""                        # e.g. "http://localhost:11434"; blank = skip that tier
    ollama_model: str = "llama3.2:3b"


@dataclass
class WildcatConfig:
    radio: RadioConfig = field(default_factory=RadioConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    meshd: MeshdConfig = field(default_factory=MeshdConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    bbs: BBSConfig = field(default_factory=BBSConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    observatory: ObservatoryConfig = field(default_factory=ObservatoryConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    # provenance
    source: Optional[Path] = None      # the file this came from
    source_kind: str = "defaults"      # "toml" | "legacy-ini" | "defaults"
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Validation helpers — every failure becomes one line in the final report.
# --------------------------------------------------------------------------

class _Ctx:
    def __init__(self, label: str, base_dir: Path) -> None:
        self.label = label            # shown as the prefix of every message
        self.base_dir = base_dir      # relative paths resolve from here
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where} {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _got(value: Any) -> str:
    return f"(got {value!r})"


def _take_str(ctx: _Ctx, table: Dict[str, Any], key: str, where: str, default: Any,
              choices: Optional[Tuple[str, ...]] = None, allow_none: bool = False) -> Any:
    if key not in table:
        return default
    v = table[key]
    if v is None and allow_none:
        return None
    if not isinstance(v, str):
        ctx.error(f"{where}.{key}", f"must be a string {_got(v)}")
        return default
    v = v.strip()
    if choices is not None and v not in choices:
        ctx.error(f"{where}.{key}", "must be one of " + ", ".join(f'"{c}"' for c in choices) + f" {_got(v)}")
        return default
    if allow_none and v == "":
        return None
    return v


def _take_int(ctx: _Ctx, table: Dict[str, Any], key: str, where: str, default: Any,
              lo: Optional[int] = None, hi: Optional[int] = None, allow_none: bool = False) -> Any:
    if key not in table:
        return default
    v = table[key]
    if v is None and allow_none:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        # tolerate "4403" strings — legacy INI values arrive as text
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            v = int(v.strip())
        else:
            ctx.error(f"{where}.{key}", f"must be an integer {_got(v)}")
            return default
    if lo is not None and hi is not None and not (lo <= v <= hi):
        ctx.error(f"{where}.{key}", f"must be between {lo} and {hi} {_got(v)}")
        return default
    if lo is not None and v < lo:
        ctx.error(f"{where}.{key}", f"must be >= {lo} {_got(v)}")
        return default
    return v


def _take_float(ctx: _Ctx, table: Dict[str, Any], key: str, where: str, default: float,
                lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    if key not in table:
        return default
    v = table[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            v = float(str(v).strip())
        except ValueError:
            ctx.error(f"{where}.{key}", f"must be a number {_got(v)}")
            return default
    v = float(v)
    if lo is not None and hi is not None and not (lo <= v <= hi):
        ctx.error(f"{where}.{key}", f"must be between {lo:g} and {hi:g} {_got(v)}")
        return default
    return v


def _take_bool(ctx: _Ctx, table: Dict[str, Any], key: str, where: str, default: bool) -> bool:
    if key not in table:
        return default
    v = table[key]
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false", "yes", "no", "1", "0", "on", "off"):
        return v.strip().lower() in ("true", "yes", "1", "on")
    ctx.error(f"{where}.{key}", f"must be true or false {_got(v)}")
    return default


def _take_str_list(ctx: _Ctx, table: Dict[str, Any], key: str, where: str, default: List[str]) -> List[str]:
    """A TOML array of strings, or (legacy) one comma-separated string. Empties dropped."""
    if key not in table:
        return list(default)
    v = table[key]
    if isinstance(v, str):
        v = v.split(",")
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        ctx.error(f"{where}.{key}", f"must be a list of strings {_got(v)}")
        return list(default)
    return [x.strip() for x in v if x and x.strip()]


def _take_path(ctx: _Ctx, table: Dict[str, Any], key: str, where: str, default: Path) -> Path:
    if key not in table:
        return default
    v = table[key]
    if not isinstance(v, str) or not v.strip():
        ctx.error(f"{where}.{key}", f"must be a non-empty path string {_got(v)}")
        return default
    p = Path(os.path.expanduser(v.strip()))
    if not p.is_absolute():
        p = ctx.base_dir / p
    return p.resolve()


def _menu_letters(ctx: _Ctx, table: Dict[str, Any], key: str, where: str, default: List[str]) -> List[str]:
    raw = _take_str_list(ctx, table, key, where, default)
    out: List[str] = []
    for item in raw:
        letter = item.strip().upper()
        if len(letter) != 1 or not letter.isalpha():
            ctx.error(f"{where}.{key}", f"entries must be single letters {_got(item)}")
            continue
        if letter not in KNOWN_MENU_LETTERS:
            ctx.warn(f"{ctx.label}: {where}.{key} has letter {letter!r} the BBS menu builder doesn't know; it will be ignored")
        out.append(letter)
    return out


def _node_ids(ctx: _Ctx, table: Dict[str, Any], key: str, where: str) -> List[str]:
    ids = _take_str_list(ctx, table, key, where, [])
    for n in ids:
        if not _NODE_ID_RE.match(n):
            ctx.warn(f"{ctx.label}: {where}.{key} entry {n!r} doesn't look like a Meshtastic node id (expected !xxxxxxxx)")
    return ids


def _table(ctx: _Ctx, parent: Dict[str, Any], key: str, where: str) -> Dict[str, Any]:
    """Fetch a sub-table; a missing table is simply ``{}`` (→ all defaults)."""
    if key not in parent:
        return {}
    v = parent[key]
    if not isinstance(v, dict):
        ctx.error(where, f"must be a table (a [{where.strip('[]')}] section) {_got(v)}")
        return {}
    return v


def _warn_unknown(ctx: _Ctx, table: Dict[str, Any], where: str, known: Tuple[str, ...]) -> None:
    for k in table:
        if k not in known:
            ctx.warn(f"{ctx.label}: unknown key {where}.{k} — ignored (typo?)")


# --------------------------------------------------------------------------
# Building the typed config from a plain dict (TOML or migrated INI).
# --------------------------------------------------------------------------

def build(data: Dict[str, Any], *, label: str = "config", base_dir: Optional[Path] = None,
          source: Optional[Path] = None, source_kind: str = "toml") -> WildcatConfig:
    """Validate ``data`` and return a :class:`WildcatConfig`, or raise :class:`ConfigError`.

    ``data`` is the parsed TOML document (nested dicts). ``base_dir`` is what
    relative paths resolve against (default: the source file's directory).
    """
    if base_dir is None:
        base_dir = paths.project_root_for(source)
    ctx = _Ctx(label, base_dir)
    if not isinstance(data, dict):
        raise ConfigError(f"{label}: top level must be a table of sections")

    _warn_unknown(ctx, data, "", ("radio", "mqtt", "meshd", "telemetry", "bbs", "database", "observatory", "brain"))

    # [radio]
    r = _table(ctx, data, "radio", "[radio]")
    _warn_unknown(ctx, r, "[radio]", ("type", "host", "port", "tcp_port", "channel_index", "hop_limit"))
    radio = RadioConfig(
        type=_take_str(ctx, r, "type", "[radio]", "tcp", choices=RADIO_TYPES),
        host=_take_str(ctx, r, "host", "[radio]", None, allow_none=True),
        port=_take_str(ctx, r, "port", "[radio]", None, allow_none=True),
        tcp_port=_take_int(ctx, r, "tcp_port", "[radio]", MESHTASTIC_TCP_PORT, 1, 65535),
        channel_index=_take_int(ctx, r, "channel_index", "[radio]", 0, 0, 7),
        hop_limit=_take_int(ctx, r, "hop_limit", "[radio]", 3, 1, 7),
    )
    if radio.type == "tcp" and not radio.host:
        ctx.error("[radio].host", 'is required when type = "tcp" (the node\'s IP, e.g. "192.168.86.37")')
    if radio.type == "serial" and radio.host:
        ctx.warn(f"{label}: [radio].host is set but type = \"serial\"; host is ignored")

    # [mqtt]
    m = _table(ctx, data, "mqtt", "[mqtt]")
    _warn_unknown(ctx, m, "[mqtt]", ("enabled", "host", "port", "topic_prefix", "username", "password"))
    mqtt = MqttConfig(
        enabled=_take_bool(ctx, m, "enabled", "[mqtt]", False),
        host=_take_str(ctx, m, "host", "[mqtt]", "localhost"),
        port=_take_int(ctx, m, "port", "[mqtt]", 1883, 1, 65535),
        topic_prefix=_take_str(ctx, m, "topic_prefix", "[mqtt]", "wildcat"),
        username=_take_str(ctx, m, "username", "[mqtt]", None, allow_none=True),
        password=_take_str(ctx, m, "password", "[mqtt]", None, allow_none=True),
    )
    if mqtt.topic_prefix.endswith("/") or "#" in mqtt.topic_prefix or "+" in mqtt.topic_prefix:
        ctx.error("[mqtt].topic_prefix", f"must be a plain topic segment like \"wildcat\" {_got(mqtt.topic_prefix)}")

    # [meshd]
    md = _table(ctx, data, "meshd", "[meshd]")
    _warn_unknown(ctx, md, "[meshd]", ("nodes_publish_interval", "tx_pacing_seconds", "max_chunk_chars",
                                      "reconnect_min_seconds", "reconnect_max_seconds"))
    meshd = MeshdConfig(
        nodes_publish_interval=_take_int(ctx, md, "nodes_publish_interval", "[meshd]", 60, 5, 3600),
        tx_pacing_seconds=_take_float(ctx, md, "tx_pacing_seconds", "[meshd]", 2.0, 0.2, 30.0),
        max_chunk_chars=_take_int(ctx, md, "max_chunk_chars", "[meshd]", 200, 50, 230),
        reconnect_min_seconds=_take_int(ctx, md, "reconnect_min_seconds", "[meshd]", 5, 1, 600),
        reconnect_max_seconds=_take_int(ctx, md, "reconnect_max_seconds", "[meshd]", 120, 1, 3600),
    )
    if meshd.reconnect_max_seconds < meshd.reconnect_min_seconds:
        ctx.error("[meshd].reconnect_max_seconds", "must be >= reconnect_min_seconds")

    # [telemetry]
    tl = _table(ctx, data, "telemetry", "[telemetry]")
    _warn_unknown(ctx, tl, "[telemetry]", ("source",))
    telemetry = TelemetryConfig(source=_take_str(ctx, tl, "source", "[telemetry]", "radio", choices=SOURCES))

    # [bbs]
    b = _table(ctx, data, "bbs", "[bbs]")
    _warn_unknown(ctx, b, "[bbs]", ("name", "source", "sync_nodes", "allowed_nodes", "content_dir",
                                   "weather_api_key", "menu", "js8call"))
    menu_t = _table(ctx, b, "menu", "[bbs.menu]")
    _warn_unknown(ctx, menu_t, "[bbs.menu]", ("main", "bbs", "utilities"))
    menu = MenuConfig(
        main=_menu_letters(ctx, menu_t, "main", "[bbs.menu]", DEFAULT_MAIN_MENU),
        bbs=_menu_letters(ctx, menu_t, "bbs", "[bbs.menu]", DEFAULT_BBS_MENU),
        utilities=_menu_letters(ctx, menu_t, "utilities", "[bbs.menu]", DEFAULT_UTILITIES_MENU),
    )
    for name, items in (("main", menu.main), ("bbs", menu.bbs), ("utilities", menu.utilities)):
        if not items:
            ctx.error(f"[bbs.menu].{name}", "must list at least one menu letter (delete the key to get the default)")
        elif "X" not in items:
            ctx.warn(f"{label}: [bbs.menu].{name} has no 'X' (exit) entry; users can still send X, but the menu won't show it")
    js_t = _table(ctx, b, "js8call", "[bbs.js8call]")
    _warn_unknown(ctx, js_t, "[bbs.js8call]", ("host", "port", "db_file", "groups", "store_messages", "urgent_groups"))
    js8 = JS8CallConfig(
        host=_take_str(ctx, js_t, "host", "[bbs.js8call]", None, allow_none=True),
        port=_take_int(ctx, js_t, "port", "[bbs.js8call]", None, 1, 65535, allow_none=True),
        db_file=_take_str(ctx, js_t, "db_file", "[bbs.js8call]", "js8call.db"),
        groups=_take_str_list(ctx, js_t, "groups", "[bbs.js8call]", []),
        store_messages=_take_bool(ctx, js_t, "store_messages", "[bbs.js8call]", True),
        urgent_groups=_take_str_list(ctx, js_t, "urgent_groups", "[bbs.js8call]", []),
    )
    if bool(js8.host) != bool(js8.port):
        ctx.error("[bbs.js8call]", "needs BOTH host and port to enable JS8Call (or neither to disable it)")
    bbs = BBSConfig(
        name=_take_str(ctx, b, "name", "[bbs]", BBSConfig.name),
        source=_take_str(ctx, b, "source", "[bbs]", "radio", choices=SOURCES),
        sync_nodes=_node_ids(ctx, b, "sync_nodes", "[bbs]"),
        allowed_nodes=_node_ids(ctx, b, "allowed_nodes", "[bbs]"),
        content_dir=_take_path(ctx, b, "content_dir", "[bbs]", base_dir / "bbs"),
        weather_api_key=_take_str(ctx, b, "weather_api_key", "[bbs]", ""),
        menu=menu,
        js8call=js8,
    )

    # [database]
    d = _table(ctx, data, "database", "[database]")
    _warn_unknown(ctx, d, "[database]", ("path", "busy_timeout_ms", "wal"))
    database = DatabaseConfig(
        path=_take_path(ctx, d, "path", "[database]", base_dir / "shared" / "bulletins.db"),
        busy_timeout_ms=_take_int(ctx, d, "busy_timeout_ms", "[database]", 5000, 0, 600000),
        wal=_take_bool(ctx, d, "wal", "[database]", True),
    )

    # [observatory]
    o = _table(ctx, data, "observatory", "[observatory]")
    _warn_unknown(ctx, o, "[observatory]", ("host", "port", "debug", "secret_key", "refresh_interval",
                                           "max_recent_messages", "active_node_threshold", "bbs_node_id"))
    observatory = ObservatoryConfig(
        host=_take_str(ctx, o, "host", "[observatory]", "0.0.0.0"),
        port=_take_int(ctx, o, "port", "[observatory]", 5000, 1, 65535),
        debug=_take_bool(ctx, o, "debug", "[observatory]", False),
        secret_key=_take_str(ctx, o, "secret_key", "[observatory]", ObservatoryConfig.secret_key),
        refresh_interval=_take_int(ctx, o, "refresh_interval", "[observatory]", 5, 1, 3600),
        max_recent_messages=_take_int(ctx, o, "max_recent_messages", "[observatory]", 20, 1, 1000),
        active_node_threshold=_take_int(ctx, o, "active_node_threshold", "[observatory]", 3600, 1, None),
        bbs_node_id=_take_str(ctx, o, "bbs_node_id", "[observatory]", ObservatoryConfig.bbs_node_id),
    )
    if observatory.debug:
        ctx.warn(f"{label}: [observatory].debug = true exposes the Werkzeug debugger (remote code execution) to anyone on the LAN — never leave this on")

    # [brain]
    br = _table(ctx, data, "brain", "[brain]")
    _warn_unknown(ctx, br, "[brain]", ("enabled", "trigger_prefix", "persona", "max_reply_chars", "max_chunks",
                                      "per_node_per_hour", "per_node_per_day", "global_per_hour",
                                      "max_channel_util_pct", "providers", "admin_nodes",
                                      "cli_model", "analyst_model", "cli_timeout", "analyst_enabled", "ollama_url", "ollama_model"))
    brain = BrainConfig(
        enabled=_take_bool(ctx, br, "enabled", "[brain]", False),
        trigger_prefix=_take_str(ctx, br, "trigger_prefix", "[brain]", "?"),
        persona=_take_str(ctx, br, "persona", "[brain]", "Bobcat"),
        max_reply_chars=_take_int(ctx, br, "max_reply_chars", "[brain]", 320, 40, 1000),
        max_chunks=_take_int(ctx, br, "max_chunks", "[brain]", 3, 1, 5),
        per_node_per_hour=_take_int(ctx, br, "per_node_per_hour", "[brain]", 6, 1, None),
        per_node_per_day=_take_int(ctx, br, "per_node_per_day", "[brain]", 30, 1, None),
        global_per_hour=_take_int(ctx, br, "global_per_hour", "[brain]", 40, 1, None),
        max_channel_util_pct=_take_int(ctx, br, "max_channel_util_pct", "[brain]", 20, 1, 100),
        providers=_take_str_list(ctx, br, "providers", "[brain]", ["anthropic", "ollama", "canned"]),
        admin_nodes=_node_ids(ctx, br, "admin_nodes", "[brain]"),
        cli_model=_take_str(ctx, br, "cli_model", "[brain]", "claude-sonnet-5"),
        analyst_model=_take_str(ctx, br, "analyst_model", "[brain]", "claude-sonnet-5"),
        cli_timeout=_take_int(ctx, br, "cli_timeout", "[brain]", 120, 10, 600),
        analyst_enabled=_take_bool(ctx, br, "analyst_enabled", "[brain]", True),
        ollama_url=_take_str(ctx, br, "ollama_url", "[brain]", ""),
        ollama_model=_take_str(ctx, br, "ollama_model", "[brain]", "llama3.2:3b"),
    )
    for p in brain.providers:
        if p not in ("anthropic", "ollama", "canned"):
            ctx.error("[brain].providers", f'entries must be "anthropic", "ollama" or "canned" {_got(p)}')
    if not brain.trigger_prefix:
        ctx.error("[brain].trigger_prefix", "must not be empty")

    # The node accepts ONE API client. [mqtt].enabled starts meshd, which takes that
    # slot — so every consumer must then be on the bus; and a bus consumer needs meshd.
    for where, src in (("[bbs].source", bbs.source), ("[telemetry].source", telemetry.source)):
        if src == "bus" and not mqtt.enabled:
            ctx.error(where, '= "bus" requires [mqtt].enabled = true (meshd publishes the packets)')
        if src == "radio" and mqtt.enabled:
            ctx.error(where, '= "radio" would fight meshd for the node\'s single API socket while '
                             '[mqtt].enabled = true — set it to "bus" (or disable mqtt)')

    if ctx.errors:
        raise ConfigError(
            f"{label}: {len(ctx.errors)} problem(s):\n" + "\n".join(f"  - {e}" for e in ctx.errors)
        )

    return WildcatConfig(radio=radio, mqtt=mqtt, meshd=meshd, telemetry=telemetry, bbs=bbs, database=database,
                         observatory=observatory, brain=brain,
                         source=source, source_kind=source_kind, warnings=ctx.warnings)


# --------------------------------------------------------------------------
# Loading from disk
# --------------------------------------------------------------------------

def load_file(path: Path) -> WildcatConfig:
    """Load and validate one specific file (``.toml`` or legacy ``.ini``)."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"config file does not exist: {path}")
    label = _label_for(path)
    if path.suffix.lower() == ".ini":
        from .legacy_ini import ini_to_dict  # local import: keeps configparser off the hot path
        data = ini_to_dict(path)
        cfg = build(data, label=label, source=path, source_kind="legacy-ini")
        cfg.warnings.insert(0, (
            f"{label}: running from the LEGACY INI config. Run `wildcat config migrate --write` "
            f"to create config/wildcat.toml (the INI keeps working until you do)."))
        return cfg
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:  # type: ignore[attr-defined]
        raise ConfigError(f"{label}: not valid TOML — {e}") from None
    return build(data, label=label, source=path, source_kind="toml")


def load(explicit: Optional[str] = None) -> WildcatConfig:
    """Resolve the config location (see module docstring), load, validate, install as the singleton."""
    global _current
    try:
        path, _kind = paths.find_config(explicit)
    except FileNotFoundError as e:
        raise ConfigError(str(e)) from None
    cfg = load_file(path)
    for w in cfg.warnings:
        log.warning(w)
    _current = cfg
    return cfg


_current: Optional[WildcatConfig] = None


def get_config() -> WildcatConfig:
    """The process-wide config, loading it on first use.

    Services should call :func:`load` explicitly at startup (so ``--config``
    is honored and errors surface at boot); library modules use this lazily.
    """
    if _current is None:
        return load()
    return _current


def set_config(cfg: WildcatConfig) -> None:
    """Install a config object (tests, or a service that built one itself)."""
    global _current
    _current = cfg


def reset() -> None:
    """Forget the singleton (tests)."""
    global _current
    _current = None


def _label_for(path: Path) -> str:
    """``config/wildcat.toml`` when the file is inside the repo, else the absolute path."""
    try:
        return str(path.relative_to(paths.repo_root()))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# Serializing back out (``wildcat config show``, ``migrate --write``)
# --------------------------------------------------------------------------

def _toml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, Path):
        v = str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    raise TypeError(f"cannot serialize {type(v).__name__} to TOML")


_PROVENANCE = {"source", "source_kind", "warnings"}


def to_toml(cfg: WildcatConfig, *, redact: bool = False) -> str:
    """Render a config as TOML text (every key explicit, comments omitted)."""
    lines: List[str] = []

    def emit(obj: Any, name: str) -> None:
        scalars = []
        subtables = []
        for f in fields(obj):
            if f.name in _PROVENANCE:
                continue
            v = getattr(obj, f.name)
            if is_dataclass(v):
                subtables.append((f.name, v))
            elif v is None:
                continue
            else:
                if redact and f.name in ("password", "secret_key", "weather_api_key") and v:
                    v = "***"
                scalars.append((f.name, v))
        lines.append(f"[{name}]")
        for k, v in scalars:
            lines.append(f"{k} = {_toml_scalar(v)}")
        lines.append("")
        for sub, v in subtables:
            emit(v, f"{name}.{sub}")

    for f in fields(cfg):
        if f.name in _PROVENANCE:
            continue
        emit(getattr(cfg, f.name), f.name)
    return "\n".join(lines).rstrip() + "\n"
