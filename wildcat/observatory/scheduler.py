"""Scheduled jobs — automatic bulletins, broadcasts and the daily digest.

Small on purpose: a ``schedules`` table in the Den's SQLite, a daily
``HH:MM`` (local time) with an optional weekday filter, and three kinds of job:

* ``digest_bulletin`` — write the digest and post it to a board
  (``{"board": "General", "hours": 24}``)
* ``bulletin``        — post a fixed bulletin (``{"board", "subject", "content"}``)
* ``broadcast``       — send a fixed text broadcast (``{"text", "channel"}``) —
  airtime-conscious by construction: at most once per day per job.

The scheduler is pure (injected clock + actions) and ticked by the bridge's
loop; every run is recorded (``last_run``, ``last_result``) and the next run is
computed after each one, so a missed slot (the Den was off) runs once at the
next tick rather than piling up. Jobs never run more than once per calendar
slot: ``next_run`` is always strictly after ``last_run``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import closing
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("wildcat.observatory.scheduler")

KINDS = ("digest_bulletin", "bulletin", "broadcast")
Action = Callable[[Dict[str, Any]], Dict[str, Any]]


def next_run(after: float, hour: int, minute: int, days: Optional[List[int]] = None) -> float:
    """The first local ``hour:minute`` strictly after ``after`` whose weekday is allowed
    (``days`` = Monday-0 … Sunday-6, None/empty = every day)."""
    allowed = set(days) if days else set(range(7))
    t = time.localtime(after)
    for delta in range(0, 8):
        day = time.localtime(after + delta * 86400)
        cand = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, hour, minute, 0, 0, 0, -1))
        if cand > after and time.localtime(cand).tm_wday in allowed:
            return cand
    raise ValueError("no allowed day in the next week")   # pragma: no cover - impossible with a non-empty set


def validate(kind: str, hour: Any, minute: Any, days: Any, payload: Any) -> Dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(hour, int) or not 0 <= hour <= 23 or not isinstance(minute, int) or not 0 <= minute <= 59:
        raise ValueError("time must be HH:MM (24 h)")
    if days is not None:
        if not isinstance(days, list) or not days or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
            raise ValueError("days must be a list of weekdays 0 (Mon) – 6 (Sun), or omitted for every day")
    p = dict(payload or {})
    if kind == "digest_bulletin":
        p = {"board": (str(p.get("board") or "General").strip()[:40] or "General"), "hours": int(p.get("hours", 24) if p.get("hours") is not None else 24)}
        if not 1 <= p["hours"] <= 168:
            raise ValueError("hours must be 1–168")
    elif kind == "bulletin":
        p = {"board": (str(p.get("board") or "General").strip()[:40] or "General"), "subject": str(p.get("subject") or "").strip()[:80],
             "content": str(p.get("content") or "").strip()[:2000]}
        if not p["subject"] or not p["content"]:
            raise ValueError("a bulletin needs a subject and content")
    elif kind == "broadcast":
        p = {"text": " ".join(str(p.get("text") or "").split()), "channel": int(p.get("channel") or 0)}
        if not p["text"] or len(p["text"].encode("utf-8")) > 200:
            raise ValueError("broadcast text must be 1–200 bytes (one packet)")
        if not 0 <= p["channel"] <= 7:
            raise ValueError("channel must be 0–7")
    return p


class Scheduler:
    def __init__(self, db_path: str, actions: Dict[str, Action], clock: Callable[[], float] = time.time) -> None:
        self.db_path, self.actions, self.clock = db_path, actions, clock
        self._ensured = False

    # ---- storage --------------------------------------------------------------------------
    def ensure(self) -> None:
        if self._ensured:
            return
        with closing(sqlite3.connect(self.db_path, timeout=3)) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                hour INTEGER NOT NULL, minute INTEGER NOT NULL, days TEXT, payload TEXT NOT NULL,
                created_at INTEGER NOT NULL, last_run INTEGER, last_result TEXT, next_run REAL)""")
            c.commit()
        self._ensured = True

    @staticmethod
    def _row(r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        d["enabled"] = bool(d["enabled"])
        d["days"] = json.loads(d["days"]) if d.get("days") else None
        try:
            d["payload"] = json.loads(d["payload"])
        except (ValueError, TypeError):
            d["payload"] = {}
        try:
            d["last_result"] = json.loads(d["last_result"]) if d.get("last_result") else None
        except ValueError:
            pass
        return d

    def _write(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        self.ensure()
        conn = sqlite3.connect(self.db_path, timeout=3)
        try:
            conn.execute("PRAGMA busy_timeout = 3000")
            out = fn(conn)
            conn.commit()
            return out
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list(self) -> List[Dict[str, Any]]:
        self.ensure()
        with closing(sqlite3.connect(self.db_path, timeout=3)) as c:
            c.row_factory = sqlite3.Row
            return [self._row(r) for r in c.execute("SELECT * FROM schedules ORDER BY hour, minute, id")]

    def get(self, sid: int) -> Optional[Dict[str, Any]]:
        self.ensure()
        with closing(sqlite3.connect(self.db_path, timeout=3)) as c:
            c.row_factory = sqlite3.Row
            r = c.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
            return self._row(r) if r else None

    def add(self, kind: str, name: str, hour: int, minute: int, payload: Dict[str, Any], days: Optional[List[int]] = None,
            enabled: bool = True) -> Dict[str, Any]:
        p = validate(kind, hour, minute, days, payload)
        now = self.clock()
        name = (name or "").strip()[:60] or f"{kind} {hour:02d}:{minute:02d}"
        nxt = next_run(now, hour, minute, days)
        sid = self._write(lambda c: c.execute(
            "INSERT INTO schedules (kind, name, enabled, hour, minute, days, payload, created_at, next_run) VALUES (?,?,?,?,?,?,?,?,?)",
            (kind, name, 1 if enabled else 0, hour, minute, json.dumps(days) if days else None, json.dumps(p), int(now), nxt)).lastrowid)
        return self.get(int(sid))  # type: ignore[return-value]

    def update(self, sid: int, **fields: Any) -> Dict[str, Any]:
        cur = self.get(sid)
        if not cur:
            raise KeyError("no such schedule")
        kind = cur["kind"]
        hour = fields.get("hour", cur["hour"]); minute = fields.get("minute", cur["minute"])
        days = fields.get("days", cur["days"]); payload = fields.get("payload", cur["payload"])
        enabled = bool(fields.get("enabled", cur["enabled"]))
        name = (str(fields.get("name", cur["name"])) or cur["name"]).strip()[:60]
        p = validate(kind, hour, minute, days, payload)
        nxt = next_run(max(self.clock(), cur.get("last_run") or 0), hour, minute, days)
        self._write(lambda c: c.execute("UPDATE schedules SET name=?, enabled=?, hour=?, minute=?, days=?, payload=?, next_run=? WHERE id=?",
                                        (name, 1 if enabled else 0, hour, minute, json.dumps(days) if days else None, json.dumps(p), nxt, sid)))
        return self.get(sid)  # type: ignore[return-value]

    def remove(self, sid: int) -> bool:
        return bool(self._write(lambda c: c.execute("DELETE FROM schedules WHERE id = ?", (sid,)).rowcount))

    # ---- running ----------------------------------------------------------------------------
    def run(self, sid: int, manual: bool = False) -> Dict[str, Any]:
        job = self.get(sid)
        if not job:
            raise KeyError("no such schedule")
        now = self.clock()
        action = self.actions.get(job["kind"])
        try:
            if action is None:
                raise RuntimeError(f"no action for kind {job['kind']}")
            res = action(job["payload"])
            result = {"ok": True, "at": now, "manual": manual, **{k: v for k, v in (res or {}).items() if k != "ok"}}
        except Exception as e:
            log.error("schedule %s (%s) failed: %s", job["id"], job["name"], e)
            result = {"ok": False, "at": now, "manual": manual, "error": str(e)}
        nxt = next_run(now, job["hour"], job["minute"], job["days"])
        self._write(lambda c: c.execute("UPDATE schedules SET last_run=?, last_result=?, next_run=? WHERE id=?",
                                        (int(now), json.dumps(result, default=str), nxt, sid)))
        return result

    def due(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = self.clock() if now is None else now
        return [j for j in self.list() if j["enabled"] and j.get("next_run") is not None and j["next_run"] <= now]

    def tick(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Run every enabled job whose slot has passed. Returns the results."""
        now = self.clock() if now is None else now
        out = []
        try:
            jobs = self.due(now)
        except sqlite3.Error as e:
            log.warning("scheduler: %s", e)
            return out
        for j in jobs:
            out.append({"id": j["id"], "name": j["name"], **self.run(j["id"])})
        return out
