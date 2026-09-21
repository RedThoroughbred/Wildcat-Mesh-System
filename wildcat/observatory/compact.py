"""Compact, ASCII-only, read-only views of the Den for tiny clients (the Pico panel).

Pure functions: they take the live roster / packet ring / DB rows and return small dicts.
No Flask, no I/O, so they are unit-tested without a broker or a radio. The web layer
(`web.py`) only wires them to `/v2/api/nodes/compact`, `/v2/api/messages/recent` and
`/v2/api/channels/compact`.

Why: `/v2/api/nodes` is ~28 KB and `/v2/api/state` ~43 KB. A microcontroller with a
few KB of RX buffer can't read those, can't render emoji, and doesn't want long keys
repeated 80 times. Everything here is capped, ASCII, and short-keyed.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, Iterable, List, Optional

from .sos import is_distress

NODE_LIMIT_MAX = 40
MSG_LIMIT_MAX = 40
SHORT_MAX = 8
LONG_MAX = 24
TEXT_MAX = 160


def ascii_clean(value: Any, maxlen: int) -> str:
    """Fold to plain ASCII (é→e, emoji dropped), collapse whitespace, cap the length."""
    if value is None:
        return ""
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    s = " ".join(s.split())
    return s[:maxlen]


def _num(v: Any, digits: Optional[int] = None) -> Any:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return round(v, digits) if digits is not None else (int(v) if float(v).is_integer() else round(v, 1))


def _clamp(limit: Any, default: int, hard_max: int) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = default
    return max(1, min(hard_max, n))


def _has_pos(node: Dict[str, Any]) -> bool:
    pos = node.get("position")
    return (isinstance(pos, dict) and _num(pos.get("lat"), 4) is not None
            and _num(pos.get("lon"), 4) is not None)


def compact_nodes(roster: Dict[str, Dict[str, Any]], signal: Dict[str, Any], my_id: Optional[str],
                  now: float, limit: Any = 24, transport: str = "all",
                  pos_only: bool = False) -> Dict[str, Any]:
    """The most recently heard nodes, newest first, minimal fields.

    `transport="rf"` keeps only nodes actually heard over the radio (rf/both) -- on a busy mesh most
    of the roster is internet-only, which is rarely what a handheld wants to scroll.
    Fields per node: id, s (short), l (long), snr, rssi (latest live sample), hops, bat (%), hw,
    tr (rf|mqtt|both|null), age (secs since heard), lat/lon (4 dp) when known.
    `pos_only` keeps only nodes with a position (for the map). `withpos` counts every roster node
    (base excluded) that has a position, whatever `limit` / `pos_only` / `transport` say.
    """
    n = _clamp(limit, 24, NODE_LIMIT_MAX)
    rows: List[Dict[str, Any]] = []
    withpos = 0
    for nid, node in roster.items():
        if nid == my_id:
            continue
        has_pos = _has_pos(node)
        withpos += 1 if has_pos else 0
        if pos_only and not has_pos:
            continue
        tr = node.get("transport")
        if transport == "rf" and tr not in ("rf", "both"):
            continue
        heard = node.get("last_heard")
        heard = heard if isinstance(heard, (int, float)) and not isinstance(heard, bool) else None
        ring = signal.get(nid)
        last = ring[-1] if ring else None
        pos = node.get("position") if isinstance(node.get("position"), dict) else None
        row: Dict[str, Any] = {
            "id": nid,
            "s": ascii_clean(node.get("short_name") or nid[-4:], SHORT_MAX) or nid[-4:],
            "l": ascii_clean(node.get("long_name"), LONG_MAX),
            "snr": _num(node.get("snr")),
            "rssi": _num(last.get("rssi")) if isinstance(last, dict) else None,
            "hops": _num(node.get("hops_away")),
            "bat": _num(node.get("battery")),
            "hw": ascii_clean(node.get("hw"), 16),
            "tr": tr if tr in ("rf", "mqtt", "both") else None,
            "age": None if heard is None else max(0, int(now - heard)),
        }
        if has_pos:
            row["lat"] = _num(pos["lat"], 4)
            row["lon"] = _num(pos["lon"], 4)
        row["_h"] = heard if heard is not None else -1.0
        rows.append(row)
    rows.sort(key=lambda r: r["_h"], reverse=True)
    for r in rows:
        del r["_h"]
    me = roster.get(my_id or "", {})
    my: Dict[str, Any] = {"id": my_id, "s": ascii_clean(me.get("short_name"), SHORT_MAX),
                          "l": ascii_clean(me.get("long_name"), LONG_MAX),
                          "bat": _num(me.get("battery")), "volt": _num(me.get("voltage"), 2)}
    if _has_pos(me):
        my["lat"] = _num(me["position"]["lat"], 4)
        my["lon"] = _num(me["position"]["lon"], 4)
    return {
        "now": int(now),
        "my": my,
        "total": len(roster),
        "withpos": withpos,
        "n": rows[:n],
    }


def compact_messages(packets: Iterable[Dict[str, Any]], db_rows: Iterable[Dict[str, Any]],
                     my_id: Optional[str], now: float, limit: Any = 20, maxlen: int = TEXT_MAX) -> Dict[str, Any]:
    """Recent text messages, newest first: the live packet ring first, then DB rows for history.

    Fields per message: f (from id), s (short name), to (node id, or null for a channel broadcast),
    c (channel), t (text, ASCII, capped), age (secs), me (we sent it), snr, rssi.
    sos: true is present only on distress messages from others (never on our own sends).
    The same message present in both sources (a send is in the ring *and* logged) is listed once.
    """
    n = _clamp(limit, 20, MSG_LIMIT_MAX)
    maxlen = max(20, min(200, int(maxlen)))
    out: List[Dict[str, Any]] = []
    seen: Dict[Any, List[float]] = {}

    def add(ts: Any, frm: Optional[str], short: Any, to: Optional[str], channel: Any, text: Any,
            mine: bool, snr: Any, rssi: Any, flagged: bool = False) -> None:
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            return
        t = ascii_clean(text, maxlen)
        if not t:
            return
        prior = seen.setdefault((frm or "", t), [])   # ring copy vs DB copy of one message: within 3 s
        if any(abs(ts - x) <= 3 for x in prior):
            return
        prior.append(float(ts))
        out.append({
            "f": frm, "s": ascii_clean(short, SHORT_MAX) or (frm or "")[-4:], "to": to,
            "c": channel if isinstance(channel, int) and not isinstance(channel, bool) else 0,
            "t": t, "age": max(0, int(now - ts)), "me": bool(mine), "snr": _num(snr), "rssi": _num(rssi),
            "_ts": float(ts),
        })
        if not mine and (flagged or is_distress(text)):
            out[-1]["sos"] = True

    for p in packets:
        if p.get("kind") != "text":
            continue
        to = None if p.get("broadcast") else p.get("to")
        add(p.get("ts"), p.get("from"), p.get("from_name"), to, p.get("channel"), p.get("text"),
            bool(p.get("sent")) or (bool(my_id) and p.get("from") == my_id), p.get("snr"), p.get("rssi"),
            p.get("sos") is True)
    for r in db_rows:
        add(r.get("ts"), r.get("sender_id"), r.get("short_name"), None if r.get("broadcast") else r.get("to"),
            r.get("channel"), r.get("text"), bool(r.get("mine")), r.get("snr"), r.get("rssi"))

    out.sort(key=lambda m: m["_ts"], reverse=True)
    for m in out:
        del m["_ts"]
    return {"now": int(now), "my": my_id, "m": out[:n]}


def compact_channels(configured: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """The Den node's channel slots: index, name, role. Disabled/empty slots are dropped; never any key."""
    rows = []
    for c in configured:
        role = c.get("role")
        if role in (None, "DISABLED", "DISABLE"):
            continue
        idx = c.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool):
            continue
        rows.append({"i": idx, "n": ascii_clean(c.get("name"), 12) or ("ch%d" % idx),
                     "r": "P" if role == "PRIMARY" else "S"})
    return {"c": rows}
