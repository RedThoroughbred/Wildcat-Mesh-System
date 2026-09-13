"""The /v2 blueprint + Socket.IO namespace. Mounted by observatory/app.py."""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, render_template, send_from_directory

from .bridge import NAMESPACE, Bridge


def create_blueprint(bridge: Bridge, socketio) -> Blueprint:
    bp = Blueprint("v2", __name__, url_prefix="/v2")

    def _asset_version() -> str:
        """Cache-buster: the newest mtime among the v2 assets (no build step, no hashes)."""
        try:
            return str(int(max(os.path.getmtime(os.path.join(_static, f)) for f in ("app.js", "app.css"))))
        except OSError:
            return "0"

    @bp.route("/")
    def index():
        return render_template("v2/index.html", public=False, v=_asset_version())

    @bp.route("/public")
    def public():
        """Shareable read-only view: same live map + feed, no controls, no install chrome.
        (The whole /v2 API is read-only anyway; this hides the operator affordances.)"""
        return render_template("v2/index.html", public=True, v=_asset_version())

    @bp.route("/api/history")
    def api_history():
        import time
        from flask import request
        from .bridge import history
        try:
            hours = min(168.0, max(0.25, float(request.args.get("hours", 6))))
        except ValueError:
            hours = 6.0
        since = int(time.time() - hours * 3600)
        events = history(str(bridge.cfg.database.path), since, bridge.state.my_id)
        return jsonify({"since": since, "hours": hours, "events": events, "count": len(events)})

    # PWA plumbing: the manifest and the service worker must live under the /v2/ scope.
    _static = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "observatory", "static", "v2")

    @bp.route("/manifest.webmanifest")
    def manifest():
        r = send_from_directory(_static, "manifest.webmanifest", mimetype="application/manifest+json")
        r.headers["Cache-Control"] = "no-cache"
        return r

    @bp.route("/sw.js")
    def service_worker():
        r = send_from_directory(_static, "sw.js", mimetype="application/javascript")
        r.headers["Cache-Control"] = "no-cache"
        r.headers["Service-Worker-Allowed"] = "/v2/"
        return r

    @bp.route("/api/state")
    def api_state():
        return jsonify(bridge.snapshot())

    @bp.route("/api/coverage")
    def api_coverage():
        import time
        from flask import request
        try:
            hours = float(request.args.get("hours", 24 * 30))
        except ValueError:
            hours = 24 * 30
        since = int(time.time() - hours * 3600)
        return jsonify({"since": since, "points": bridge.coverage.recent(since, limit=5000),
                        "summary": bridge.coverage.summary(since)})

    @bp.route("/api/node/<nid>")
    def api_node(nid):
        from flask import request
        from .bridge import node_detail
        try:
            hours = float(request.args.get("hours", 24))
        except ValueError:
            hours = 24
        d = node_detail(bridge.state, str(bridge.cfg.database.path), nid, hours)
        if d is None:
            return jsonify({"error": "unknown node"}), 404
        return jsonify(d)

    # ---- the v1 pages' data, v2-native (wildcat/observatory/queries.py) ---------------------
    from . import queries as Q

    def _db() -> str:
        return str(bridge.cfg.database.path)

    def _hours(default: int, cap: int = 24 * 90) -> int:
        from flask import request
        try:
            return max(1, min(cap, int(request.args.get("hours", default))))
        except ValueError:
            return default

    @bp.route("/api/nodes")
    def api_nodes():
        """Every known node: the live roster row merged with its message statistics."""
        stats = Q.node_stats(_db())
        with bridge.state.lock:
            roster = {k: dict(v) for k, v in bridge.state.roster.items()}
        for nid, st in stats.items():
            row = roster.setdefault(nid, {"id": nid, "proto": None, "short_name": st.get("short_name") or nid[-4:],
                                          "long_name": st.get("long_name"), "hw": st.get("hw"), "role": st.get("role"),
                                          "position": None, "hops_away": None, "snr": None, "battery": None, "voltage": None,
                                          "last_heard": st.get("last_seen")})
            row["stats"] = {k: st[k] for k in ("message_count", "first_seen", "last_seen", "avg_snr", "best_snr", "worst_snr", "avg_rssi")}
        return jsonify({"my_id": bridge.state.my_id, "nodes": list(roster.values()), "mesh": Q.mesh_stats(_db())})

    @bp.route("/api/node/<nid>/full")
    def api_node_full(nid):
        from .bridge import node_detail
        d = node_detail(bridge.state, _db(), nid, 24 * 7)
        if d is None:
            st = Q.node_stats(_db()).get(nid)
            if st is None:
                return jsonify({"error": "unknown node"}), 404
            d = {"node": {"id": nid, "short_name": st.get("short_name") or nid[-4:], "long_name": st.get("long_name"),
                          "hw": st.get("hw"), "role": st.get("role"), "position": None, "last_heard": st.get("last_seen")},
                 "signal": [], "telemetry": [], "packets": [], "counts": {"signal": 0, "telemetry": 0, "packets_24h": 0}}
        d["stats"] = Q.node_stats(_db()).get(nid)
        d["messages"] = Q.node_messages(_db(), nid)
        d["reliability"] = Q.node_reliability(_db(), nid)
        return jsonify(d)

    @bp.route("/api/channels")
    def api_channels():
        h = _hours(24)
        return jsonify({"hours": h, "mesh": Q.mesh_stats(_db()), "activity": Q.channel_activity(_db(), h),
                        "details": Q.channel_details(_db(), h), "top_senders": Q.top_senders(_db(), h),
                        "hourly": Q.hourly_activity(_db(), h)})

    @bp.route("/api/channel/<int:channel>")
    def api_channel(channel):
        h = _hours(24)
        return jsonify({"channel": channel, "hours": h, "messages": Q.channel_messages(_db(), channel, h),
                        "details": next((d for d in Q.channel_details(_db(), h) if d["channel"] == channel), None)})

    @bp.route("/api/propagation")
    def api_propagation():
        from flask import request
        try:
            days = max(1, min(90, int(request.args.get("days", 7))))
        except ValueError:
            days = 7
        return jsonify({"days": days, "hourly": Q.hourly_snr_trends(_db(), days), **Q.best_worst(_db(), days),
                        "distribution": Q.snr_distribution(_db(), days)})

    @bp.route("/api/messages")
    def api_messages():
        h = _hours(168)
        return jsonify({"hours": h, "my_id": bridge.state.my_id, "messages": Q.bbs_messages(_db(), bridge.state.my_id, h)})

    @bp.route("/api/bulletins")
    def api_bulletins():
        from flask import request
        board = request.args.get("board") or None
        return jsonify({"boards": Q.bulletin_boards(_db()), "bulletins": Q.bulletins(_db(), board), "mail": Q.mail_summary(_db())})

    @bp.route("/api/topology")
    def api_topology():
        with bridge.state.lock:
            live = list(bridge.state.links.values())
        return jsonify({"edges": Q.neighbor_edges(_db()), "live": live})

    @bp.route("/api/logs")
    def api_logs():
        from flask import request
        kind = request.args.get("type", "messages")
        try:
            limit = int(request.args.get("limit", 100))
        except ValueError:
            limit = 100
        return jsonify({"type": kind, "rows": Q.recent_logs(_db(), kind, limit)})

    @bp.route("/api/dashboard")
    def api_dashboard():
        return jsonify({"mesh": Q.mesh_stats(_db()), "activity": Q.channel_activity(_db(), 24),
                        "low_battery": Q.low_battery(_db()), "top": Q.top_senders(_db(), 24, 5)})

    @bp.route("/api/health")
    def api_health():
        s = bridge.state
        return jsonify({"ok": True, "bus": s.bus_connected, "meshd": s.meshd.get("state"), "my_id": s.my_id,
                        "nodes": len(s.roster), "packets_seen": s.total})

    def on_connect(auth=None):
        # push the full picture to the newcomer only; deltas follow on the namespace
        socketio.emit("snapshot", bridge.snapshot(), namespace=NAMESPACE, to=_sid())

    def _sid():
        from flask import request
        return request.sid  # type: ignore[attr-defined]

    @bp.after_app_request
    def _no_stale_assets(resp):
        # No build step means no content hashes: force revalidation of the v2 page and its
        # assets so an edit (or a git pull on the Pi) shows up on the next reload.
        from flask import request
        pth = request.path
        if pth.startswith("/v2") or pth.startswith("/static/v2/") or pth.startswith("/static/vendor/"):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    socketio.on_event("connect", on_connect, namespace=NAMESPACE)
    return bp
