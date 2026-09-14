"""Observatory v2 State: pure folding of neutral envelopes → roster / links / feed."""
from __future__ import annotations

from wildcat.observatory.bridge import State, summarize

BASE = "!9e766b18"


def env(kind, frm, **extra):
    e = {"v": 1, "proto": "meshtastic", "kind": kind, "from": frm, "to": None, "broadcast": True,
         "channel": 0, "rx": {"time": 1000, "snr": 6.0, "rssi": -80, "hops": 0}, "received_at": 1000.5}
    e.update(extra)
    return e


def fresh():
    s = State()
    s.apply_roster({"my_id": BASE, "roster": {
        BASE: {"id": BASE, "short_name": "6b18", "position": {"lat": 38.88, "lon": -84.62, "alt": 278}},
        "!a0388880": {"id": "!a0388880", "short_name": "STAY", "position": {"lat": 38.8796, "lon": -84.6169, "alt": None}},
    }})
    return s


def test_direct_hop_infers_link_to_base_and_updates_node():
    s = fresh()
    ev = s.apply_packet(env("text", "!a0388880", text="hi", broadcast=False, to=BASE), now=2000)
    assert ev["packet"]["from_name"] == "STAY" and ev["packet"]["to_name"] == "6b18" and ev["packet"]["summary"] == "hi"
    assert len(ev["links"]) == 1 and ev["links"][0]["kind"] == "direct" and ev["links"][0]["snr"] == 6.0
    assert s.roster["!a0388880"]["last_heard"] == 2000 and s.roster["!a0388880"]["snr"] == 6.0
    assert s.roster["!a0388880"]["hops_away"] == 0


def test_multihop_packet_makes_no_link():
    s = fresh()
    ev = s.apply_packet(env("position", "!deadbeef", rx={"time": 1, "snr": 1.0, "rssi": -110, "hops": 2},
                            position={"lat": 39.0, "lon": -84.0, "alt": 100, "sats": 5, "time": None}), now=2000)
    assert ev["links"] == []
    n = s.roster["!deadbeef"]                       # unknown node gets a stub with a position
    assert n["short_name"] == "beef" and n["position"]["lat"] == 39.0 and n["hops_away"] == 2


def test_neighbors_make_neighbor_links_which_outrank_inference():
    s = fresh()
    s.apply_packet(env("text", "!a0388880", text="x"), now=1)
    assert s.links["!9e766b18|!a0388880"]["kind"] == "direct"
    ev = s.apply_packet(env("neighbors", "!a0388880", neighbors=[{"id": BASE, "snr": 7.25}, {"id": "!716c668c", "snr": 3.0}]), now=2)
    keys = {l["key"] for l in ev["links"]}
    assert keys == {"!9e766b18|!a0388880", "!716c668c|!a0388880"}
    assert s.links["!9e766b18|!a0388880"]["kind"] == "neighbor" and s.links["!9e766b18|!a0388880"]["count"] == 3
    assert "!716c668c" in s.roster                   # neighbour we hadn't met gets a stub


def test_telemetry_and_nodeinfo_update_roster():
    s = fresh()
    s.apply_packet(env("telemetry", "!a0388880", telemetry={"battery": 88, "voltage": 4.0, "channel_util": 3.3, "air_util_tx": None,
                                                            "uptime": None, "temperature": None, "humidity": None, "pressure": None}), now=5)
    assert s.roster["!a0388880"]["battery"] == 88 and s.roster["!a0388880"]["channel_util"] == 3.3
    s.apply_packet(env("nodeinfo", "!a0388880", nodeinfo={"id": "!a0388880", "short_name": "STAY", "long_name": "Wildcat Stay", "hw": "STATION_G2", "role": "CLIENT_BASE"}), now=6)
    assert s.roster["!a0388880"]["hw"] == "STATION_G2" and s.roster["!a0388880"]["long_name"] == "Wildcat Stay"


def test_snapshot_stats_and_rate():
    s = fresh()
    T = 1_700_000_000
    for i in range(5):
        s.apply_packet(env("telemetry", "!a0388880", telemetry={}), now=T + i)
    snap = s.snapshot(now=T + 10)
    assert snap["stats"]["total"] == 5 and snap["stats"]["per_min"] == 5 and snap["stats"]["by_kind"] == {"telemetry": 5}
    assert snap["stats"]["on_map"] == 2 and snap["stats"]["heard_1h"] == 1     # the base itself was never "heard"
    assert len(snap["packets"]) == 5 and snap["my_id"] == BASE
    assert s.rate_per_min(now=T + 65) == 0


def test_links_expire():
    s = State(link_ttl=10); s.apply_roster({"my_id": BASE, "roster": {}})
    s.apply_packet(env("text", "!a0388880", text="x"), now=0)
    assert s.snapshot(now=5)["links"] and not s.snapshot(now=20)["links"]


def test_roster_merge_keeps_known_position_and_ignores_nulls():
    s = fresh()
    s.apply_roster({"roster": {"!a0388880": {"id": "!a0388880", "short_name": None, "position": None, "battery": 50}}})
    n = s.roster["!a0388880"]
    assert n["short_name"] == "STAY" and n["position"]["lat"] == 38.8796 and n["battery"] == 50


def test_bad_envelope_ignored():
    s = fresh()
    assert s.apply_packet({"kind": "text"}, now=1) is None and s.total == 0


def test_summaries():
    assert summarize({"kind": "position", "position": {"lat": 38.88, "lon": -84.62, "alt": 278}}) == "38.88000, -84.62000 · 278 m"
    assert summarize({"kind": "telemetry", "telemetry": {"battery": 101, "voltage": 4.32, "channel_util": 4.43}}) == "on power · 4.32 V · util 4.4%"
    assert summarize({"kind": "neighbors", "neighbors": [{}, {}]}) == "hears 2 neighbours"
    assert summarize({"kind": "text", "text": "x" * 200}).endswith("…")
    assert summarize({"kind": "other", "portnum": "PAXCOUNTER_APP"}) == "PAXCOUNTER_APP"


# ---- node detail (card) ---------------------------------------------------------------

def test_signal_ring_and_node_detail(tmp_path):
    import sqlite3
    from wildcat.observatory.bridge import node_detail
    T = 1_700_000_000
    s = fresh()
    for i in range(3):
        s.apply_packet(env("telemetry", "!a0388880", rx={"time": T + i, "snr": 5.0 + i, "rssi": -80 - i, "hops": 0},
                           received_at=T + i, telemetry={}), now=T + i)
    db = tmp_path / "b.db"; c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, snr REAL, rssi INTEGER);
      CREATE TABLE telemetry_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, node_id TEXT, battery_level INTEGER, voltage REAL, channel_util REAL, air_util_tx REAL, temperature REAL);
    """)
    c.execute("INSERT INTO message_logs (timestamp,sender_id,snr,rssi) VALUES (?,?,?,?)", (T - 100, "!a0388880", 2.5, -95))
    c.execute("INSERT INTO message_logs (timestamp,sender_id,snr,rssi) VALUES (?,?,?,?)", (T + 1, "!a0388880", 99.0, -1))  # same ts as a live sample → live wins
    c.execute("INSERT INTO telemetry_logs (timestamp,node_id,battery_level,voltage,channel_util,air_util_tx,temperature) VALUES (?,?,?,?,?,?,?)", (T - 50, "!a0388880", 90, 4.0, 3.0, 0.5, None))
    c.commit(); c.close()
    d = node_detail(s, str(db), "!a0388880", hours=1, now=T + 10)
    assert d["node"]["short_name"] == "STAY"
    assert [x["snr"] for x in d["signal"]] == [2.5, 5.0, 6.0, 7.0]
    assert d["telemetry"][0]["battery"] == 90 and d["counts"]["packets_24h"] == 3
    assert node_detail(s, str(db), "!nobody", now=T) is None
    # window excludes old rows
    assert [x["snr"] for x in node_detail(s, str(db), "!a0388880", hours=0.01, now=T + 10)["signal"]] == [5.0, 6.0, 7.0]


# ---- brain exchanges + history ---------------------------------------------------------

def test_brain_exchange_recorded_and_named():
    s = fresh()
    rec = s.apply_brain({"node": "!a0388880", "prompt": "?weather 41011", "reply": "Clear, 72F", "provider": "anthropic",
                         "latency_ms": 1800, "chunks": 1}, now=5)
    assert rec["node_name"] == "STAY" and rec["provider"] == "anthropic" and s.snapshot(now=10)["brain"] == [rec]
    assert s.apply_brain({"prompt": "no node"}, now=6) is None


def test_history_unions_the_logged_tables(tmp_path):
    import sqlite3
    from wildcat.observatory.bridge import history
    db = tmp_path / "h.db"; c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, to_id INTEGER, message TEXT, snr REAL, rssi INTEGER);
      CREATE TABLE telemetry_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, node_id TEXT, battery_level INTEGER, voltage REAL, channel_util REAL);
      CREATE TABLE position_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, node_id TEXT, latitude REAL, longitude REAL, altitude REAL);
      CREATE TABLE neighbor_info (id INTEGER PRIMARY KEY, timestamp INTEGER, node_id TEXT, neighbor_id TEXT);
      CREATE TABLE node_info (node_id TEXT PRIMARY KEY, short_name TEXT);
    """)
    T = 1_700_000_000
    c.execute("INSERT INTO node_info VALUES ('!716c668c', 'GO')")
    c.execute("INSERT INTO message_logs (timestamp,sender_id,to_id,message,snr,rssi) VALUES (?,?,?,?,?,?)", (T + 30, "!716c668c", 2658560792, "M", 5.5, -86))
    c.execute("INSERT INTO message_logs (timestamp,sender_id,to_id,message,snr,rssi) VALUES (?,?,?,?,?,?)", (T + 40, "!9e766b18", 1902929548, "menu…", None, None))
    c.execute("INSERT INTO telemetry_logs (timestamp,node_id,battery_level,voltage,channel_util) VALUES (?,?,?,?,?)", (T + 10, "!716c668c", 80, 3.9, 2.0))
    c.execute("INSERT INTO position_logs (timestamp,node_id,latitude,longitude,altitude) VALUES (?,?,?,?,?)", (T + 20, "!716c668c", 38.9, -84.6, 200))
    c.executemany("INSERT INTO neighbor_info (timestamp,node_id,neighbor_id) VALUES (?,?,?)", [(T + 50, "!716c668c", "!a"), (T + 50, "!716c668c", "!b")])
    c.execute("INSERT INTO message_logs (timestamp,sender_id,to_id,message,snr,rssi) VALUES (?,?,?,?,?,?)", (T - 999, "!716c668c", 4294967295, "old", 1, -100))
    c.commit(); c.close()
    ev = history(str(db), T)
    assert [e["kind"] for e in ev] == ["telemetry", "position", "text", "text", "neighbors"]
    assert ev[2]["from_name"] == "GO" and ev[2]["to"] == "!9e766b18" and ev[2]["broadcast"] is False and ev[2]["snr"] == 5.5
    assert ev[0]["summary"] == "80% · 3.90 V · util 2.0%" and ev[4]["summary"] == "hears 2 neighbours"
    assert ev[1]["position"] == {"lat": 38.9, "lon": -84.6}
    assert history(str(tmp_path / "missing.db"), 0) == []


# ---------------------------------------------------------------------------- RF vs MQTT
def test_mqtt_packets_count_as_evidence_but_never_as_radio_truth():
    s = fresh()
    mq = env("text", "!a0388880", text="hi", rx={"time": 1000, "snr": 4.0, "rssi": -70, "hops": 0, "via_mqtt": True})
    ev = s.apply_packet(mq, now=2000)
    n = s.roster["!a0388880"]
    assert n["mqtt_count"] == 1 and n["rf_count"] == 0 and n["last_mqtt"] == 2000 and n["last_rf"] is None
    assert n["transport"] == "mqtt" and n["rf_heard"] is False
    assert ev["packet"]["via_mqtt"] is True and ev["packet"]["snr"] is None and ev["packet"]["hops"] is None
    assert ev["links"] == [] and s.links == {} and "!a0388880" not in s.signal      # no direct link, no SNR sample
    assert n.get("snr") is None and n.get("hops_away") is None
    rf = env("text", "!a0388880", text="hi", rx={"time": 1000, "snr": 6.0, "rssi": -80, "hops": 0, "via_mqtt": False})
    ev = s.apply_packet(rf, now=2500)
    assert n["rf_count"] == 1 and n["last_rf"] == 2500 and n["transport"] == "both" and n["rf_heard"] is True
    assert ev["packet"]["via_mqtt"] is False and len(ev["links"]) == 1 and n["snr"] == 6.0


def test_unknown_transport_when_the_producer_omits_the_flag():
    s = fresh()
    ev = s.apply_packet(env("text", "!a0388880", text="hi"), now=2000)       # v1-style rx block, no via_mqtt
    n = s.roster["!a0388880"]
    assert ev["packet"]["via_mqtt"] is None and n["rf_count"] == 0 and n["mqtt_count"] == 0
    assert n["transport"] is None and n["rf_heard"] is False
    assert len(ev["links"]) == 1                                              # hops 0 still infers the link (pre-v2 behaviour)


def test_roster_prior_from_the_radios_node_db_until_live_evidence_arrives():
    s = fresh()
    s.apply_roster({"my_id": BASE, "roster": {"!0000000a": {"id": "!0000000a", "short_name": "A", "via_mqtt": True, "last_heard": 900},
                                               "!0000000b": {"id": "!0000000b", "short_name": "B", "via_mqtt": False, "last_heard": 900},
                                               "!0000000c": {"id": "!0000000c", "short_name": "C", "via_mqtt": False}}})
    a, b, c = s.roster["!0000000a"], s.roster["!0000000b"], s.roster["!0000000c"]
    assert a["transport"] == "mqtt" and a["rf_heard"] is False
    assert b["transport"] == "rf" and b["rf_heard"] is True
    assert c["transport"] is None                                              # never heard at all
    s.apply_packet(env("position", "!0000000a", position={"lat": 1, "lon": 2}, rx={"time": 1, "snr": 5, "rssi": -80, "hops": 1, "via_mqtt": False}), now=3000)
    assert a["transport"] == "rf" and a["rf_heard"] is True                    # live evidence outranks the prior


def test_persisted_evidence_is_folded_in_at_startup():
    s = fresh()
    n = s.apply_transport([{"node_id": "!a0388880", "rf_count": 12, "mqtt_count": 0, "last_rf": 800, "last_mqtt": None},
                           {"node_id": "!0000000d", "rf_count": 0, "mqtt_count": 5, "last_rf": None, "last_mqtt": 700}])
    assert n == 2
    stay, d = s.roster["!a0388880"], s.roster["!0000000d"]
    assert stay["transport"] == "rf" and stay["rf_count"] == 12 and stay["last_rf"] == 800
    assert d["transport"] == "mqtt" and d["last_heard"] == 700 and d["short_name"] == "000d"


def test_node_detail_carries_links_with_the_far_ends_position(tmp_path):
    from wildcat.observatory.bridge import node_detail
    s = fresh()
    s.apply_packet(env("text", "!a0388880", text="hi"), now=2000)                                       # STAY heard direct → link STAY↔base
    s.apply_packet(env("neighbors", "!a0388880", neighbors=[{"id": "!0000000c", "snr": 3.5}], rx={"time": 1, "snr": 5, "rssi": -80, "hops": 1}), now=2100)
    d = node_detail(s, str(tmp_path / "none.db"), "!a0388880", now=2200)
    ids = {l["id"]: l for l in d["links"]}
    assert set(ids) == {BASE, "!0000000c"}
    assert ids[BASE]["kind"] == "direct" and ids[BASE]["position"] == {"lat": 38.88, "lon": -84.62, "alt": 278} and ids[BASE]["name"] == "6b18"
    assert ids["!0000000c"]["kind"] == "neighbor" and ids["!0000000c"]["snr"] == 3.5 and ids["!0000000c"]["position"] is None
    assert d["base"]["id"] == BASE and d["base"]["position"]["lat"] == 38.88 and d["counts"]["links"] == 2
    assert node_detail(s, str(tmp_path / "none.db"), BASE, now=2200)["links"][0]["id"] == "!a0388880"
