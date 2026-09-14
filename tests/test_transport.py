"""The RF/MQTT evidence store and the one transport rule."""
from __future__ import annotations

import sqlite3

from wildcat.observatory.coverage import coverage_point
from wildcat.observatory.transport import TransportStore, transport_of, annotate


def test_transport_rule():
    assert transport_of({"rf_count": 3, "mqtt_count": 0}) == "rf"
    assert transport_of({"rf_count": 0, "mqtt_count": 2}) == "mqtt"
    assert transport_of({"rf_count": 1, "mqtt_count": 1}) == "both"
    assert transport_of({"via_mqtt": True}) == "mqtt"
    assert transport_of({"via_mqtt": False, "last_heard": 5}) == "rf"
    assert transport_of({"via_mqtt": False}) is None and transport_of({}) is None
    n = annotate({"rf_count": 0, "mqtt_count": 4})
    assert n["transport"] == "mqtt" and n["rf_heard"] is False


def test_store_accumulates_flushes_and_reloads(tmp_path):
    db = str(tmp_path / "t.db")
    sqlite3.connect(db).close()
    st = TransportStore(db)
    assert st.flush() == 0
    st.note("!a", False, 100); st.note("!a", False, 130); st.note("!a", True, 120); st.note("!b", True, 50)
    assert st.flush(now=200) == 2
    rows = {r["node_id"]: r for r in st.load()}
    assert rows["!a"]["rf_count"] == 2 and rows["!a"]["mqtt_count"] == 1 and rows["!a"]["first_rf"] == 100 and rows["!a"]["last_rf"] == 130 and rows["!a"]["last_mqtt"] == 120
    assert rows["!b"]["rf_count"] == 0 and rows["!b"]["last_rf"] is None and rows["!b"]["last_mqtt"] == 50 and rows["!b"]["updated"] == 200
    st.note("!a", False, 300); st.flush(now=301)
    a = {r["node_id"]: r for r in st.load()}["!a"]
    assert a["rf_count"] == 3 and a["first_rf"] == 100 and a["last_rf"] == 300      # counts add, first stays, last moves


def test_store_keeps_the_batch_when_the_write_fails(tmp_path):
    st = TransportStore(str(tmp_path / "missing" / "t.db"))
    st.note("!a", False, 1)
    try:
        st.flush(); assert False
    except sqlite3.Error:
        pass
    assert st._dirty["!a"]["rf"] == 1


def test_coverage_ignores_internet_bridged_packets():
    env = {"from": "!x", "kind": "position", "position": {"lat": 1, "lon": 2, "alt": None}, "rx": {"snr": 5, "rssi": -80, "hops": 0, "via_mqtt": True}}
    assert coverage_point(env, None, 10.0, "!base") is None
    env["rx"]["via_mqtt"] = False
    assert coverage_point(env, None, 10.0, "!base") is not None
