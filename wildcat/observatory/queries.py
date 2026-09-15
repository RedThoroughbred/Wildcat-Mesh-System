"""Read-only analytics over the shared DB — the v1 Observatory's queries, ported.

Self-contained (path in, dicts out) so the /v2 views don't import v1 code and the
whole thing is testable against a temp database. Node ids are opaque strings, so a
MeshCore source that logs into the same tables shows up identically.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from typing import Any, Dict, List, Optional

BROADCAST = 4294967295


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=3)
    conn.execute("PRAGMA busy_timeout = 3000")
    conn.row_factory = sqlite3.Row
    return conn


def _rows(db_path: str, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    try:
        with closing(_conn(db_path)) as c:
            return [dict(r) for r in c.execute(sql, args)]
    except sqlite3.Error:
        return []


def _one(db_path: str, sql: str, args: tuple = ()) -> Optional[Dict[str, Any]]:
    r = _rows(db_path, sql, args)
    return r[0] if r else None


def mesh_stats(db_path: str, now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    d, h = int(now - 86400), int(now - 3600)
    s = _one(db_path, "SELECT COUNT(*) n, AVG(snr) snr FROM message_logs WHERE timestamp >= ?", (d,)) or {}
    a = _one(db_path, "SELECT COUNT(DISTINCT sender_id) n FROM message_logs WHERE timestamp >= ?", (h,)) or {}
    t = _one(db_path, "SELECT COUNT(DISTINCT sender_id) n FROM message_logs") or {}
    return {"messages_24h": s.get("n") or 0, "avg_snr": round(s["snr"], 1) if s.get("snr") is not None else None,
            "active_nodes": a.get("n") or 0, "total_nodes": t.get("n") or 0}


def node_stats(db_path: str) -> Dict[str, Dict[str, Any]]:
    """Per-node message statistics (the v1 nodes table), keyed by node id."""
    rows = _rows(db_path, """
        SELECT m.sender_id id, MAX(m.sender_short_name) short_name, i.long_name, i.hw_model hw, i.role,
               COUNT(*) message_count, MIN(m.timestamp) first_seen, MAX(m.timestamp) last_seen,
               AVG(m.snr) avg_snr, MAX(m.snr) best_snr, MIN(m.snr) worst_snr, AVG(m.rssi) avg_rssi
        FROM message_logs m LEFT JOIN node_info i ON i.node_id = m.sender_id
        GROUP BY m.sender_id""")
    out = {}
    for r in rows:
        for k in ("avg_snr", "avg_rssi"):
            if r[k] is not None:
                r[k] = round(r[k], 1)
        out[r["id"]] = r
    return out


def node_messages(db_path: str, node_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    return _rows(db_path, "SELECT timestamp ts, message text, snr, rssi, channel_index channel, to_id"
                          " FROM message_logs WHERE sender_id = ? ORDER BY timestamp DESC LIMIT ?", (node_id, limit))


def node_reliability(db_path: str, node_id: str, days: int = 7) -> Dict[str, Any]:
    """Connection quality: share of messages by SNR band + per-day counts."""
    since = int(time.time() - days * 86400)
    rows = _rows(db_path, "SELECT snr, rssi, timestamp FROM message_logs WHERE sender_id = ? AND timestamp >= ?", (node_id, since))
    bands = {"excellent": 0, "good": 0, "fair": 0, "poor": 0, "unknown": 0}
    per_day: Dict[str, int] = {}
    for r in rows:
        s = r["snr"]
        if s is None: bands["unknown"] += 1
        elif s >= 8: bands["excellent"] += 1
        elif s >= 3: bands["good"] += 1
        elif s >= -3: bands["fair"] += 1
        else: bands["poor"] += 1
        day = time.strftime("%Y-%m-%d", time.localtime(r["timestamp"]))
        per_day[day] = per_day.get(day, 0) + 1
    n = len(rows)
    return {"messages": n, "bands": bands,
            "reliability_pct": round(100 * (bands["excellent"] + bands["good"]) / n) if n else None,
            "per_day": [{"day": d, "count": c} for d, c in sorted(per_day.items())]}


def channel_activity(db_path: str, hours: int = 24) -> List[Dict[str, Any]]:
    since = int(time.time() - hours * 3600)
    return _rows(db_path, "SELECT channel_index channel, COUNT(*) count FROM message_logs"
                          " WHERE timestamp >= ? AND channel_index IS NOT NULL GROUP BY channel_index ORDER BY count DESC", (since,))


def channel_details(db_path: str, hours: int = 24) -> List[Dict[str, Any]]:
    since = int(time.time() - hours * 3600)
    rows = _rows(db_path, """
        SELECT channel_index channel, COUNT(*) message_count, COUNT(DISTINCT sender_id) unique_senders,
               AVG(snr) avg_snr, MAX(timestamp) last_message, MIN(timestamp) first_message
        FROM message_logs WHERE timestamp >= ? AND channel_index IS NOT NULL
        GROUP BY channel_index ORDER BY message_count DESC""", (since,))
    for r in rows:
        if r["avg_snr"] is not None:
            r["avg_snr"] = round(r["avg_snr"], 1)
    return rows


def top_senders(db_path: str, hours: int = 24, limit: int = 10) -> List[Dict[str, Any]]:
    since = int(time.time() - hours * 3600)
    rows = _rows(db_path, """
        SELECT sender_id id, MAX(sender_short_name) short_name, COUNT(*) message_count, AVG(snr) avg_snr
        FROM message_logs WHERE timestamp >= ? GROUP BY sender_id ORDER BY message_count DESC LIMIT ?""", (since, limit))
    for r in rows:
        if r["avg_snr"] is not None:
            r["avg_snr"] = round(r["avg_snr"], 1)
    return rows


def hourly_activity(db_path: str, hours: int = 24) -> List[Dict[str, Any]]:
    """Messages by local hour-of-day × channel (the channels heatmap)."""
    since = int(time.time() - hours * 3600)
    return _rows(db_path, """
        SELECT CAST(strftime('%H', datetime(timestamp, 'unixepoch', 'localtime')) AS INTEGER) hour,
               channel_index channel, COUNT(*) count
        FROM message_logs WHERE timestamp >= ? AND channel_index IS NOT NULL
        GROUP BY hour, channel_index ORDER BY hour, channel_index""", (since,))


def channel_messages(db_path: str, channel: int, hours: int = 24, limit: int = 500) -> List[Dict[str, Any]]:
    since = int(time.time() - hours * 3600)
    return _rows(db_path, """
        SELECT timestamp ts, sender_id, sender_short_name short_name, message text, snr, rssi
        FROM message_logs WHERE channel_index = ? AND timestamp >= ? AND (to_id = ? OR to_id = ?)
        ORDER BY timestamp DESC LIMIT ?""", (channel, since, BROADCAST, str(BROADCAST), limit))


def chat_messages(db_path: str, my_id: Optional[str], hours: int = 168, limit: int = 1500) -> List[Dict[str, Any]]:
    """Every text message in the window — DMs both ways and channel broadcasts — shaped for
    the conversations view: {ts, sender_id, short_name, text, snr, rssi, to, channel, broadcast, mine}."""
    since = int(time.time() - hours * 3600)
    rows = _rows(db_path, """
        SELECT timestamp ts, sender_id, sender_short_name short_name, message text, snr, rssi, to_id, channel_index channel
        FROM message_logs WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?""", (since, limit))
    for r in rows:
        to = r.pop("to_id")
        r["broadcast"] = to in (BROADCAST, str(BROADCAST), None)
        r["to"] = None if r["broadcast"] else (f"!{int(to):08x}" if isinstance(to, int) else str(to))
        r["mine"] = bool(my_id) and r["sender_id"] == my_id
        r["channel"] = r["channel"] if isinstance(r["channel"], int) else 0
    return rows


def bbs_messages(db_path: str, bbs_node_id: Optional[str], hours: int = 168, limit: int = 500) -> List[Dict[str, Any]]:
    """Direct messages (not broadcasts) — the conversations with the Den."""
    since = int(time.time() - hours * 3600)
    rows = _rows(db_path, """
        SELECT timestamp ts, sender_id, sender_short_name short_name, message text, snr, rssi, to_id
        FROM message_logs WHERE to_id != ? AND to_id != ? AND timestamp >= ? ORDER BY timestamp DESC LIMIT ?""",
        (BROADCAST, str(BROADCAST), since, limit))
    for r in rows:
        r["from_bbs"] = bool(bbs_node_id) and r["sender_id"] == bbs_node_id
        to = r.pop("to_id")
        r["to"] = f"!{int(to):08x}" if isinstance(to, int) else (str(to) if to is not None else None)
    return rows


def hourly_snr_trends(db_path: str, days: int = 7) -> List[Dict[str, Any]]:
    since = int(time.time() - days * 86400)
    rows = _rows(db_path, """
        SELECT CAST(strftime('%H', datetime(timestamp, 'unixepoch', 'localtime')) AS INTEGER) hour,
               AVG(snr) avg_snr, AVG(rssi) avg_rssi, COUNT(*) message_count, COUNT(DISTINCT sender_id) node_count
        FROM message_logs WHERE timestamp >= ? AND snr IS NOT NULL GROUP BY hour ORDER BY hour""", (since,))
    for r in rows:
        r["avg_snr"] = round(r["avg_snr"], 1); r["avg_rssi"] = round(r["avg_rssi"], 1) if r["avg_rssi"] is not None else None
    return rows


def best_worst(db_path: str, days: int = 7, limit: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    since = int(time.time() - days * 86400)
    q = "SELECT sender_id id, sender_short_name short_name, snr, rssi, timestamp ts FROM message_logs WHERE timestamp >= ? AND snr IS NOT NULL ORDER BY snr {} LIMIT ?"
    return {"best": _rows(db_path, q.format("DESC"), (since, limit)), "worst": _rows(db_path, q.format("ASC"), (since, limit))}


def snr_distribution(db_path: str, days: int = 7) -> List[Dict[str, Any]]:
    since = int(time.time() - days * 86400)
    return _rows(db_path, "SELECT CAST(snr AS INTEGER) snr, COUNT(*) count FROM message_logs"
                          " WHERE timestamp >= ? AND snr IS NOT NULL GROUP BY CAST(snr AS INTEGER) ORDER BY snr", (since,))


def neighbor_edges(db_path: str, days: int = 7) -> List[Dict[str, Any]]:
    since = int(time.time() - days * 86400)
    return _rows(db_path, """
        SELECT ni.node_id a, ni.neighbor_id b, ni.snr, ni.timestamp ts, n1.short_name a_name, n2.short_name b_name
        FROM neighbor_info ni LEFT JOIN node_info n1 ON n1.node_id = ni.node_id LEFT JOIN node_info n2 ON n2.node_id = ni.neighbor_id
        WHERE ni.timestamp = (SELECT MAX(timestamp) FROM neighbor_info WHERE node_id = ni.node_id AND neighbor_id = ni.neighbor_id)
          AND ni.timestamp > ? ORDER BY ni.timestamp DESC""", (since,))


def low_battery(db_path: str, threshold: int = 20, limit: int = 10) -> List[Dict[str, Any]]:
    return _rows(db_path, """
        SELECT node_id id, battery_level battery, voltage, timestamp ts FROM telemetry_logs t
        WHERE battery_level IS NOT NULL AND battery_level < ? AND timestamp = (SELECT MAX(timestamp) FROM telemetry_logs WHERE node_id = t.node_id)
        ORDER BY battery_level LIMIT ?""", (threshold, limit))


def recent_logs(db_path: str, kind: str = "messages", limit: int = 100) -> List[Dict[str, Any]]:
    """The admin 'live logs' feed: messages | telemetry | positions | neighbors."""
    limit = max(1, min(500, limit))
    if kind == "telemetry":
        return _rows(db_path, "SELECT timestamp ts, node_id id, battery_level battery, voltage, channel_util, air_util_tx, temperature FROM telemetry_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
    if kind == "positions":
        return _rows(db_path, "SELECT timestamp ts, node_id id, latitude lat, longitude lon, altitude alt, satellites_in_view sats FROM position_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
    if kind == "neighbors":
        return _rows(db_path, "SELECT timestamp ts, node_id id, neighbor_id neighbor, snr FROM neighbor_info ORDER BY timestamp DESC LIMIT ?", (limit,))
    return _rows(db_path, "SELECT timestamp ts, sender_id id, sender_short_name short_name, to_id, channel_index channel, message text, snr, rssi FROM message_logs ORDER BY timestamp DESC LIMIT ?", (limit,))


def bulletins(db_path: str, board: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """The BBS's public boards (General / Info / News / Urgent)."""
    if board:
        return _rows(db_path, "SELECT id, board, sender_short_name sender, date, subject, content FROM bulletins"
                              " WHERE board = ? COLLATE NOCASE ORDER BY id DESC LIMIT ?", (board, limit))
    return _rows(db_path, "SELECT id, board, sender_short_name sender, date, subject, content FROM bulletins ORDER BY id DESC LIMIT ?", (limit,))


def bulletin_boards(db_path: str) -> List[Dict[str, Any]]:
    return _rows(db_path, "SELECT board, COUNT(*) count FROM bulletins GROUP BY board ORDER BY count DESC, board")


def mail_summary(db_path: str) -> Dict[str, Any]:
    """Mail is private: counts only, never contents."""
    tot = _one(db_path, "SELECT COUNT(*) n, COUNT(DISTINCT recipient) recipients FROM mail") or {}
    per = _rows(db_path, "SELECT recipient, COUNT(*) waiting, MAX(date) latest FROM mail GROUP BY recipient ORDER BY waiting DESC LIMIT 20")
    return {"total": tot.get("n") or 0, "recipients": tot.get("recipients") or 0, "per_recipient": per}


def direct_links(db_path: str, days: int = 7) -> List[Dict[str, Any]]:
    """Nodes the base heard with zero hops (from rx_points) — persisted direct RF links."""
    since = int(time.time() - days * 86400)
    return _rows(db_path, "SELECT node_id id, COUNT(*) count, AVG(snr) snr, MAX(ts) last FROM rx_points"
                          " WHERE hops = 0 AND ts >= ? GROUP BY node_id ORDER BY last DESC", (since,))
