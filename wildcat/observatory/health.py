"""Mesh health: gauges + alerts, computed from the live State and the DB. Pure where it
matters (``compute`` takes plain inputs) so the thresholds are unit-tested.

Alerts (each: {level: "warn"|"crit", kind, node?, text}):
  util      base channel utilization ≥ 25 % warn, ≥ 40 % crit (community norm: stay under ~25 %)
  airtime   base air-time TX ≥ 10 % warn (we are talking too much)
  battery   a battery-powered node under 20 % warn, under 10 % crit (latest telemetry)
  quiet     a node that was regular (≥ 3 packets in the previous 24 h) but silent for ≥ 2 h
  meshd     radio not connected / bus down
  silence   nothing heard at all for ≥ 15 min while the radio says connected
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

UTIL_WARN, UTIL_CRIT = 25.0, 40.0
AIR_WARN = 10.0
BATT_WARN, BATT_CRIT = 20, 10
QUIET_AFTER = 2 * 3600
REGULAR_MIN = 3
SILENCE_AFTER = 15 * 60


def compute(now: float, my_id: Optional[str], roster: Dict[str, Dict[str, Any]], meshd: Dict[str, Any],
            bus_connected: bool, rate_buckets: List[Dict[str, Any]], low_battery: List[Dict[str, Any]],
            regular_counts: Dict[str, int], last_packet_ts: Optional[float]) -> Dict[str, Any]:
    alerts: List[Dict[str, Any]] = []
    me = roster.get(my_id or "", {})
    util, air = me.get("channel_util"), me.get("air_util_tx")
    if util is not None:
        if util >= UTIL_CRIT: alerts.append({"level": "crit", "kind": "util", "text": f"Channel utilization {util:.0f}% — the mesh is saturated; hold off on non-essential traffic"})
        elif util >= UTIL_WARN: alerts.append({"level": "warn", "kind": "util", "text": f"Channel utilization {util:.0f}% — above the ~25% community comfort line"})
    if air is not None and air >= AIR_WARN:
        alerts.append({"level": "warn", "kind": "airtime", "text": f"This node's air-time TX is {air:.1f}% — we're transmitting a lot (BBS/brain replies?)"})
    for b in low_battery:
        lvl = "crit" if (b.get("battery") or 0) < BATT_CRIT else "warn"
        nm = roster.get(b["id"], {}).get("short_name") or str(b["id"])[-4:]
        alerts.append({"level": lvl, "kind": "battery", "node": b["id"], "text": f"{nm} battery {b['battery']}%" + (f" · {b['voltage']:.2f} V" if b.get("voltage") is not None else "")})
    quiet: List[Dict[str, Any]] = []
    for nid, n in roster.items():
        if nid == my_id: continue
        cnt = regular_counts.get(nid, 0); lh = n.get("last_heard") or 0
        if cnt >= REGULAR_MIN and lh and now - lh >= QUIET_AFTER and now - lh < 24 * 3600:
            quiet.append({"id": nid, "name": n.get("short_name") or nid[-4:], "silent_for": int(now - lh), "was": cnt})
    quiet.sort(key=lambda q: -q["silent_for"])
    for q in quiet[:6]:
        alerts.append({"level": "warn", "kind": "quiet", "node": q["id"], "text": f"{q['name']} has gone quiet — {q['silent_for'] // 3600}h {q['silent_for'] % 3600 // 60}m since its last packet (was {q['was']} pkts/day)"})
    if not bus_connected:
        alerts.append({"level": "crit", "kind": "meshd", "text": "MQTT bus is down — the Observatory is blind"})
    elif meshd.get("state") != "connected":
        alerts.append({"level": "crit", "kind": "meshd", "text": f"meshd reports the radio is {meshd.get('state', 'unknown')}" + (f": {meshd['error']}" if meshd.get("error") else "")})
    elif last_packet_ts and now - last_packet_ts >= SILENCE_AFTER:
        alerts.append({"level": "warn", "kind": "silence", "text": f"Nothing heard for {int((now - last_packet_ts) // 60)} min while the radio says connected — antenna? node rebooted?"})
    order = {"crit": 0, "warn": 1}
    alerts.sort(key=lambda a: order[a["level"]])
    score = 100 - sum(25 if a["level"] == "crit" else 8 for a in alerts)
    return {"now": now, "score": max(0, score), "level": "crit" if any(a["level"] == "crit" for a in alerts) else "warn" if alerts else "ok",
            "gauges": {"channel_util": util, "air_util_tx": air, "battery": me.get("battery"), "voltage": me.get("voltage")},
            "rate": rate_buckets, "alerts": alerts, "quiet": quiet, "low_battery": low_battery,
            "counts": {"alerts": len(alerts), "crit": sum(1 for a in alerts if a["level"] == "crit")}}


def rate_buckets(events_ts: List[float], now: float, hours: float = 6.0, buckets: int = 72) -> List[Dict[str, Any]]:
    """Packets per bucket over the window (for the trend sparkline), oldest first."""
    since = now - hours * 3600; span = hours * 3600
    out = [{"t": since + span * (i + 0.5) / buckets, "n": 0} for i in range(buckets)]
    for ts in events_ts:
        if ts < since or ts > now: continue
        i = min(buckets - 1, int((ts - since) / span * buckets)); out[i]["n"] += 1
    return out


def regular_counts(events: List[Dict[str, Any]], now: float) -> Dict[str, int]:
    """Packets per node in the window 26h→2h ago (what 'regular' meant before it went quiet)."""
    lo, hi = now - 26 * 3600, now - QUIET_AFTER
    out: Dict[str, int] = {}
    for e in events:
        ts = e.get("ts") or 0
        if lo <= ts < hi and e.get("from"):
            out[e["from"]] = out.get(e["from"], 0) + 1
    return out
