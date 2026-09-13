"""Part A — the operator's analyst: ask questions about the mesh in the dashboard.

One question = one to three CLI calls: the model may answer directly from the
mesh brief, or reply with a single fenced ``sql`` block; we run it through the
read-only gate and hand the rows back for the next round (max ``MAX_ROUNDS``).
Everything the model can do to the database is: SELECT.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

from . import cli, sqlgate
from .context import mesh_brief, schema

MAX_ROUNDS = 3
MAX_HISTORY = 6

SYSTEM = """You are Bobcat, the analyst for Wildcat Mesh — a Meshtastic LoRa mesh in Northern Kentucky run from "the Den" (a Raspberry Pi/Mac hub). You talk to the operator (Seth) in the dashboard.

You are given a live brief of the mesh and the schema of its SQLite database. Answer from the brief when it's enough. When you need data, reply with ONLY one fenced block:
```sql
SELECT ...
```
— a single SQLite SELECT (no writes, no PRAGMA, no mail table), and nothing else in that reply. The system runs it and returns the rows, then asks you to continue. Use timestamps as unix seconds (strftime('%s','now') is fine) and LIMIT your rows. You may do this up to 3 times per question.

Style: concrete and numeric, name nodes by short name and id, say what you don't know. Plain text with short paragraphs or bullets; no headers. Under ~200 words unless asked for more. LoRa facts: SNR above ~5 dB is comfortable, below −7 dB packets drop; hops_away 0 means heard directly by the Den; channel utilization above 25% is crowded."""

_SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> Optional[str]:
    """The single SQL block if the reply is (essentially) only that; else None."""
    m = _SQL_BLOCK.search(text or "")
    if not m:
        return None
    rest = _SQL_BLOCK.sub("", text).strip()
    if len(rest) > 120:                 # the model explained AND queried — treat as an answer, not a tool call
        return None
    return m.group(1).strip()


def build_prompt(brief: str, sch: str, history: List[Dict[str, str]], question: str,
                 rounds: List[Dict[str, Any]]) -> str:
    parts = ["=== MESH BRIEF ===", brief, "", "=== DATABASE SCHEMA (SQLite, read-only) ===", sch or "(no database)", ""]
    if history:
        parts.append("=== EARLIER IN THIS CONVERSATION ===")
        for h in history[-MAX_HISTORY:]:
            parts.append(f"{'Operator' if h.get('role') == 'user' else 'Bobcat'}: {str(h.get('text') or '')[:800]}")
        parts.append("")
    parts += ["=== QUESTION ===", question.strip(), ""]
    for i, r in enumerate(rounds, 1):
        parts.append(f"=== QUERY {i} (you asked for this) ===")
        parts.append(r["sql"])
        if r.get("error"):
            parts.append(f"RESULT: rejected — {r['error']}. Fix it or answer without it.")
        else:
            res = r["result"]
            parts.append(f"RESULT ({len(res['rows'])} rows{', truncated' if res['truncated'] else ''}; columns: {', '.join(res['columns'])}):")
            for row in res["rows"][:60]:
                parts.append(json.dumps(row, default=str))
            if len(res["rows"]) > 60:
                parts.append(f"... {len(res['rows']) - 60} more rows")
        parts.append("")
    if rounds:
        parts.append("Now answer the question using these results (or ask for one more query if you truly need it).")
    return "\n".join(parts)


def ask(question: str, history: List[Dict[str, str]], state: Any, db_path: str, model: str, timeout: int,
        health: Optional[Dict[str, Any]] = None, runner: Callable[..., Iterator[Dict[str, Any]]] = cli.stream,
        run_sql: Callable[[str, str], Dict[str, Any]] = sqlgate.run_readonly) -> Iterator[Dict[str, Any]]:
    """Stream events for the UI: status · delta · sql · done · error."""
    t0 = time.time()
    brief = mesh_brief(state, db_path, health)
    sch = schema(db_path)
    rounds: List[Dict[str, Any]] = []
    total_cost = 0.0
    yield {"type": "status", "text": f"asking {model} via the local claude CLI"}
    for rnd in range(MAX_ROUNDS + 1):
        prompt = build_prompt(brief, sch, history, question, rounds)
        text = ""; err = None; cost = None
        for ev in runner(prompt, SYSTEM, model, timeout):
            if ev["type"] == "delta":
                text += ev["text"]; yield {"type": "delta", "text": ev["text"], "round": rnd}
            elif ev["type"] == "done":
                text = ev.get("text") or text; cost = ev.get("cost_usd")
            elif ev["type"] == "error":
                err = ev["text"]
        if cost:
            total_cost += float(cost)
        if err:
            yield {"type": "error", "text": err, "cost_usd": total_cost, "ms": int((time.time() - t0) * 1000)}
            return
        sql = extract_sql(text) if rnd < MAX_ROUNDS else None
        if sql is None:
            yield {"type": "done", "text": text, "rounds": len(rounds), "cost_usd": round(total_cost, 4),
                   "ms": int((time.time() - t0) * 1000), "model": model}
            return
        yield {"type": "retract", "round": rnd}          # the UI drops the streamed SQL text; it shows as a query card
        try:
            res = run_sql(db_path, sql)
            rounds.append({"sql": sql, "result": res})
            yield {"type": "sql", "sql": sql, "columns": res["columns"], "rows": res["rows"][:20], "count": len(res["rows"]),
                   "truncated": res["truncated"], "ms": res["ms"]}
        except sqlgate.RejectedSQL as e:
            rounds.append({"sql": sql, "error": str(e)})
            yield {"type": "sql", "sql": sql, "error": str(e)}
    yield {"type": "error", "text": "gave up after too many queries", "cost_usd": total_cost, "ms": int((time.time() - t0) * 1000)}
