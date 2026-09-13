"""``wildcat doctor`` — preflight for The Den.

Answers "why isn't the BBS answering?" before you DM the node wondering.
Every check is a small function that appends a :class:`Check` to a
:class:`Report`; the probes that touch the outside world (TCP connect, serial
ports, ``systemctl``) are injectable so the logic is unit-tested with no
hardware, and the real probes run on the Pi.

Checks, in order:

  python    version (3.9+; note when < 3.11 → tomli in use)
  config    parses + validates (warnings listed); which file, which kind
  deps      every pinned dependency importable, with versions
  database  path/dir writable, opens, quick_check, journal mode, tables, last packet age
  content   messages.json / fortunes.txt / trivia.txt present
  radio     tcp: connect host:4403 (and a bounded /24 scan for a stale IP);
            serial: device exists + r/w perms, or exactly one auto-detectable port
  mqtt      broker reachable (only when [mqtt].enabled)
  systemd   v2 units present/enabled/active; legacy units not fighting them

Exit: 0 all ok/warn · 1 any fail · 2 config unreadable.
"""
from __future__ import annotations

import glob
import importlib
import ipaddress
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import ConfigError, WildcatConfig, load

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
_GLYPH = {OK: "✔", WARN: "⚠", FAIL: "✖", SKIP: "–"}
_COLOR = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", SKIP: "\033[90m"}
_RESET = "\033[0m"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


@dataclass
class Report:
    checks: List[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str, hint: str = "") -> Check:
        c = Check(name, status, detail, hint)
        self.checks.append(c)
        return c

    def count(self, status: str) -> int:
        return sum(1 for c in self.checks if c.status == status)

    @property
    def exit_code(self) -> int:
        return 1 if self.count(FAIL) else 0

    def render(self, color: bool = False) -> str:
        lines = ["wildcat doctor — The Den preflight"]
        for c in self.checks:
            g = _GLYPH[c.status]
            if color:
                g = f"{_COLOR[c.status]}{g}{_RESET}"
            lines.append(f"  {g} {c.name:<10} {c.detail}")
            if c.hint:
                for h in c.hint.splitlines():
                    lines.append(f"    {'':<10} → {h}")
        lines.append(f"{self.count(OK)} ok · {self.count(WARN)} warn · {self.count(FAIL)} fail · {self.count(SKIP)} skipped")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Probes (the only functions that touch the world; all injectable)
# --------------------------------------------------------------------------

def probe_tcp(host: str, port: int, timeout: float = 3.0) -> Tuple[bool, str]:
    """Try a TCP connect. Returns (reachable, error-or-empty)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as e:
        return False, str(e.strerror or e)


def resolve_host(host: str) -> Optional[str]:
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def scan_lan(ip: str, port: int, timeout: float = 0.25, workers: int = 64) -> List[str]:
    """Bounded /24 sweep for anything answering ``port`` — the stale-IP finder.

    Only runs for private IPv4 addresses (a home LAN); never for loopback or
    public space. ~254 connects at 0.25 s in 64 threads ≈ 1–2 s.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return []
    if not (addr.version == 4 and addr.is_private and not addr.is_loopback):
        return []
    net = ipaddress.ip_network(f"{ip}/24", strict=False)

    def hit(h: str) -> Optional[str]:
        ok, _ = probe_tcp(h, port, timeout)
        return h if ok else None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [h for h in ex.map(hit, (str(h) for h in net.hosts())) if h]


def find_serial_ports() -> List[Tuple[str, str]]:
    """``[(device, description)]`` — pyserial's enumerator, else a /dev glob."""
    try:
        from serial.tools import list_ports  # type: ignore
        return [(p.device, p.description or "") for p in list_ports.comports()]
    except Exception:
        pats = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/cu.usbserial*", "/dev/cu.usbmodem*", "/dev/cu.SLAB*"]
        return [(d, "") for pat in pats for d in sorted(glob.glob(pat))]


def systemctl_show(unit: str) -> Dict[str, str]:
    """``{LoadState, ActiveState, UnitFileState}`` for a unit, or {} when systemd is absent."""
    if shutil.which("systemctl") is None:
        return {}
    try:
        out = subprocess.run(["systemctl", "show", "-p", "LoadState,ActiveState,UnitFileState", unit],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def import_version(module: str, dist: str) -> Tuple[bool, str]:
    """(importable, version-or-error) — version from package metadata when available."""
    try:
        importlib.import_module(module)
    except Exception as e:  # ImportError, or a broken wheel raising anything
        return False, f"{type(e).__name__}: {e}"
    try:
        from importlib.metadata import version
        return True, version(dist)
    except Exception:
        return True, "?"


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def check_python(r: Report) -> None:
    v = sys.version_info
    where = sys.executable
    if v < (3, 9):
        r.add("python", FAIL, f"{platform.python_version()} ({where})", "Python 3.9+ is required")
    elif v < (3, 11):
        r.add("python", OK, f"{platform.python_version()} ({where}) — TOML via the tomli package")
    else:
        r.add("python", OK, f"{platform.python_version()} ({where})")


def check_config(r: Report, explicit: Optional[str] = None) -> Optional[WildcatConfig]:
    try:
        cfg = load(explicit)
    except ConfigError as e:
        r.add("config", FAIL, "cannot load", str(e))
        return None
    r.add("config", OK, f"{cfg.source} [{cfg.source_kind}]")
    for w in cfg.warnings:
        r.add("config", WARN, w)
    return cfg


# (import name, distribution name, why)
REQUIRED_DEPS: Sequence[Tuple[str, str, str]] = (
    ("meshtastic", "meshtastic", "radio"),
    ("pubsub", "pypubsub", "radio events"),
    ("serial", "pyserial", "serial ports"),
    ("requests", "requests", "BBS weather"),
    ("flask", "flask", "observatory"),
    ("flask_socketio", "flask-socketio", "observatory"),
    ("apscheduler", "apscheduler", "observatory"),
)
MQTT_DEP = ("paho.mqtt.client", "paho-mqtt", "MQTT bus")


def check_deps(r: Report, cfg: Optional[WildcatConfig],
               importer: Callable[[str, str], Tuple[bool, str]] = import_version) -> None:
    deps = list(REQUIRED_DEPS)
    if cfg is not None and cfg.mqtt.enabled:
        deps.append(MQTT_DEP)
    good, bad = [], []
    for mod, dist, why in deps:
        ok, info = importer(mod, dist)
        (good if ok else bad).append((dist, info, why))
    if good:
        r.add("deps", OK, ", ".join(f"{d} {v}" for d, v, _ in good))
    for dist, err, why in bad:
        r.add("deps", FAIL, f"{dist} not importable ({why}) — {err}",
              "pip install -e '.[den]'  (inside the venv)")
    if cfg is not None and not cfg.mqtt.enabled:
        r.add("deps", SKIP, "paho-mqtt not checked ([mqtt].enabled = false)")


BBS_TABLES = ("bulletins", "mail", "channels", "message_logs")
OBS_TABLES = ("telemetry_logs", "position_logs", "neighbor_info", "node_info")


def check_database(r: Report, cfg: WildcatConfig, now: Callable[[], float] = time.time) -> None:
    path = cfg.database.path
    if not path.exists():
        # db_operations.get_db_connection() mkdirs the parent, so what matters is
        # whether the nearest EXISTING ancestor is writable.
        anc = path.parent
        while not anc.exists() and anc != anc.parent:
            anc = anc.parent
        if anc.is_dir() and os.access(anc, os.W_OK):
            r.add("database", WARN, f"{path} does not exist yet — created on first BBS start")
        else:
            r.add("database", FAIL, f"{path} does not exist and {anc} is not a writable directory",
                  f"mkdir -p {path.parent} && chown {os.environ.get('USER', '$USER')} {path.parent}  (or fix [database].path)")
        return
    if not os.access(path, os.R_OK | os.W_OK):
        r.add("database", FAIL, f"{path} is not readable+writable by {os.environ.get('USER', 'this user')}")
        return
    try:
        conn = sqlite3.connect(str(path), timeout=cfg.database.busy_timeout_ms / 1000)
        try:
            qc = conn.execute("PRAGMA quick_check").fetchone()[0]
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            last = None
            if "message_logs" in tables:
                last = conn.execute("SELECT MAX(timestamp) FROM message_logs").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        r.add("database", FAIL, f"{path}: {e}")
        return
    if qc != "ok":
        r.add("database", FAIL, f"{path}: quick_check → {qc}", "restore from backups/ (see deploy/)")
        return
    size_mb = path.stat().st_size / 1e6
    r.add("database", OK, f"{path} ({size_mb:.1f} MB, journal_mode={mode})")
    if cfg.database.wal and str(mode).lower() != "wal":
        r.add("database", WARN, f"journal_mode is {mode}; the BBS switches it to WAL on next start")
    missing = [t for t in BBS_TABLES + OBS_TABLES if t not in tables]
    if missing:
        r.add("database", WARN, "tables not created yet: " + ", ".join(missing) + " (first start creates them)")
    if last:
        age = int(now()) - int(last)
        human = f"{age // 3600}h {age % 3600 // 60}m" if age >= 3600 else f"{age // 60}m {age % 60}s"
        status = OK if age < 6 * 3600 else WARN
        r.add("database", status, f"last packet logged {human} ago")
    elif "message_logs" in tables:
        r.add("database", WARN, "message_logs is empty — nothing has been heard yet")


def check_content(r: Report, cfg: WildcatConfig) -> None:
    d = cfg.bbs.content_dir
    if not d.is_dir():
        r.add("content", FAIL, f"[bbs].content_dir {d} is not a directory")
        return
    required = {"messages.json": "menu text (BBS falls back to built-in strings)"}
    optional = {"fortunes.txt": "[F]ortune shows 'Error generating fortune'",
                "trivia.txt": "trivia game is unavailable"}
    present = [n for n in list(required) + list(optional) if (d / n).is_file()]
    if present:
        r.add("content", OK, f"{d}: " + ", ".join(present))
    for n, why in required.items():
        if n not in present:
            r.add("content", WARN, f"{n} missing in {d} — {why}")
    for n, why in optional.items():
        if n not in present:
            r.add("content", WARN, f"{n} missing in {d} — {why}",
                  "it is git-ignored; copy yours in (the Observatory admin page can edit it)")


def check_radio(r: Report, cfg: WildcatConfig, network: bool = True,
                probe: Callable[[str, int, float], Tuple[bool, str]] = probe_tcp,
                resolve: Callable[[str], Optional[str]] = resolve_host,
                scan: Callable[[str, int], List[str]] = scan_lan,
                ports: Callable[[], List[Tuple[str, str]]] = find_serial_ports) -> None:
    radio = cfg.radio
    if radio.type == "tcp":
        target = f"tcp {radio.host}:{radio.tcp_port}"
        if not network:
            r.add("radio", SKIP, f"{target} (--no-network)")
            return
        ip = resolve(radio.host or "")
        if ip is None:
            r.add("radio", FAIL, f"{target} — hostname does not resolve", "check [radio].host")
            return
        ok, err = probe(ip, radio.tcp_port, 3.0)
        if ok:
            r.add("radio", OK, f"{target} reachable")
            return
        found = scan(ip, radio.tcp_port)
        if found:
            r.add("radio", FAIL, f"{target} — {err or 'unreachable'}",
                  f"LAN scan found a Meshtastic API port open at: {', '.join(found)}\n"
                  f"update [radio].host in {cfg.source} (or give the node a DHCP reservation)")
        else:
            r.add("radio", FAIL, f"{target} — {err or 'unreachable'}",
                  "no other host on that /24 answers :4403 — is the node powered and on Wi-Fi?\n"
                  "(nodes accept ONE API client: a phone/app connected over TCP will block this)")
        return

    # serial
    if radio.port:
        p = radio.port
        if not os.path.exists(p):
            avail = ports()
            hint = ("available: " + ", ".join(d for d, _ in avail)) if avail else "no serial devices found — is the node plugged in?"
            r.add("radio", FAIL, f"serial {p} does not exist", hint)
        elif not os.access(p, os.R_OK | os.W_OK):
            r.add("radio", FAIL, f"serial {p} exists but is not readable+writable",
                  f"sudo usermod -aG dialout {os.environ.get('USER', '$USER')}  then log out/in")
        else:
            r.add("radio", OK, f"serial {p}")
        return
    avail = ports()
    if len(avail) == 1:
        d, desc = avail[0]
        r.add("radio", OK, f"serial auto-detect → {d} {desc}".rstrip())
    elif not avail:
        r.add("radio", FAIL, "serial auto-detect: no serial devices found", "plug the node in, or set [radio].port")
    else:
        r.add("radio", FAIL, "serial auto-detect: several devices — " + ", ".join(d for d, _ in avail),
              "set [radio].port to the node's device")


def check_mqtt(r: Report, cfg: WildcatConfig, network: bool = True,
               probe: Callable[[str, int, float], Tuple[bool, str]] = probe_tcp) -> None:
    m = cfg.mqtt
    if not m.enabled:
        r.add("mqtt", SKIP, "disabled ([mqtt].enabled = false) — meshd/bus not in use")
        return
    if not network:
        r.add("mqtt", SKIP, f"{m.host}:{m.port} (--no-network)")
        return
    ok, err = probe(m.host, m.port, 3.0)
    if ok:
        r.add("mqtt", OK, f"broker {m.host}:{m.port} reachable")
    else:
        r.add("mqtt", FAIL, f"broker {m.host}:{m.port} — {err or 'unreachable'}",
              "sudo apt install mosquitto && sudo systemctl enable --now mosquitto")


V2_UNITS = ("wildcat.target", "wildcat-meshd.service", "wildcat-bbs.service",
            "wildcat-telemetry.service", "wildcat-observatory.service")
LEGACY_UNITS = ("mesh-bbs.service", "telemetry-logger.service", "mesh-observatory.service")


def check_systemd(r: Report, show: Callable[[str], Dict[str, str]] = systemctl_show) -> None:
    states = {u: show(u) for u in V2_UNITS + LEGACY_UNITS}
    if not any(states.values()):
        r.add("systemd", SKIP, "systemctl not available here (not the Pi?)")
        return

    def present(u: str) -> bool:
        return states[u].get("LoadState") == "loaded"

    v2_present = [u for u in V2_UNITS if present(u)]
    if not v2_present:
        r.add("systemd", WARN, "no wildcat-* units installed", "deploy/install.sh installs + enables them")
    for u in v2_present:
        st = states[u]
        active, enabled = st.get("ActiveState"), st.get("UnitFileState")
        if active == "active":
            r.add("systemd", OK if enabled in ("enabled", "static", "indirect") else WARN,
                  f"{u} active ({enabled})", "" if enabled == "enabled" else f"sudo systemctl enable {u}")
        elif active == "failed":
            r.add("systemd", FAIL, f"{u} FAILED", f"journalctl -u {u} -n 50")
        else:
            r.add("systemd", WARN, f"{u} {active} ({enabled})", f"sudo systemctl start {u}")
    for u in LEGACY_UNITS:
        st = states[u]
        if not present(u):
            continue
        live = st.get("ActiveState") == "active" or st.get("UnitFileState") == "enabled"
        if v2_present and live:
            r.add("systemd", FAIL, f"legacy {u} is still enabled/active alongside the v2 units",
                  f"two processes will fight for the node's single API socket: sudo systemctl disable --now {u}")
        elif live:
            r.add("systemd", OK, f"legacy {u} (pre-v2 — fine until deploy/install.sh runs)")


# --------------------------------------------------------------------------

def run_checks(explicit: Optional[str] = None, network: bool = True) -> Report:
    r = Report()
    check_python(r)
    cfg = check_config(r, explicit)
    check_deps(r, cfg)
    if cfg is None:
        return r
    check_database(r, cfg)
    check_content(r, cfg)
    check_radio(r, cfg, network=network)
    check_mqtt(r, cfg, network=network)
    check_systemd(r)
    return r


def run(args) -> int:
    """CLI entry (see wildcat.cli)."""
    r = run_checks(getattr(args, "config", None), network=not getattr(args, "no_network", False))
    color = sys.stdout.isatty() and not getattr(args, "no_color", False) and os.environ.get("TERM") != "dumb"
    print(r.render(color=color))
    if any(c.name == "config" and c.status == FAIL for c in r.checks):
        return 2
    return r.exit_code
