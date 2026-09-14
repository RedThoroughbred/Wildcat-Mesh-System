"""Packet dicts (as the meshtastic Python lib hands them to ``meshtastic.receive``)
→ bus envelopes, and back.

Everything here is pure and JSON-symmetric: ``restore(json_safe(p)) == p`` for
the shapes meshtastic produces (nested dicts/lists, str/int/float/bool/None,
and ``bytes`` payloads). Consumers that already understand meshtastic packets
(the BBS's ``on_receive``, the telemetry ``log_*`` functions) get the original
packet back byte-for-byte from ``envelope["packet"]``.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, Optional, Tuple

BROADCAST_NUM = 0xFFFFFFFF

ENVELOPE_VERSION = 2      # v2 (2026-09-14): rx.via_mqtt (bool) on meshtastic packets; roster.via_mqtt. Additive — v1 consumers ignore it.
PROTO = "meshtastic"

PORT_KINDS = {
    "TEXT_MESSAGE_APP": "text",
    "POSITION_APP": "position",
    "TELEMETRY_APP": "telemetry",
    "NEIGHBORINFO_APP": "neighbors",
    "NODEINFO_APP": "nodeinfo",
    "ROUTING_APP": "routing",
    "TRACEROUTE_APP": "traceroute",
    "RANGE_TEST_APP": "rangetest",
    "STORE_FORWARD_APP": "storeforward",
    "ADMIN_APP": "admin",
}
_BYTES_KEY = "__bytes_b64__"


def json_safe(obj: Any) -> Any:
    """Recursively make a packet JSON-serializable. bytes → {"__bytes_b64__": "..."}."""
    if isinstance(obj, bytes):
        return {_BYTES_KEY: base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # protobuf enums / other objects: fall back to their string form
    return str(obj)


def restore(obj: Any) -> Any:
    """Inverse of :func:`json_safe`."""
    if isinstance(obj, dict):
        if len(obj) == 1 and _BYTES_KEY in obj:
            return base64.b64decode(obj[_BYTES_KEY])
        return {k: restore(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [restore(v) for v in obj]
    return obj


def kind_of(packet: Dict[str, Any]) -> str:
    decoded = packet.get("decoded")
    if not isinstance(decoded, dict):
        return "encrypted"          # no decoded section = not for us / unknown key
    return PORT_KINDS.get(str(decoded.get("portnum", "")), "other")


def envelope(packet: Dict[str, Any], received_at: float) -> Tuple[str, Dict[str, Any]]:
    """``(kind, envelope)``. The envelope is what goes on ``wildcat/rx/<kind>``."""
    kind = kind_of(packet)
    decoded = packet.get("decoded") if isinstance(packet.get("decoded"), dict) else {}
    env: Dict[str, Any] = neutral_fields(packet, kind)
    env["received_at"] = received_at
    # meshtastic-specific extras (legacy consumers + BusInterface use these; the UI never does)
    env.update({
        "portnum": decoded.get("portnum"),
        "from_num": packet.get("from"),
        "to_num": packet.get("to"),
        "id": packet.get("id"),
        "hopLimit": packet.get("hopLimit"),
        "hopStart": packet.get("hopStart"),
        "receivedAt": received_at,
        "packet": json_safe(packet),
    })
    if kind == "text":
        text = decoded.get("text")
        if text is None:
            raw = decoded.get("payload")
            if isinstance(raw, bytes):
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("utf-8", errors="replace")
        env["text"] = text
    return kind, env


# --------------------------------------------------------------------------
# The protocol-NEUTRAL fields (docs/OBSERVATORY_V2.md §2c). Every producer —
# meshd today, a MeshCore adapter later — fills these; the UI reads only these.
# --------------------------------------------------------------------------

def node_id(num: Any) -> Optional[str]:
    """Meshtastic node num → canonical '!xxxxxxxx' string id (None if unknown)."""
    if isinstance(num, bool) or not isinstance(num, int) or num < 0:
        return None
    if num == BROADCAST_NUM:
        return None
    return f"!{num:08x}"


def _num(v: Any) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def neutral_fields(packet: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """The neutral part of an envelope for a meshtastic packet."""
    decoded = packet.get("decoded") if isinstance(packet.get("decoded"), dict) else {}
    hop_start, hop_limit = _num(packet.get("hopStart")), _num(packet.get("hopLimit"))
    hops = int(hop_start - hop_limit) if hop_start is not None and hop_limit is not None and hop_start >= hop_limit else None
    to_num = packet.get("to")
    out: Dict[str, Any] = {
        "v": ENVELOPE_VERSION,
        "proto": PROTO,
        "kind": kind,
        "from": packet.get("fromId") or node_id(packet.get("from")),
        "to": None if to_num == BROADCAST_NUM else (packet.get("toId") if packet.get("toId") not in (None, "^all") else node_id(to_num)),
        "broadcast": to_num == BROADCAST_NUM,
        "channel": packet.get("channel", 0),
        "rx": {"time": _num(packet.get("rxTime")), "snr": _num(packet.get("rxSnr")),
               "rssi": _num(packet.get("rxRssi")), "hops": hops,
               # True when the node got this packet from its MQTT uplink, not the radio. Meshtastic
               # only serialises the flag when set, so absent == heard over RF. Producers for other
               # protocols omit the key entirely (unknown), never guess.
               "via_mqtt": bool(packet.get("viaMqtt"))},
    }
    if kind == "position":
        pos = decoded.get("position") if isinstance(decoded.get("position"), dict) else {}
        lat, lon = pos.get("latitude"), pos.get("longitude")
        if lat is None and isinstance(pos.get("latitudeI"), int):
            lat = pos["latitudeI"] / 1e7
        if lon is None and isinstance(pos.get("longitudeI"), int):
            lon = pos["longitudeI"] / 1e7
        out["position"] = {"lat": _num(lat), "lon": _num(lon), "alt": _num(pos.get("altitude")),
                           "sats": _num(pos.get("satsInView")), "time": _num(pos.get("time"))}
    elif kind == "telemetry":
        tel = decoded.get("telemetry") if isinstance(decoded.get("telemetry"), dict) else {}
        dm = tel.get("deviceMetrics") if isinstance(tel.get("deviceMetrics"), dict) else {}
        em = tel.get("environmentMetrics") if isinstance(tel.get("environmentMetrics"), dict) else {}
        out["telemetry"] = {
            "battery": _num(dm.get("batteryLevel")), "voltage": _num(dm.get("voltage")),
            "channel_util": _num(dm.get("channelUtilization")), "air_util_tx": _num(dm.get("airUtilTx")),
            "uptime": _num(dm.get("uptimeSeconds")),
            "temperature": _num(em.get("temperature")), "humidity": _num(em.get("relativeHumidity")),
            "pressure": _num(em.get("barometricPressure")),
        }
    elif kind == "neighbors":
        ni = decoded.get("neighborinfo") if isinstance(decoded.get("neighborinfo"), dict) else {}
        out["neighbors"] = [
            {"id": node_id(n.get("nodeId")), "snr": _num(n.get("snr")), "last_heard": _num(n.get("lastRxTime"))}
            for n in (ni.get("neighbors") or []) if isinstance(n, dict) and node_id(n.get("nodeId"))
        ]
    elif kind == "nodeinfo":
        u = decoded.get("user") if isinstance(decoded.get("user"), dict) else {}
        out["nodeinfo"] = {"id": u.get("id") or out["from"], "short_name": u.get("shortName"),
                           "long_name": u.get("longName"), "hw": u.get("hwModel"), "role": u.get("role")}
    return out


def roster_entry(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One neutral roster row from a meshtastic ``interface.nodes`` value."""
    if not isinstance(node, dict):
        return None
    user = node.get("user") if isinstance(node.get("user"), dict) else {}
    nid = user.get("id") or node_id(node.get("num"))
    if not nid:
        return None
    pos = node.get("position") if isinstance(node.get("position"), dict) else {}
    lat, lon = pos.get("latitude"), pos.get("longitude")
    if lat is None and isinstance(pos.get("latitudeI"), int):
        lat = pos["latitudeI"] / 1e7
    if lon is None and isinstance(pos.get("longitudeI"), int):
        lon = pos["longitudeI"] / 1e7
    dm = node.get("deviceMetrics") if isinstance(node.get("deviceMetrics"), dict) else {}
    return {
        "id": nid, "proto": PROTO,
        "short_name": user.get("shortName"), "long_name": user.get("longName"),
        "hw": user.get("hwModel"), "role": user.get("role"),
        "last_heard": _num(node.get("lastHeard")), "hops_away": _num(node.get("hopsAway")),
        "snr": _num(node.get("snr")),
        "via_mqtt": bool(node.get("viaMqtt")),      # the radio's node DB: this entry was last learned over MQTT
        "position": {"lat": _num(lat), "lon": _num(lon), "alt": _num(pos.get("altitude"))} if _num(lat) is not None and _num(lon) is not None else None,
        "position_ts": _num(pos.get("time")) if _num(lat) is not None else None,
        "battery": _num(dm.get("batteryLevel")), "voltage": _num(dm.get("voltage")),
        "channel_util": _num(dm.get("channelUtilization")), "air_util_tx": _num(dm.get("airUtilTx")),
    }


def roster(nodes: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for v in (nodes or {}).values():
        e = roster_entry(v)
        if e:
            out[e["id"]] = e
    return out


def packet_from_envelope(env: Dict[str, Any]) -> Dict[str, Any]:
    """The original packet dict, bytes restored — feed it to legacy handlers as-is."""
    return restore(env["packet"])
