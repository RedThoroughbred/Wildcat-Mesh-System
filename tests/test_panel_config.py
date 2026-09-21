"""PanelConfigStore: validation, merge semantics, atomic persistence."""
from __future__ import annotations

import json
import os
import stat

import pytest

from wildcat.observatory.panel_config import DEFAULT_PRESETS, PanelConfigError, PanelConfigStore


@pytest.fixture
def store(tmp_path):
    return PanelConfigStore(tmp_path / "panel.json")


def test_defaults(store):
    c = store.get()
    assert c == {"rev": 0, "updated": 0, "presets": DEFAULT_PRESETS, "accent": "teal",
                 "pollSecs": 10, "defaultChannel": 0, "nodeSort": "recent",
                 "panicText": "Net check - anyone on?"}
    assert len(c["presets"]) == 9


def test_get_returns_a_copy(store):
    store.get()["presets"].append("x")
    assert len(store.get()["presets"]) == 9


@pytest.mark.parametrize("v,ok", [(5, True), (120, True), (4, False), (121, False), ("10", False), (True, False), (10.5, False)])
def test_poll_bounds(store, v, ok):
    if ok:
        assert store.update({"pollSecs": v})["pollSecs"] == v
    else:
        with pytest.raises(PanelConfigError, match="pollSecs"):
            store.update({"pollSecs": v})


@pytest.mark.parametrize("v,ok", [(0, True), (7, True), (-1, False), (8, False), (None, False)])
def test_channel_bounds(store, v, ok):
    if ok:
        assert store.update({"defaultChannel": v})["defaultChannel"] == v
    else:
        with pytest.raises(PanelConfigError, match="defaultChannel"):
            store.update({"defaultChannel": v})


def test_enums(store):
    for a in ("teal", "sky", "lime", "gold", "orange", "pink", "violet", "magenta"):
        assert store.update({"accent": a})["accent"] == a
    for s in ("recent", "signal", "name"):
        assert store.update({"nodeSort": s})["nodeSort"] == s
    with pytest.raises(PanelConfigError, match="accent"):
        store.update({"accent": "red"})
    with pytest.raises(PanelConfigError, match="nodeSort"):
        store.update({"nodeSort": "size"})


def test_presets_limits(store):
    assert len(store.update({"presets": ["a"] * 9})["presets"]) == 9
    with pytest.raises(PanelConfigError, match="presets"):
        store.update({"presets": ["a"] * 10})
    assert store.update({"presets": ["x" * 48]})["presets"] == ["x" * 48]
    with pytest.raises(PanelConfigError, match="48"):
        store.update({"presets": ["x" * 49]})
    with pytest.raises(PanelConfigError, match="presets"):
        store.update({"presets": "hello"})
    with pytest.raises(PanelConfigError, match="presets"):
        store.update({"presets": [3]})


def test_blank_presets_dropped_and_ascii_folded(store):
    got = store.update({"presets": ["  ", "", "Café ok \U0001F31E", "\x01\x02", "a   b"]})["presets"]
    assert got == ["Cafe ok", "a b"]
    assert store.update({"presets": []})["presets"] == []
    # blanks do not count toward the 9-entry cap
    assert len(store.update({"presets": ["a"] * 9 + ["", " "]})["presets"]) == 9


def test_partial_merge_leaves_other_fields(store):
    store.update({"accent": "gold", "pollSecs": 30})
    c = store.update({"nodeSort": "name"})
    assert c["accent"] == "gold" and c["pollSecs"] == 30 and c["nodeSort"] == "name"
    assert c["presets"] == DEFAULT_PRESETS


def test_rev_increments_only_on_success(store):
    assert store.update({"pollSecs": 20})["rev"] == 1
    assert store.get()["updated"] > 0
    with pytest.raises(PanelConfigError):
        store.update({"pollSecs": 999})
    assert store.get()["rev"] == 1
    assert store.update({"pollSecs": 21})["rev"] == 2
    assert store.reset()["rev"] == 3


def test_failed_validation_changes_nothing(store, tmp_path):
    store.update({"accent": "sky"})
    before = store.get()
    raw = (tmp_path / "panel.json").read_bytes()
    with pytest.raises(PanelConfigError):
        store.update({"accent": "lime", "pollSecs": 1})     # first field valid, second not
    assert store.get() == before and (tmp_path / "panel.json").read_bytes() == raw
    with pytest.raises(PanelConfigError):
        store.update(["not", "a", "dict"])


def test_unknown_and_readonly_keys_ignored(store):
    c = store.update({"rev": 99, "updated": 5, "now": 1, "tz": 2, "bogus": "x", "accent": "pink"})
    assert c["rev"] == 1 and c["updated"] != 5 and c["accent"] == "pink"
    assert "bogus" not in c and "now" not in c and "tz" not in c


def test_reset_restores_defaults(store):
    store.update({"accent": "pink", "presets": ["hi"]})
    c = store.reset()
    assert c["accent"] == "teal" and c["presets"] == DEFAULT_PRESETS and c["rev"] == 2


def test_persists_across_instances(tmp_path):
    p = tmp_path / "panel.json"
    PanelConfigStore(p).update({"accent": "violet", "defaultChannel": 3, "presets": ["hey"]})
    c = PanelConfigStore(p).get()
    assert c["accent"] == "violet" and c["defaultChannel"] == 3 and c["presets"] == ["hey"] and c["rev"] == 1


@pytest.mark.parametrize("junk", [b"{not json", b"", b"[1,2]", b"\xff\xfe"])
def test_corrupt_file_gives_defaults(tmp_path, junk):
    p = tmp_path / "panel.json"
    p.write_bytes(junk)
    s = PanelConfigStore(p)
    assert s.get()["rev"] == 0 and s.get()["accent"] == "teal"
    assert s.update({"accent": "sky"})["rev"] == 1        # and it can be written over


def test_invalid_fields_in_file_default_individually(tmp_path):
    p = tmp_path / "panel.json"
    p.write_text(json.dumps({"rev": 4, "accent": "sky", "pollSecs": 9999}))
    c = PanelConfigStore(p).get()
    assert c["rev"] == 4 and c["accent"] == "sky" and c["pollSecs"] == 10


def test_file_mode_0600_and_no_temp_left(tmp_path):
    p = tmp_path / "panel.json"
    s = PanelConfigStore(p)
    s.update({"pollSecs": 15})
    s.reset()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert [f.name for f in tmp_path.iterdir()] == ["panel.json"]


def test_panic_text_default_update_persist_reset(store, tmp_path):
    assert store.get()["panicText"] == "Net check - anyone on?"
    assert store.update({"panicText": "  Caf\u00e9 check \U0001F31E "})["panicText"] == "Cafe check"
    assert PanelConfigStore(tmp_path / "panel.json").get()["panicText"] == "Cafe check"
    assert store.update({"accent": "gold"})["panicText"] == "Cafe check"      # partial merge keeps it
    assert store.reset()["panicText"] == "Net check - anyone on?"


@pytest.mark.parametrize("v", ["", "   ", "\U0001F31E", "x" * 49, 5, None])
def test_panic_text_invalid(store, v):
    with pytest.raises(PanelConfigError, match="panicText"):
        store.update({"panicText": v})
    assert store.get()["rev"] == 0


def test_panic_text_48_ok(store):
    assert store.update({"panicText": "y" * 48})["panicText"] == "y" * 48
