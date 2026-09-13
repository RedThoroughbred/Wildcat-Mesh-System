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
from typing import Any, Dict, Tuple

BROADCAST_NUM = 0xFFFFFFFF

PORT_KINDS = {
    "TEXT_MESSAGE_APP": "text",
    "POSITION_APP": "position",
    "TELEMETRY_APP": "telemetry",
    "NEIGHBORINFO_APP": "neighborinfo",
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
    env: Dict[str, Any] = {
        "kind": kind,
        "portnum": decoded.get("portnum"),
        "from": packet.get("from"),
        "fromId": packet.get("fromId"),
        "to": packet.get("to"),
        "toId": packet.get("toId"),
        "broadcast": packet.get("to") == BROADCAST_NUM,
        "channel": packet.get("channel", 0),
        "id": packet.get("id"),
        "rxTime": packet.get("rxTime"),
        "rxSnr": packet.get("rxSnr"),
        "rxRssi": packet.get("rxRssi"),
        "hopLimit": packet.get("hopLimit"),
        "hopStart": packet.get("hopStart"),
        "receivedAt": received_at,
        "packet": json_safe(packet),
    }
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


def packet_from_envelope(env: Dict[str, Any]) -> Dict[str, Any]:
    """The original packet dict, bytes restored — feed it to legacy handlers as-is."""
    return restore(env["packet"])
