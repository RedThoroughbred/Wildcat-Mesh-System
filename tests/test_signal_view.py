"""Signal dashboard: pure shaping functions + the /v2 routes end-to-end (no broker, no radio)."""
from __future__ import annotations

import time
from collections import deque
from pathlib import Path

import pytest

from wildcat.observatory import signal_view as SV

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000.0
BASE = "!ba5a3f4b"


def _roster():
    return {
        BASE: {"id": BASE, "short_name": "T96A", "position": {"lat": 38.88, "lon": -84.617}},
        "!a1": {"id": "!a1", "short_name": "GO", "long_name": "Wildcat Go", "transport": "rf", "hops_away": 0, "last_heard": NOW - 30,
                "snr": 6.0, "position": {"lat": 38.89, "lon": -84.617}},
        "!a2": {"id": "!a2", "short_name": "FAR", "transport": "rf", "hops_away": 1, "last_heard": NOW - 90,
                "snr": -12.5, "position": {"lat": 38.88, "lon": -84.517}},
        "!a3": {"id": "!a3", "short_name": "MID", "transport": "both", "hops_away": 0, "last_heard": NOW - 10, "snr": -3.0},
        "!a4": {"id": "!a4", "short_name": "NET", "transport": "mqtt", "snr": -9.0, "last_heard": NOW - 5},
        "!a5": {"id": "!a5", "short_name": "NEW", "transport": "rf", "last_heard": NOW - 200},
    }


def _rings():
    return {
        "!a1": deque([{"ts": NOW - 60, "snr": 5.0, "rssi": -30, "hops": 0}, {"ts": NOW - 30, "snr": 6.0, "rssi": -28, "hops": 0}], maxlen=300),
        "!a2": deque([{"ts": NOW - 120, "snr": -12.5, "rssi": -118, "hops": 1}], maxlen=300),
        "!a3": deque([{"ts": NOW - 10, "snr": -3.0, "rssi": None, "hops": 0}], maxlen=300),
    }


def test_classify_snr_first_then_rssi():
    assert SV.classify(6) == "strong" and SV.classify(0) == "strong"
    assert SV.classify(-0.1) == "ok" and SV.classify(-10) == "ok"
    assert SV.classify(-10.1) == "weak"
    assert SV.classify(None, -95) == "strong" and SV.classify(None, -110) == "ok" and SV.classify(None, -120) == "weak"
    assert SV.classify(-20, -50) == "weak", "SNR wins over RSSI"
    assert SV.classify(None, None) is None and SV.classify(True) is None


def test_bearing_and_distance():
    b, km = SV.bearing_km((38.88, -84.617), (38.89, -84.617))
    assert abs(b - 0) < 0.5 and abs(km - 1.11) < 0.02
    b, km = SV.bearing_km((38.88, -84.617), (38.88, -84.517))
    assert abs(b - 90) < 0.5 and abs(km - 8.66) < 0.1
    b, _ = SV.bearing_km((38.88, -84.617), (38.78, -84.717))
    assert 180 < b < 270


def test_overview_orders_worst_first_and_skips_internet_only():
    o = SV.signal_overview(_roster(), _rings(), BASE, NOW)
    ids = [n["id"] for n in o["nodes"]]
    assert ids == ["!a2", "!a3", "!a1", "!a5"], "worst SNR first, unmeasured last, mqtt-only and base excluded"
    s = o["summary"]
    assert s["rf"] == 4 and s["measured"] == 3 and s["internet_only"] == 1
    assert s["best"]["id"] == "!a1" and s["worst"]["id"] == "!a2" and s["avg_snr"] == round((6 - 12.5 - 3) / 3, 1)
    assert s["classes"] == {"strong": 1, "ok": 1, "weak": 1}
    assert sum(s["hist"]["counts"]) == 3 and len(s["hist"]["counts"]) == len(SV.HIST_EDGES) + 1
    go = next(n for n in o["nodes"] if n["id"] == "!a1")
    assert go["snr"] == 6.0 and go["rssi"] == -28 and go["cls"] == "strong" and go["n"] == 2 and go["spark"] == [5.0, 6.0]
    assert go["brg"] == 0 and 1.0 < go["km"] < 1.2 and go["lat"] == 38.89
    assert "brg" not in next(n for n in o["nodes"] if n["id"] == "!a3"), "no position, no bearing"
    assert o["my"]["lat"] == 38.88 and o["thresholds"]["weak_snr"] == -10.0


def test_overview_max_age_hides_stale_links_but_counts_them():
    o = SV.signal_overview(_roster(), _rings(), BASE, NOW, max_age=100)
    ids = {n["id"] for n in o["nodes"]}
    assert ids == {"!a1", "!a2", "!a3"} and o["summary"]["older"] == 1 and o["max_age"] == 100
    o = SV.signal_overview(_roster(), _rings(), BASE, NOW, max_age=60)
    assert {n["id"] for n in o["nodes"]} == {"!a1", "!a3"} and o["summary"]["older"] == 2
    assert SV.signal_overview(_roster(), _rings(), BASE, NOW)["summary"]["older"] == 0


def test_overview_empty_and_no_base_position():
    o = SV.signal_overview({}, {}, None, NOW)
    assert o["nodes"] == [] and o["summary"]["best"] is None and o["summary"]["avg_snr"] is None
    r = _roster(); r[BASE].pop("position")
    o = SV.signal_overview(r, _rings(), BASE, NOW)
    assert all("brg" not in n for n in o["nodes"]) and "lat" not in o["my"]


def test_history_merges_db_but_ring_wins_and_after_is_ring_only():
    node = _roster()["!a1"]
    db = [{"ts": int(NOW - 60), "snr": 99, "rssi": None, "hops": None},        # same second as a ring sample: dropped
          {"ts": int(NOW - 500), "snr": 4.0, "rssi": -40, "hops": None}]
    h = SV.signal_history(node, _rings()["!a1"], db, NOW, minutes=30)
    assert [s[0] for s in h["samples"]] == [int(NOW - 500), int(NOW - 60), int(NOW - 30)]
    assert h["snr"] == 6.0 and h["rssi"] == -28 and h["cls"] == "strong"
    assert h["stats"]["n"] == 3 and h["stats"]["snr_min"] == 4.0 and h["stats"]["snr_max"] == 6.0
    live = SV.signal_history(node, _rings()["!a1"], db, NOW, minutes=30, after=NOW - 45)
    assert [s[0] for s in live["samples"]] == [int(NOW - 30)], "after= serves only the live ring, newer only"
    old = SV.signal_history(node, _rings()["!a1"], [], NOW, minutes=1)
    assert [s[0] for s in old["samples"]] == [int(NOW - 60), int(NOW - 30)]
    assert SV.signal_history(node, _rings()["!a1"], [], NOW, minutes=99999)["minutes"] == 1440.0, "window is capped at 24 h"


def test_history_no_samples_falls_back_to_roster_snr():
    h = SV.signal_history(_roster()["!a5"], deque(), [], NOW)
    assert h["samples"] == [] and h["snr"] is None and h["cls"] is None
    h = SV.signal_history(_roster()["!a3"], deque(), [], NOW)
    assert h["snr"] == -3.0 and h["cls"] == "ok"


flask = pytest.importorskip("flask")
flask_socketio = pytest.importorskip("flask_socketio")
from wildcat.config import build
from wildcat.observatory.bridge import Bridge
from wildcat.observatory.web import create_blueprint


@pytest.fixture
def client(tmp_path):
    (tmp_path / "content").mkdir()
    cfg = build({"radio": {"type": "serial"}, "database": {"path": str(tmp_path / "b.db")},
                 "bbs": {"content_dir": str(tmp_path / "content")}})
    app = flask.Flask("sig-test", template_folder=str(ROOT / "observatory" / "templates"),
                      static_folder=str(ROOT / "observatory" / "static"))
    sio = flask_socketio.SocketIO(app, async_mode="threading")
    bridge = Bridge(cfg, sio)
    now = time.time()
    bridge.state.apply_roster({"my_id": BASE, "roster": {
        BASE: {"id": BASE, "short_name": "T96A", "position": {"lat": 38.88, "lon": -84.617}},
        "!a1": {"id": "!a1", "short_name": "GO", "transport": "rf", "last_heard": now - 5, "snr": 6.0, "position": {"lat": 38.89, "lon": -84.6}},
        "!a2": {"id": "!a2", "short_name": "NET", "transport": "mqtt", "last_heard": now - 5, "snr": -9.0}}})
    # real packets through the real folding path: RF samples build the ring, an MQTT one must not
    bridge.state.apply_packet({"from": "!a1", "kind": "text", "proto": "meshtastic", "received_at": int(now) - 20,
                               "rx": {"snr": 4.5, "rssi": -31, "hops": 0, "via_mqtt": False}}, now - 20)
    bridge.state.apply_packet({"from": "!a1", "kind": "telemetry", "proto": "meshtastic", "received_at": int(now) - 5,
                               "rx": {"snr": 6.25, "rssi": -29, "hops": 0, "via_mqtt": False}}, now - 5)
    bridge.state.apply_packet({"from": "!a2", "kind": "text", "proto": "meshtastic", "received_at": int(now) - 4,
                               "rx": {"snr": -9.0, "rssi": -100, "hops": 3, "via_mqtt": True}}, now - 4)
    app.register_blueprint(create_blueprint(bridge, sio))
    return app.test_client()


def test_signal_page_renders(client):
    r = client.get("/v2/signal")
    assert r.status_code == 200 and r.mimetype == "text/html"
    assert b"/v2/api/signal" in r.data and b"vendor/leaflet/leaflet.js" in r.data


def test_signal_overview_route_uses_real_rings(client):
    j = client.get("/v2/api/signal").get_json()
    assert [n["id"] for n in j["nodes"]] == ["!a1"], "internet-only node is not a radio link"
    n = j["nodes"][0]
    assert n["snr"] == 6.25 and n["rssi"] == -29 and n["cls"] == "strong" and n["n"] == 2 and n["spark"] == [4.5, 6.25]
    assert j["summary"]["internet_only"] == 1 and j["summary"]["rf"] == 1 and j["my"]["s"] == "T96A"
    assert client.get("/v2/api/signal?max_age=60").get_json()["summary"]["rf"] == 1
    assert client.get("/v2/api/signal?max_age=junk").status_code == 200


def test_signal_node_route(client):
    j = client.get("/v2/api/signal/!a1?minutes=30").get_json()
    assert j["id"] == "!a1" and len(j["samples"]) == 2 and j["samples"][-1][1] == 6.25 and j["stats"]["snr_min"] == 4.5
    newest = j["samples"][-1][0]
    j2 = client.get("/v2/api/signal/!a1?after=%d" % newest).get_json()
    assert [s[0] for s in j2["samples"]] == [newest]
    assert client.get("/v2/api/signal/!a2").get_json()["samples"] == [], "MQTT packet left no sample"
    assert client.get("/v2/api/signal/!nope").status_code == 404
    assert client.get("/v2/api/signal/!a1?minutes=abc&after=zzz").status_code == 200


def test_signal_routes_are_read_only(client):
    assert client.post("/v2/api/signal").status_code in (401, 405)
