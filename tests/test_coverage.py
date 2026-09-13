"""Coverage: rx-point derivation is honest (fresh fix or nothing), the store dedups,
and the history backfill joins DMs to the nearest fix within the window."""
from __future__ import annotations

import sqlite3

from wildcat.observatory.coverage import MAX_POS_AGE, CoverageStore, coverage_point

BASE = "!9e766b18"
NOW = 1_700_000_000.0


def env(kind="text", frm="!716c668c", snr=5.5, rssi=-86, hops=0, **extra):
    e = {"v": 1, "proto": "meshtastic", "kind": kind, "from": frm, "rx": {"time": NOW, "snr": snr, "rssi": rssi, "hops": hops},
         "received_at": NOW}
    e.update(extra)
    return e


def test_position_packet_pins_itself():
    p = coverage_point(env("position", position={"lat": 38.9, "lon": -84.6, "alt": 250, "sats": 6, "time": None}), None, NOW, BASE)
    assert p and p["lat"] == 38.9 and p["pos_age"] == 0 and p["snr"] == 5.5 and p["hops"] == 0 and p["kind"] == "position"


def test_other_packets_use_a_fresh_roster_fix_only():
    node = {"position": {"lat": 38.9, "lon": -84.6, "alt": None}, "position_ts": NOW - 120}
    p = coverage_point(env(), node, NOW, BASE)
    assert p and p["pos_age"] == 120 and p["node_id"] == "!716c668c"
    stale = {"position": {"lat": 38.9, "lon": -84.6}, "position_ts": NOW - MAX_POS_AGE - 1}
    assert coverage_point(env(), stale, NOW, BASE) is None
    unknown_age = {"position": {"lat": 38.9, "lon": -84.6}}
    assert coverage_point(env(), unknown_age, NOW, BASE) is None
    assert coverage_point(env(), None, NOW, BASE) is None


def test_own_packets_no_signal_and_null_island_rejected():
    node = {"position": {"lat": 38.9, "lon": -84.6}, "position_ts": NOW}
    assert coverage_point(env(frm=BASE), node, NOW, BASE) is None
    assert coverage_point(env(snr=None, rssi=None), node, NOW, BASE) is None
    zero = {"position": {"lat": 0.0, "lon": 0.0}, "position_ts": NOW}
    assert coverage_point(env(), zero, NOW, BASE) is None


def test_store_add_dedup_recent_summary(tmp_path):
    st = CoverageStore(str(tmp_path / "c.db")); st.ensure()
    node = {"position": {"lat": 38.9, "lon": -84.6}, "position_ts": NOW}
    p = coverage_point(env(), node, NOW, BASE)
    assert st.add(p) is True and st.add(p) is False
    p2 = coverage_point(env(hops=2, snr=-9.0), node, NOW + 5, BASE); p2["ts"] = int(NOW + 5)
    st.add(p2)
    pts = st.recent(int(NOW) - 10)
    assert [x["ts"] for x in pts] == [int(NOW + 5), int(NOW)]
    s = st.summary(0)
    assert s["count"] == 2 and s["direct"] == 1 and s["nodes"] == 1 and s["sources"] == {"live": 2}


def test_backfill_joins_messages_to_nearest_fix(tmp_path):
    db = tmp_path / "b.db"
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, snr REAL, rssi INTEGER);
      CREATE TABLE position_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, node_id TEXT, latitude REAL, longitude REAL, altitude REAL);
    """)
    T = 1_700_000_000
    c.executemany("INSERT INTO position_logs (timestamp,node_id,latitude,longitude,altitude) VALUES (?,?,?,?,?)", [
        (T, "!716c668c", 38.900, -84.600, 200), (T + 300, "!716c668c", 38.905, -84.605, 205),
        (T + 5000, "!716c668c", 38.950, -84.650, 210), (T, "!a0388880", 38.88, -84.62, 278)])
    c.executemany("INSERT INTO message_logs (timestamp,sender_id,snr,rssi) VALUES (?,?,?,?)", [
        (T + 100, "!716c668c", 6.0, -85),      # nearest fix: T (100 s) not T+300 (200 s)
        (T + 290, "!716c668c", 2.0, -95),      # nearest: T+300
        (T + 2000, "!716c668c", -5.0, -110),   # no fix within 600 s → skipped
        (T + 10, BASE, 9.0, -60),              # base's own outgoing → skipped
        (T + 10, "!deadbeef", 1.0, -100),      # never had a fix → skipped
        (T + 20, "!a0388880", None, None),     # no signal → not selected
    ])
    c.commit(); c.close()
    st = CoverageStore(str(db)); st.ensure()
    assert st.backfill_history(BASE) == 2
    assert st.backfill_history(BASE) == 0         # idempotent
    pts = sorted(st.recent(0), key=lambda p: p["ts"])
    assert [(p["lat"], p["snr"], p["pos_age"], p["source"], p["hops"]) for p in pts] == [
        (38.9, 6.0, 100, "history", None), (38.905, 2.0, 10, "history", None)]
