"""The daily mesh digest — "here's what happened" — written by the local ``claude`` CLI
from a deterministic fact sheet.

Two halves, deliberately separate:

* :func:`gather` reads the database + the live State and produces **facts** (numbers,
  names, times) with no model involved. That is what the prompt gets, and what is
  stored next to the prose, so a reader can always check the summary against the
  facts it was written from.
* :func:`summarize` turns the facts into ~150 words of plain text through the
  tool-less CLI (one call, budget-capped). If the CLI is unavailable the digest is
  the facts themselves, rendered plainly — never nothing.

Digests are stored in the Den's SQLite (``digests`` table, created here on first
use) and can be posted to the BBS as a bulletin (the ``bulletins`` table the BBS
serves), so mesh users can read it from the BBS menu.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from typing import Any, Callable, Dict, Iterator, List, Optional

from ..observatory import queries as Q
from . import cli

SYSTEM = (
    "You write the daily digest for Wildcat Mesh, a community LoRa mesh radio network in Northern Kentucky run "
    "from 'the Den'. You are given a fact sheet for the last {hours} hours. Write the digest for the people on the "
    "mesh: lead with the one-line headline of the day, then 3 to 5 short paragraphs or bullets covering activity, "
    "who was new or went quiet, signal and battery concerns, anything urgent, and what to watch tomorrow. Use ONLY "
    "numbers and names from the fact sheet; if a section has nothing, skip it rather than pad. Plain text, no "
    "markdown, no headers, under 170 words. Name nodes by short name."
)

DISTRESS_SQL = "(message LIKE '🆘%' OR UPPER(message) LIKE 'SOS%' OR UPPER(message) LIKE 'MAYDAY%' OR UPPER(message) LIKE 'EMERGENCY%')"


def _fmt_t(ts: Optional[float]) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "?"


def gather(db_path: str, state: Any, health: Optional[Dict[str, Any]] = None, hours: int = 24,
           now: Optional[float] = None) -> Dict[str, Any]:
    """The deterministic fact sheet (everything the prose may say)."""
    now = now or time.time()
    since = now - hours * 3600
    f: Dict[str, Any] = {"now": now, "since": since, "hours": hours}
    with state.lock:
        roster = {k: dict(v) for k, v in state.roster.items()}
        my = state.my_id
        brain = [dict(b) for b in state.brain]
    f["den"] = {"id": my, "name": roster.get(my or "", {}).get("short_name") or "the Den"}
    f["nodes_known"] = len(roster)
    f["heard_in_window"] = sum(1 for n in roster.values() if (n.get("last_heard") or 0) >= since)
    try:
        st = Q.mesh_stats(db_path, now)
        f["messages"] = st.get("messages_24h") if hours == 24 else Q._one(db_path, "SELECT COUNT(*) n FROM message_logs WHERE timestamp >= ?", (int(since),))["n"]
        f["avg_snr"] = st.get("avg_snr")
        f["senders"] = (Q._one(db_path, "SELECT COUNT(DISTINCT sender_id) n FROM message_logs WHERE timestamp >= ?", (int(since),)) or {}).get("n", 0)
        f["top_senders"] = [{"name": r["short_name"], "id": r["id"], "n": r["message_count"]} for r in Q.top_senders(db_path, hours, 5)]
        f["channels"] = [{"channel": r["channel"], "n": r["count"]} for r in Q.channel_activity(db_path, hours)]
        stats = Q.node_stats(db_path)
        f["new_nodes"] = [{"name": s.get("short_name") or nid[-4:], "id": nid, "first": _fmt_t(s.get("first_seen"))}
                          for nid, s in stats.items() if (s.get("first_seen") or 0) >= since][:8]
        f["low_battery"] = [{"id": r["id"], "name": roster.get(r["id"], {}).get("short_name") or str(r["id"])[-4:], "battery": r["battery"]}
                            for r in Q.low_battery(db_path, 25, 6)]
        f["distress"] = [{"from": r["sender_short_name"], "text": (r["message"] or "")[:120], "at": _fmt_t(r["timestamp"])} for r in
                         Q._rows(db_path, f"SELECT sender_short_name, message, timestamp FROM message_logs WHERE timestamp >= ? AND {DISTRESS_SQL} ORDER BY timestamp DESC LIMIT 5", (int(since),))]
        bl = Q._rows(db_path, "SELECT board, sender_short_name, subject, date FROM bulletins ORDER BY id DESC LIMIT 30")
        f["bulletins"] = [b for b in bl if _bulletin_ts(b.get("date")) >= since][:6]
    except sqlite3.Error as e:
        f["db_error"] = str(e)
    f["bobcat"] = {"questions": sum(1 for b in brain if (b.get("ts") or 0) >= since),
                   "answered": sum(1 for b in brain if (b.get("ts") or 0) >= since and b.get("status", "sent") == "sent")}
    quiet = []
    for n in roster.values():
        lh = n.get("last_heard") or 0
        if since - 6 * 3600 <= lh < now - 6 * 3600 and n.get("id") != my:
            quiet.append({"name": n.get("short_name") or n["id"][-4:], "id": n["id"], "last": _fmt_t(lh)})
    f["went_quiet"] = sorted(quiet, key=lambda q: q["last"])[:8]
    if health:
        f["health"] = {"score": health.get("score"), "level": health.get("level"),
                       "alerts": [a.get("text") for a in (health.get("alerts") or [])][:6]}
    return f


def _bulletin_ts(date: Any) -> float:
    try:
        return time.mktime(time.strptime(str(date), "%Y-%m-%d %H:%M"))
    except (ValueError, TypeError):
        return 0.0


def facts_text(f: Dict[str, Any]) -> str:
    """The fact sheet as plain lines — the prompt, and the fallback digest."""
    L = [f"Window: last {f['hours']} h ending {time.strftime('%Y-%m-%d %H:%M', time.localtime(f['now']))}. Den node: {f['den']['name']} ({f['den']['id']}).",
         f"Nodes: {f['nodes_known']} known, {f['heard_in_window']} heard in the window. Messages: {f.get('messages', 0)} from {f.get('senders', 0)} senders"
         + (f", average SNR {f['avg_snr']} dB" if f.get("avg_snr") is not None else "") + "."]
    if f.get("top_senders"):
        L.append("Most active: " + ", ".join(f"{t['name']} ({t['n']})" for t in f["top_senders"]) + ".")
    if f.get("channels"):
        L.append("By channel: " + ", ".join(f"ch{c['channel']} {c['n']}" for c in f["channels"]) + ".")
    if f.get("new_nodes"):
        L.append("New voices: " + ", ".join(f"{n['name']} (first heard {n['first']})" for n in f["new_nodes"]) + ".")
    if f.get("went_quiet"):
        L.append("Went quiet (last heard): " + ", ".join(f"{q['name']} {q['last']}" for q in f["went_quiet"]) + ".")
    if f.get("low_battery"):
        L.append("Low battery: " + ", ".join(f"{b['name']} {b['battery']}%" for b in f["low_battery"]) + ".")
    if f.get("distress"):
        L.append("DISTRESS messages: " + "; ".join(f"{d['at']} {d['from']}: {d['text']}" for d in f["distress"]) + ".")
    if f.get("bulletins"):
        L.append("Bulletins posted: " + "; ".join(f"[{b['board']}] {b['subject']} by {b['sender_short_name']}" for b in f["bulletins"]) + ".")
    bc = f.get("bobcat") or {}
    if bc.get("questions"):
        L.append(f"Bobcat: {bc['questions']} question(s) asked, {bc['answered']} answered.")
    h = f.get("health")
    if h:
        L.append(f"Mesh health now: score {h['score']} ({h['level']})" + ("; alerts: " + "; ".join(h["alerts"]) if h.get("alerts") else "; no alerts") + ".")
    if f.get("db_error"):
        L.append(f"(database error while gathering: {f['db_error']})")
    return "\n".join(L)


def summarize(facts: Dict[str, Any], model: str, timeout: int = 120,
              runner: Optional[Callable[..., Iterator[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """One CLI call over the fact sheet. Never raises; falls back to the facts."""
    runner = runner or cli.stream            # late-bound so tests can swap the CLI out
    t0 = time.time()
    sheet = facts_text(facts)
    text, cost, err = "", None, None
    try:
        for ev in runner(sheet, SYSTEM.format(hours=facts["hours"]), model, timeout, max_budget_usd=0.15):
            if ev["type"] == "delta":
                text += ev["text"]
            elif ev["type"] == "done":
                text = ev.get("text") or text
                cost = ev.get("cost_usd")
                if ev.get("is_error"):
                    err = text or "claude reported an error"
            elif ev["type"] == "error":
                err = ev["text"]
    except Exception as e:      # a runner that doesn't accept max_budget_usd, or worse
        err = str(e)
    text = " ".join(l.strip() for l in text.strip().splitlines() if l.strip()) if text and not err else ""
    rec = {"generated_at": time.time(), "hours": facts["hours"], "model": model if text else None,
           "text": text or sheet, "facts": facts, "facts_text": sheet, "cost_usd": cost if text else 0.0,
           "ms": int((time.time() - t0) * 1000), "fallback": not text, "error": err}
    return rec


# ---------------------------------------------------------------------------- storage
def ensure(db_path: str) -> None:
    with closing(sqlite3.connect(db_path, timeout=3)) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS digests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at INTEGER NOT NULL, hours INTEGER NOT NULL,
            model TEXT, text TEXT NOT NULL, facts TEXT NOT NULL, cost_usd REAL, ms INTEGER, fallback INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'operator', bulletin_id TEXT)""")
        c.commit()


def save(db_path: str, rec: Dict[str, Any], source: str = "operator") -> int:
    ensure(db_path)
    conn = sqlite3.connect(db_path, timeout=3)
    try:
        conn.execute("PRAGMA busy_timeout = 3000")
        cur = conn.execute("INSERT INTO digests (generated_at, hours, model, text, facts, cost_usd, ms, fallback, source) VALUES (?,?,?,?,?,?,?,?,?)",
                           (int(rec["generated_at"]), rec["hours"], rec.get("model"), rec["text"], json.dumps(rec.get("facts") or {}, default=str),
                            rec.get("cost_usd"), rec.get("ms"), 1 if rec.get("fallback") else 0, source))
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    try:
        d["facts"] = json.loads(d["facts"]) if d.get("facts") else {}
    except ValueError:
        d["facts"] = {}
    d["fallback"] = bool(d.get("fallback"))
    return d


def latest(db_path: str, limit: int = 10) -> List[Dict[str, Any]]:
    ensure(db_path)
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)) as c:
        c.row_factory = sqlite3.Row
        return [_row(r) for r in c.execute("SELECT * FROM digests ORDER BY id DESC LIMIT ?", (limit,))]


def get(db_path: str, digest_id: int) -> Optional[Dict[str, Any]]:
    ensure(db_path)
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)) as c:
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM digests WHERE id = ?", (digest_id,)).fetchone()
        return _row(r) if r else None


def post_bulletin(db_path: str, digest_id: int, board: str, sender: str, subject: Optional[str] = None) -> Dict[str, Any]:
    """Post a stored digest to the BBS's bulletins table (what `[B]ulletins` on the mesh
    serves). No radio traffic: the BBS's node-to-node bulletin sync runs inside the BBS
    process; a digest posted here is read from THIS Den."""
    d = get(db_path, digest_id)
    if not d:
        raise KeyError("no such digest")
    board = (board or "General").strip()[:40] or "General"
    subject = (subject or f"Mesh digest {time.strftime('%Y-%m-%d', time.localtime(d['generated_at']))}").strip()[:80]
    uid = str(uuid.uuid4())
    conn = sqlite3.connect(db_path, timeout=3)
    try:
        conn.execute("PRAGMA busy_timeout = 3000")
        conn.execute("""CREATE TABLE IF NOT EXISTS bulletins (id INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT NOT NULL,
                        sender_short_name TEXT NOT NULL, date TEXT NOT NULL, subject TEXT NOT NULL, content TEXT NOT NULL, unique_id TEXT NOT NULL)""")
        conn.execute("INSERT INTO bulletins (board, sender_short_name, date, subject, content, unique_id) VALUES (?,?,?,?,?,?)",
                     (board, sender[:20] or "Den", time.strftime("%Y-%m-%d %H:%M"), subject, d["text"], uid))
        conn.execute("UPDATE digests SET bulletin_id = ? WHERE id = ?", (uid, digest_id))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"unique_id": uid, "board": board, "subject": subject, "digest_id": digest_id}
