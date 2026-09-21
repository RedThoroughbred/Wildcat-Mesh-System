"""Persisted settings for the Pico handheld panel, edited from a phone via /v2/panel.

`PanelConfigStore` is a small thread-safe JSON store: validate a partial patch, then bump `rev`,
stamp `updated`, and write atomically (tmp file in the same directory + os.replace, mode 0600).
A missing or corrupt file means defaults; nothing here ever raises on read.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .compact import ascii_clean

ACCENTS = ("teal", "sky", "lime", "gold", "orange", "pink", "violet", "magenta")
NODE_SORTS = ("recent", "signal", "name")
MAX_PRESETS = 9
PRESET_MAX = 48
POLL_MIN, POLL_MAX = 5, 120
CHANNEL_MIN, CHANNEL_MAX = 0, 7

DEFAULT_PANIC_TEXT = "Net check - anyone on?"
DEFAULT_PRESETS = ["OK", "Yes", "No", "On my way", "Arrived safe", "All good here",
                   "Call me when you can", "Need help - please reply", "Testing the Den panel"]


def defaults() -> Dict[str, Any]:
    return {"rev": 0, "updated": 0, "presets": list(DEFAULT_PRESETS), "accent": "teal",
            "pollSecs": 10, "defaultChannel": 0, "nodeSort": "recent",
            "panicText": DEFAULT_PANIC_TEXT}


class PanelConfigError(ValueError):
    """A patch field failed validation; the message names the field and why."""


def _int(value: Any, field: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise PanelConfigError("%s must be an integer %d..%d" % (field, lo, hi))
    v = int(value)
    if not lo <= v <= hi:
        raise PanelConfigError("%s must be between %d and %d" % (field, lo, hi))
    return v


def _presets(value: Any) -> List[str]:
    if not isinstance(value, list):
        raise PanelConfigError("presets must be a list of strings")
    out: List[str] = []
    for i, raw in enumerate(value):
        if not isinstance(raw, str):
            raise PanelConfigError("presets[%d] must be a string" % i)
        s = "".join(ch for ch in ascii_clean(raw, 10_000) if 32 <= ord(ch) < 127).strip()
        if not s:
            continue
        if len(s) > PRESET_MAX:
            raise PanelConfigError("presets[%d] is longer than %d characters" % (i, PRESET_MAX))
        out.append(s)
    if len(out) > MAX_PRESETS:
        raise PanelConfigError("presets has more than %d entries" % MAX_PRESETS)
    return out


def _panic_text(value: Any) -> str:
    if not isinstance(value, str):
        raise PanelConfigError("panicText must be a string")
    s = "".join(ch for ch in ascii_clean(value, 10_000) if 32 <= ord(ch) < 127).strip()
    if not s:
        raise PanelConfigError("panicText must not be blank")
    if len(s) > PRESET_MAX:
        raise PanelConfigError("panicText is longer than %d characters" % PRESET_MAX)
    return s


def validate_patch(patch: Any) -> Dict[str, Any]:
    """Return the cleaned subset of `patch` (unknown / read-only keys dropped) or raise PanelConfigError."""
    if not isinstance(patch, dict):
        raise PanelConfigError("body must be a JSON object")
    clean: Dict[str, Any] = {}
    if "presets" in patch:
        clean["presets"] = _presets(patch["presets"])
    if "panicText" in patch:
        clean["panicText"] = _panic_text(patch["panicText"])
    if "accent" in patch:
        if patch["accent"] not in ACCENTS:
            raise PanelConfigError("accent must be one of: " + ", ".join(ACCENTS))
        clean["accent"] = patch["accent"]
    if "pollSecs" in patch:
        clean["pollSecs"] = _int(patch["pollSecs"], "pollSecs", POLL_MIN, POLL_MAX)
    if "defaultChannel" in patch:
        clean["defaultChannel"] = _int(patch["defaultChannel"], "defaultChannel", CHANNEL_MIN, CHANNEL_MAX)
    if "nodeSort" in patch:
        if patch["nodeSort"] not in NODE_SORTS:
            raise PanelConfigError("nodeSort must be one of: " + ", ".join(NODE_SORTS))
        clean["nodeSort"] = patch["nodeSort"]
    return clean


def _sanitize_loaded(raw: Any) -> Dict[str, Any]:
    """Best-effort read of a stored file: keep each valid field, default the rest."""
    cfg = defaults()
    if not isinstance(raw, dict):
        return cfg
    for key, val in raw.items():
        if key in ("presets", "accent", "pollSecs", "defaultChannel", "nodeSort", "panicText"):
            try:
                cfg.update(validate_patch({key: val}))
            except PanelConfigError:
                pass
    for key in ("rev", "updated"):
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            cfg[key] = v
    return cfg


class PanelConfigStore:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._cfg = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return _sanitize_loaded(json.load(f))
        except (OSError, ValueError):
            return defaults()

    def _write(self, cfg: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".panel-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _commit(self, new: Dict[str, Any]) -> Dict[str, Any]:
        new["rev"] = self._cfg["rev"] + 1
        new["updated"] = int(time.time())
        self._write(new)          # persist first: a failed write leaves memory untouched
        self._cfg = new
        return copy.deepcopy(new)

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._cfg)

    def update(self, patch: Any) -> Dict[str, Any]:
        """Partial merge. Raises PanelConfigError (changing nothing) on invalid input."""
        clean = validate_patch(patch)
        with self._lock:
            new = copy.deepcopy(self._cfg)
            new.update(clean)
            return self._commit(new)

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            return self._commit(defaults())
