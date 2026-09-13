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
