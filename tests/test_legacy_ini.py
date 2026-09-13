"""Legacy bbs/config.ini → v2 config, and the migrate command."""
from __future__ import annotations

from pathlib import Path

import pytest

from wildcat import cli, config
from wildcat.config import DEFAULT_MAIN_MENU, ConfigError, load, load_file

FULL_INI = """
[interface]
type = tcp
hostname = 192.168.86.37

[sync]
bbs_nodes = !17d7e4b7,!18e9f5a3

[allow_list]
allowed_nodes = !9e766b18

[menu]
main_menu_items = W, N, R, Q, G, B, U, X
bbs_menu_items = M, B, C, J, X
utilities_menu_items = S, F, W, X

[js8call]
host = 192.168.1.100
port = 2442
db_file = js8call.db
js8groups = @GRP1,@GRP2
store_messages = False
js8urgent = @URGNT
"""


def test_upstream_example_ini_loads(repo_root):
    cfg = load_file(repo_root / "bbs" / "example_config.ini")
    assert cfg.source_kind == "legacy-ini"
    assert cfg.radio.type == "serial"
    assert cfg.bbs.menu.main == ["Q", "B", "U", "X"]      # upstream TC² menu, as written in that file
    assert cfg.bbs.sync_nodes == [] and cfg.bbs.allowed_nodes == []
    assert not cfg.bbs.js8call.enabled
    assert any("LEGACY INI" in w for w in cfg.warnings)


def test_full_ini_maps_every_section(write):
    cfg = load_file(write("bbs/config.ini", FULL_INI))
    assert cfg.radio.type == "tcp" and cfg.radio.host == "192.168.86.37"
    assert cfg.bbs.sync_nodes == ["!17d7e4b7", "!18e9f5a3"]
    assert cfg.bbs.allowed_nodes == ["!9e766b18"]
    assert cfg.bbs.menu.main == DEFAULT_MAIN_MENU
    assert cfg.bbs.menu.utilities == ["S", "F", "W", "X"]
    js8 = cfg.bbs.js8call
    assert js8.enabled and js8.host == "192.168.1.100" and js8.port == 2442
    assert js8.groups == ["@GRP1", "@GRP2"] and js8.urgent_groups == ["@URGNT"]
    assert js8.store_messages is False


def test_ini_without_menu_section_uses_defaults(write):
    """The `KeyError: 'menu'` bug — a menu-less INI now boots with the stock menu."""
    cfg = load_file(write("bbs/config.ini", "[interface]\ntype = tcp\nhostname = 10.0.0.5\n"))
    assert cfg.bbs.menu.main == DEFAULT_MAIN_MENU


def test_ini_tcp_without_hostname_is_a_clear_error(write):
    with pytest.raises(ConfigError) as ei:
        load_file(write("bbs/config.ini", "[interface]\ntype = tcp\n"))
    assert "[radio].host is required" in str(ei.value)


def test_unknown_ini_section_is_warned(write):
    cfg = load_file(write("bbs/config.ini", "[interface]\ntype = serial\n[weather]\nkey = x\n"))
    assert any("legacy_ini_section_weather" in w for w in cfg.warnings)


def test_load_falls_through_to_legacy_ini_when_no_toml(write, tmp_path):
    write("bbs/config.ini", "[interface]\ntype = serial\n")
    cfg = load()
    assert cfg.source_kind == "legacy-ini"
    assert cfg.source == (tmp_path / "bbs" / "config.ini").resolve()


def test_toml_wins_over_legacy_ini(write):
    write("bbs/config.ini", "[interface]\ntype = serial\n")
    write("config/wildcat.toml", 'radio.type = "serial"\n[bbs]\nname = "toml"\n')
    cfg = load()
    assert cfg.source_kind == "toml" and cfg.bbs.name == "toml"


def test_migrate_dry_run_then_write(write, tmp_path, capsys):
    ini = write("bbs/config.ini", FULL_INI)
    out = tmp_path / "config" / "wildcat.toml"
    assert cli.main(["config", "migrate", "--ini", str(ini), "-o", str(out)]) == 0
    assert not out.exists()
    assert "dry run" in capsys.readouterr().err

    assert cli.main(["config", "migrate", "--ini", str(ini), "-o", str(out), "--write"]) == 0
    assert out.exists()
    config.reset()
    migrated = load_file(out)
    original = load_file(ini)
    for section in ("radio", "mqtt", "bbs", "database", "observatory", "brain"):
        assert getattr(migrated, section) == getattr(original, section), section
    assert migrated.source_kind == "toml"

    # refuses to clobber without --force
    assert cli.main(["config", "migrate", "--ini", str(ini), "-o", str(out), "--write"]) == 1
    assert cli.main(["config", "migrate", "--ini", str(ini), "-o", str(out), "--write", "--force"]) == 0


def test_migrate_with_no_ini_explains(tmp_path, capsys):
    assert cli.main(["config", "migrate"]) == 2
    assert "nothing to migrate" in capsys.readouterr().err
