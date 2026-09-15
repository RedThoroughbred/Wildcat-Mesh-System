"""The /v2 blueprint + Socket.IO namespace. Mounted by observatory/app.py."""
from __future__ import annotations

import os
import time

from flask import Blueprint, jsonify, render_template, send_from_directory

from .bridge import NAMESPACE, Bridge


def create_blueprint(bridge: Bridge, socketio) -> Blueprint:
    bp = Blueprint("v2", __name__, url_prefix="/v2")

    def url_for_index() -> str:
        return bp.url_prefix.rstrip("/") + "/"

    def _asset_version() -> str:
        """Cache-buster: the newest mtime among the v2 assets (no build step, no hashes)."""
        try:
            return str(int(max(os.path.getmtime(os.path.join(_static, f)) for f in ("app.js", "app.css"))))
        except OSError:
            return "0"

    COOKIE = "wildcat_op"

    def _token() -> str:
        return (getattr(bridge.cfg.observatory, "operator_token", "") or "").strip()

    def _authorized() -> bool:
        """Writes need the operator token once one is configured: a bearer/X-Wildcat-Token
        header (Shortcuts, scripts) or the cookie the dashboard sets from /v2/?token=…"""
        from flask import request
        import hmac
        tok = _token()
        if not tok:
            return True
        auth = request.headers.get("Authorization", "")
        given = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-Wildcat-Token", "")
        given = given or request.cookies.get(COOKIE, "")
        return bool(given) and hmac.compare_digest(given, tok)

    @bp.before_request
    def _gate_writes():
        from flask import request
        if request.method in ("POST", "PATCH", "PUT", "DELETE") and not _authorized():
            return jsonify({"error": "operator token required", "hint": "send Authorization: Bearer <token>, or open /v2/?token=<token> once on this device"}), 401

    @bp.route("/")
    def index():
        from flask import request, redirect, make_response
        tok = _token()
        given = request.args.get("token")
        if tok and given is not None:
            import hmac
            if hmac.compare_digest(given, tok):
                resp = make_response(redirect(url_for_index()))
                resp.set_cookie(COOKIE, tok, max_age=365 * 86400, httponly=True, samesite="Lax", path="/v2")
                return resp
            return jsonify({"error": "wrong operator token"}), 403
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

    @bp.route("/api/about")
    def api_about():
        """What a first-time visitor needs to know about this mesh (public view welcome card)."""
        o, b = bridge.cfg.observatory, bridge.cfg.bbs
        with bridge.state.lock:
            R = list(bridge.state.roster.values()); my = bridge.state.my_id
            den = bridge.state.roster.get(my or "", {})
        now = time.time()
        return jsonify({"name": o.community_name or b.name, "blurb": o.community_blurb, "den": {"id": my, "name": den.get("short_name") or "the Den"},
                        "counts": {"nodes": len(R), "rf": sum(1 for n in R if n.get("transport") in ("rf", "both")),
                                   "internet_only": sum(1 for n in R if n.get("transport") == "mqtt"),
                                   "heard_1h": sum(1 for n in R if (n.get("last_heard") or 0) >= now - 3600)},
                        "bobcat": bool(bridge.cfg.brain.enabled and (bridge.state.brain_status or {}).get("running"))})

    @bp.route("/api/auth")
    def api_auth():
        """Does this device hold the operator token? (No token configured → everything is open.)"""
        return jsonify({"required": bool(_token()), "authorized": _authorized()})

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

    @bp.route("/api/chat")
    def api_chat():
        """Conversations: every text in the window (DMs + channel broadcasts), newest first."""
        h = _hours(168)
        return jsonify({"hours": h, "my_id": bridge.state.my_id, "messages": Q.chat_messages(_db(), bridge.state.my_id, h)})

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

    # ---- admin: operator actions (parity with v1's admin page; the public VIEW hides them,
    # the API itself is LAN-trust like v1 — put the Den behind Tailscale/LAN, not the internet)
    @bp.route("/api/config")
    def api_config():
        from ..config import to_toml
        c = bridge.cfg
        return jsonify({"source": str(c.source), "kind": c.source_kind, "editable": c.source_kind == "toml",
                        "toml": to_toml(c, redact=True), "warnings": c.warnings,
                        "bbs": {"name": c.bbs.name, "sync_nodes": c.bbs.sync_nodes, "allowed_nodes": c.bbs.allowed_nodes, "quiet_nodes": c.bbs.quiet_nodes,
                                "weather_api_key": "***" if c.bbs.weather_api_key else "",
                                "menu": {"main": c.bbs.menu.main, "bbs": c.bbs.menu.bbs, "utilities": c.bbs.menu.utilities}},
                        "radio": {"type": c.radio.type, "host": c.radio.host, "port": c.radio.port}})

    CONTENT_FILES = {"fortunes": "fortunes.txt", "trivia": "trivia.txt", "messages": "messages.json"}

    @bp.route("/api/content/<name>", methods=["GET", "POST"])
    def api_content(name):
        from flask import request
        import shutil, time as _t
        fn = CONTENT_FILES.get(name)
        if not fn:
            return jsonify({"error": "unknown content file"}), 404
        path = bridge.cfg.bbs.content_dir / fn
        if request.method == "GET":
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            return jsonify({"name": name, "path": str(path), "text": text, "exists": path.exists()})
        body = request.get_json(silent=True) or {}
        text = body.get("text")
        if not isinstance(text, str):
            return jsonify({"error": "text required"}), 400
        if name == "messages":
            import json as _json
            try:
                _json.loads(text)
            except ValueError as e:
                return jsonify({"error": f"messages.json must be valid JSON: {e}"}), 400
        try:
            if path.exists():
                shutil.copy2(path, str(path) + ".bak")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "path": str(path), "backup": str(path) + ".bak" if path.exists() else None, "lines": text.count("\n") + 1})

    @bp.route("/api/tx", methods=["POST"])
    def api_tx():
        """Operator send: a neutral TX request on the bus (meshd owns, paces and chunks it).
        Body: {"to": "!nodeid" | "^all", "text": "…", "channel": 0}. Returns the tracked send."""
        from flask import request
        if bridge.bus is None:
            return jsonify({"error": "the bus is off ([mqtt].enabled = false) — nothing owns the radio for the Observatory to ask"}), 503
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text or len(text.encode("utf-8")) > 200:
            return jsonify({"error": "text must be 1–200 bytes (one LoRa packet)"}), 400
        to = body.get("to") or "^all"
        if to != "^all" and not (isinstance(to, str) and to.startswith("!") and len(to) == 9):
            return jsonify({"error": "'to' must be a node id like !9e766b18 or ^all"}), 400
        try:
            channel = int(body.get("channel", 0))
        except (TypeError, ValueError):
            channel = 0
        if not 0 <= channel <= 7:
            return jsonify({"error": "channel must be 0–7"}), 400
        try:
            rec = bridge.send(to, text, channel)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503
        return jsonify({"ok": True, **rec})

    @bp.route("/api/sos", methods=["GET"])
    def api_sos():
        return jsonify(bridge.sos.status())

    @bp.route("/api/sos", methods=["POST"])
    def api_sos_start():
        """Operator: broadcast an SOS now, optionally repeating. Body: {"text", "channel"?,
        "interval"? (s), "repeat"? (0 = until stopped, 1 = once, n = n sends), "include_position"?}."""
        from flask import request
        if bridge.bus is None:
            return jsonify({"error": "the bus is off ([mqtt].enabled = false) — nothing owns the radio"}), 503
        body = request.get_json(silent=True) or {}
        try:
            st = bridge.sos.start(str(body.get("text") or ""), channel=int(body.get("channel", 0)),
                                  interval=int(body.get("interval", 300)), repeat=int(body.get("repeat", 1)),
                                  include_position=bool(body.get("include_position", True)), by=str(body.get("by") or "operator"))
        except (TypeError, ValueError) as e:
            return jsonify({"error": str(e)}), 400
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503
        return jsonify({"ok": True, **st})

    @bp.route("/api/sos", methods=["DELETE"])
    def api_sos_stop():
        return jsonify({"ok": True, **bridge.sos.stop(by="operator")})

    @bp.route("/api/tx")
    def api_tx_list():
        with bridge.state.lock:
            return jsonify({"sends": list(bridge.state.tx.values())[-50:]})

    @bp.route("/api/tx/<tx_id>")
    def api_tx_one(tx_id):
        with bridge.state.lock:
            rec = bridge.state.tx.get(tx_id)
        return (jsonify(rec), 200) if rec else (jsonify({"error": "unknown send"}), 404)

    @bp.route("/api/restart", methods=["POST"])
    def api_restart():
        """Restart the BBS service (systemd on the Pi). Elsewhere: says so."""
        import shutil, subprocess
        from flask import request
        unit = (request.get_json(silent=True) or {}).get("unit", "wildcat-bbs")
        if unit not in ("wildcat-bbs", "wildcat-telemetry", "wildcat-meshd", "mesh-bbs"):
            return jsonify({"error": "unknown unit"}), 400
        if shutil.which("systemctl") is None:
            return jsonify({"ok": False, "message": "no systemd on this host — restart the process by hand (see logs/)"}), 501
        try:
            r = subprocess.run(["sudo", "-n", "systemctl", "restart", f"{unit}.service"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as e:
            return jsonify({"ok": False, "message": str(e)}), 500
        if r.returncode != 0:
            return jsonify({"ok": False, "message": (r.stderr or r.stdout).strip() or f"exit {r.returncode}"}), 500
        return jsonify({"ok": True, "message": f"{unit} restarted"})

    @bp.route("/api/services")
    def api_services():
        """What's running: bus, meshd, this process; systemd units when present."""
        import shutil, subprocess, time as _t
        units = {}
        if shutil.which("systemctl"):
            for u in ("wildcat-meshd", "wildcat-bbs", "wildcat-telemetry", "wildcat-observatory", "mosquitto"):
                try:
                    out = subprocess.run(["systemctl", "show", "-p", "ActiveState,SubState", f"{u}.service"], capture_output=True, text=True, timeout=5).stdout
                    units[u] = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
                except (OSError, subprocess.SubprocessError):
                    pass
        s = bridge.state
        return jsonify({"bus": s.bus_connected, "meshd": s.meshd, "my_id": s.my_id, "uptime": _t.time() - bridge.started_at,
                        "packets_seen": s.total, "mqtt_enabled": bridge.cfg.mqtt.enabled, "units": units, "systemd": bool(units)})

    def health_report():
        """The mesh-health report as a dict (no request context needed — the scheduler uses it too)."""
        import time as _t
        from . import health as Hm
        from .bridge import history
        now = _t.time()
        events = history(_db(), int(now - 26 * 3600), bridge.state.my_id, limit=20000)
        with bridge.state.lock:
            roster = {k: dict(v) for k, v in bridge.state.roster.items()}
            meshd, bus, my = dict(bridge.state.meshd), bridge.state.bus_connected, bridge.state.my_id
            last_ts = bridge.state.packets[-1]["ts"] if bridge.state.packets else None
        return Hm.compute(now, my, roster, meshd, bus, Hm.rate_buckets([e["ts"] for e in events], now),
                          Q.low_battery(_db()), Hm.regular_counts(events, now), last_ts)
    bridge.health_fn = health_report

    @bp.route("/api/health-report")
    def api_health_report():
        return jsonify(health_report())

    # ---- v2-native exports (no links out to v1) ----------------------------------------
    def _csv(rows, cols, filename):
        import csv, io
        from flask import Response
        buf = io.StringIO(); w = csv.writer(buf); w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") if r.get(c) is not None else "" for c in cols])
        return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

    @bp.route("/api/export/nodes.csv")
    def export_nodes():
        stats = Q.node_stats(_db())
        with bridge.state.lock:
            roster = {k: dict(v) for k, v in bridge.state.roster.items()}
        rows = []
        for nid in sorted(set(roster) | set(stats)):
            n, st = roster.get(nid, {}), stats.get(nid, {})
            pos = n.get("position") or {}
            rows.append({"id": nid, "short_name": n.get("short_name") or st.get("short_name"), "long_name": n.get("long_name") or st.get("long_name"),
                         "hw": n.get("hw") or st.get("hw"), "role": n.get("role") or st.get("role"), "proto": n.get("proto"),
                         "last_heard": n.get("last_heard") or st.get("last_seen"), "hops_away": n.get("hops_away"), "snr": n.get("snr"),
                         "battery": n.get("battery"), "voltage": n.get("voltage"), "lat": pos.get("lat"), "lon": pos.get("lon"),
                         "message_count": st.get("message_count"), "avg_snr": st.get("avg_snr"), "best_snr": st.get("best_snr"),
                         "worst_snr": st.get("worst_snr"), "avg_rssi": st.get("avg_rssi"), "first_seen": st.get("first_seen")})
        return _csv(rows, ["id", "short_name", "long_name", "hw", "role", "proto", "last_heard", "hops_away", "snr", "battery", "voltage",
                           "lat", "lon", "message_count", "avg_snr", "best_snr", "worst_snr", "avg_rssi", "first_seen"], "wildcat-nodes.csv")

    @bp.route("/api/export/messages.csv")
    def export_messages():
        rows = Q.recent_logs(_db(), "messages", 1000)
        return _csv(rows, ["ts", "id", "short_name", "to_id", "channel", "text", "snr", "rssi"], "wildcat-messages.csv")

    @bp.route("/api/export/coverage.csv")
    def export_coverage():
        import time as _t
        rows = bridge.coverage.recent(int(_t.time() - 90 * 86400), limit=50000)
        return _csv(rows, ["ts", "node_id", "proto", "lat", "lon", "alt", "snr", "rssi", "hops", "pos_age", "kind", "source"], "wildcat-coverage.csv")

    # ---- config editor: the safe, BBS-facing subset, written back as TOML --------------
    EDITABLE = ("name", "sync_nodes", "allowed_nodes", "quiet_nodes", "weather_api_key")

    @bp.route("/api/config", methods=["POST"])
    def api_config_save():
        """Edit [bbs] name / sync_nodes / allowed_nodes / weather key and [bbs.menu] lists.
        Validated through the same schema as boot; the file is rewritten as TOML with a .bak."""
        import shutil
        from flask import request
        from ..config import ConfigError, build, to_toml, set_config, tomllib
        body = request.get_json(silent=True) or {}
        cfg = bridge.cfg
        if cfg.source is None or cfg.source_kind != "toml":
            return jsonify({"error": "config is not a wildcat.toml (run `wildcat config migrate --write` first)"}), 400
        data = tomllib.loads(to_toml(cfg))            # current effective config, every key explicit
        b = data.setdefault("bbs", {})
        for k in EDITABLE:
            if k in body:
                b[k] = body[k]
        if isinstance(body.get("menu"), dict):
            m = b.setdefault("menu", {})
            for k in ("main", "bbs", "utilities"):
                if k in body["menu"]:
                    m[k] = body["menu"][k]
        try:
            new = build(data, label=str(cfg.source), source=cfg.source, source_kind="toml")
        except ConfigError as e:
            return jsonify({"error": str(e)}), 400
        try:
            shutil.copy2(cfg.source, str(cfg.source) + ".bak")
            cfg.source.write_text("# rewritten by Observatory v2's config editor — every key explicit; comments from the\n"
                                  "# example file are in config/wildcat.example.toml\n\n" + to_toml(new), encoding="utf-8")
        except OSError as e:
            return jsonify({"error": str(e)}), 500
        set_config(new); bridge.cfg = new
        return jsonify({"ok": True, "path": str(cfg.source), "bbs": {"name": new.bbs.name, "sync_nodes": new.bbs.sync_nodes,
                        "allowed_nodes": new.bbs.allowed_nodes, "quiet_nodes": new.bbs.quiet_nodes, "weather_api_key": "***" if new.bbs.weather_api_key else "",
                        "menu": {"main": new.bbs.menu.main, "bbs": new.bbs.menu.bbs, "utilities": new.bbs.menu.utilities}},
                        "warnings": new.warnings, "note": "restart the BBS to apply"})

    # ---- Bobcat, Part A: the operator's analyst (local claude CLI, read-only DB) ---------
    def _responder_status():
        """What the dashboard shows for Part B: the responder's retained status if the service
        is running, else what the config says (and that nothing is listening)."""
        b = bridge.cfg.brain
        live = bridge.state.brain_status
        base = {"running": False, "enabled": b.enabled, "prefix": b.trigger_prefix, "persona": b.persona,
                "providers": [p for p in b.providers if p != "ollama" or b.ollama_url], "model": b.cli_model,
                "max_chunks": b.max_chunks, "max_reply_chars": b.max_reply_chars,
                "limits": {"per_node_per_hour": b.per_node_per_hour, "per_node_per_day": b.per_node_per_day,
                           "global_per_hour": b.global_per_hour, "max_channel_util_pct": b.max_channel_util_pct},
                "cost_note": "each answered question ≈ one claude CLI call (a few cents) + up to %d LoRa packets of airtime" % b.max_chunks}
        if isinstance(live, dict):
            base.update(live)
            base["cost_note"] = base["cost_note"] if "cost_note" in live else base["cost_note"]
        base["config_enabled"] = b.enabled
        return base

    @bp.route("/api/brain/responder", methods=["POST"])
    def api_brain_responder():
        """The operator's hard switch for Bobcat on the air. Writes [brain].enabled to
        wildcat.toml (with a .bak) FIRST — the file is the truth across restarts — then
        publishes the retained control message the running responder acts on at once."""
        import shutil
        from flask import request
        from ..config import ConfigError, build, to_toml, set_config, tomllib
        body = request.get_json(silent=True) or {}
        want = body.get("enabled")
        if not isinstance(want, bool):
            return jsonify({"error": "enabled must be true or false"}), 400
        cfg = bridge.cfg
        if bridge.bus is None:
            return jsonify({"error": "the bus is off ([mqtt].enabled = false) — Bobcat needs meshd + the bus"}), 409
        if cfg.source is None or cfg.source_kind != "toml":
            return jsonify({"error": "config is not a wildcat.toml (run `wildcat config migrate --write` first)"}), 400
        data = tomllib.loads(to_toml(cfg))
        data.setdefault("brain", {})["enabled"] = want
        try:
            new = build(data, label=str(cfg.source), source=cfg.source, source_kind="toml")
        except ConfigError as e:
            return jsonify({"error": str(e)}), 400
        try:
            shutil.copy2(cfg.source, str(cfg.source) + ".bak")
            cfg.source.write_text("# rewritten by Observatory v2's config editor — every key explicit; comments from the\n"
                                  "# example file are in config/wildcat.example.toml\n\n" + to_toml(new), encoding="utf-8")
        except OSError as e:
            return jsonify({"error": str(e)}), 500
        set_config(new); bridge.cfg = new
        bridge.set_responder(want, by="observatory")
        return jsonify({"ok": True, "enabled": want, "path": str(cfg.source),
                        "note": "on the air now" if want else "Bobcat is off — questions are recorded, nothing is sent",
                        "responder": _responder_status()})

    # ---- the daily digest (Bobcat writes it from a deterministic fact sheet) ----------------
    @bp.route("/api/digest")
    def api_digest_list():
        from ..brain import digest as DG
        from flask import request
        try:
            limit = max(1, min(50, int(request.args.get("limit", 10))))
        except ValueError:
            limit = 10
        try:
            items = DG.latest(_db(), limit)
        except Exception as e:
            return jsonify({"error": str(e), "digests": []}), 500
        return jsonify({"digests": items, "model": bridge.cfg.brain.cli_model,
                        "enabled": bridge.cfg.brain.analyst_enabled, "public": False})

    @bp.route("/api/digest", methods=["POST"])
    def api_digest_make():
        """Operator: write the digest now (one claude CLI call, a few cents). Body: {"hours"?: 24}.
        Falls back to the plain fact sheet when the CLI is unavailable — never nothing."""
        from flask import request
        from ..brain import digest as DG
        if not bridge.cfg.brain.analyst_enabled:
            return jsonify({"error": "Bobcat's operator features are disabled ([brain].analyst_enabled = false)"}), 403
        body = request.get_json(silent=True) or {}
        try:
            hours = max(1, min(168, int(body.get("hours", 24))))
        except (TypeError, ValueError):
            hours = 24
        try:
            rep = health_report()
        except Exception:
            rep = None
        try:
            rec = DG.write(_db(), bridge.state, bridge.cfg.brain.cli_model, bridge.cfg.brain.cli_timeout, hours=hours, health=rep,
                           source=str(body.get("source") or "operator"))
        except Exception as e:
            return jsonify({"error": f"could not write the digest: {e}"}), 500
        return jsonify({"ok": True, **rec})

    @bp.route("/api/digest/<int:digest_id>/bulletin", methods=["POST"])
    def api_digest_bulletin(digest_id: int):
        """Operator: post a stored digest to the BBS bulletins (this Den's board; no radio traffic)."""
        from flask import request
        from ..brain import digest as DG
        body = request.get_json(silent=True) or {}
        my = bridge.state.my_id
        sender = (bridge.state.roster.get(my or "", {}).get("short_name") if my else None) or bridge.cfg.bbs.name[:20] or "Den"
        try:
            out = DG.post_bulletin(_db(), digest_id, str(body.get("board") or "General"), sender, body.get("subject"))
        except KeyError:
            return jsonify({"error": "no such digest"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, **out})

    # ---- scheduled posts (bulletins, broadcasts, the daily digest) ------------------------
    @bp.route("/api/schedules")
    def api_schedules():
        from .scheduler import KINDS
        try:
            return jsonify({"schedules": bridge.scheduler.list(), "kinds": list(KINDS), "now": time.time(),
                            "tz": time.strftime("%Z"), "public": False})
        except Exception as e:
            return jsonify({"error": str(e), "schedules": []}), 500

    @bp.route("/api/schedules", methods=["POST"])
    def api_schedules_add():
        from flask import request
        b = request.get_json(silent=True) or {}
        try:
            hh, mm = _hhmm(b.get("time"), b.get("hour"), b.get("minute"))
            job = bridge.scheduler.add(str(b.get("kind") or ""), str(b.get("name") or ""), hh, mm, b.get("payload") or {},
                                       days=b.get("days"), enabled=bool(b.get("enabled", True)))
        except (TypeError, ValueError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, **job})

    @bp.route("/api/schedules/<int:sid>", methods=["PATCH"])
    def api_schedules_edit(sid: int):
        from flask import request
        b = request.get_json(silent=True) or {}
        fields = {k: b[k] for k in ("name", "enabled", "days", "payload") if k in b}
        try:
            if "time" in b or "hour" in b:
                fields["hour"], fields["minute"] = _hhmm(b.get("time"), b.get("hour"), b.get("minute"))
            job = bridge.scheduler.update(sid, **fields)
        except KeyError:
            return jsonify({"error": "no such schedule"}), 404
        except (TypeError, ValueError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, **job})

    @bp.route("/api/schedules/<int:sid>", methods=["DELETE"])
    def api_schedules_delete(sid: int):
        return (jsonify({"ok": True}) if bridge.scheduler.remove(sid) else (jsonify({"error": "no such schedule"}), 404))

    @bp.route("/api/schedules/<int:sid>/run", methods=["POST"])
    def api_schedules_run(sid: int):
        try:
            res = bridge.scheduler.run(sid, manual=True)
        except KeyError:
            return jsonify({"error": "no such schedule"}), 404
        return jsonify({"ok": bool(res.get("ok")), **res}), (200 if res.get("ok") else 500)

    def _hhmm(t, h, m):
        if isinstance(t, str) and ":" in t:
            hh, mm = t.split(":", 1)
            return int(hh), int(mm)
        if h is None:
            raise ValueError("time must be HH:MM")
        return int(h), int(m or 0)

    @bp.route("/api/brain/status")
    def api_brain_status():
        from ..brain import cli as bcli
        st = bcli.status()
        st.update({"analyst_enabled": bridge.cfg.brain.analyst_enabled, "model": bridge.cfg.brain.analyst_model,
                   "responder_enabled": bridge.cfg.brain.enabled, "cost_note": "each question is one to three CLI calls (cents)",
                   "responder": _responder_status()})
        return jsonify(st)

    @bp.route("/api/brain/ask", methods=["POST"])
    def api_brain_ask():
        """Server-sent events: status · delta · retract · sql · done · error."""
        import json as _json
        from flask import Response, request, stream_with_context
        from ..brain import analyst as A
        from . import health as Hm
        if not bridge.cfg.brain.analyst_enabled:
            return jsonify({"error": "the analyst is disabled ([brain].analyst_enabled = false)"}), 403
        body = request.get_json(silent=True) or {}
        q = (body.get("question") or "").strip()
        if not q or len(q) > 2000:
            return jsonify({"error": "question must be 1–2000 characters"}), 400
        hist = [h for h in (body.get("history") or []) if isinstance(h, dict)][-A.MAX_HISTORY:]
        model = body.get("model") or bridge.cfg.brain.analyst_model
        try:
            rep = api_health_report().get_json()
        except Exception:
            rep = None

        def gen():
            for ev in A.ask(q, hist, bridge.state, _db(), model, bridge.cfg.brain.cli_timeout, health=rep):
                yield "data: " + _json.dumps(ev, default=str) + "\n\n"
        return Response(stream_with_context(gen()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
        if pth.startswith("/v2/api/"):
            # A native shell (Capacitor: capacitor://localhost / http://localhost) calls this API
            # cross-origin. Read endpoints are LAN-trust already; writes stay LAN-trust like v1.
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    socketio.on_event("connect", on_connect, namespace=NAMESPACE)
    return bp
