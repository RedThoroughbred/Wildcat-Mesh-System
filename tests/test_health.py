from __future__ import annotations

from wildcat.observatory import health as H

NOW = 1_700_000_000.0
BASE = "!9e766b18"


def roster(**over):
    r = {BASE: {"id": BASE, "short_name": "6b18", "channel_util": 5.0, "air_util_tx": 1.0, "battery": 101, "last_heard": NOW},
         "!a": {"id": "!a", "short_name": "STAY", "last_heard": NOW - 60}, "!b": {"id": "!b", "short_name": "GO", "last_heard": NOW - 3 * 3600}}
    for k, v in over.items(): r[k].update(v)
    return r


def base_args(**kw):
    a = dict(now=NOW, my_id=BASE, roster=roster(), meshd={"state": "connected"}, bus_connected=True, rate_buckets=[],
             low_battery=[], regular_counts={}, last_packet_ts=NOW - 30)
    a.update(kw); return a


def test_all_clear():
    r = H.compute(**base_args())
    assert r["level"] == "ok" and r["score"] == 100 and r["alerts"] == [] and r["gauges"]["channel_util"] == 5.0


def test_utilization_and_airtime():
    r = H.compute(**base_args(roster=roster(**{BASE: {"channel_util": 30.0}})))
    assert r["level"] == "warn" and r["alerts"][0]["kind"] == "util"
    r = H.compute(**base_args(roster=roster(**{BASE: {"channel_util": 45.0, "air_util_tx": 12.0}})))
    assert r["level"] == "crit" and [a["kind"] for a in r["alerts"]] == ["util", "airtime"] and r["score"] == 100 - 25 - 8


def test_battery_levels_and_names():
    r = H.compute(**base_args(low_battery=[{"id": "!a", "battery": 15, "voltage": 3.5}, {"id": "!zz", "battery": 5, "voltage": None}]))
    kinds = [(a["level"], a["text"][:4]) for a in r["alerts"]]
    assert ("crit", "!zz "[:4]) in kinds or any(a["level"] == "crit" and "!zz"[-4:] in a["text"] for a in r["alerts"])
    assert any(a["level"] == "warn" and a["text"].startswith("STAY battery 15%") for a in r["alerts"])


def test_gone_quiet_requires_regularity():
    r = H.compute(**base_args(regular_counts={"!b": 12}))
    assert r["quiet"] == [{"id": "!b", "name": "GO", "silent_for": 3 * 3600, "was": 12}]
    assert r["alerts"][0]["kind"] == "quiet" and "GO has gone quiet" in r["alerts"][0]["text"]
    assert H.compute(**base_args(regular_counts={"!b": 1}))["quiet"] == []          # a one-off node isn't "quiet"
    assert H.compute(**base_args(regular_counts={"!a": 12}))["quiet"] == []         # heard a minute ago


def test_bus_meshd_silence():
    assert H.compute(**base_args(bus_connected=False))["alerts"][0]["kind"] == "meshd"
    r = H.compute(**base_args(meshd={"state": "disconnected", "error": "refused"}))
    assert r["level"] == "crit" and "refused" in r["alerts"][0]["text"]
    r = H.compute(**base_args(last_packet_ts=NOW - 20 * 60))
    assert r["alerts"][0]["kind"] == "silence" and "20 min" in r["alerts"][0]["text"]


def test_rate_buckets_and_regular_counts():
    b = H.rate_buckets([NOW - 10, NOW - 20, NOW - 5 * 3600, NOW - 99999], NOW, hours=6, buckets=6)
    assert [x["n"] for x in b] == [0, 1, 0, 0, 0, 2]      # NOW−5h lands in bucket 1 of 6
    ev = [{"ts": NOW - 3 * 3600, "from": "!b"}, {"ts": NOW - 4 * 3600, "from": "!b"}, {"ts": NOW - 60, "from": "!b"}, {"ts": NOW - 30 * 3600, "from": "!b"}]
    assert H.regular_counts(ev, NOW) == {"!b": 2}
