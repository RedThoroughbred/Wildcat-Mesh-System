"""Static invariants of the v2 hash router (the browser QA pass of 2026-09-13 found
these classes of bug; these checks keep them from coming back without a browser).

The behavioural sequences themselves live in tests/qa/router_qa.js and are run
with a real browser (see docs/QA_V2.md).
"""
from __future__ import annotations

import re
from pathlib import Path

APP = (Path(__file__).resolve().parent.parent / "observatory" / "static" / "v2" / "app.js").read_text()
HTML = (Path(__file__).resolve().parent.parent / "observatory" / "templates" / "v2" / "index.html").read_text()
CSS = (Path(__file__).resolve().parent.parent / "observatory" / "static" / "v2" / "app.css").read_text()


def views_region() -> str:
    return APP[APP.index("  const VIEWS = {"):APP.index("  function countUp() {")]


def test_every_view_write_is_guarded_against_stale_renders():
    r = views_region()
    writes = re.findall(r"^\s*(?:if \(this\.seq !== S\.viewSeq\) return; )?viewBody\.innerHTML = ", r, re.M)
    assert writes, "no view writes found?"
    unguarded = [w for w in writes if "this.seq" not in w]
    assert not unguarded, f"{len(unguarded)} unguarded viewBody writes inside VIEWS"
    # every view is an `async render` method so `this` is the view object
    assert r.count("async render(") == r.count("title:") or r.count("async render(") >= 11


def test_action_routes_put_the_url_back():
    for action in ("coverage", "replay", "cat"):
        assert re.search(rf'name === "{action}"\) \{{.*actionDone\(\)', APP), action
    assert 'history.replaceState(null, "", homeUrl())' in APP                 # home is the bare /v2/, never '#/'
    assert 'if (location.hash) history.replaceState(null, "", homeUrl())' in APP


def test_sidebar_highlight_maps_detail_routes():
    assert 'NAV_PARENT = { node: "nodes", channel: "channels" }' in APP


def test_opening_a_view_leaves_home_only_modes():
    show = APP[APP.index("async function showView"):APP.index("function closeView")]
    assert "tlExitReplay()" in show and '$("card").hidden = true' in show


def test_live_map_click_is_always_live():
    assert 'a.dataset.view === "home"' in APP and "tlExitReplay()" in APP.split('a.dataset.view === "home"')[1][:400]


def test_home_only_panels_hide_while_viewing():
    rule = re.search(r"body\.viewing \.feed[^{]*\{ display: none !important; \}", CSS)
    assert rule and ".compose" in rule.group(0) and ".card" in rule.group(0) and ".timeline" in rule.group(0)


def test_unknown_route_recovers():
    assert 'if (!VIEWS[name]) { toast("No such page' in APP


def test_brand_is_a_home_link_and_nav_entries_have_titles():
    assert 'class="brand-link"' in HTML and 'href="#/"' in HTML
    for view in ("nodes", "channels", "messages", "propagation", "topology", "health", "digest", "admin", "api", "dashboard"):
        assert re.search(rf'data-view="{view}"[^>]*title="', HTML), view

# --- QA finding 10: phone header -------------------------------------------

def test_phone_header_badge_sizes_to_its_count():
    # the mobile `.tool { width: 30px }` rule must not clamp the alert badge
    assert re.search(r"\.tool\.alert-btn\s*\{[^}]*width:\s*auto", CSS)
    assert re.search(r"\.alert-btn\s*\{[^}]*white-space:\s*nowrap", CSS)
    # the count is capped so the badge never grows past two glyphs
    assert 'n > 9 ? "9+" : String(n)' in APP


def test_phone_header_is_one_row_with_share_in_the_drawer():
    mobile = CSS[CSS.index("@media (max-width: 760px)"):]
    assert re.search(r"\.tools\s*\{[^}]*margin-left:\s*auto", mobile)
    assert re.search(r"\.tools #share\s*\{\s*display:\s*none", mobile)
    # every link in the brand chain can shrink, so the title ellipsizes instead of pushing the tools to a second row
    assert re.search(r"\.brand \.brand-link > div\s*\{\s*min-width:\s*0", mobile)
    assert 'id="sb-share"' in HTML
    assert 'on($("sb-share"), "click", sharePublic)' in APP or '$("sb-share")' in APP
    # the drawer button is hidden on desktop and in the public view
    assert re.search(r"^\.sb-share\s*\{\s*display:\s*none", CSS, re.M)
    assert re.search(r"body\.public \.sb-share\s*\{\s*display:\s*none", CSS)


# --- QA finding 11: Replay in the public view ------------------------------

def test_replay_is_operator_only():
    # the timeline (scrubber, speeds, back-to-live) is hidden in /v2/public, so the
    # Replay entry must be hidden there too and the action must not start a replay
    assert re.search(r'data-view="replay" class="op-only"', HTML)
    assert re.search(r"body\.public [^{]*\.op-only[^{]*\{ display: none !important; \}", CSS)
    assert 'if (name === "replay") { closeView(); actionDone(); if (PUBLIC) { toast(' in APP


# --- SOS broadcast ---------------------------------------------------------------

def test_sos_controls_are_operator_only_and_the_sheet_is_home_only():
    assert re.search(r'id="sos-btn"', HTML) and re.search(r'class="tool sos-btn op-only"', HTML)
    assert re.search(r'<section class="glass sos-sheet op-only" id="sos" hidden', HTML)
    assert re.search(r'id="sos-stop"', HTML) and 'class="link-btn op-only" id="sos-stop"' in HTML
    rule = re.search(r"body\.viewing \.feed[^{]*\{ display: none !important; \}", CSS)
    assert rule and ".sos-sheet" in rule.group(0)
    # two taps to send, never one
    assert 'sosGo.textContent = "Tap again to broadcast now"' in APP and "SOS.armTimer = setTimeout(sosDisarm, 6000)" in APP
    # incoming distress is styled + alarmed
    assert '(p.sos ? " sos" : "")' in APP and "function sosIncoming" in APP


# --- RF vs MQTT ------------------------------------------------------------------

def test_rf_only_toggle_and_internet_only_styling_exist():
    assert 'id="rf-only"' in HTML and 'id="c-link"' in HTML and 'class="sw mqtt"' in HTML
    assert re.search(r"\.node\.mqtt-only \.core \{[^}]*background: transparent", CSS)      # hollow, not solid
    assert re.search(r"body\.rfonly \.pkt\[data-via=\"mqtt\"\] \{ display: none !important; \}", CSS)
    assert 'el.dataset.via = "mqtt"' in APP and "function applyRfOnly" in APP and "function linkLabel" in APP
    assert "Internet-only — not reachable on your radio." in APP


# --- desktop scale ------------------------------------------------------------------

def test_ui_scales_from_one_root_font_size():
    assert re.search(r"html \{ font-size: var\(--fs\); \}", CSS)
    assert not re.search(r"html, body \{[^}]*font:", CSS)                # rem on <html> is the browser default, not --fs
    assert re.search(r"@media \(min-width: 1600px\) \{\s*:root \{ --fs: 15px;", CSS) and re.search(r"@media \(min-width: 2200px\) \{\s*:root \{ --fs: 16px;", CSS)
    assert len(re.findall(r"font-size: \d+(?:\.\d+)?px", CSS)) <= 3     # type is rem-based so the whole UI scales together
    assert "function miniMap" in APP and 'id="minimap"' in APP and "minimap-empty" in APP
    assert "Open in classic v1" not in APP                                 # v2 never links out to v1


# --- polish pass -----------------------------------------------------------------

def test_polish_accessibility_and_phone_guards():
    assert 'aria-label="chime on new messages"' in HTML and 'aria-label="mesh health alerts"' in HTML
    assert HTML.count('<div class="stat" title=') == 5                     # every topbar stat explains itself
    assert 'id="offline"' in HTML and 'sock.on("connect", () => { $("offline").hidden = true; })' in APP
    assert 'el.tabIndex = 0; el.setAttribute("role", "button")' in APP       # feed cards reachable by keyboard
    assert "prefers-reduced-motion: reduce" in CSS and ":focus-visible { outline: 2px solid var(--terracotta)" in CSS
    assert 'id="view-retry"' in APP                                           # a failed view offers a retry
    assert re.search(r"@media \(max-width: 760px\) \{[^@]*\.sidebar a\[data-view=\"replay\"\] \{ display: none; \}", CSS)
    assert 'window.innerWidth <= 760) { toast("Replay needs a wider screen' in APP
    assert "internet-only node — it has never been heard on your radio" in APP
