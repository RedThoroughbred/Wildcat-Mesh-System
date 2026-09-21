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
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, List, Optional

from ..config import WildcatConfig
from .coverage import CoverageStore, coverage_point
from .scheduler import Scheduler
from .sos import SosController, is_distress
from .transport import TransportStore, annotate as annotate_transport

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


_EVIDENCE_DEFAULTS = {"rf_count": 0, "mqtt_count": 0, "last_rf": None, "last_mqtt": None, "transport": None, "rf_heard": False}


def _with_evidence_defaults(node: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in _EVIDENCE_DEFAULTS.items():
        node.setdefault(k, v)
    return node


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
        self.signal: Dict[str, Deque[Dict[str, Any]]] = {}      # per node: recent rx samples (live only)
        self.brain: Deque[Dict[str, Any]] = deque(maxlen=100)     # Ask-the-Cat exchanges (wildcat/brain/exchange)
        self.brain_status: Optional[Dict[str, Any]] = None         # the responder's retained wildcat/brain/status
        self.sos: Dict[str, Any] = {"active": False, "sent": 0, "ended": None}   # the SOS controller's status (mirrored)
        self.channels: List[Dict[str, Any]] = []                   # the Den node's channel slots (wildcat/meshd/channels)
        self.tx: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()  # operator sends by id: queued → sent → delivered | failed
        self._tx_by_packet: Dict[int, str] = {}                    # radio packet id → our send id (for ACK correlation)
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
                self.roster[nid] = annotate_transport(_with_evidence_defaults(merged))

    def apply_status(self, payload: Dict[str, Any]) -> None:
        with self.lock:
            self.meshd = payload
            if payload.get("myNodeNum") is not None and not self.my_id:
                self.my_id = f"!{int(payload['myNodeNum']):08x}"

    def _touch_node(self, nid: str, now: float) -> Dict[str, Any]:
        n = self.roster.get(nid)
        if n is None:
            n = {"id": nid, "proto": None, "short_name": nid[-4:], "long_name": None, "hw": None, "role": None,
                 "position": None, "hops_away": None, "snr": None, "battery": None, "voltage": None,
                 "rf_count": 0, "mqtt_count": 0, "last_rf": None, "last_mqtt": None, "transport": None, "rf_heard": False}
            self.roster[nid] = n
        n["last_heard"] = now
        return n

    def apply_transport(self, rows: List[Dict[str, Any]]) -> int:
        """Fold persisted RF/MQTT evidence (node_transport rows) into the roster at startup."""
        n = 0
        with self.lock:
            for r in rows:
                nid = r.get("node_id")
                if not nid:
                    continue
                node = self.roster.get(nid)
                if node is None:
                    node = self._touch_node(nid, 0)
                    node["last_heard"] = max(r.get("last_rf") or 0, r.get("last_mqtt") or 0) or None
                node["rf_count"] = (node.get("rf_count") or 0) + (r.get("rf_count") or 0)
                node["mqtt_count"] = (node.get("mqtt_count") or 0) + (r.get("mqtt_count") or 0)
                for k in ("last_rf", "last_mqtt"):
                    if r.get(k):
                        node[k] = max(node.get(k) or 0, r[k])
                annotate_transport(node)
                n += 1
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
            via = rx.get("via_mqtt") if isinstance(rx.get("via_mqtt"), bool) else None
            if via is True:
                node["mqtt_count"] = (node.get("mqtt_count") or 0) + 1; node["last_mqtt"] = now
            elif via is False:
                node["rf_count"] = (node.get("rf_count") or 0) + 1; node["last_rf"] = now
            annotate_transport(node)
            if via is True:
                # an internet-bridged packet says nothing about the radio path: no SNR sample,
                # no hops, no inferred direct link — only that the node exists
                rx = {k: v for k, v in rx.items() if k in ("time", "via_mqtt")}
            if rx.get("snr") is not None or rx.get("rssi") is not None:
                ring = self.signal.get(frm)
                if ring is None:
                    ring = self.signal[frm] = deque(maxlen=300)
                ring.append({"ts": int(env.get("received_at") or now), "snr": rx.get("snr"), "rssi": rx.get("rssi"), "hops": rx.get("hops")})
            if rx.get("snr") is not None:
                node["snr"] = rx["snr"]
            if rx.get("hops") is not None:
                node["hops_away"] = rx["hops"]
            if kind == "position" and (env.get("position") or {}).get("lat") is not None:
                p = env["position"]
                node["position"] = {"lat": p["lat"], "lon": p["lon"], "alt": p.get("alt")}
                node["position_ts"] = now
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
                "snr": rx.get("snr"), "rssi": rx.get("rssi"), "hops": rx.get("hops"), "via_mqtt": via,
                "summary": summarize(env),
            }
            if kind == "text":
                rec["text"] = env.get("text")
                if is_distress(env.get("text")):
                    rec["sos"] = True
            self.packets.append(rec)
            return {"packet": rec, "node": dict(node), "links": [dict(l) for l in links], "my_id": self.my_id}

    # ---- operator sends ------------------------------------------------------------------
    def new_tx(self, tx_id: str, to: Any, to_id: Optional[str], text: str, channel: int, now: float,
               broadcast: bool, sos: bool = False) -> Dict[str, Any]:
        with self.lock:
            rec = {"id": tx_id, "ts": now, "to": to_id, "to_name": (self.roster.get(to_id or "", {}).get("short_name") if to_id else None),
                   "broadcast": broadcast, "channel": channel, "text": text, "state": "queued", "packet_id": None,
                   "chunks": None, "sent_at": None, "delivered_at": None, "error": None, "acked_by": None, "sos": bool(sos)}
            self.tx[tx_id] = rec
            while len(self.tx) > 100:
                old_id, old_rec = self.tx.popitem(last=False)
                if old_rec.get("packet_id") is not None:
                    self._tx_by_packet.pop(old_rec["packet_id"], None)
            # the feed + threads see what we said, like any other packet
            pkt = {"ts": now, "kind": "text", "proto": None, "from": self.my_id, "from_name": "you", "to": to_id,
                   "to_name": rec["to_name"], "broadcast": broadcast, "channel": channel, "snr": None, "rssi": None,
                   "hops": None, "summary": text, "text": text, "sent": True, "tx_id": tx_id, "state": "queued"}
            if sos:
                pkt["sos"] = True
            self.packets.append(pkt)
            return dict(rec)

    def apply_tx_result(self, payload: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
        """meshd's wildcat/tx/result: per chunk {id, index, count, ok, packetId?, error?}."""
        tx_id = payload.get("id") if isinstance(payload, dict) else None
        with self.lock:
            rec = self.tx.get(tx_id) if tx_id else None
            if rec is None:
                return None
            if payload.get("ok"):
                rec["chunks"] = payload.get("count")
                if payload.get("packetId") is not None:
                    rec["packet_id"] = payload["packetId"]
                    self._tx_by_packet[payload["packetId"]] = tx_id
                if rec["state"] == "queued":
                    rec["state"] = "sent"; rec["sent_at"] = now
                    if rec["broadcast"]:
                        rec["state"] = "sent"          # broadcasts are never acked; 'sent' is final
            else:
                rec["state"] = "failed"; rec["error"] = payload.get("error") or "send failed"
            self._sync_packet(rec)
            return dict(rec)

    def apply_routing(self, env: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
        """A ROUTING_APP packet answering one of our sends = the mesh's ACK (or error)."""
        pkt = env.get("packet") if isinstance(env.get("packet"), dict) else {}
        dec = pkt.get("decoded") if isinstance(pkt.get("decoded"), dict) else {}
        req = dec.get("requestId")
        if not isinstance(req, int):
            return None
        with self.lock:
            tx_id = self._tx_by_packet.get(req)
            rec = self.tx.get(tx_id) if tx_id else None
            if rec is None or rec["state"] in ("delivered", "failed"):
                return None
            if rec.get("broadcast"):
                return None                      # broadcasts: any 'ack' is just a rebroadcast; 'sent' stays final
            reason = (dec.get("routing") or {}).get("errorReason") if isinstance(dec.get("routing"), dict) else None
            who = env.get("from")
            if reason not in (None, "NONE", 0):
                rec["state"] = "failed"; rec["error"] = f"routing: {reason}"
            elif who and rec.get("to") and who == rec["to"]:
                # the destination itself answered → really delivered
                rec["state"] = "delivered"; rec["delivered_at"] = now; rec["acked_by"] = who
            else:
                # Meshtastic's implicit ACK: we (or a neighbour) heard the packet rebroadcast.
                # Honest label: it's on the mesh, but the destination hasn't confirmed.
                if rec["state"] in ("queued", "sent"):
                    rec["state"] = "relayed"; rec["acked_by"] = who
            self._sync_packet(rec)
            return dict(rec)

    def _sync_packet(self, rec: Dict[str, Any]) -> None:
        for p in self.packets:
            if p.get("tx_id") == rec["id"]:
                p["state"] = rec["state"]

    def apply_brain(self, payload: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
        """An Ask-the-Cat exchange (Phase 2 publishes these on wildcat/brain/exchange):
        {node, prompt, reply, provider, latency_ms, chunks, ts, status, reason?, cost_usd?, rate_limited?}. Stored + echoed."""
        if not isinstance(payload, dict) or not payload.get("node"):
            return None
        with self.lock:
            rec = {"ts": payload.get("ts") or now, "node": payload["node"],
                   "node_name": self.roster.get(payload["node"], {}).get("short_name") or str(payload["node"])[-4:],
                   "prompt": str(payload.get("prompt") or "")[:500], "reply": str(payload.get("reply") or "")[:1000],
                   "provider": payload.get("provider"), "latency_ms": payload.get("latency_ms"),
                   "chunks": payload.get("chunks"), "rate_limited": bool(payload.get("rate_limited")),
                   "status": payload.get("status") or ("limited" if payload.get("rate_limited") else "sent"),
                   "reason": payload.get("reason"), "cost_usd": payload.get("cost_usd"), "tx_id": payload.get("tx_id")}
            self.brain.append(rec)
        return rec

    def apply_brain_status(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        with self.lock:
            self.brain_status = payload
        return payload

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
                "brain": list(self.brain), "brain_status": self.brain_status, "sos": dict(self.sos), "channels": list(self.channels), "tx": list(self.tx.values())[-30:],
                "stats": {"total": self.total, "by_kind": self.by_kind, "per_min": self.rate_per_min(now),
                          "nodes": len(self.roster), "heard_1h": heard_1h, "on_map": on_map},
            }


def node_detail(state: State, db_path: str, nid: str, hours: float = 24.0, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Everything the node card shows: the roster row, signal samples (live ring ∪ message_logs
    ∪ rx_points), telemetry history (telemetry_logs), and this node's recent packets."""
    now = time.time() if now is None else now
    since = int(now - hours * 3600)
    with state.lock:
        node = state.roster.get(nid)
        if node is None:
            return None
        node = dict(node)
        samples = {s["ts"]: dict(s) for s in state.signal.get(nid, ()) if s["ts"] >= since}
        packets = [p for p in state.packets if p["from"] == nid][-30:]
        links = []
        for l in state.links.values():
            if nid not in (l["a"], l["b"]):
                continue
            other = l["b"] if l["a"] == nid else l["a"]
            o = state.roster.get(other, {})
            links.append({"id": other, "name": o.get("short_name") or other[-4:], "kind": l["kind"], "snr": l.get("snr"),
                          "count": l.get("count"), "last": l.get("last"), "position": o.get("position"), "transport": o.get("transport")})
        links.sort(key=lambda x: -(x.get("last") or 0))
        base = state.roster.get(state.my_id or "", {})
        base = {"id": state.my_id, "name": base.get("short_name") or "Den", "position": base.get("position")} if state.my_id else None
    telemetry: List[Dict[str, Any]] = []
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=2)
            conn.row_factory = sqlite3.Row
            try:
                have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "message_logs" in have:
                    for r in conn.execute("SELECT timestamp ts, snr, rssi FROM message_logs WHERE sender_id=? AND timestamp>=?"
                                          " AND (snr IS NOT NULL OR rssi IS NOT NULL) ORDER BY timestamp DESC LIMIT 500", (nid, since)):
                        samples.setdefault(r["ts"], {"ts": r["ts"], "snr": r["snr"], "rssi": r["rssi"], "hops": None})
                if "rx_points" in have:
                    for r in conn.execute("SELECT ts, snr, rssi, hops FROM rx_points WHERE node_id=? AND ts>=? ORDER BY ts DESC LIMIT 500", (nid, since)):
                        samples.setdefault(r["ts"], {"ts": r["ts"], "snr": r["snr"], "rssi": r["rssi"], "hops": r["hops"]})
                if "telemetry_logs" in have:
                    telemetry = [dict(r) for r in conn.execute(
                        "SELECT timestamp ts, battery_level battery, voltage, channel_util, air_util_tx, temperature"
                        " FROM telemetry_logs WHERE node_id=? AND timestamp>=? ORDER BY timestamp DESC LIMIT 500", (nid, since))]
                    telemetry.reverse()
            finally:
                conn.close()
        except sqlite3.Error as e:
            log.warning("node_detail db: %s", e)
    signal = sorted(samples.values(), key=lambda s: s["ts"])
    return {"node": node, "since": since, "signal": signal, "telemetry": telemetry, "packets": packets, "links": links, "base": base,
            "counts": {"signal": len(signal), "telemetry": len(telemetry), "packets_24h": len(packets), "links": len(links)}}


HISTORY_KINDS = ("text", "telemetry", "position", "neighbors")


def history(db_path: str, since_ts: int, my_id: Optional[str] = None, limit: int = 6000) -> List[Dict[str, Any]]:
    """Every event the Den has recorded since ``since_ts``, oldest first, in the feed's
    packet shape (ts/kind/from/to/snr/rssi/summary) — from the tables the BBS and the
    telemetry logger already write. This is what the timeline scrubs and replay plays."""
    if not os.path.exists(db_path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            names: Dict[str, str] = {}
            if "node_info" in have:
                names = {r["node_id"]: r["short_name"] for r in conn.execute("SELECT node_id, short_name FROM node_info") if r["short_name"]}

            def nm(nid: Any) -> str:
                return names.get(nid) or (str(nid)[-4:] if nid else "?")

            if "message_logs" in have:
                for r in conn.execute("SELECT timestamp ts, sender_id, to_id, message, snr, rssi FROM message_logs"
                                      " WHERE timestamp >= ? ORDER BY timestamp LIMIT ?", (since_ts, limit)):
                    to = r["to_id"]
                    bcast = to in (4294967295, "4294967295", None)
                    to_id = None if bcast else (f"!{int(to):08x}" if isinstance(to, int) else str(to))
                    text = r["message"] or ""
                    out.append({"ts": r["ts"], "kind": "text", "from": r["sender_id"], "from_name": nm(r["sender_id"]),
                                "to": to_id, "to_name": nm(to_id) if to_id else None, "broadcast": bcast,
                                "snr": r["snr"], "rssi": r["rssi"], "hops": None, "text": text,
                                "summary": text if len(text) <= 160 else text[:157] + "…"})
            if "telemetry_logs" in have:
                for r in conn.execute("SELECT timestamp ts, node_id, battery_level b, voltage v, channel_util u FROM telemetry_logs"
                                      " WHERE timestamp >= ? ORDER BY timestamp LIMIT ?", (since_ts, limit)):
                    bits = []
                    if r["b"] is not None: bits.append(f"{int(r['b'])}%" if r["b"] <= 100 else "on power")
                    if r["v"] is not None: bits.append(f"{r['v']:.2f} V")
                    if r["u"] is not None: bits.append(f"util {r['u']:.1f}%")
                    out.append({"ts": r["ts"], "kind": "telemetry", "from": r["node_id"], "from_name": nm(r["node_id"]),
                                "to": None, "to_name": None, "broadcast": True, "snr": None, "rssi": None, "hops": None,
                                "summary": " · ".join(bits) or "telemetry"})
            if "position_logs" in have:
                for r in conn.execute("SELECT timestamp ts, node_id, latitude la, longitude lo, altitude al FROM position_logs"
                                      " WHERE timestamp >= ? AND latitude IS NOT NULL ORDER BY timestamp LIMIT ?", (since_ts, limit)):
                    alt = f" · {int(r['al'])} m" if r["al"] is not None else ""
                    out.append({"ts": r["ts"], "kind": "position", "from": r["node_id"], "from_name": nm(r["node_id"]),
                                "to": None, "to_name": None, "broadcast": True, "snr": None, "rssi": None, "hops": None,
                                "position": {"lat": r["la"], "lon": r["lo"]},
                                "summary": f"{r['la']:.5f}, {r['lo']:.5f}{alt}"})
            if "neighbor_info" in have:
                for r in conn.execute("SELECT timestamp ts, node_id, COUNT(*) n FROM neighbor_info WHERE timestamp >= ?"
                                      " GROUP BY timestamp, node_id ORDER BY timestamp LIMIT ?", (since_ts, limit)):
                    out.append({"ts": r["ts"], "kind": "neighbors", "from": r["node_id"], "from_name": nm(r["node_id"]),
                                "to": None, "to_name": None, "broadcast": True, "snr": None, "rssi": None, "hops": None,
                                "summary": f"hears {r['n']} neighbour{'s' if r['n'] != 1 else ''}"})
        finally:
            conn.close()
    except sqlite3.Error as e:
        log.warning("history db: %s", e)
        return []
    out.sort(key=lambda e: e["ts"])
    return out[-limit:]


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
                                                 "last_heard": r["timestamp"], **_EVIDENCE_DEFAULTS})
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
        self.sos = SosController(sender=lambda msg, ch: self.send("^all", msg, ch, priority=0, sos=True),
                                 publish=self._on_sos, name_fn=self._my_name, position_fn=self._my_position)
        self.transport = TransportStore(str(cfg.database.path))
        self._next_flush = 0.0
        self.health_fn: Any = None            # web.py sets this: () -> the health report dict (no Flask context needed)
        self.scheduler = Scheduler(str(cfg.database.path), {
            "digest_bulletin": self._job_digest_bulletin, "bulletin": self._job_bulletin, "broadcast": self._job_broadcast})
        self._next_sched = 0.0
        self.started_at = time.time()
        self.coverage = CoverageStore(str(self.cfg.database.path))

    def start(self) -> None:
        seeded = seed_from_db(self.state, str(self.cfg.database.path))
        log.info("observatory v2: %d node positions seeded from the database", seeded)
        try:
            self.coverage.ensure()
            n = self.coverage.backfill_history(self.state.my_id)
            log.info("observatory v2: coverage table ready, %d rx points backfilled from message/position history", n)
        except Exception:
            log.exception("coverage store init failed")
        try:
            n = self.state.apply_transport(self.transport.load())
            log.info("observatory v2: RF/MQTT evidence restored for %d nodes", n)
        except Exception:
            log.exception("transport store init failed")
        threading.Thread(target=self._sos_loop, name="v2-sos", daemon=True).start()
        if not self.cfg.mqtt.enabled:
            log.warning("observatory v2: [mqtt].enabled = false — live view is off, rendering from the database only")
            return
        from ..bus import MqttBus
        self.bus = MqttBus(self.cfg.mqtt, client_id=f"wildcat-observatory-{os.getpid()}")
        self.wire(self.bus)
        self.bus.start()
        threading.Thread(target=self._watch_bus, name="v2-bus-watch", daemon=True).start()

    def wire(self, bus: Any) -> None:
        """Subscribe everything the Observatory listens to (any Bus implementation)."""
        bus.subscribe("nodes", self._on_nodes)
        bus.subscribe("meshd/status", self._on_status)
        bus.subscribe("rx/+", self._on_rx)
        bus.subscribe("brain/exchange", self._on_brain)
        bus.subscribe("brain/status", self._on_brain_status)
        bus.subscribe("meshd/channels", self._on_channels)
        bus.subscribe("tx/result", self._on_tx_result)

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

    def _seed_links(self) -> None:
        """Rebuild direct links + NeighborInfo edges from the DB once we know who we are,
        so a restart doesn't blank the map's links and the Topology view."""
        if getattr(self, "_links_seeded", False) or not self.state.my_id:
            return
        self._links_seeded = True
        from . import queries as Q
        db = str(self.cfg.database.path)
        n = 0
        with self.state.lock:
            for r in Q.direct_links(db, 7):
                if r["id"] and r["id"] != self.state.my_id:
                    l = self.state._link(r["id"], self.state.my_id, r["snr"], "direct", float(r["last"]))
                    l["count"] = max(l["count"], int(r["count"])); n += 1
            for e in Q.neighbor_edges(db, 7):
                if e["a"] and e["b"] and e["a"] != e["b"]:
                    self.state._link(e["a"], e["b"], e["snr"], "neighbor", float(e["ts"])); n += 1
        if n:
            log.info("observatory v2: %d links seeded from the database", n)

    def _on_nodes(self, topic: str, payload: Dict[str, Any]) -> None:
        self.state.apply_roster(payload)
        self._seed_links()
        self._emit("roster", {"my_id": self.state.my_id, "roster": self.state.roster})

    def _on_status(self, topic: str, payload: Dict[str, Any]) -> None:
        self.state.apply_status(payload)
        self._seed_links()
        self._emit("status", {"bus": self.state.bus_connected, "meshd": payload, "my_id": self.state.my_id})

    def _on_tx_result(self, topic: str, payload: Dict[str, Any]) -> None:
        rec = self.state.apply_tx_result(payload, time.time())
        if rec:
            self._emit("tx", rec)

    def send(self, to: Any, text: str, channel: int = 0, wantAck: Optional[bool] = None, priority: int = 3,
             sos: bool = False) -> Dict[str, Any]:
        """The operator's one way out: a neutral TX request on the bus (meshd paces + chunks it).
        ``priority`` 0 is the most urgent (SOS); ordinary operator sends are 3."""
        if self.bus is None:
            raise RuntimeError("the bus is off ([mqtt].enabled = false) — nothing owns the radio")
        now = time.time()
        broadcast = to in ("^all", None, "", "broadcast")
        to_id = None if broadcast else str(to).strip()
        tx_id = f"{'sos' if sos else 'obs'}-{int(now * 1000)}"
        rec = self.state.new_tx(tx_id, to, to_id, text, channel, now, broadcast, sos=sos)
        self.bus.publish("tx", {"to": "^all" if broadcast else to_id, "text": text, "channel": channel,
                                "wantAck": (not broadcast) if wantAck is None else bool(wantAck), "priority": int(priority), "id": tx_id})
        self._record_outgoing(now, to_id, text, channel)
        self._emit("packet", {"packet": next(p for p in reversed(self.state.packets) if p.get("tx_id") == tx_id),
                              "node": None, "links": [], "my_id": self.state.my_id, "per_min": self.state.rate_per_min(now)})
        self._emit("tx", rec)
        return rec

    # ---- SOS ------------------------------------------------------------------------------
    def _my_name(self) -> str:
        my = self.state.my_id
        return (self.state.roster.get(my or "", {}).get("short_name") if my else None) or "the Den"

    def _my_position(self) -> Optional[Dict[str, Any]]:
        my = self.state.my_id
        pos = self.state.roster.get(my or "", {}).get("position") if my else None
        return pos if isinstance(pos, dict) else None

    def _on_sos(self, st: Dict[str, Any]) -> None:
        with self.state.lock:
            self.state.sos = dict(st)
        if self.bus is not None:
            try:
                self.bus.publish("alert/sos", st, retain=True)
            except Exception:
                log.exception("could not publish the SOS state")
        self._emit("sos", st)

    def _sos_loop(self) -> None:
        """The bridge's one timer thread: SOS repeats every second, scheduled posts every 30 s."""
        while True:
            time.sleep(1.0)
            try:
                self.sos.tick()
            except Exception as e:
                log.error("SOS repeat failed: %s", e)
            now = time.time()
            if now >= self._next_flush:
                self._next_flush = now + 15
                try:
                    self.transport.flush(now)
                except Exception as e:
                    log.warning("could not persist RF/MQTT evidence: %s", e)
            if now >= self._next_sched:
                self._next_sched = now + 30
                try:
                    for r in self.scheduler.tick(now):
                        log.info("scheduled %s: %s", r.get("name"), "ok" if r.get("ok") else r.get("error"))
                        self._emit("schedule", r)
                except Exception:
                    log.exception("scheduler tick failed")

    # ---- scheduled jobs ----------------------------------------------------------------
    def _health(self) -> Optional[Dict[str, Any]]:
        try:
            return self.health_fn() if self.health_fn else None
        except Exception:
            return None

    def _job_digest_bulletin(self, p: Dict[str, Any]) -> Dict[str, Any]:
        from ..brain import digest as DG
        rec = DG.write(str(self.cfg.database.path), self.state, self.cfg.brain.cli_model, self.cfg.brain.cli_timeout,
                       hours=int(p.get("hours") or 24), health=self._health(), source="schedule")
        out = DG.post_bulletin(str(self.cfg.database.path), rec["id"], p.get("board") or "General", self._my_name())
        return {"digest_id": rec["id"], "fallback": rec.get("fallback"), "cost_usd": rec.get("cost_usd"), **out}

    def _job_bulletin(self, p: Dict[str, Any]) -> Dict[str, Any]:
        from ..brain import digest as DG
        return DG.bbs_post(str(self.cfg.database.path), p.get("board") or "General", p.get("subject") or "", p.get("content") or "", self._my_name())

    def _job_broadcast(self, p: Dict[str, Any]) -> Dict[str, Any]:
        rec = self.send("^all", p["text"], int(p.get("channel") or 0))
        return {"tx_id": rec["id"]}

    def _record_outgoing(self, now: float, to_id: Optional[str], text: str, channel: int) -> None:
        """Mirror what the BBS does for its replies: log our outgoing text so the Messages
        threads, replay and exports include the operator's side of the conversation."""
        db = str(self.cfg.database.path)
        if not os.path.exists(db):
            return
        my = self.state.my_id or "unknown"
        name = self.state.roster.get(my, {}).get("short_name") or "Den"
        to_num = 4294967295
        if to_id and to_id.startswith("!"):
            try:
                to_num = int(to_id[1:], 16)
            except ValueError:
                pass
        try:
            conn = sqlite3.connect(db, timeout=3)
            try:
                conn.execute("PRAGMA busy_timeout = 3000")
                conn.execute("INSERT INTO message_logs (timestamp, sender_id, sender_short_name, to_id, channel_index, message, snr, rssi, hop_limit)"
                             " VALUES (?,?,?,?,?,?,?,?,?)", (int(now), my, name, to_num, channel, text, None, None, None))
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as e:
            log.warning("could not log outgoing message: %s", e)

    def _record_incoming(self, env: Dict[str, Any], now: float) -> None:
        """Persist a received channel (broadcast) text into message_logs so the per-channel view, the
        conversations view and the Pico's history have it. Without this the Den only *displays* what it
        hears (live feed / packet ring) and only logs what it sends -- the old BBS did the receiving side.
        Skipped when the Den's own node IS the BBS node (the BBS is then logging the same packets), and a
        row that is already there (same sender / channel / text within 10 s) is never doubled."""
        if env.get("kind") != "text" or not env.get("broadcast"):
            return
        frm, text, my = env.get("from"), env.get("text"), self.state.my_id
        if not isinstance(frm, str) or not frm or not text or frm == my or my == self.cfg.observatory.bbs_node_id:
            return
        db = str(self.cfg.database.path)
        if not os.path.exists(db):
            return
        rx = env.get("rx") if isinstance(env.get("rx"), dict) else {}
        if rx.get("via_mqtt") is True:      # an internet-bridged packet carries no measurement of *our* radio path
            rx = {}
        ts = int(env.get("received_at") or now)
        channel = env.get("channel") if isinstance(env.get("channel"), int) and not isinstance(env.get("channel"), bool) else 0
        name = self.state.roster.get(frm, {}).get("short_name") or frm[-4:]
        try:
            conn = sqlite3.connect(db, timeout=3)
            try:
                conn.execute("PRAGMA busy_timeout = 3000")
                if conn.execute("SELECT 1 FROM message_logs WHERE sender_id=? AND channel_index=? AND message=?"
                                " AND ABS(timestamp-?) <= 10 LIMIT 1", (frm, channel, text, ts)).fetchone():
                    return
                conn.execute("INSERT INTO message_logs (timestamp, sender_id, sender_short_name, to_id, channel_index, message, snr, rssi, hop_limit)"
                             " VALUES (?,?,?,?,?,?,?,?,?)", (ts, frm, name, 4294967295, channel, text, rx.get("snr"), rx.get("rssi"), env.get("hopLimit")))
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as e:
            log.warning("could not log received channel message: %s", e)

    def _on_rx(self, topic: str, env: Dict[str, Any]) -> None:
        now = time.time()
        if env.get("kind") == "routing":
            ack = self.state.apply_routing(env, now)
            if ack:
                self._emit("tx", ack)
        ev = self.state.apply_packet(env, now)
        if ev:
            self._record_incoming(env, now)
            if isinstance(ev["packet"].get("via_mqtt"), bool):
                self.transport.note(ev["packet"]["from"], ev["packet"]["via_mqtt"], now)
            ev["per_min"] = self.state.rate_per_min(now)
            self._emit("packet", ev)
            pt = coverage_point(env, ev.get("node"), now, self.state.my_id)
            if pt:
                try:
                    if self.coverage.add(pt):
                        self._emit("rxpoint", pt)
                except Exception:
                    log.exception("could not store rx point")

    def _on_brain(self, topic: str, payload: Dict[str, Any]) -> None:
        rec = self.state.apply_brain(payload, time.time())
        if rec:
            self._emit("brain", rec)

    def _on_channels(self, topic: str, payload: Dict[str, Any]) -> None:
        chans = payload.get("channels") if isinstance(payload, dict) else None
        if isinstance(chans, list):
            with self.state.lock:
                self.state.channels = [c for c in chans if isinstance(c, dict)]
            self._emit("channels", {"channels": self.state.channels})

    def _on_brain_status(self, topic: str, payload: Dict[str, Any]) -> None:
        st = self.state.apply_brain_status(payload)
        if st is not None:
            self._emit("brain_status", st)

    def set_responder(self, enabled: bool, by: str = "observatory") -> None:
        """The operator's switch: a retained control message the running responder honours
        immediately (the caller also patches wildcat.toml so a restart agrees)."""
        if self.bus is None:
            raise RuntimeError("the bus is off ([mqtt].enabled = false) — Bobcat needs meshd + the bus")
        self.bus.publish("brain/control", {"enabled": bool(enabled), "by": by, "ts": time.time()}, retain=True)

    def snapshot(self) -> Dict[str, Any]:
        snap = self.state.snapshot(time.time())
        snap["uptime"] = time.time() - self.started_at
        snap["mqtt_enabled"] = self.cfg.mqtt.enabled
        return snap
