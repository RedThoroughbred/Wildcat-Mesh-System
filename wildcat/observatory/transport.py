"""Which way we hear each node: over the radio (RF) or through the internet (MQTT).

A resilience question — "what is actually reachable when the internet is gone?" —
so the answer is kept per node and persisted (``node_transport`` table) across
Observatory restarts. Counts are folded in memory and flushed in one write every
few seconds by the bridge's timer thread (commit-or-rollback-and-close, D-022).

``transport_of(node)`` is the single rule the UI relies on:

* live evidence wins — packets seen over RF and/or via MQTT → ``rf`` / ``mqtt`` / ``both``;
* with no live evidence yet, the radio's own node DB is the prior: a node it learned via
  MQTT is ``mqtt``, one it learned over the air is ``rf``;
* otherwise ``None`` (we simply don't know — e.g. a node seeded from old database rows).

``rf_heard`` is True only when there is RF evidence (live or prior).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS node_transport (
    node_id TEXT PRIMARY KEY, rf_count INTEGER NOT NULL DEFAULT 0, mqtt_count INTEGER NOT NULL DEFAULT 0,
    first_rf INTEGER, last_rf INTEGER, last_mqtt INTEGER, updated INTEGER NOT NULL)"""


def transport_of(node: Dict[str, Any]) -> Optional[str]:
    rf, mq = node.get("rf_count") or 0, node.get("mqtt_count") or 0
    if rf and mq:
        return "both"
    if rf:
        return "rf"
    if mq:
        return "mqtt"
    prior = node.get("via_mqtt")
    if prior is True:
        return "mqtt"
    if prior is False and node.get("last_heard"):
        return "rf"
    return None


def annotate(node: Dict[str, Any]) -> Dict[str, Any]:
    """Set node['transport'] / node['rf_heard'] from the evidence on the row (in place)."""
    t = transport_of(node)
    node["transport"] = t
    node["rf_heard"] = t in ("rf", "both")
    return node


class TransportStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._dirty: Dict[str, Dict[str, Any]] = {}
        self._ensured = False

    def ensure(self) -> None:
        if self._ensured:
            return
        with closing(sqlite3.connect(self.db_path, timeout=3)) as c, c:
            c.execute(SCHEMA)
        self._ensured = True

    def note(self, nid: str, via_mqtt: bool, ts: float) -> None:
        """Remember one packet (cheap: memory only until flush())."""
        with self._lock:
            d = self._dirty.setdefault(nid, {"rf": 0, "mqtt": 0, "first_rf": None, "last_rf": None, "last_mqtt": None})
            if via_mqtt:
                d["mqtt"] += 1; d["last_mqtt"] = max(d["last_mqtt"] or 0, ts)
            else:
                d["rf"] += 1; d["last_rf"] = max(d["last_rf"] or 0, ts)
                d["first_rf"] = min(d["first_rf"], ts) if d["first_rf"] else ts

    def flush(self, now: Optional[float] = None) -> int:
        if not self._dirty:
            return 0
        self.ensure()                              # before taking the batch: a failure here loses nothing
        with self._lock:
            batch, self._dirty = self._dirty, {}
        now = int(now or time.time())
        conn = sqlite3.connect(self.db_path, timeout=3)
        try:
            conn.execute("PRAGMA busy_timeout = 3000")
            for nid, d in batch.items():
                conn.execute("""INSERT INTO node_transport (node_id, rf_count, mqtt_count, first_rf, last_rf, last_mqtt, updated)
                                VALUES (?,?,?,?,?,?,?)
                                ON CONFLICT(node_id) DO UPDATE SET
                                  rf_count = rf_count + excluded.rf_count, mqtt_count = mqtt_count + excluded.mqtt_count,
                                  first_rf = COALESCE(first_rf, excluded.first_rf),
                                  last_rf = MAX(COALESCE(last_rf, 0), COALESCE(excluded.last_rf, 0)),
                                  last_mqtt = MAX(COALESCE(last_mqtt, 0), COALESCE(excluded.last_mqtt, 0)),
                                  updated = excluded.updated""",
                             (nid, d["rf"], d["mqtt"], int(d["first_rf"]) if d["first_rf"] else None,
                              int(d["last_rf"]) if d["last_rf"] else None, int(d["last_mqtt"]) if d["last_mqtt"] else None, now))
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            with self._lock:                       # keep the evidence for the next try
                for nid, d in batch.items():
                    cur = self._dirty.setdefault(nid, {"rf": 0, "mqtt": 0, "first_rf": None, "last_rf": None, "last_mqtt": None})
                    cur["rf"] += d["rf"]; cur["mqtt"] += d["mqtt"]
                    cur["first_rf"] = min(x for x in (cur["first_rf"], d["first_rf"]) if x) if (cur["first_rf"] or d["first_rf"]) else None
                    cur["last_rf"] = max(cur["last_rf"] or 0, d["last_rf"] or 0) or None
                    cur["last_mqtt"] = max(cur["last_mqtt"] or 0, d["last_mqtt"] or 0) or None
            raise
        finally:
            conn.close()
        return len(batch)

    def load(self) -> List[Dict[str, Any]]:
        self.ensure()
        with closing(sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=3)) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM node_transport")]
