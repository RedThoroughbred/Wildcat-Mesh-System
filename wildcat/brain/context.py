"""The mesh brief: what the model gets told before any question.

Compact, plain text, built from the live State + the DB. Also the schema of
the tables the model may query (columns only — enough to write SELECTs).
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import Any, Dict, List, Optional

TABLES = ("message_logs", "telemetry_logs", "position_logs", "neighbor_info", "node_info",
          "bulletins", "rx_points", "channels")


def schema(db_path: str) -> str:
    """``table(col, col, …)`` lines for the queryable tables (privacy: mail is excluded)."""
    out: List[str] = []
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)) as c:
            have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in TABLES:
                if t in have:
                    cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
                    out.append(f"{t}({', '.join(cols)})")
    except sqlite3.Error:
        pass
    return "\n".join(out)


def _ago(ts: Optional[float], now: float) -> str:
    if not ts:
        return "never"
    d = max(0, int(now - ts))
    return f"{d // 60}m" if d < 3600 else f"{d // 3600}h{d % 3600 // 60:02d}m" if d < 86400 else f"{d // 86400}d"


def mesh_brief(state: Any, db_path: str, health: Optional[Dict[str, Any]] = None, now: Optional[float] = None,
               max_nodes: int = 40) -> str:
    """A few hundred words describing the mesh right now."""
    now = now or time.time()
    with state.lock:
        roster = {k: dict(v) for k, v in state.roster.items()}
        links = list(state.links.values())
        packets = list(state.packets)[-15:]
        my = state.my_id
        meshd = dict(state.meshd)
    me = roster.get(my or "", {})
    heard = sorted((n for n in roster.values() if (n.get("last_heard") or 0) >= now - 3600), key=lambda n: -(n.get("last_heard") or 0))
    lines = [
        f"Now: {time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(now))} (unix {int(now)}).",
        f"This Den's node: {my} '{me.get('short_name')}' ({me.get('hw') or '?'}, role {me.get('role') or '?'}), radio {meshd.get('state', '?')}; "
        f"channel util {me.get('channel_util')}%, air-time tx {me.get('air_util_tx')}%.",
        f"Roster: {len(roster)} nodes known, {len(heard)} heard in the last hour, {sum(1 for n in roster.values() if n.get('position'))} with GPS.",
    ]
    if heard:
        lines.append("Heard in the last hour (name id hops snr last):")
        for n in heard[:max_nodes]:
            hops = n.get("hops_away"); hops = "direct" if hops == 0 else (f"{hops}h" if hops is not None else "?")
            lines.append(f"  {n.get('short_name') or '?':>5} {n['id']} {hops:>6} {n.get('snr') if n.get('snr') is not None else '?':>6} {_ago(n.get('last_heard'), now):>6}"
                         + (f" batt {n['battery']}%" if n.get("battery") is not None and n['battery'] <= 100 else ""))
    if links:
        lines.append("Links (a↔b kind snr): " + "; ".join(
            f"{roster.get(l['a'], {}).get('short_name') or l['a'][-4:]}↔{roster.get(l['b'], {}).get('short_name') or l['b'][-4:]} {l['kind']} {l.get('snr')}" for l in links[:30]))
    if health:
        lines.append(f"Health score {health.get('score')} ({health.get('level')}); alerts: " +
                     ("; ".join(a["text"] for a in health.get("alerts", [])[:6]) or "none"))
    if packets:
        lines.append("Last packets (from kind summary):")
        for p in packets:
            lines.append(f"  {_ago(p.get('ts'), now):>5} {p.get('from_name') or p.get('from')} {p.get('kind')}: {str(p.get('summary') or '')[:80]}")
    return "\n".join(lines)
