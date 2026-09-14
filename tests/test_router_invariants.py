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
    assert 'history.replaceState(null, "", location.pathname + location.search + "#/")' in APP


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
    for view in ("nodes", "channels", "messages", "propagation", "topology", "health", "admin", "api", "dashboard"):
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
    assert 'id="sb-share"' in HTML
    assert 'on($("sb-share"), "click", sharePublic)' in APP or '$("sb-share")' in APP
    # the drawer button is hidden on desktop and in the public view
    assert re.search(r"^\.sb-share\s*\{\s*display:\s*none", CSS, re.M)
    assert re.search(r"body\.public \.sb-share\s*\{\s*display:\s*none", CSS)
