"""The /v2 blueprint end-to-end through Flask's test client (no broker, no radio)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")
flask_socketio = pytest.importorskip("flask_socketio")

from wildcat.config import build
from wildcat.observatory.bridge import Bridge
from wildcat.observatory.web import create_blueprint

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path):
    (tmp_path / "content").mkdir()
    cfg = build({"radio": {"type": "serial"}, "database": {"path": str(tmp_path / "b.db")},
                 "bbs": {"content_dir": str(tmp_path / "content")}})
    app = flask.Flask("obs-test", template_folder=str(ROOT / "observatory" / "templates"),
                      static_folder=str(ROOT / "observatory" / "static"))
    sio = flask_socketio.SocketIO(app, async_mode="threading")
    bridge = Bridge(cfg, sio)
    bridge.state.apply_roster({"my_id": "!9e766b18", "roster": {"!9e766b18": {"id": "!9e766b18", "short_name": "6b18",
                               "position": {"lat": 38.88, "lon": -84.62, "alt": 278}}}})
    app.register_blueprint(create_blueprint(bridge, sio))
    return app.test_client()


def test_page_and_state(client):
    r = client.get("/v2/")
    assert r.status_code == 200 and b'rel="manifest"' in r.data and b"apple-touch-icon" in r.data
    s = client.get("/v2/api/state").get_json()
    assert s["my_id"] == "!9e766b18" and s["mqtt_enabled"] is False and s["stats"]["on_map"] == 1


def test_manifest_and_service_worker(client):
    m = client.get("/v2/manifest.webmanifest")
    assert m.status_code == 200 and m.mimetype == "application/manifest+json"
    data = json.loads(m.data)
    assert data["scope"] == "/v2/" and data["display"] == "standalone" and len(data["icons"]) == 3
    for icon in data["icons"]:
        assert (ROOT / "observatory" / "static" / icon["src"].split("/static/")[1]).is_file(), icon["src"]
    sw = client.get("/v2/sw.js")
    assert sw.status_code == 200 and sw.headers["Service-Worker-Allowed"] == "/v2/"
    assert sw.mimetype == "application/javascript" and b"SHELL_URLS" in sw.data


def test_icons_are_real_pngs():
    for name in ("icon-192.png", "icon-512.png", "icon-512-maskable.png", "apple-touch-icon.png"):
        data = (ROOT / "observatory" / "static" / "v2" / "icons" / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 2000, name


def test_coverage_and_node_and_health(client):
    assert client.get("/v2/api/coverage").get_json()["summary"]["count"] == 0
    assert client.get("/v2/api/node/!9e766b18").get_json()["node"]["short_name"] == "6b18"
    assert client.get("/v2/api/node/!nobody").status_code == 404
    assert client.get("/v2/api/health").get_json()["ok"] is True


def test_public_view_and_history(client):
    r = client.get("/v2/public")
    assert r.status_code == 200 and b'class="public"' in r.data
    assert b'class="public"' not in client.get("/v2/").data
    h = client.get("/v2/api/history?hours=1").get_json()
    assert h["count"] == 0 and h["hours"] == 1
    assert client.get("/v2/api/history?hours=9999").get_json()["hours"] == 168


def test_admin_endpoints(client, tmp_path):
    c = client.get("/v2/api/config").get_json()
    assert c["kind"] == "defaults" or c["kind"] in ("toml", "legacy-ini", "defaults")
    assert "[radio]" in c["toml"]
    # content editor round-trip goes to the configured content dir
    r = client.post("/v2/api/content/fortunes", json={"text": "one\ntwo\n"})
    assert r.status_code == 200 and r.get_json()["lines"] == 3
    assert client.get("/v2/api/content/fortunes").get_json()["text"] == "one\ntwo\n"
    assert client.post("/v2/api/content/messages", json={"text": "{not json"}).status_code == 400
    assert client.get("/v2/api/content/nope").status_code == 404
    # tx without a bus is refused clearly; bad text rejected
    assert client.post("/v2/api/tx", json={"text": "hi"}).status_code == 503
    assert client.post("/v2/api/restart", json={"unit": "evil"}).status_code == 400
    sv = client.get("/v2/api/services").get_json()
    assert sv["mqtt_enabled"] is False and "uptime" in sv


def test_exports_and_config_editor(tmp_path):
    (tmp_path / "content").mkdir()
    toml = tmp_path / "wildcat.toml"
    toml.write_text('radio.type = "serial"\n[database]\npath = "%s"\n[bbs]\ncontent_dir = "%s"\n' % (tmp_path / "b.db", tmp_path / "content"))
    from wildcat.config import load_file
    cfg = load_file(toml)
    app = flask.Flask("obs-test2", template_folder=str(ROOT / "observatory" / "templates"), static_folder=str(ROOT / "observatory" / "static"))
    sio = flask_socketio.SocketIO(app, async_mode="threading")
    bridge = Bridge(cfg, sio)
    app.register_blueprint(create_blueprint(bridge, sio))
    c = app.test_client()
    r = c.get("/v2/api/export/nodes.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv" and r.data.startswith(b"id,short_name")
    assert c.get("/v2/api/export/messages.csv").status_code == 200 and c.get("/v2/api/export/coverage.csv").status_code == 200
    g = c.get("/v2/api/config").get_json()
    assert g["editable"] is True and g["bbs"]["menu"]["main"][0] == "W"
    r = c.post("/v2/api/config", json={"name": "Test BBS", "sync_nodes": ["!17d7e4b7"], "menu": {"main": ["q", "x"]}})
    assert r.status_code == 200, r.get_json()
    j = r.get_json(); assert j["bbs"]["name"] == "Test BBS" and j["bbs"]["menu"]["main"] == ["Q", "X"] and j["bbs"]["sync_nodes"] == ["!17d7e4b7"]
    assert (tmp_path / "wildcat.toml.bak").exists()
    again = load_file(toml)
    assert again.bbs.name == "Test BBS" and again.bbs.menu.main == ["Q", "X"] and again.radio.type == "serial"
    bad = c.post("/v2/api/config", json={"menu": {"main": ["QQ"]}})
    assert bad.status_code == 400 and "[bbs.menu].main" in bad.get_json()["error"]


def test_responder_switch_writes_the_file_and_flips_the_bus(tmp_path):
    from wildcat.bus import MemoryBus
    from wildcat.config import load_file
    (tmp_path / "content").mkdir()
    toml = tmp_path / "wildcat.toml"
    toml.write_text('radio.type = "serial"\n[mqtt]\nenabled = true\n[bbs]\nsource = "bus"\ncontent_dir = "%s"\n[telemetry]\nsource = "bus"\n[database]\npath = "%s"\n'
                    % (tmp_path / "content", tmp_path / "b.db"))
    cfg = load_file(toml)
    assert cfg.brain.enabled is False                      # the default: off
    app = flask.Flask("obs-test3", template_folder=str(ROOT / "observatory" / "templates"), static_folder=str(ROOT / "observatory" / "static"))
    sio = flask_socketio.SocketIO(app, async_mode="threading")
    bridge = Bridge(cfg, sio)
    app.register_blueprint(create_blueprint(bridge, sio))
    c = app.test_client()
    # no bus wired → refuse (and touch nothing)
    r = c.post("/v2/api/brain/responder", json={"enabled": True})
    assert r.status_code == 409 and not (tmp_path / "wildcat.toml.bak").exists()
    bus = MemoryBus(); bridge.bus = bus; bridge.wire(bus)         # what start() does with the real MqttBus
    st = c.get("/v2/api/brain/status").get_json()["responder"]
    assert st["running"] is False and st["enabled"] is False and st["config_enabled"] is False and "cost_note" in st
    assert c.post("/v2/api/brain/responder", json={"enabled": "yes"}).status_code == 400
    r = c.post("/v2/api/brain/responder", json={"enabled": True})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["enabled"] is True and j["responder"]["config_enabled"] is True
    assert bus.last("brain/control")["enabled"] is True and bus.last("brain/control")["by"] == "observatory"
    assert (tmp_path / "wildcat.toml.bak").exists() and load_file(toml).brain.enabled is True
    # the running responder's retained status is what the panel shows
    bus.publish("brain/status", {"running": True, "enabled": True, "counts": {"hour": 2, "day": 5}, "brake": False, "providers": ["claude-cli", "canned"]}, retain=True)
    st = c.get("/v2/api/brain/status").get_json()["responder"]
    assert st["running"] is True and st["counts"] == {"hour": 2, "day": 5} and st["providers"] == ["claude-cli", "canned"]
    snap = c.get("/v2/api/state").get_json()
    assert snap["brain_status"]["running"] is True
    r = c.post("/v2/api/brain/responder", json={"enabled": False})
    assert r.status_code == 200 and bus.last("brain/control")["enabled"] is False and load_file(toml).brain.enabled is False
    # an exchange with a reason is kept verbatim for the panel
    bus.publish("brain/exchange", {"node": "!716c668c", "prompt": "hi", "reply": "", "provider": None, "status": "off",
                                   "reason": "responder is off", "chunks": 0, "latency_ms": 0, "ts": 1})
    x = c.get("/v2/api/state").get_json()["brain"][-1]
    assert x["status"] == "off" and x["reason"] == "responder is off" and x["node"] == "!716c668c"


def test_sos_routes_broadcast_at_top_priority_and_stop(tmp_path):
    from wildcat.bus import MemoryBus
    (tmp_path / "content").mkdir()
    cfg = build({"radio": {"type": "serial"}, "mqtt": {"enabled": True}, "bbs": {"source": "bus", "content_dir": str(tmp_path / "content")},
                 "telemetry": {"source": "bus"}, "database": {"path": str(tmp_path / "b.db")}})
    app = flask.Flask("obs-test4", template_folder=str(ROOT / "observatory" / "templates"), static_folder=str(ROOT / "observatory" / "static"))
    sio = flask_socketio.SocketIO(app, async_mode="threading")
    bridge = Bridge(cfg, sio)
    app.register_blueprint(create_blueprint(bridge, sio))
    c = app.test_client()
    assert c.post("/v2/api/sos", json={"text": "help"}).status_code == 503          # no bus → nothing owns the radio
    bus = MemoryBus(); bridge.bus = bus; bridge.wire(bus)
    bus.publish("nodes", {"my_id": "!9e766b18", "roster": {"!9e766b18": {"id": "!9e766b18", "short_name": "6b18", "position": {"lat": 38.88, "lon": -84.62}}}}, retain=True)
    assert c.get("/v2/api/sos").get_json()["active"] is False
    assert c.post("/v2/api/sos", json={"text": ""}).status_code == 400
    assert c.post("/v2/api/sos", json={"text": "x", "repeat": 3, "interval": 5}).status_code == 400
    r = c.post("/v2/api/sos", json={"text": "Barn fire on Rich Road, need pumps", "repeat": 3, "interval": 120})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["active"] is True and j["sent"] == 1 and j["message"].startswith("🆘 SOS from 6b18: Barn fire on Rich Road, need pumps @ 38.88000,-84.62000 [1/3]")
    tx = [p for t, p in bus.published if t == "wildcat/tx"][-1]
    assert tx["priority"] == 0 and tx["to"] == "^all" and tx["id"].startswith("sos-") and tx["text"] == j["message"]
    assert bus.last("alert/sos")["active"] is True and bus.last("alert/sos")["sent"] == 1
    snap = c.get("/v2/api/state").get_json()
    assert snap["sos"]["active"] is True and snap["packets"][-1]["sos"] is True and snap["packets"][-1]["sent"] is True
    assert snap["tx"][-1]["sos"] is True and snap["tx"][-1]["id"] == j["last_tx_id"]
    r = c.delete("/v2/api/sos")
    assert r.status_code == 200 and r.get_json()["ended"] == "stopped" and bus.last("alert/sos")["active"] is False


def test_digest_routes_generate_store_and_post_as_bulletin(tmp_path, monkeypatch):
    import sqlite3
    from wildcat.brain import cli as bcli
    (tmp_path / "content").mkdir()
    dbp = tmp_path / "b.db"
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, sender_short_name TEXT, to_id INTEGER, channel_index INTEGER, message TEXT, snr REAL, rssi INTEGER, hop_limit INTEGER)")
    c.execute("INSERT INTO message_logs (timestamp,sender_id,sender_short_name,to_id,channel_index,message,snr,rssi) VALUES (strftime('%s','now')-100,'!716c668c','GO',4294967295,0,'hi',5.0,-80)")
    c.commit(); c.close()
    cfg = build({"radio": {"type": "serial"}, "database": {"path": str(dbp)}, "bbs": {"content_dir": str(tmp_path / "content")}})
    app = flask.Flask("obs-test5", template_folder=str(ROOT / "observatory" / "templates"), static_folder=str(ROOT / "observatory" / "static"))
    sio = flask_socketio.SocketIO(app, async_mode="threading")
    bridge = Bridge(cfg, sio)
    bridge.state.apply_roster({"my_id": "!9e766b18", "roster": {"!9e766b18": {"id": "!9e766b18", "short_name": "6b18"}, "!716c668c": {"id": "!716c668c", "short_name": "GO"}}})
    app.register_blueprint(create_blueprint(bridge, sio))
    cl = app.test_client()
    assert cl.get("/v2/api/digest").get_json()["digests"] == []
    seen = {}
    def fake_stream(prompt, system, model, timeout=120, max_budget_usd=0.5, binary=None):
        seen["prompt"] = prompt
        yield {"type": "delta", "text": "GO said hi. Quiet otherwise."}
        yield {"type": "done", "text": "GO said hi. Quiet otherwise.", "cost_usd": 0.011, "ms": 9, "model": model, "is_error": False}
    monkeypatch.setattr(bcli, "stream", fake_stream)
    r = cl.post("/v2/api/digest", json={"hours": 24})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["text"] == "GO said hi. Quiet otherwise." and j["id"] == 1 and j["fallback"] is False and j["cost_usd"] == 0.011
    assert "Most active: GO (1)" in seen["prompt"] and j["facts"]["messages"] == 1
    lst = cl.get("/v2/api/digest").get_json()["digests"]
    assert len(lst) == 1 and lst[0]["id"] == 1 and lst[0]["source"] == "operator"
    assert cl.post("/v2/api/digest/99/bulletin", json={"board": "General"}).status_code == 404
    r = cl.post("/v2/api/digest/1/bulletin", json={"board": "General"})
    assert r.status_code == 200, r.get_json()
    row = sqlite3.connect(dbp).execute("SELECT board, sender_short_name, subject, content FROM bulletins").fetchone()
    assert row[0] == "General" and row[1] == "6b18" and row[2].startswith("Mesh digest ") and row[3] == "GO said hi. Quiet otherwise."
    assert cl.get("/v2/api/digest").get_json()["digests"][0]["bulletin_id"]
    # the CLI going away yields the fact sheet, not an error
    def dead_stream(*a, **k):
        yield {"type": "error", "text": "claude CLI not found"}
    monkeypatch.setattr(bcli, "stream", dead_stream)
    j = cl.post("/v2/api/digest", json={}).get_json()
    assert j["fallback"] is True and j["text"].startswith("Window: last 24 h") and j["model"] is None


def test_schedule_routes(tmp_path, monkeypatch):
    import sqlite3
    from wildcat.bus import MemoryBus
    from wildcat.brain import cli as bcli
    (tmp_path / "content").mkdir()
    dbp = tmp_path / "b.db"
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, sender_short_name TEXT, to_id INTEGER, channel_index INTEGER, message TEXT, snr REAL, rssi INTEGER, hop_limit INTEGER)")
    c.commit(); c.close()
    cfg = build({"radio": {"type": "serial"}, "mqtt": {"enabled": True}, "bbs": {"source": "bus", "content_dir": str(tmp_path / "content")},
                 "telemetry": {"source": "bus"}, "database": {"path": str(dbp)}})
    app = flask.Flask("obs-test6", template_folder=str(ROOT / "observatory" / "templates"), static_folder=str(ROOT / "observatory" / "static"))
    sio = flask_socketio.SocketIO(app, async_mode="threading")
    bridge = Bridge(cfg, sio)
    bridge.state.apply_roster({"my_id": "!9e766b18", "roster": {"!9e766b18": {"id": "!9e766b18", "short_name": "6b18"}}})
    app.register_blueprint(create_blueprint(bridge, sio))
    bus = MemoryBus(); bridge.bus = bus; bridge.wire(bus)
    cl = app.test_client()
    g = cl.get("/v2/api/schedules").get_json()
    assert g["schedules"] == [] and "digest_bulletin" in g["kinds"]
    assert cl.post("/v2/api/schedules", json={"kind": "broadcast", "time": "20:00", "payload": {"text": ""}}).status_code == 400
    assert cl.post("/v2/api/schedules", json={"kind": "bulletin", "time": "7:00", "payload": {"board": "Net", "subject": "s", "content": "c"}}).status_code == 200
    r = cl.post("/v2/api/schedules", json={"kind": "digest_bulletin", "name": "Morning digest", "time": "07:00", "payload": {"board": "Digest"}})
    assert r.status_code == 200 and r.get_json()["payload"] == {"board": "Digest", "hours": 24}
    did = r.get_json()["id"]
    r = cl.post("/v2/api/schedules", json={"kind": "broadcast", "name": "Net call", "time": "19:55", "days": [1], "payload": {"text": "Net in 5 on ch0", "channel": 0}})
    assert r.status_code == 200
    bid = r.get_json()["id"]
    assert len(cl.get("/v2/api/schedules").get_json()["schedules"]) == 3
    r = cl.patch(f"/v2/api/schedules/{did}", json={"enabled": False, "time": "06:30"})
    assert r.status_code == 200 and r.get_json()["enabled"] is False and r.get_json()["hour"] == 6 and r.get_json()["minute"] == 30
    assert cl.patch("/v2/api/schedules/99", json={"enabled": True}).status_code == 404
    # run now: the digest job writes + posts (CLI swapped for a script), the broadcast job sends on the bus
    def fake_stream(prompt, system, model, timeout=120, max_budget_usd=0.5, binary=None):
        yield {"type": "done", "text": "All quiet.", "cost_usd": 0.01, "ms": 1, "model": model, "is_error": False}
    monkeypatch.setattr(bcli, "stream", fake_stream)
    r = cl.post(f"/v2/api/schedules/{did}/run")
    assert r.status_code == 200, r.get_json()
    j = r.get_json(); assert j["ok"] and j["manual"] and j["board"] == "Digest" and j["digest_id"] == 1
    row = sqlite3.connect(dbp).execute("SELECT board, content, sender_short_name FROM bulletins").fetchone()
    assert row == ("Digest", "All quiet.", "6b18")
    assert cl.get("/v2/api/digest").get_json()["digests"][0]["source"] == "schedule"
    r = cl.post(f"/v2/api/schedules/{bid}/run")
    assert r.status_code == 200 and r.get_json()["tx_id"].startswith("obs-")
    tx = [p for t, p in bus.published if t == "wildcat/tx"][-1]
    assert tx["text"] == "Net in 5 on ch0" and tx["to"] == "^all"
    job = next(s for s in cl.get("/v2/api/schedules").get_json()["schedules"] if s["id"] == bid)
    assert job["last_result"]["ok"] is True and job["last_run"]
    assert cl.delete(f"/v2/api/schedules/{bid}").status_code == 200 and cl.delete(f"/v2/api/schedules/{bid}").status_code == 404


def test_operator_token_gates_writes_via_header_or_cookie(tmp_path):
    from wildcat.bus import MemoryBus
    (tmp_path / "content").mkdir()
    cfg = build({"radio": {"type": "serial"}, "mqtt": {"enabled": True}, "bbs": {"source": "bus", "content_dir": str(tmp_path / "content")},
                 "telemetry": {"source": "bus"}, "database": {"path": str(tmp_path / "b.db")}, "observatory": {"operator_token": "s3cret-token"}})
    app = flask.Flask("obs-test7", template_folder=str(ROOT / "observatory" / "templates"), static_folder=str(ROOT / "observatory" / "static"))
    sio = flask_socketio.SocketIO(app, async_mode="threading")
    bridge = Bridge(cfg, sio)
    app.register_blueprint(create_blueprint(bridge, sio))
    bus = MemoryBus(); bridge.bus = bus; bridge.wire(bus)
    c = app.test_client()
    assert c.get("/v2/api/state").status_code == 200                                  # reads stay open
    a = c.get("/v2/api/auth").get_json(); assert a == {"required": True, "authorized": False}
    r = c.post("/v2/api/tx", json={"text": "hi"}); assert r.status_code == 401 and "token" in r.get_json()["error"]
    assert c.post("/v2/api/tx", json={"text": "hi"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.delete("/v2/api/sos").status_code == 401
    r = c.post("/v2/api/tx", json={"text": "hi"}, headers={"Authorization": "Bearer s3cret-token"}); assert r.status_code == 200, r.get_json()
    r = c.post("/v2/api/tx", json={"text": "hi2"}, headers={"X-Wildcat-Token": "s3cret-token"}); assert r.status_code == 200
    assert [p for t, p in bus.published if t == "wildcat/tx"][-1]["text"] == "hi2"
    # the dashboard hand-off: /v2/?token= sets the cookie, then writes work with no header
    assert c.get("/v2/?token=wrong").status_code == 403
    r = c.get("/v2/?token=s3cret-token"); assert r.status_code == 302 and r.headers["Location"].endswith("/v2/")
    assert c.get("/v2/api/auth").get_json()["authorized"] is True
    assert c.post("/v2/api/tx", json={"text": "hi3"}).status_code == 200
    assert "***" in c.get("/v2/api/config").get_json()["toml"] and "s3cret" not in c.get("/v2/api/config").get_json()["toml"]


def test_no_token_means_open_writes_as_before(client):
    assert client.get("/v2/api/auth").get_json() == {"required": False, "authorized": True}
