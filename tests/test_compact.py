"""Compact, ASCII, capped views for tiny clients (the Pico panel)."""
from __future__ import annotations

import json

from wildcat.observatory.compact import ascii_clean, compact_channels, compact_messages, compact_nodes

ME = "!ba5a3f4b"
NOW = 10_000.0


def roster():
    return {
        ME: {"id": ME, "short_name": "T96A", "long_name": "Wildcat Brain", "last_heard": NOW - 1},
        "!aaaa0001": {"id": "!aaaa0001", "short_name": "OLD", "long_name": "Old One", "last_heard": NOW - 900,
                      "snr": 5.5, "hops_away": 1, "battery": 80, "hw": "RAK4631", "transport": "rf",
                      "position": {"lat": 39.123456, "lon": -84.5, "alt": 10}},
        "!aaaa0002": {"id": "!aaaa0002", "short_name": "NEW", "long_name": "Dry Ridge Solar \U0001F31E", "last_heard": NOW - 5,
                      "snr": -18.0, "hops_away": 3, "transport": "mqtt"},
        "!aaaa0003": {"id": "!aaaa0003", "short_name": "☀️☀", "last_heard": None, "transport": "both"},
    }


def test_ascii_clean_folds_and_caps():
    assert ascii_clean("café \U0001F31E  ok", 40) == "cafe ok"
    assert ascii_clean(None, 5) == ""
    assert ascii_clean("abcdefghij", 4) == "abcd"


def test_nodes_sorted_newest_first_and_excludes_self():
    d = compact_nodes(roster(), {"!aaaa0001": [{"rssi": -101}]}, ME, NOW, limit=24)
    assert [n["id"] for n in d["n"]] == ["!aaaa0002", "!aaaa0001", "!aaaa0003"]   # unheard node last
    assert d["my"]["s"] == "T96A" and d["total"] == 4
    old = d["n"][1]
    assert old["rssi"] == -101 and old["snr"] == 5.5 and old["hops"] == 1 and old["bat"] == 80
    assert old["age"] == 900 and old["lat"] == 39.1235 and old["tr"] == "rf"


def test_my_block_carries_battery_and_voltage():
    r = roster()
    r[ME].update({"battery": 87, "voltage": 4.1234})
    my = compact_nodes(r, {}, ME, NOW)["my"]
    assert my == {"id": ME, "s": "T96A", "l": "Wildcat Brain", "bat": 87, "volt": 4.12}
    my = compact_nodes(roster(), {}, ME, NOW)["my"]          # no telemetry yet -> nulls
    assert my["bat"] is None and my["volt"] is None


def test_nodes_are_ascii_and_short():
    d = compact_nodes(roster(), {}, ME, NOW)
    for n in d["n"]:
        for k in ("s", "l", "hw"):
            assert n[k].isascii()
        assert len(n["s"]) <= 8 and len(n["l"]) <= 24
    assert d["n"][0]["l"] == "Dry Ridge Solar"
    assert d["n"][2]["s"] == "aaaa0003"[-4:] or d["n"][2]["s"]                    # emoji-only name falls back


def test_nodes_limit_and_rf_filter():
    assert len(compact_nodes(roster(), {}, ME, NOW, limit=1)["n"]) == 1
    assert len(compact_nodes(roster(), {}, ME, NOW, limit="junk")["n"]) == 3
    rf = compact_nodes(roster(), {}, ME, NOW, transport="rf")["n"]
    assert [n["id"] for n in rf] == ["!aaaa0001", "!aaaa0003"]
    assert len(compact_nodes(roster(), {}, ME, NOW, limit=9999)["n"]) <= 40


def test_nodes_payload_stays_small():
    big = {f"!{i:08x}": {"id": f"!{i:08x}", "short_name": "N%d" % i, "long_name": "A Long Node Name %d" % i, "last_heard": NOW - i,
                         "snr": 4.25, "hops_away": 2, "battery": 55, "hw": "HELTEC_MESH_NODE_T114", "transport": "rf",
                         "position": {"lat": 38.6891776, "lon": -84.5381632, "alt": 5}} for i in range(200)}
    assert len(json.dumps(compact_nodes(big, {}, ME, NOW, limit=24))) < 5000


PKTS = [
    {"kind": "position", "ts": NOW - 1},
    {"kind": "text", "ts": NOW - 30, "from": "!aaaa0001", "from_name": "OLD", "broadcast": True, "channel": 0, "text": "hello ❤ world", "snr": 6.0, "rssi": -90},
    {"kind": "text", "ts": NOW - 10, "from": ME, "from_name": "you", "broadcast": False, "to": "!aaaa0002", "channel": 0, "text": "yo", "sent": True},
]


def test_messages_newest_first_ascii_and_flags():
    d = compact_messages(PKTS, [], ME, NOW, limit=20)
    assert [m["t"] for m in d["m"]] == ["yo", "hello world"]
    assert d["m"][0]["me"] is True and d["m"][0]["to"] == "!aaaa0002"
    assert d["m"][1]["to"] is None and d["m"][1]["age"] == 30 and d["m"][1]["rssi"] == -90


def test_messages_merge_db_and_dedupe():
    db = [{"ts": NOW - 10.5, "sender_id": ME, "short_name": "T96A", "text": "yo", "snr": None, "rssi": None,
           "to": "!aaaa0002", "channel": 0, "broadcast": False, "mine": True},
          {"ts": NOW - 500, "sender_id": "!aaaa0003", "short_name": "OLD3", "text": "older", "snr": 1.0, "rssi": -100,
           "to": None, "channel": 2, "broadcast": True, "mine": False}]
    d = compact_messages(PKTS, db, ME, NOW, limit=20)
    assert [m["t"] for m in d["m"]] == ["yo", "hello world", "older"]           # "yo" only once
    assert d["m"][2]["c"] == 2


def test_messages_limit_maxlen_and_blank_dropped():
    pk = [{"kind": "text", "ts": NOW - i, "from": "!a", "from_name": "A", "broadcast": True, "channel": 0, "text": "x" * 300} for i in range(60)]
    pk.append({"kind": "text", "ts": NOW, "from": "!a", "from_name": "A", "broadcast": True, "channel": 0, "text": "\U0001F31E"})
    d = compact_messages(pk, [], ME, NOW, limit=10)
    assert len(d["m"]) == 10 and all(len(m["t"]) <= 160 for m in d["m"])
    assert compact_messages(pk, [], ME, NOW, limit=1, maxlen=40)["m"][0]["t"] == "x" * 40


def test_channels_drop_disabled_and_no_keys():
    cfg = [{"index": 0, "name": "LongFast", "role": "PRIMARY", "psk_set": True, "default_psk": True},
           {"index": 1, "name": "PrivateChat", "role": "SECONDARY", "psk_set": True},
           {"index": 4, "name": "", "role": "DISABLED"}, {"index": 5, "role": "DISABLE"}]
    d = compact_channels(cfg)
    assert d == {"c": [{"i": 0, "n": "LongFast", "r": "P"}, {"i": 1, "n": "PrivateChat", "r": "S"}]}
    assert "psk" not in json.dumps(d)


# ---- map support: pos filter, my.lat/lon, withpos; sos flag -------------------------------------

def _many(n_pos, n_nopos):
    r = {ME: {"id": ME, "short_name": "T96A", "position": {"lat": 39.0, "lon": -84.0}, "last_heard": NOW}}
    for i in range(n_pos):
        r["!p%07d" % i] = {"id": "!p%07d" % i, "short_name": "P%d" % i, "long_name": "Ridge %d" % i,
                           "last_heard": NOW - i, "transport": "rf", "snr": 4.5, "hops_away": 1, "battery": 50,
                           "hw": "TBEAM", "position": {"lat": 39.123456 + i / 1000, "lon": -84.123456}}
    for i in range(n_nopos):
        r["!n%07d" % i] = {"id": "!n%07d" % i, "short_name": "N%d" % i, "last_heard": NOW - 5000 - i}
    return r


def test_pos_filter_and_withpos():
    out = compact_nodes(_many(5, 7), {}, ME, NOW, limit=40, pos_only=True)
    assert len(out["n"]) == 5 and all("lat" in r and "lon" in r for r in out["n"])
    assert out["withpos"] == 5 and out["total"] == 13
    assert ME not in [r["id"] for r in out["n"]]
    full = compact_nodes(_many(5, 7), {}, ME, NOW, limit=40)
    assert len(full["n"]) == 12 and full["withpos"] == 5


def test_withpos_independent_of_limit_and_pos_still_newest_first():
    out = compact_nodes(_many(30, 3), {}, ME, NOW, limit=4, pos_only=True)
    assert len(out["n"]) == 4 and out["withpos"] == 30
    assert [r["age"] for r in out["n"]] == sorted(r["age"] for r in out["n"])


def test_my_lat_lon_present_and_absent():
    out = compact_nodes(_many(1, 0), {}, ME, NOW)
    assert out["my"]["lat"] == 39.0 and out["my"]["lon"] == -84.0
    assert {"id", "s", "l", "bat", "volt"} <= set(out["my"])
    r = _many(1, 0)
    del r[ME]["position"]
    assert "lat" not in compact_nodes(r, {}, ME, NOW)["my"]
    r[ME]["position"] = {"lat": None, "lon": 1}
    assert "lon" not in compact_nodes(r, {}, ME, NOW)["my"]


def test_pos_payload_small_and_ascii():
    out = compact_nodes(_many(60, 0), {}, ME, NOW, limit=40, pos_only=True)
    raw = json.dumps(out, separators=(",", ":"))
    assert len(out["n"]) == 40 and len(raw) < 6000
    raw.encode("ascii")


def test_sos_flag_only_on_distress_from_others():
    pk = [
        {"kind": "text", "ts": NOW - 5, "from": "!aaaa0001", "from_name": "OLD", "text": "SOS need help", "channel": 0},
        {"kind": "text", "ts": NOW - 6, "from": "!aaaa0001", "text": "lunch at noon", "channel": 0},
        {"kind": "text", "ts": NOW - 7, "from": ME, "text": "SOS sent by us", "sent": True, "channel": 0},
        {"kind": "text", "ts": NOW - 8, "from": "!aaaa0002", "text": "plain but flagged", "sos": True},
    ]
    m = {x["t"]: x for x in compact_messages(pk, [], ME, NOW)["m"]}
    assert m["SOS need help"]["sos"] is True
    assert "sos" not in m["lunch at noon"]
    assert "sos" not in m["SOS sent by us"]
    assert m["plain but flagged"]["sos"] is True
