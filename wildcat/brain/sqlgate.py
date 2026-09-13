"""Read-only SQL gate for model-proposed queries.

The model never touches the database directly. It writes SQL; this module
accepts only a single SELECT (or WITH … SELECT), rejects every keyword that
could write, attach, or reach the filesystem, and then runs it on a
connection opened read-only (``mode=ro`` + ``PRAGMA query_only``) with a step
budget, a wall-clock timeout, and a row cap. Belt, braces, and a second belt.
"""
from __future__ import annotations

import re
import sqlite3
import time
from typing import Any, Dict, List, Tuple

MAX_ROWS = 200
MAX_STEPS = 2_000_000          # sqlite VM steps before we abort (≈ well under a second)
TIMEOUT_S = 5.0

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|vacuum|attach|detach|pragma|reindex|"
    r"transaction|begin|commit|rollback|savepoint|release|load_extension|writefile|readfile|"
    r"fts3_tokenizer|zipfile|edit)\b", re.IGNORECASE)
_COMMENT = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)


class RejectedSQL(ValueError):
    pass


def check_select(sql: str) -> str:
    """Return the normalized statement or raise :class:`RejectedSQL` with the reason."""
    if not isinstance(sql, str):
        raise RejectedSQL("not a string")
    s = _COMMENT.sub(" ", sql).strip()
    if not s:
        raise RejectedSQL("empty query")
    s = s.rstrip(";").strip()
    if ";" in s:
        raise RejectedSQL("one statement only")
    head = s.split(None, 1)[0].lower()
    if head not in ("select", "with"):
        raise RejectedSQL("only SELECT (or WITH … SELECT) is allowed")
    if head == "with" and not re.search(r"\bselect\b", s, re.IGNORECASE):
        raise RejectedSQL("WITH must end in a SELECT")
    m = _FORBIDDEN.search(s)
    if m:
        raise RejectedSQL(f"'{m.group(1)}' is not allowed")
    if re.search(r"\bmail\b", s, re.IGNORECASE):
        raise RejectedSQL("the mail table is private and off-limits")
    if len(s) > 4000:
        raise RejectedSQL("query too long")
    return s


def run_readonly(db_path: str, sql: str, max_rows: int = MAX_ROWS) -> Dict[str, Any]:
    """Execute a gated SELECT read-only. Returns {columns, rows, truncated, ms}."""
    s = check_select(sql)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=TIMEOUT_S)
    try:
        conn.execute("PRAGMA query_only = 1")
        conn.execute(f"PRAGMA busy_timeout = {int(TIMEOUT_S * 1000)}")
        t0 = time.time()
        steps = {"n": 0}

        def budget() -> int:
            steps["n"] += 1
            return 1 if (steps["n"] > MAX_STEPS // 1000 or time.time() - t0 > TIMEOUT_S) else 0
        conn.set_progress_handler(budget, 1000)
        cur = conn.execute(s)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows: List[Tuple[Any, ...]] = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return {"columns": cols, "rows": [list(r) for r in rows], "truncated": truncated,
                "ms": int((time.time() - t0) * 1000)}
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e).lower():
            raise RejectedSQL("query exceeded its time/step budget") from None
        raise RejectedSQL(f"sqlite: {e}") from None
    finally:
        conn.close()
