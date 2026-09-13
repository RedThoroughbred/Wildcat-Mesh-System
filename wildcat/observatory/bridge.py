"""Bus → State → browsers.

:class:`State` is pure (no I/O): it folds neutral envelopes (docs/OBSERVATORY_V2.md
§2c) into a roster, a link table, a packet ring and counters, and returns the
delta each packet caused so the UI can animate exactly that. :class:`Bridge`
wires it to the MQTT bus and to Flask-SocketIO. The UI never sees a protocol-
specific field; a MeshCore adapter publishing the same envelope lights up the
same map.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from ..config import WildcatConfig

log = logging.getLogger("wildcat.observatory")

NAMESPACE = "/v2"
KINDS = ("text", "position", "telemetry", "neighbors", "nodeinfo", "routing", "traceroute", "rangetest",
         "storeforward", "admin", "other", "encrypted")


def _link_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def summarize(env: Dict[str, Any]) -> str:
    """One human line per packet for the feed."""
    k = env.get("kind")
    if k == "text":
        t = env.get("text") or ""
        return t if len(t) <= 160 else t[:157] + "…"
    if k == "position":
        p = env.get("position") or {}
        if p.get("lat") is not None:
            alt = f" · {int(p['alt'])} m" if p.get("alt") is not None else ""
            return f"{p['lat']:.5f}, {p['lon']:.5f}{alt}"
        return "position (no fix)"
    if k == "telemetry":
        t = env.get("telemetry") or {}
        bits = []
        if t.get("battery") is not None:
            bits.append(f"{int(t['battery'])}%" if t["battery"] <= 100 else "on power")
        if t.get("voltage") is not None:
            bits.append(f"{t['voltage']:.2f} V")
        if t.get("channel_util") is not None:
            bits.append(f"util {t['channel_util']:.1f}%")
        if t.get("temperature") is not None:
            bits.append(f"{t['temperature']:.1f} °C")
        return " · ".join(bits) or "telemetry"
    if k == "neighbors":
        n = env.get("neighbors") or []
        return f"hears {len(n)} neighbour{'s' if len(n) != 1 else ''}"
    if k == "nodeinfo":
        i = env.get("nodeinfo") or {}
        return f"{i.get('short_name') or ''} · {i.get('long_name') or ''} · {i.get('hw') or ''}".strip(" ·")
    if k == "encrypted":
        return "encrypted (not our channel)"
    return env.get("portnum") or k or "packet"


class State:
    def __init__(self, ring: int = 200, link_ttl: float = 6 * 3600) -> None:
        self.my_id: Optional[str] = None
        self.meshd: Dict[str, Any] = {"state": "unknown"}
        self.bus_connected = False
        self.roster: Dict[str, Dict[str, Any]] = {}
        self.links: Dict[str, Dict[str, Any]] = {}
        self.packets: Deque[Dict[str, Any]] = deque(maxlen=ring)
        self.link_ttl = link_ttl
        self.total = 0
        self.by_kind: Dict[str, int] = {}
        self._times: Deque[float] = deque(maxlen=5000)
        self.lock = threading.RLock()

    # ---- folding ---------------------------------------------------------------------
    def apply_roster(self, payload: Dict[str, Any]) -> None:
        with self.lock:
            if payload.get("my_id"):
                self.my_id = payload["my_id"]
            roster = payload.get("roster")
            if not isinstance(roster, dict):
                return
            for nid, entry in roster.items():
                if not isinstance(entry, dict):
                    continue
                cur = self.roster.get(nid, {})
                merged = {**cur, **{k: v for k, v in entry.items() if v is not None}}
                if entry.get("position") is None and cur.get("position"):
                    merged["position"] = cur["position"]
                self.roster[nid] = merged

    def apply_status(self, payload: Dict[str, Any]) -> None:
        with self.lock:
            self.meshd = payload
            if payload.get("myNodeNum") is not None and not self.my_id:
                self.my_id = f"!{int(payload['myNodeNum']):08x}"

    def _touch_node(self, nid: str, now: float) -> Dict[str, Any]:
        n = self.roster.get(nid)
        if n is None:
            n = {"id": nid, "proto": None, "short_name": nid[-4:], "long_name": None, "hw": None, "role": None,
                 "position": None, "hops_away": None, "snr": None, "battery": None, "voltage": None}
            self.roster[nid] = n
        n["last_heard"] = now
        return n

    def _link(self, a: str, b: str, snr: Optional[float], kind: str, now: float) -> Dict[str, Any]:
        key = _link_key(a, b)
        l = self.links.get(key)
        if l is None:
            l = {"key": key, "a": a, "b": b, "count": 0, "kind": kind, "snr": None, "last": now}
            self.links[key] = l
        l["count"] += 1
        l["last"] = now
        if snr is not None:
            l["snr"] = snr
        if kind == "neighbor":            # a NeighborInfo report outranks inference
            l["kind"] = "neighbor"
        return l

    def apply_packet(self, env: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
        """Fold one envelope; return the delta event for the UI (or None if unusable)."""
        frm = env.get("from")
        if not isinstance(frm, str) or not frm:
            return None
        kind = env.get("kind") or "other"
        rx = env.get("rx") if isinstance(env.get("rx"), dict) else {}
        with self.lock:
            self.total += 1
            self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
            self._times.append(now)
            node = self._touch_node(frm, now)
            if env.get("proto"):
                node["proto"] = env["proto"]
            if rx.get("snr") is not None:
                node["snr"] = rx["snr"]
            if rx.get("hops") is not None:
                node["hops_away"] = rx["hops"]
            if kind == "position" and (env.get("position") or {}).get("lat") is not None:
                p = env["position"]
                node["position"] = {"lat": p["lat"], "lon": p["lon"], "alt": p.get("alt")}
            elif kind == "telemetry":
                t = env.get("telemetry") or {}
                for k in ("battery", "voltage", "channel_util", "air_util_tx"):
                    if t.get(k) is not None:
                        node[k] = t[k]
            elif kind == "nodeinfo":
                i = env.get("nodeinfo") or {}
                for src, dst in (("short_name", "short_name"), ("long_name", "long_name"), ("hw", "hw"), ("role", "role")):
                    if i.get(src):
                        node[dst] = i[src]
            links: List[Dict[str, Any]] = []
            if rx.get("hops") == 0 and self.my_id and frm != self.my_id:
                links.append(self._link(frm, self.my_id, rx.get("snr"), "direct", now))
            if kind == "neighbors":
                for nb in env.get("neighbors") or []:
                    if isinstance(nb, dict) and nb.get("id") and nb["id"] != frm:
                        self._touch_node(nb["id"], self.roster.get(nb["id"], {}).get("last_heard") or 0) if nb["id"] not in self.roster else None
                        links.append(self._link(frm, nb["id"], nb.get("snr"), "neighbor", now))
            to = env.get("to")
            rec = {
                "ts": env.get("received_at") or now, "kind": kind, "proto": env.get("proto"),
                "from": frm, "from_name": node.get("short_name") or frm[-4:],
                "to": to, "to_name": (self.roster.get(to, {}).get("short_name") if to else None),
                "broadcast": bool(env.get("broadcast")), "channel": env.get("channel"),
                "snr": rx.get("snr"), "rssi": rx.get("rssi"), "hops": rx.get("hops"),
                "summary": summarize(env),
            }
            if kind == "text":
                rec["text"] = env.get("text")
            self.packets.append(rec)
            return {"packet": rec, "node": dict(node), "links": [dict(l) for l in links], "my_id": self.my_id}

    # ---- views --------------------------------------------------------------------------
    def rate_per_min(self, now: float) -> float:
        cutoff = now - 60
        return float(sum(1 for t in self._times if t >= cutoff))

    def prune_links(self, now: float) -> None:
        with self.lock:
            dead = [k for k, l in self.links.items() if now - l["last"] > self.link_ttl]
            for k in dead:
                del self.links[k]

    def snapshot(self, now: float) -> Dict[str, Any]:
        with self.lock:
            self.prune_links(now)
            hour = now - 3600
            heard_1h = sum(1 for n in self.roster.values() if (n.get("last_heard") or 0) >= hour)
            on_map = sum(1 for n in self.roster.values() if n.get("position"))
            return {
                "now": now, "my_id": self.my_id, "meshd": self.meshd, "bus": self.bus_connected,
                "roster": self.roster, "links": list(self.links.values()), "packets": list(self.packets),
                "stats": {"total": self.total, "by_kind": self.by_kind, "per_min": self.rate_per_min(now),
                          "nodes": len(self.roster), "heard_1h": heard_1h, "on_map": on_map},
            }


def seed_from_db(state: State, db_path: str) -> int:
    """Positions/names from the shared DB for nodes the retained roster lacks (and for
    bus-off mode). Returns the number of rows folded in."""
    if not os.path.exists(db_path):
        return 0
    n = 0
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT p.node_id, p.latitude, p.longitude, p.altitude, p.timestamp,
                       i.short_name, i.long_name, i.hw_model, i.role
                FROM position_logs p LEFT JOIN node_info i ON i.node_id = p.node_id
                WHERE p.timestamp = (SELECT MAX(timestamp) FROM position_logs WHERE node_id = p.node_id)
                  AND p.latitude IS NOT NULL""").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        log.warning("seed_from_db: %s", e)
        return 0
    with state.lock:
        for r in rows:
            nid = r["node_id"]
            if not nid or not str(nid).startswith("!"):
                continue
            node = state.roster.setdefault(nid, {"id": nid, "proto": None, "short_name": r["short_name"] or nid[-4:],
                                                 "long_name": r["long_name"], "hw": r["hw_model"], "role": r["role"],
                                                 "hops_away": None, "snr": None, "battery": None, "voltage": None,
                                                 "last_heard": r["timestamp"]})
            if not node.get("position"):
                node["position"] = {"lat": r["latitude"], "lon": r["longitude"], "alt": r["altitude"]}
                n += 1
    return n


class Bridge:
    """Runtime glue: MQTT subscriptions → State → Socket.IO namespace /v2."""

    def __init__(self, cfg: WildcatConfig, socketio: Any) -> None:
        self.cfg = cfg
        self.socketio = socketio
        self.state = State()
        self.bus: Any = None
        self.started_at = time.time()

    def start(self) -> None:
        seeded = seed_from_db(self.state, str(self.cfg.database.path))
        log.info("observatory v2: %d node positions seeded from the database", seeded)
        if not self.cfg.mqtt.enabled:
            log.warning("observatory v2: [mqtt].enabled = false — live view is off, rendering from the database only")
            return
        from ..bus import MqttBus
        self.bus = MqttBus(self.cfg.mqtt, client_id=f"wildcat-observatory-{os.getpid()}")
        self.bus.subscribe("nodes", self._on_nodes)
        self.bus.subscribe("meshd/status", self._on_status)
        self.bus.subscribe("rx/+", self._on_rx)
        self.bus.start()
        threading.Thread(target=self._watch_bus, name="v2-bus-watch", daemon=True).start()

    def _watch_bus(self) -> None:
        last = None
        while True:
            cur = bool(self.bus.connected.is_set())
            if cur != last:
                self.state.bus_connected = cur
                self._emit("status", {"bus": cur, "meshd": self.state.meshd, "my_id": self.state.my_id})
                last = cur
            time.sleep(1)

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        try:
            self.socketio.emit(event, data, namespace=NAMESPACE)
        except Exception:
            log.exception("emit %s failed", event)

    def _on_nodes(self, topic: str, payload: Dict[str, Any]) -> None:
        self.state.apply_roster(payload)
        self._emit("roster", {"my_id": self.state.my_id, "roster": self.state.roster})

    def _on_status(self, topic: str, payload: Dict[str, Any]) -> None:
        self.state.apply_status(payload)
        self._emit("status", {"bus": self.state.bus_connected, "meshd": payload, "my_id": self.state.my_id})

    def _on_rx(self, topic: str, env: Dict[str, Any]) -> None:
        now = time.time()
        ev = self.state.apply_packet(env, now)
        if ev:
            ev["per_min"] = self.state.rate_per_min(now)
            self._emit("packet", ev)

    def snapshot(self) -> Dict[str, Any]:
        snap = self.state.snapshot(time.time())
        snap["uptime"] = time.time() - self.started_at
        snap["mqtt_enabled"] = self.cfg.mqtt.enabled
        return snap
