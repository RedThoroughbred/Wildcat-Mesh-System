"""Read the legacy ``bbs/config.ini`` into the v2 config shape.

The v1 BBS (upstream TC²) used four INI sections. This module maps them onto
the TOML tables so the *same* validator (``wildcat.config.build``) runs on
both — one schema, two on-disk formats, and the old file keeps working until
``wildcat config migrate --write`` retires it.

    [interface] type / hostname / port   → [radio] type / host / port
    [sync]      bbs_nodes                → [bbs] sync_nodes
    [allow_list] allowed_nodes           → [bbs] allowed_nodes
    [menu]      *_menu_items             → [bbs.menu] main / bbs / utilities
    [js8call]   …                        → [bbs.js8call] …

Anything else in the INI is reported as a warning, not silently dropped.
"""
from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any, Dict, List

KNOWN_SECTIONS = ("interface", "sync", "allow_list", "menu", "js8call")


def _csv(value: str) -> List[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def ini_to_dict(path: Path) -> Dict[str, Any]:
    """Parse ``path`` (which must exist) and return a TOML-shaped nested dict."""
    parser = configparser.ConfigParser()
    read = parser.read(path, encoding="utf-8")
    if not read:
        # configparser's infamous silent no-op: make it loud.
        raise FileNotFoundError(f"could not read legacy config: {path}")

    out: Dict[str, Any] = {}
    unknown: List[str] = []

    if parser.has_section("interface"):
        s = parser["interface"]
        radio: Dict[str, Any] = {}
        if "type" in s:
            radio["type"] = s["type"].strip()
        if s.get("hostname", "").strip():
            radio["host"] = s["hostname"].strip()
        if s.get("port", "").strip():
            radio["port"] = s["port"].strip()
        out["radio"] = radio

    bbs: Dict[str, Any] = {}
    if parser.has_section("sync") and parser["sync"].get("bbs_nodes", "").strip():
        bbs["sync_nodes"] = _csv(parser["sync"]["bbs_nodes"])
    if parser.has_section("allow_list") and parser["allow_list"].get("allowed_nodes", "").strip():
        bbs["allowed_nodes"] = _csv(parser["allow_list"]["allowed_nodes"])

    if parser.has_section("menu"):
        s = parser["menu"]
        menu: Dict[str, Any] = {}
        if s.get("main_menu_items", "").strip():
            menu["main"] = _csv(s["main_menu_items"])
        if s.get("bbs_menu_items", "").strip():
            menu["bbs"] = _csv(s["bbs_menu_items"])
        if s.get("utilities_menu_items", "").strip():
            menu["utilities"] = _csv(s["utilities_menu_items"])
        if menu:
            bbs["menu"] = menu

    if parser.has_section("js8call"):
        s = parser["js8call"]
        js8: Dict[str, Any] = {}
        if s.get("host", "").strip():
            js8["host"] = s["host"].strip()
        if s.get("port", "").strip():
            js8["port"] = s["port"].strip()          # validator coerces digit strings
        if s.get("db_file", "").strip():
            js8["db_file"] = s["db_file"].strip()
        if s.get("js8groups", "").strip():
            js8["groups"] = _csv(s["js8groups"])
        if "store_messages" in s:
            js8["store_messages"] = s["store_messages"].strip()
        if s.get("js8urgent", "").strip():
            js8["urgent_groups"] = _csv(s["js8urgent"])
        if js8:
            bbs["js8call"] = js8

    if bbs:
        out["bbs"] = bbs

    for section in parser.sections():
        if section not in KNOWN_SECTIONS:
            unknown.append(section)
    if unknown:
        # Surface through the validator's unknown-key path so it shows as a warning.
        for section in unknown:
            out[f"legacy_ini_section_{section}"] = dict(parser[section])
    return out
