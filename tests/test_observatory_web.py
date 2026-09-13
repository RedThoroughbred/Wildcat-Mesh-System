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
