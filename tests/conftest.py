"""Test bootstrap: make ``wildcat`` importable without ``pip install -e .``,
keep every test isolated from any real config on this machine, and stub the
hardware-only third-party modules so the BBS code can be imported with no
radio and no ``meshtastic`` wheel (it doesn't build on every dev Python)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BBS_DIR = ROOT / "bbs"
if str(BBS_DIR) not in sys.path:          # bbs modules import each other as top-level names
    sys.path.insert(1, str(BBS_DIR))


def _stub(name: str, **attrs):
    if name in sys.modules:
        return
    try:
        __import__(name)
        return
    except ImportError:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


# Only used at call time by the BBS handlers; never exercised in unit tests.
_stub("meshtastic", BROADCAST_NUM=0xFFFFFFFF)
_stub("meshtastic.stream_interface", StreamInterface=object)
_stub("meshtastic.serial_interface", SerialInterface=object)
_stub("meshtastic.tcp_interface", TCPInterface=object)
_stub("serial")
_stub("serial.tools")
_stub("serial.tools.list_ports", comports=lambda: [])
_stub("requests")
_stub("pubsub", pub=types.SimpleNamespace(subscribe=lambda *a, **k: None))


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """No test may see a real config on this machine, and none may leak a
    singleton into the next test."""
    from wildcat import config, paths

    monkeypatch.delenv(paths.ENV_CONFIG, raising=False)
    monkeypatch.setattr(paths, "default_config_path", lambda: tmp_path / "config" / "wildcat.toml")
    monkeypatch.setattr(paths, "legacy_ini_path", lambda: tmp_path / "bbs" / "config.ini")
    config.reset()
    yield
    config.reset()


@pytest.fixture
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def write(tmp_path):
    """``write("name.toml", text) -> Path``"""
    def _write(name: str, text: str) -> Path:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p
    return _write
