"""wildcat/observatory/queries.py against a temp DB in the BBS/telemetry schema."""
from __future__ import annotations

import sqlite3
import time

import pytest

from wildcat.observatory import queries as Q

BASE = "!9e766b18"; GO = "!716c668c"; STAY = "!a0388880"


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "b.db"; c = sqlite3.connect(p)
    c.executescript("""
      CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, sender_short_name TEXT, to_id INTEGER, channel_index INTEGER, message TEXT, snr REAL, rssi INTEGER, hop_limit INTEGER);
      CREATE TABLE node_info (node_id TEXT PRIMARY KEY, short_name TEXT, long_name TEXT, hw_model TEXT, role TEXT);
      CREATE TABLE telemetry_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, node_id TEXT, battery_level INTEGER, voltage REAL, channel_util REAL, air_util_tx REAL, temperature REAL);
      CREATE TABLE position_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, node_id TEXT, latitude REAL, longitude REAL, altitude REAL, satellites_in_view INTEGER);
      CREATE TABLE neighbor_info (id INTEGER PRIMARY KEY, timestamp INTEGER, node_id TEXT, neighbor_id TEXT, snr REAL);
      CREATE TABLE bulletins (id INTEGER PRIMARY KEY, board TEXT, sender_short_name TEXT, date TEXT, subject TEXT, content TEXT, unique_id TEXT);
      CREATE TABLE mail (id INTEGER PRIMARY KEY, sender TEXT, sender_short_name TEXT, recipient TEXT, date TEXT, subject TEXT, content TEXT, unique_id TEXT);
    """)
    c.execute("INSERT INTO bulletins (board,sender_short_name,date,subject,content,unique_id) VALUES ('General','GO','2026-09-13','hello','first post','u1')")
    c.execute("INSERT INTO bulletins (board,sender_short_name,date,subject,content,unique_id) VALUES ('Urgent','STAY','2026-09-13','storm','take cover','u2')")
    c.execute("INSERT INTO mail (sender,sender_short_name,recipient,date,subject,content,unique_id) VALUES ('!716c668c','GO','!a0388880','2026-09-13','secret','private','m1')")
    now = int(time.time())
    c.execute("INSERT INTO node_info VALUES (?,?,?,?,?)", (GO, "GO", "Wildcat Go", "TRACKER_T1000_E", "CLIENT"))
    msgs = [(now - 100, GO, "GO", 2658560792, 0, "M", 9.0, -70), (now - 90, BASE, "6b18", 1902929548, 0, "menu", None, None),
            (now - 80, GO, "GO", 4294967295, 0, "hi all", 2.0, -95), (now - 70, STAY, "STAY", 4294967295, 1, "ch1", -6.0, -110),
            (now - 5 * 86400, GO, "GO", 4294967295, 0, "old", 1.0, -100)]
    c.executemany("INSERT INTO message_logs (timestamp,sender_id,sender_short_name,to_id,channel_index,message,snr,rssi) VALUES (?,?,?,?,?,?,?,?)", msgs)
    c.execute("INSERT INTO telemetry_logs (timestamp,node_id,battery_level,voltage) VALUES (?,?,?,?)", (now - 50, STAY, 12, 3.4))
    c.execute("INSERT INTO telemetry_logs (timestamp,node_id,battery_level,voltage) VALUES (?,?,?,?)", (now - 40, STAY, 15, 3.5))
    c.executemany("INSERT INTO neighbor_info (timestamp,node_id,neighbor_id,snr) VALUES (?,?,?,?)", [(now - 60, STAY, GO, 7.0), (now - 30, STAY, GO, 8.0)])
    c.commit(); c.close()
    return str(p)


def test_mesh_stats_and_node_stats(db):
    m = Q.mesh_stats(db)
    assert m["messages_24h"] == 4 and m["active_nodes"] == 3 and m["total_nodes"] == 3 and m["avg_snr"] == 1.7
    s = Q.node_stats(db)
    assert s[GO]["message_count"] == 3 and s[GO]["best_snr"] == 9.0 and s[GO]["worst_snr"] == 1.0 and s[GO]["long_name"] == "Wildcat Go"
    assert s[GO]["avg_rssi"] == -88.3


def test_node_messages_and_reliability(db):
    assert [m["text"] for m in Q.node_messages(db, GO)] == ["hi all", "M", "old"]
    r = Q.node_reliability(db, GO, days=7)
    assert r["messages"] == 3 and r["bands"]["excellent"] == 1 and r["bands"]["fair"] == 2 and r["reliability_pct"] == 33


def test_channels(db):
    assert Q.channel_activity(db, 24) == [{"channel": 0, "count": 3}, {"channel": 1, "count": 1}]
    d = Q.channel_details(db, 24)
    assert d[0]["channel"] == 0 and d[0]["unique_senders"] == 2 and d[0]["avg_snr"] == 5.5
    assert Q.top_senders(db, 24)[0]["id"] == GO
    hourly = Q.hourly_activity(db, 24)
    assert sum(r["count"] for r in hourly) == 4 and all(0 <= r["hour"] <= 23 for r in hourly)
    assert [m["text"] for m in Q.channel_messages(db, 0, 24)] == ["hi all"]     # broadcasts only


def test_bbs_messages_flags_the_den(db):
    m = Q.bbs_messages(db, BASE, 24)
    assert [(x["text"], x["from_bbs"], x["to"]) for x in m] == [("menu", True, "!716c668c"), ("M", False, "!9e766b18")]


def test_propagation(db):
    t = Q.hourly_snr_trends(db, 7)
    assert sum(r["message_count"] for r in t) == 4
    bw = Q.best_worst(db, 7)
    assert bw["best"][0]["snr"] == 9.0 and bw["worst"][0]["snr"] == -6.0
    assert {r["snr"]: r["count"] for r in Q.snr_distribution(db, 7)} == {9: 1, 2: 1, -6: 1, 1: 1}


def test_topology_low_battery_logs(db):
    e = Q.neighbor_edges(db)
    assert len(e) == 1 and e[0]["snr"] == 8.0 and e[0]["b_name"] == "GO"
    lb = Q.low_battery(db)
    assert lb == [{"id": STAY, "battery": 15, "voltage": 3.5, "ts": lb[0]["ts"]}]
    assert len(Q.recent_logs(db, "messages", 2)) == 2 and Q.recent_logs(db, "neighbors")[0]["neighbor"] == GO
    assert Q.recent_logs(db, "positions") == []


def test_missing_db_is_empty(tmp_path):
    assert Q.mesh_stats(str(tmp_path / "none.db"))["messages_24h"] == 0 and Q.node_stats(str(tmp_path / "none.db")) == {}


def test_bulletins_and_mail_privacy(db):
    assert [b["board"] for b in Q.bulletins(db)] == ["Urgent", "General"]
    assert Q.bulletins(db, "general")[0]["subject"] == "hello"
    assert Q.bulletin_boards(db) == [{"board": "General", "count": 1}, {"board": "Urgent", "count": 1}]
    m = Q.mail_summary(db)
    assert m["total"] == 1 and m["per_recipient"][0]["recipient"] == "!a0388880"
    assert "content" not in m["per_recipient"][0] and "subject" not in m["per_recipient"][0]
