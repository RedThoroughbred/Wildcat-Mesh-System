"""The /v2 blueprint + Socket.IO namespace. Mounted by observatory/app.py."""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, render_template, send_from_directory

from .bridge import NAMESPACE, Bridge


def create_blueprint(bridge: Bridge, socketio) -> Blueprint:
    bp = Blueprint("v2", __name__, url_prefix="/v2")

    @bp.route("/")
    def index():
        return render_template("v2/index.html", public=False)

    @bp.route("/public")
    def public():
        """Shareable read-only view: same live map + feed, no controls, no install chrome.
        (The whole /v2 API is read-only anyway; this hides the operator affordances.)"""
        return render_template("v2/index.html", public=True)

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

    socketio.on_event("connect", on_connect, namespace=NAMESPACE)
    return bp
