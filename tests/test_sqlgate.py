from __future__ import annotations

import sqlite3

import pytest

from wildcat.brain import sqlgate as G


@pytest.mark.parametrize("sql", [
    "SELECT 1", "select count(*) from message_logs", "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT * FROM nodes -- comment\n", "SELECT 1;", "  select\n 2 ",
])
def test_accepts_selects(sql):
    assert G.check_select(sql).lower().startswith(("select", "with"))


@pytest.mark.parametrize("sql,why", [
    ("DELETE FROM message_logs", "only SELECT"), ("SELECT 1; DROP TABLE x", "one statement"),
    ("PRAGMA journal_mode", "only SELECT"), ("SELECT load_extension('x')", "not allowed"),
    ("select * from mail", "private"), ("", "empty"), ("WITH x AS (DELETE FROM t) SELECT 1", "not allowed"),
    ("SELECT writefile('/etc/passwd', 'x')", "not allowed"), ("ATTACH 'x' AS y", "only SELECT"),
    ("select 1 /* pragma */ union select 2", None),   # comments stripped; this one passes
])
def test_rejects_everything_else(sql, why):
    if why is None:
        G.check_select(sql); return
    with pytest.raises(G.RejectedSQL) as ei:
        G.check_select(sql)
    assert why in str(ei.value)


def test_run_readonly_truly_readonly(tmp_path):
    db = tmp_path / "b.db"; c = sqlite3.connect(db)
    c.execute("CREATE TABLE t (x INTEGER)"); c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(300)]); c.commit(); c.close()
    r = G.run_readonly(str(db), "SELECT x FROM t ORDER BY x")
    assert r["columns"] == ["x"] and len(r["rows"]) == 200 and r["truncated"] is True
    with pytest.raises(G.RejectedSQL):
        G.run_readonly(str(db), "INSERT INTO t VALUES (1)")
    with pytest.raises(G.RejectedSQL) as ei:
        G.run_readonly(str(db), "SELECT * FROM nope")
    assert "no such table" in str(ei.value)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 300


def test_run_readonly_step_budget(tmp_path):
    db = tmp_path / "b.db"; sqlite3.connect(db).execute("CREATE TABLE t (x)").connection.commit()
    with pytest.raises(G.RejectedSQL) as ei:   # a recursive CTE that never ends
        G.run_readonly(str(db), "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c) SELECT count(*) FROM c")
    assert "budget" in str(ei.value)
