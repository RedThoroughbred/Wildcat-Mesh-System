"""wildcat.config — schema, defaults, validation messages, path resolution."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from wildcat import config, paths
from wildcat.config import (DEFAULT_BBS_MENU, DEFAULT_MAIN_MENU, DEFAULT_UTILITIES_MENU,
                            ConfigError, build, get_config, load, load_file, to_toml)

try:
    import tomllib
except ImportError:
    import tomli as tomllib

SERIAL = {"radio": {"type": "serial"}}


# ---- the two bugs from the design doc, as regression tests -------------------

def test_missing_menu_table_gives_default_menu_not_keyerror():
    cfg = build(SERIAL)                      # no [bbs] at all
    assert cfg.bbs.menu.main == DEFAULT_MAIN_MENU
    assert cfg.bbs.menu.bbs == DEFAULT_BBS_MENU
    assert cfg.bbs.menu.utilities == DEFAULT_UTILITIES_MENU


def test_partial_menu_table_fills_the_rest():
    cfg = build({**SERIAL, "bbs": {"menu": {"main": ["Q", "X"]}}})
    assert cfg.bbs.menu.main == ["Q", "X"]
    assert cfg.bbs.menu.bbs == DEFAULT_BBS_MENU


def test_load_is_independent_of_cwd(write, tmp_path, monkeypatch):
    p = write("elsewhere/wildcat.toml", 'radio.type = "serial"\n[database]\npath = "data/x.db"\n')
    monkeypatch.chdir(tmp_path / "elsewhere")
    a = load(str(p))
    monkeypatch.chdir(tmp_path)
    config.reset()
    b = load(str(p))
    assert a.database.path == b.database.path
    assert a.database.path.is_absolute()
    # relative paths resolve against the project root for that config, not the CWD
    assert str(a.database.path).endswith(os.path.join("data", "x.db"))
    assert not str(a.database.path).startswith(str(tmp_path / "elsewhere" / "data"))


def test_missing_file_lists_every_path_tried(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load()
    msg = str(ei.value)
    assert "no Wildcat config found" in msg
    assert str(tmp_path / "config" / "wildcat.toml") in msg
    assert str(tmp_path / "bbs" / "config.ini") in msg
    assert "/etc/wildcat/wildcat.toml" in msg


def test_explicit_missing_path_is_an_error_not_a_fallthrough(tmp_path, write):
    write("config/wildcat.toml", 'radio.type = "serial"\n')   # a valid default exists…
    with pytest.raises(ConfigError) as ei:
        load(str(tmp_path / "nope.toml"))                     # …but we asked for a specific file
    assert "nope.toml" in str(ei.value)
    assert "--config" in str(ei.value)


# ---- validation messages -------------------------------------------------------

def test_bad_radio_type_message():
    with pytest.raises(ConfigError) as ei:
        build({"radio": {"type": "usb"}}, label="config/wildcat.toml")
    assert '[radio].type must be one of "tcp", "serial" (got \'usb\')' in str(ei.value)
    assert str(ei.value).startswith("config/wildcat.toml:")


def test_tcp_requires_host():
    with pytest.raises(ConfigError) as ei:
        build({})
    assert "[radio].host is required when type = \"tcp\"" in str(ei.value)


def test_brain_max_chunks_range_message_matches_design_doc():
    with pytest.raises(ConfigError) as ei:
        build({**SERIAL, "brain": {"max_chunks": 9}}, label="config/wildcat.toml")
    assert "config/wildcat.toml" in str(ei.value)
    assert "[brain].max_chunks must be between 1 and 5 (got 9)" in str(ei.value)


def test_all_problems_reported_together():
    with pytest.raises(ConfigError) as ei:
        build({"radio": {"type": "tcp", "hop_limit": 0}, "observatory": {"port": "eighty"},
               "brain": {"providers": ["gpt"]}})
    msg = str(ei.value)
    assert "4 problem(s)" in msg
    for needle in ("[radio].host", "[radio].hop_limit", "[observatory].port", "[brain].providers"):
        assert needle in msg


def test_section_that_is_not_a_table():
    with pytest.raises(ConfigError) as ei:
        build({**SERIAL, "bbs": "yes"})
    assert "[bbs] must be a table" in str(ei.value)


def test_unknown_keys_warn_but_do_not_fail():
    cfg = build({**SERIAL, "radio": {"type": "serial", "hostnme": "x"}, "extra": {}})
    assert not any("hostnme" in e for e in [])  # no exception raised
    assert any("[radio].hostnme" in w for w in cfg.warnings)
    assert any(".extra" in w for w in cfg.warnings)


def test_js8call_needs_both_host_and_port():
    with pytest.raises(ConfigError) as ei:
        build({**SERIAL, "bbs": {"js8call": {"host": "1.2.3.4"}}})
    assert "[bbs.js8call] needs BOTH host and port" in str(ei.value)
    cfg = build({**SERIAL, "bbs": {"js8call": {"host": "1.2.3.4", "port": 2442}}})
    assert cfg.bbs.js8call.enabled


def test_observatory_debug_true_is_loudly_warned():
    cfg = build({**SERIAL, "observatory": {"debug": True}})
    assert cfg.observatory.debug is True
    assert any("Werkzeug debugger" in w for w in cfg.warnings)


def test_invalid_toml_is_a_config_error(write):
    p = write("bad.toml", "[radio\ntype = 'tcp'\n")
    with pytest.raises(ConfigError) as ei:
        load_file(p)
    assert "not valid TOML" in str(ei.value)


# ---- normalization ---------------------------------------------------------------

def test_menu_letters_are_normalized_and_validated():
    cfg = build({**SERIAL, "bbs": {"menu": {"main": ["w", " n ", "X"]}}})
    assert cfg.bbs.menu.main == ["W", "N", "X"]
    with pytest.raises(ConfigError) as ei:
        build({**SERIAL, "bbs": {"menu": {"main": ["QQ", "X"]}}})
    assert "entries must be single letters (got 'QQ')" in str(ei.value)
    with pytest.raises(ConfigError) as ei:
        build({**SERIAL, "bbs": {"menu": {"utilities": []}}})
    assert "[bbs.menu].utilities must list at least one" in str(ei.value)


def test_node_id_lists_accept_csv_strings_and_arrays():
    a = build({**SERIAL, "bbs": {"sync_nodes": "!17d7e4b7, !18e9f5a3"}})
    b = build({**SERIAL, "bbs": {"sync_nodes": ["!17d7e4b7", "!18e9f5a3"]}})
    assert a.bbs.sync_nodes == b.bbs.sync_nodes == ["!17d7e4b7", "!18e9f5a3"]
    c = build({**SERIAL, "bbs": {"allowed_nodes": ["12345678"]}})
    assert any("doesn't look like a Meshtastic node id" in w for w in c.warnings)


def test_ints_tolerate_digit_strings_from_ini():
    cfg = build({**SERIAL, "observatory": {"port": "5001"}})
    assert cfg.observatory.port == 5001


# ---- example file + round trip ----------------------------------------------------

def test_example_file_is_valid_and_every_key_is_known(repo_root):
    cfg = load_file(repo_root / "config" / "wildcat.example.toml")
    assert cfg.radio.type == "tcp" and cfg.radio.host == "192.168.86.37"
    assert cfg.bbs.menu.main == DEFAULT_MAIN_MENU
    assert cfg.database.path == (repo_root / "shared" / "bulletins.db").resolve()
    assert cfg.bbs.content_dir == (repo_root / "bbs").resolve()
    assert cfg.observatory.debug is False
    assert [w for w in cfg.warnings if "unknown key" in w] == []


def test_to_toml_round_trips(repo_root):
    cfg = load_file(repo_root / "config" / "wildcat.example.toml")
    text = to_toml(cfg)
    again = build(tomllib.loads(text), source=cfg.source)
    for section in ("radio", "mqtt", "meshd", "telemetry", "bbs", "database", "observatory", "brain"):
        assert getattr(again, section) == getattr(cfg, section), section


def test_to_toml_keeps_the_consumers_source_keys():
    # regression: [bbs].source / [telemetry].source were dropped as if they were the
    # top-level provenance field, so re-saving a bus-mode Den failed validation
    cfg = build({**SERIAL, "mqtt": {"enabled": True}, "bbs": {"source": "bus"}, "telemetry": {"source": "bus"}})
    text = to_toml(cfg)
    assert 'source = "bus"' in text.split("[telemetry]")[1].split("[")[0]
    again = build(tomllib.loads(text), source=cfg.source)
    assert again.bbs.source == "bus" and again.telemetry.source == "bus" and again.mqtt.enabled is True


def test_to_toml_redacts_secrets():
    cfg = build({**SERIAL, "bbs": {"weather_api_key": "abc"}, "mqtt": {"password": "pw"}})
    text = to_toml(cfg, redact=True)
    assert "abc" not in text and "pw" not in text and '"***"' in text
    assert "abc" in to_toml(cfg)


# ---- singleton + env ------------------------------------------------------------

def test_env_var_is_honored(write, monkeypatch):
    p = write("env/wildcat.toml", 'radio.type = "serial"\n[bbs]\nname = "from env"\n')
    monkeypatch.setenv(paths.ENV_CONFIG, str(p))
    assert get_config().bbs.name == "from env"
    assert get_config() is get_config()


def test_default_repo_path_is_used(write):
    write("config/wildcat.toml", 'radio.type = "serial"\n[bbs]\nname = "repo default"\n')
    assert load().bbs.name == "repo default"
    assert load().source_kind == "toml"


# ---- step 5 tables ---------------------------------------------------------------------

def test_meshd_and_source_defaults():
    cfg = build(SERIAL)
    assert cfg.meshd.tx_pacing_seconds == 2.0 and cfg.meshd.max_chunk_chars == 200
    assert cfg.telemetry.source == "radio" and cfg.bbs.source == "radio"


def test_bus_source_requires_mqtt_enabled():
    with pytest.raises(ConfigError) as ei:
        build({**SERIAL, "bbs": {"source": "bus"}, "telemetry": {"source": "bus"}})
    msg = str(ei.value)
    assert "[bbs].source" in msg and "[telemetry].source" in msg and "[mqtt].enabled" in msg
    cfg = build({**SERIAL, "mqtt": {"enabled": True}, "bbs": {"source": "bus"}, "telemetry": {"source": "bus"}})
    assert cfg.bbs.source == "bus"


def test_mqtt_enabled_forbids_radio_sources():
    with pytest.raises(ConfigError) as ei:
        build({**SERIAL, "mqtt": {"enabled": True}})           # sources default to "radio"
    msg = str(ei.value)
    assert "[bbs].source" in msg and "[telemetry].source" in msg and "single API socket" in msg


def test_meshd_validation_messages():
    with pytest.raises(ConfigError) as ei:
        build({**SERIAL, "meshd": {"tx_pacing_seconds": "fast", "max_chunk_chars": 500,
                                   "reconnect_min_seconds": 60, "reconnect_max_seconds": 10}})
    msg = str(ei.value)
    assert "[meshd].tx_pacing_seconds must be a number" in msg
    assert "[meshd].max_chunk_chars must be between 50 and 230" in msg
    assert "[meshd].reconnect_max_seconds must be >= reconnect_min_seconds" in msg
    cfg = build({**SERIAL, "meshd": {"tx_pacing_seconds": 1}})
    assert cfg.meshd.tx_pacing_seconds == 1.0


def test_brain_cli_keys():
    cfg = build(SERIAL)
    assert cfg.brain.analyst_enabled is True and cfg.brain.cli_timeout == 120 and cfg.brain.ollama_url == ""
    cfg = build({**SERIAL, "brain": {"cli_model": "claude-haiku-4-5-20251001", "cli_timeout": 30, "analyst_enabled": False}})
    assert cfg.brain.cli_model == "claude-haiku-4-5-20251001" and cfg.brain.analyst_enabled is False
    with pytest.raises(ConfigError):
        build({**SERIAL, "brain": {"cli_timeout": 5}})


def test_community_fields_round_trip():
    cfg = build({**SERIAL, "observatory": {"community_name": "Wildcat Mesh NKY", "community_blurb": "Northern Kentucky's LoRa mesh."}})
    assert cfg.observatory.community_name == "Wildcat Mesh NKY"
    again = build(tomllib.loads(to_toml(cfg)), source=cfg.source)
    assert again.observatory.community_blurb == "Northern Kentucky's LoRa mesh."
