"""Per-node radio signal views for the signal dashboard (/v2/signal) -- pure functions, no Flask, no I/O.

The Den already keeps a rolling in-memory ring of RF samples per node (`state.signal[nid]`, appended in
`bridge.State.apply_packet`: ts / snr / rssi / hops, RF packets only -- internet-bridged packets say nothing
about the radio path). This module reads those rings (plus, for a node's history, the DB rows the Den has
already logged) and shapes them for the dashboard and, being small and ASCII-safe, for tiny clients too.

Classes: strong / ok / weak, from SNR when we have it, else RSSI. LoRa decodes far below the noise floor
(LongFast to about -17 dB SNR), so "weak" is a link that is approaching that edge, not a dead one.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from .compact import ascii_clean

STRONG_SNR = 0.0        # >= 0 dB SNR: healthy
WEAK_SNR = -10.0        # <  -10 dB SNR: marginal
STRONG_RSSI = -100.0    # fallbacks when a sample carries RSSI but no SNR
WEAK_RSSI = -115.0

THRESHOLDS = {"strong_snr": STRONG_SNR, "weak_snr": WEAK_SNR, "strong_rssi": STRONG_RSSI, "weak_rssi": WEAK_RSSI}

HIST_EDGES = [-15, -10, -5, 0, 5, 10]       # 7 buckets: <-15, -15..-10, ..., >=10
SPARK_POINTS = 24
HISTORY_MAX = 600


def _num(v: Any, digits: int = 2) -> Any:
    """Numbers only (bools and junk -> None); Meshtastic SNR is in quarter-dB steps, so keep 2 decimals."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v) if float(v).is_integer() else round(v, digits)


def classify(snr: Any, rssi: Any = None) -> Optional[str]:
    """'strong' | 'ok' | 'weak', or None when there is no measurement."""
    if isinstance(snr, (int, float)) and not isinstance(snr, bool):
        return "strong" if snr >= STRONG_SNR else ("ok" if snr >= WEAK_SNR else "weak")
    if isinstance(rssi, (int, float)) and not isinstance(rssi, bool):
        return "strong" if rssi >= STRONG_RSSI else ("ok" if rssi >= WEAK_RSSI else "weak")
    return None


def _has_pos(node: Optional[Dict[str, Any]]) -> bool:
    pos = (node or {}).get("position")
    return (isinstance(pos, dict) and isinstance(pos.get("lat"), (int, float))
            and isinstance(pos.get("lon"), (int, float)))


def bearing_km(base: Tuple[float, float], p: Tuple[float, float]) -> Tuple[float, float]:
    """(bearing degrees 0..359 clockwise from north, great-circle km) from base to p."""
    la1, lo1, la2, lo2 = map(math.radians, (base[0], base[1], p[0], p[1]))
    dlo = lo2 - lo1
    a = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    km = 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dlo) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0, km


def _last(ring: Any) -> Optional[Dict[str, Any]]:
    return ring[-1] if ring else None


def _hist(snrs: Iterable[float]) -> List[int]:
    counts = [0] * (len(HIST_EDGES) + 1)
    for s in snrs:
        i = 0
        while i < len(HIST_EDGES) and s >= HIST_EDGES[i]:
            i += 1
        counts[i] += 1
    return counts


def signal_overview(roster: Dict[str, Dict[str, Any]], signal: Dict[str, Any], my_id: Optional[str],
                    now: float, max_age: Optional[float] = None) -> Dict[str, Any]:
    """Every RF-heard node with its latest SNR/RSSI, class, trend spark and (when both ends have a
    position) bearing/distance from the base -- worst link first -- plus the summary strip.

    Internet-only (MQTT) nodes carry an upstream SNR that says nothing about *our* radio path, so they
    are counted but not listed. A node is listed when it is RF/both, or when we hold RF samples for it.
    `max_age` (seconds) drops nodes not heard within that window (counted in summary["older"]): a node
    last heard days ago still carries its old SNR, which says nothing about the link today.
    """
    base = roster.get(my_id or "")
    base_pos = (base["position"]["lat"], base["position"]["lon"]) if _has_pos(base) else None
    rows: List[Dict[str, Any]] = []
    internet_only = 0
    older = 0
    for nid, node in roster.items():
        if nid == my_id:
            continue
        ring = signal.get(nid)
        tr = node.get("transport")
        rf = tr in ("rf", "both") or bool(ring)
        if not rf:
            internet_only += 1 if tr == "mqtt" else 0
            continue
        heard0 = node.get("last_heard")
        if max_age is not None and not (isinstance(heard0, (int, float)) and not isinstance(heard0, bool) and now - heard0 <= max_age):
            older += 1
            continue
        last = _last(ring)
        snr = _num(last.get("snr")) if last else _num(node.get("snr"))
        rssi = _num(last.get("rssi")) if last else None
        heard = node.get("last_heard")
        heard = heard if isinstance(heard, (int, float)) and not isinstance(heard, bool) else None
        vals = [s["snr"] for s in (list(ring)[-10:] if ring else ()) if isinstance(s.get("snr"), (int, float))]
        spark = [s.get("snr") for s in (list(ring)[-SPARK_POINTS:] if ring else ())]
        row: Dict[str, Any] = {
            "id": nid,
            "s": ascii_clean(node.get("short_name") or nid[-4:], 8) or nid[-4:],
            "l": ascii_clean(node.get("long_name"), 24),
            "snr": snr, "rssi": rssi, "cls": classify(snr, rssi),
            "hops": _num(node.get("hops_away")), "tr": tr if tr in ("rf", "mqtt", "both") else None,
            "age": None if heard is None else max(0, int(now - heard)),
            "sage": max(0, int(now - last["ts"])) if last and isinstance(last.get("ts"), (int, float)) else None,
            "n": len(ring) if ring else 0,
            "avg": round(sum(vals) / len(vals), 1) if vals else None,
            "spark": [None if v is None else _num(v) for v in spark],
        }
        if _has_pos(node) and base_pos:
            brg, km = bearing_km(base_pos, (node["position"]["lat"], node["position"]["lon"]))
            row.update({"lat": _num(node["position"]["lat"], 4), "lon": _num(node["position"]["lon"], 4),
                        "brg": round(brg), "km": round(km, 2)})
        elif _has_pos(node):
            row.update({"lat": _num(node["position"]["lat"], 4), "lon": _num(node["position"]["lon"], 4)})
        rows.append(row)

    rows.sort(key=lambda r: (r["snr"] is None, r["snr"] if r["snr"] is not None else 0.0))   # worst first, unmeasured last
    measured = [r for r in rows if r["snr"] is not None]
    summary: Dict[str, Any] = {
        "rf": len(rows), "measured": len(measured), "internet_only": internet_only, "older": older,
        "avg_snr": round(sum(r["snr"] for r in measured) / len(measured), 1) if measured else None,
        "best": ({"id": measured[-1]["id"], "s": measured[-1]["s"], "snr": measured[-1]["snr"]} if measured else None),
        "worst": ({"id": measured[0]["id"], "s": measured[0]["s"], "snr": measured[0]["snr"]} if measured else None),
        "hist": {"edges": HIST_EDGES, "counts": _hist(r["snr"] for r in measured)},
        "classes": {c: sum(1 for r in rows if r["cls"] == c) for c in ("strong", "ok", "weak")},
    }
    me: Dict[str, Any] = {"id": my_id, "s": ascii_clean((base or {}).get("short_name"), 8)}
    if base_pos:
        me.update({"lat": _num(base_pos[0], 4), "lon": _num(base_pos[1], 4)})
    return {"now": int(now), "max_age": max_age, "my": me, "thresholds": THRESHOLDS, "summary": summary, "nodes": rows}


def _db_samples(db_path: Optional[str], nid: str, since: int) -> List[Dict[str, Any]]:
    """History the Den already logged (survives a restart): message SNR/RSSI and positioned rx points."""
    out: List[Dict[str, Any]] = []
    if not db_path:
        return out
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        try:
            have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "message_logs" in have:
                for ts, snr, rssi in conn.execute(
                        "SELECT timestamp, snr, rssi FROM message_logs WHERE sender_id=? AND timestamp>=?"
                        " AND (snr IS NOT NULL OR rssi IS NOT NULL) ORDER BY timestamp DESC LIMIT 500", (nid, since)):
                    out.append({"ts": int(ts), "snr": snr, "rssi": rssi, "hops": None})
            if "rx_points" in have:
                for ts, snr, rssi, hops in conn.execute(
                        "SELECT ts, snr, rssi, hops FROM rx_points WHERE node_id=? AND ts>=?"
                        " AND (snr IS NOT NULL OR rssi IS NOT NULL) ORDER BY ts DESC LIMIT 500", (nid, since)):
                    out.append({"ts": int(ts), "snr": snr, "rssi": rssi, "hops": hops})
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return out


def signal_history(node: Dict[str, Any], ring: Any, db_rows: Iterable[Dict[str, Any]], now: float,
                   minutes: float = 30.0, after: Optional[float] = None) -> Dict[str, Any]:
    """One node's SNR/RSSI samples over time (oldest first) + stats -- the antenna-tuning feed.

    Live ring samples are authoritative; DB rows only fill seconds the ring does not cover (a fresh
    restart). `after` returns only samples at/after that timestamp (cheap live polling; the client
    de-duplicates the boundary sample).
    """
    minutes = max(1.0, min(24 * 60.0, float(minutes)))
    since = int(now - minutes * 60)
    if after is not None:
        since = max(since, int(after))
    samples: Dict[Tuple[int, Any, Any], Dict[str, Any]] = {}
    ring_ts = set()
    for s in (ring or ()):
        ts = s.get("ts")
        if isinstance(ts, (int, float)) and ts >= since:
            samples[(int(ts), s.get("snr"), s.get("rssi"))] = {"ts": int(ts), "snr": s.get("snr"), "rssi": s.get("rssi"), "hops": s.get("hops")}
            ring_ts.add(int(ts))
    if after is None:
        for s in db_rows:
            if s["ts"] >= since and s["ts"] not in ring_ts:
                samples.setdefault((s["ts"], s.get("snr"), s.get("rssi")), s)
    ordered = sorted(samples.values(), key=lambda s: s["ts"])[-HISTORY_MAX:]
    snrs = [s["snr"] for s in ordered if isinstance(s.get("snr"), (int, float))]
    rssis = [s["rssi"] for s in ordered if isinstance(s.get("rssi"), (int, float))]
    latest = ordered[-1] if ordered else (ring[-1] if ring else None)
    snr = _num(latest.get("snr")) if latest else _num(node.get("snr"))
    rssi = _num(latest.get("rssi")) if latest else None
    heard = node.get("last_heard")
    stats: Dict[str, Any] = {"n": len(ordered)}
    if snrs:
        recent = snrs[-5:]
        stats.update({"snr_min": _num(min(snrs)), "snr_max": _num(max(snrs)), "snr_avg": round(sum(snrs) / len(snrs), 1),
                      "snr_recent": round(sum(recent) / len(recent), 1)})
    if rssis:
        stats.update({"rssi_min": _num(min(rssis)), "rssi_max": _num(max(rssis)), "rssi_avg": round(sum(rssis) / len(rssis), 1)})
    return {
        "id": node.get("id"), "s": ascii_clean(node.get("short_name") or (node.get("id") or "")[-4:], 8),
        "l": ascii_clean(node.get("long_name"), 24), "now": int(now), "minutes": minutes,
        "tr": node.get("transport"), "hops": _num(node.get("hops_away")),
        "age": None if not isinstance(heard, (int, float)) else max(0, int(now - heard)),
        "snr": snr, "rssi": rssi, "cls": classify(snr, rssi),
        "samples": [[s["ts"], _num(s.get("snr")), _num(s.get("rssi")), _num(s.get("hops"))] for s in ordered],
        "stats": stats,
    }
