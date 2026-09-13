"""Operator console: sends ride the bus, get tracked queued → sent → delivered/failed,
show up in the feed, and are logged like the BBS's own replies."""
from __future__ import annotations

import sqlite3

import pytest

flask = pytest.importorskip("flask")
flask_socketio = pytest.importorskip("flask_socketio")

from pathlib import Path
from wildcat.bus import MemoryBus
from wildcat.config import build
from wildcat.observatory.bridge import Bridge, State
from wildcat.observatory.web import create_blueprint

ROOT = Path(__file__).resolve().parent.parent
BASE, GO = "!9e766b18", "!716c668c"


def test_state_tx_lifecycle():
    s = State(); s.apply_roster({"my_id": BASE, "roster": {GO: {"id": GO, "short_name": "GO"}}})
    rec = s.new_tx("obs-1", GO, GO, "hello", 0, 100.0, False)
    assert rec["state"] == "queued" and rec["to_name"] == "GO" and s.packets[-1]["sent"] is True and s.packets[-1]["from_name"] == "you"
    assert s.apply_tx_result({"id": "obs-1", "index": 0, "count": 1, "ok": True, "packetId": 4242}, 101.0)["state"] == "sent"
    assert s.packets[-1]["state"] == "sent"
    # the destination's routing ACK references our packet id
    ack = s.apply_routing({"kind": "routing", "from": GO, "packet": {"decoded": {"portnum": "ROUTING_APP", "requestId": 4242, "routing": {"errorReason": "NONE"}}}}, 103.0)
    assert ack["state"] == "delivered" and ack["acked_by"] == GO and s.packets[-1]["state"] == "delivered"
    assert s.apply_routing({"kind": "routing", "packet": {"decoded": {"requestId": 9999}}}, 104.0) is None   # not ours
    # a failed send
    s.new_tx("obs-2", GO, GO, "x", 0, 110.0, False)
    f = s.apply_tx_result({"id": "obs-2", "ok": False, "error": "bad 'to'"}, 111.0)
    assert f["state"] == "failed" and f["error"] == "bad 'to'"
    # routing error after a sent packet
    s.new_tx("obs-3", GO, GO, "y", 0, 120.0, False); s.apply_tx_result({"id": "obs-3", "ok": True, "packetId": 7, "count": 1}, 121.0)
    e = s.apply_routing({"kind": "routing", "from": GO, "packet": {"decoded": {"requestId": 7, "routing": {"errorReason": "NO_ROUTE"}}}}, 122.0)
    assert e["state"] == "failed" and "NO_ROUTE" in e["error"]
    # broadcasts are final at 'sent'
    s.new_tx("obs-4", "^all", None, "hi all", 0, 130.0, True)
    assert s.apply_tx_result({"id": "obs-4", "ok": True, "packetId": 8, "count": 1}, 131.0)["state"] == "sent"
    assert [t["id"] for t in s.snapshot(200.0)["tx"]] == ["obs-1", "obs-2", "obs-3", "obs-4"]


@pytest.fixture
def den(tmp_path):
    db = tmp_path / "b.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, sender_short_name TEXT, to_id INTEGER, channel_index INTEGER, message TEXT, snr REAL, rssi INTEGER, hop_limit INTEGER)")
    c.commit(); c.close()
    (tmp_path / "content").mkdir()
    cfg = build({"radio": {"type": "serial"}, "mqtt": {"enabled": True}, "bbs": {"source": "bus", "content_dir": str(tmp_path / "content")},
                 "telemetry": {"source": "bus"}, "database": {"path": str(db)}})
    app = flask.Flask("obs-compose", template_folder=str(ROOT / "observatory" / "templates"), static_folder=str(ROOT / "observatory" / "static"))
    sio = flask_socketio.SocketIO(app, async_mode="threading")
    bridge = Bridge(cfg, sio)
    bridge.bus = MemoryBus(); bridge.wire(bridge.bus)   # stand-in for Mosquitto
    bridge.state.apply_roster({"my_id": BASE, "roster": {BASE: {"id": BASE, "short_name": "6b18"}, GO: {"id": GO, "short_name": "GO"}}})
    app.register_blueprint(create_blueprint(bridge, sio))
    return app.test_client(), bridge, db


def test_send_dm_rides_the_bus_and_is_logged(den):
    client, bridge, db = den
    r = client.post("/v2/api/tx", json={"to": GO, "text": "hey GO"})
    assert r.status_code == 200, r.get_json()
    j = r.get_json(); assert j["state"] == "queued" and j["to_name"] == "GO"
    tx = bridge.bus.last("tx")
    assert tx["to"] == GO and tx["text"] == "hey GO" and tx["wantAck"] is True and tx["id"] == j["id"] and tx["channel"] == 0
    rows = sqlite3.connect(db).execute("SELECT sender_id, sender_short_name, to_id, message FROM message_logs").fetchall()
    assert rows == [(BASE, "6b18", int("716c668c", 16), "hey GO")]
    # meshd answers on tx/result → state sent; the API shows it
    bridge.bus.publish("tx/result", {"id": j["id"], "index": 0, "count": 1, "ok": True, "packetId": 55})
    assert client.get(f"/v2/api/tx/{j['id']}").get_json()["state"] == "sent"
    assert client.get("/v2/api/tx").get_json()["sends"][0]["packet_id"] == 55
    # the feed snapshot carries it as a sent packet
    snap = client.get("/v2/api/state").get_json()
    assert snap["packets"][-1]["sent"] is True and snap["packets"][-1]["state"] == "sent"


def test_send_broadcast_and_validation(den):
    client, bridge, db = den
    r = client.post("/v2/api/tx", json={"to": "^all", "text": "hello mesh", "channel": 1})
    assert r.status_code == 200 and r.get_json()["broadcast"] is True
    tx = bridge.bus.last("tx"); assert tx["to"] == "^all" and tx["wantAck"] is False and tx["channel"] == 1
    assert sqlite3.connect(db).execute("SELECT to_id, channel_index FROM message_logs").fetchall() == [(4294967295, 1)]
    assert client.post("/v2/api/tx", json={"to": "GO", "text": "x"}).status_code == 400
    assert client.post("/v2/api/tx", json={"to": GO, "text": "x" * 201}).status_code == 400
    assert client.post("/v2/api/tx", json={"to": GO, "text": "x", "channel": 9}).status_code == 400
    assert client.get("/v2/api/tx/nope").status_code == 404
