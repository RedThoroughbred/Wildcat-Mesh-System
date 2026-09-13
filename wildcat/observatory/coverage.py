"""Coverage: where the base actually hears the mesh, measured — never interpolated.

An **rx point** is one packet the base received, pinned to where its sender was
at the time, with the SNR/RSSI *as heard at the base*. Sources:

* ``live``    — every envelope on the bus with ``rx.snr`` from a node whose position
                is known and fresh (the packet's own fix, or the roster's last fix
                if it's < ``MAX_POS_AGE`` old). ``hops == 0`` means the number is the
                base's own measurement; relayed packets carry the *last hop's*
                SNR, so they're stored with ``hops`` and shown dimmer.
* ``history`` — a one-time backfill joining the BBS's ``message_logs`` (SNR/RSSI per
                DM) with ``position_logs`` (the telemetry logger) for the same node
                within ``HISTORY_WINDOW`` seconds. Hops unknown.

Range Test packets are just envelopes of kind ``rangetest`` and land here like
any other. The UI draws points and (where ≥3 samples share a hex) bins — it
does not paint a smooth blob from three dots.
"""
from __future__ import annotations

import bisect
import logging
import sqlite3
import threading
from contextlib import closing
from typing import Any, Dict, List, Optional

log = logging.getLogger("wildcat.observatory.coverage")

MAX_POS_AGE = 30 * 60        # s: how stale a roster position may be to pin a live packet to it
HISTORY_WINDOW = 10 * 60     # s: message ↔ position match window for the backfill

SCHEMA = """
CREATE TABLE IF NOT EXISTS rx_points (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    node_id  TEXT NOT NULL,
    proto    TEXT,
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    alt      REAL,
    snr      REAL,
    rssi     REAL,
    hops     INTEGER,
    pos_age  INTEGER,
    kind     TEXT,
    source   TEXT NOT NULL DEFAULT 'live',
    UNIQUE (ts, node_id, source)
);
CREATE INDEX IF NOT EXISTS rx_points_ts ON rx_points (ts);
"""


def coverage_point(env: Dict[str, Any], node: Optional[Dict[str, Any]], now: float,
                   my_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Pure: the rx point an envelope yields, or None (own packet / no SNR / no fresh fix)."""
    frm = env.get("from")
    if not frm or frm == my_id:
        return None
    rx = env.get("rx") if isinstance(env.get("rx"), dict) else {}
    if rx.get("snr") is None and rx.get("rssi") is None:
        return None
    lat = lon = alt = None
    pos_age: Optional[int] = None
    if env.get("kind") == "position" and (env.get("position") or {}).get("lat") is not None:
        p = env["position"]
        lat, lon, alt, pos_age = p["lat"], p["lon"], p.get("alt"), 0
    elif node and node.get("position") and node["position"].get("lat") is not None:
        pts = node.get("position_ts")
        if pts is None:
            return None                      # a fix of unknown age is not a measurement
        age = now - float(pts)
        if age < 0 or age > MAX_POS_AGE:
            return None
        p = node["position"]
        lat, lon, alt, pos_age = p["lat"], p["lon"], p.get("alt"), int(age)
    if lat is None or lon is None or (abs(lat) < 0.5 and abs(lon) < 0.5):
        return None
    return {"ts": int(env.get("received_at") or now), "node_id": frm, "proto": env.get("proto"),
            "lat": float(lat), "lon": float(lon), "alt": alt, "snr": rx.get("snr"), "rssi": rx.get("rssi"),
            "hops": rx.get("hops"), "pos_age": pos_age, "kind": env.get("kind"), "source": "live"}


class CoverageStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    def ensure(self) -> None:
        with self._lock, closing(self._conn()) as c:
            c.executescript(SCHEMA)
        self.ready = True

    def _ready(self) -> bool:
        """Lazily create the table if the boot-time ensure() lost a lock race."""
        if getattr(self, "ready", False):
            return True
        try:
            self.ensure()
            return True
        except sqlite3.Error as e:
            log.warning("coverage table not ready yet: %s", e)
            return False

    def add(self, p: Dict[str, Any]) -> bool:
        """Insert one point; False if it was a duplicate."""
        if not self._ready():
            return False
        with self._lock, closing(self._conn()) as c, c:
            cur = c.execute(
                "INSERT OR IGNORE INTO rx_points (ts,node_id,proto,lat,lon,alt,snr,rssi,hops,pos_age,kind,source)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (p["ts"], p["node_id"], p.get("proto"), p["lat"], p["lon"], p.get("alt"), p.get("snr"),
                 p.get("rssi"), p.get("hops"), p.get("pos_age"), p.get("kind"), p.get("source", "live")))
            return cur.rowcount == 1

    def recent(self, since_ts: int, limit: int = 5000) -> List[Dict[str, Any]]:
        if not self._ready():
            return []
        with self._lock, closing(self._conn()) as c, c:
            rows = c.execute("SELECT ts,node_id,proto,lat,lon,alt,snr,rssi,hops,pos_age,kind,source FROM rx_points"
                             " WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (since_ts, limit)).fetchall()
        return [dict(r) for r in rows]

    def summary(self, since_ts: int) -> Dict[str, Any]:
        if not self._ready():
            return {"count": 0, "direct": 0, "nodes": 0, "first_ts": None, "last_ts": None, "sources": {}, "unavailable": True}
        with self._lock, closing(self._conn()) as c, c:
            row = c.execute("SELECT COUNT(*) n, SUM(hops = 0) direct, COUNT(DISTINCT node_id) nodes,"
                            " MIN(ts) first_ts, MAX(ts) last_ts FROM rx_points WHERE ts >= ?", (since_ts,)).fetchone()
            src = c.execute("SELECT source, COUNT(*) n FROM rx_points WHERE ts >= ? GROUP BY source", (since_ts,)).fetchall()
        return {"count": row["n"] or 0, "direct": row["direct"] or 0, "nodes": row["nodes"] or 0,
                "first_ts": row["first_ts"], "last_ts": row["last_ts"], "sources": {r["source"]: r["n"] for r in src}}

    def backfill_history(self, my_id: Optional[str], window: int = HISTORY_WINDOW) -> int:
        """message_logs (SNR per DM) × position_logs (nearest fix within ``window``) → rx points.
        Idempotent (UNIQUE on ts/node/source)."""
        added = 0
        with self._lock, closing(self._conn()) as c, c:
            have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"message_logs", "position_logs"} <= have:
                return 0
            fixes: Dict[str, List[sqlite3.Row]] = {}
            for r in c.execute("SELECT node_id, timestamp, latitude, longitude, altitude FROM position_logs"
                               " WHERE latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY node_id, timestamp"):
                fixes.setdefault(r["node_id"], []).append(r)
            times = {k: [r["timestamp"] for r in v] for k, v in fixes.items()}
            msgs = c.execute("SELECT sender_id, timestamp, snr, rssi FROM message_logs"
                             " WHERE (snr IS NOT NULL OR rssi IS NOT NULL)").fetchall()
            for m in msgs:
                nid = m["sender_id"]
                if not nid or nid == my_id or nid not in fixes:
                    continue
                ts = m["timestamp"]
                i = bisect.bisect_left(times[nid], ts)
                best = None
                for j in (i - 1, i):
                    if 0 <= j < len(times[nid]) and abs(times[nid][j] - ts) <= window:
                        if best is None or abs(times[nid][j] - ts) < abs(times[nid][best] - ts):
                            best = j
                if best is None:
                    continue
                f = fixes[nid][best]
                cur = c.execute(
                    "INSERT OR IGNORE INTO rx_points (ts,node_id,proto,lat,lon,alt,snr,rssi,hops,pos_age,kind,source)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ts, nid, None, f["latitude"], f["longitude"], f["altitude"], m["snr"], m["rssi"], None,
                     abs(f["timestamp"] - ts), "text", "history"))
                added += cur.rowcount
        return added
