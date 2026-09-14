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
