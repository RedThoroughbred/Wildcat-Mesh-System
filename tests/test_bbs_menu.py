"""The BBS's menu builder against the new config layer — the actual bug site.

Imports ``bbs/command_handlers.py`` with the hardware modules stubbed (conftest),
so this runs with no radio and no meshtastic wheel.
"""
from __future__ import annotations

import importlib
import sys

from wildcat import config
from wildcat.config import DEFAULT_MAIN_MENU, load


def _fresh_command_handlers():
    for name in ("command_handlers", "db_operations", "utils"):
        sys.modules.pop(name, None)
    return importlib.import_module("command_handlers")


def test_import_reads_no_config_and_no_files(tmp_path, monkeypatch):
    """D-004: importing the handlers must not touch the config or the CWD."""
    monkeypatch.chdir(tmp_path)          # nothing here — a v1 import would have exploded
    config.reset()
    ch = _fresh_command_handlers()       # no config loaded at all, still fine
    assert ch.MESSAGES._data is None     # lazy


def test_menu_without_menu_table_renders_stock_menu(write, tmp_path, monkeypatch):
    """The `KeyError: 'menu'` bug: a config with no [bbs.menu] renders the stock menu."""
    p = write("c.toml", 'radio.type = "serial"\n')
    monkeypatch.chdir(tmp_path)          # and it must not matter where we run from
    load(str(p))
    ch = _fresh_command_handlers()
    assert ch._menu("main") == DEFAULT_MAIN_MENU
    text = ch.build_menu(ch._menu("main"), "💾Wildcat TC² BBS💾")
    # (Q renders as [Q]uote under a header containing "BBS" — existing build_menu behavior)
    for label in ("[W]eather", "[N]etwork Info", "[R]esources", "[Q]uote",
                  "[G]ames", "[B]ulletins", "[U]tilities", "E[X]IT"):
        assert label in text, label


def test_custom_menu_from_toml(write):
    p = write("c.toml", 'radio.type = "serial"\n[bbs.menu]\nmain = ["q", "x"]\n')
    load(str(p))
    ch = _fresh_command_handlers()
    text = ch.build_menu(ch._menu("main"), "hdr")
    assert "[Q]uick Commands" in text and "E[X]IT" in text and "[W]eather" not in text


def test_content_files_resolve_to_repo_bbs_dir_from_any_cwd(write, tmp_path, monkeypatch, repo_root):
    p = write("c.toml", 'radio.type = "serial"\n')
    monkeypatch.chdir(tmp_path)
    load(str(p))
    ch = _fresh_command_handlers()
    assert ch._content("fortunes.txt") == (repo_root / "bbs" / "fortunes.txt").resolve()
    # messages.json ships in the repo, so the lazy proxy finds the real labels
    assert ch.MESSAGES.get("menu_labels", {}).get("weather") == "[W]eather"


def test_content_dir_override(write, tmp_path):
    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "messages.json").write_text('{"menu_labels": {"weather": "[W]x"}}')
    p = write("c.toml", f'radio.type = "serial"\n[bbs]\ncontent_dir = "{tmp_path / "content"}"\n')
    load(str(p))
    ch = _fresh_command_handlers()
    assert ch.MESSAGES.get("menu_labels")["weather"] == "[W]x"
