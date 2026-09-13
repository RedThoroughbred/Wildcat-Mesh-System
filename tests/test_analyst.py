"""The analyst loop with a fake CLI: brief → optional gated SQL rounds → answer."""
from __future__ import annotations

import sqlite3

from wildcat.brain import analyst, cli
from wildcat.brain.context import mesh_brief, schema
from wildcat.observatory.bridge import State

BASE, GO = "!9e766b18", "!716c668c"


def fake_state():
    s = State()
    s.apply_roster({"my_id": BASE, "roster": {BASE: {"id": BASE, "short_name": "6b18", "channel_util": 4.0, "position": {"lat": 1, "lon": 2}},
                                              GO: {"id": GO, "short_name": "GO", "hops_away": 0, "snr": 6.5}}})
    s.apply_packet({"kind": "text", "from": GO, "rx": {"snr": 6.5, "rssi": -80, "hops": 0}, "text": "M", "received_at": 1e9}, now=1e9)
    return s


def db(tmp_path):
    p = tmp_path / "b.db"; c = sqlite3.connect(p)
    c.execute("CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, sender_short_name TEXT, to_id INTEGER, channel_index INTEGER, message TEXT, snr REAL, rssi INTEGER, hop_limit INTEGER)")
    c.execute("CREATE TABLE mail (id INTEGER PRIMARY KEY, sender TEXT, content TEXT)")
    c.executemany("INSERT INTO message_logs (timestamp,sender_id,sender_short_name,to_id,channel_index,message,snr,rssi) VALUES (?,?,?,?,?,?,?,?)",
                  [(1e9 + i, GO, "GO", 4294967295, 0, f"m{i}", 5.0, -80) for i in range(7)])
    c.commit(); c.close(); return str(p)


def test_brief_and_schema(tmp_path):
    d = db(tmp_path); s = fake_state()
    b = mesh_brief(s, d, {"score": 90, "level": "ok", "alerts": []}, now=1e9 + 60)
    assert "6b18" in b and "GO" in b and "direct" in b and "Health score 90" in b
    sch = schema(d)
    assert sch.startswith("message_logs(") and "mail" not in sch


def test_extract_sql():
    assert analyst.extract_sql("```sql\nSELECT 1\n```") == "SELECT 1"
    assert analyst.extract_sql("Sure!\n```sql\nSELECT 1\n```\nrunning it") == "SELECT 1"
    assert analyst.extract_sql("The answer is 42, because " + "x" * 200 + "\n```sql\nSELECT 1\n```") is None
    assert analyst.extract_sql("no query here") is None


def scripted(replies):
    calls = []
    def runner(prompt, system, model, timeout):
        calls.append(prompt)
        text = replies[len(calls) - 1]
        for chunk in (text[:5], text[5:]):
            if chunk: yield {"type": "delta", "text": chunk}
        yield {"type": "done", "text": text, "cost_usd": 0.01, "ms": 10, "model": model, "is_error": False}
    return runner, calls


def test_two_round_question_runs_gated_sql(tmp_path):
    d = db(tmp_path)
    runner, calls = scripted(["```sql\nSELECT COUNT(*) AS n FROM message_logs WHERE sender_id = '!716c668c'\n```",
                              "GO (!716c668c) sent 7 messages in the window."])
    evs = list(analyst.ask("how many messages from GO?", [], fake_state(), d, "test-model", 30, runner=runner))
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "status" and "sql" in kinds and kinds[-1] == "done"
    sql = next(e for e in evs if e["type"] == "sql")
    assert sql["rows"] == [[7]] and sql["columns"] == ["n"]
    done = evs[-1]; assert done["rounds"] == 1 and done["cost_usd"] == 0.02 and "7 messages" in done["text"]
    assert "=== QUERY 1" in calls[1] and "[7]" in calls[1] and "=== MESH BRIEF" in calls[0]


def test_rejected_sql_is_fed_back_not_run(tmp_path):
    d = db(tmp_path)
    runner, calls = scripted(["```sql\nDELETE FROM message_logs\n```", "I can't modify data; here's what I know instead."])
    evs = list(analyst.ask("delete everything", [], fake_state(), d, "m", 30, runner=runner))
    sql = next(e for e in evs if e["type"] == "sql")
    assert "only SELECT" in sql["error"] and "rejected" in calls[1]
    assert sqlite3.connect(d).execute("SELECT COUNT(*) FROM message_logs").fetchone()[0] == 7
    assert evs[-1]["type"] == "done"


def test_round_cap_and_cli_error(tmp_path):
    d = db(tmp_path)
    runner, calls = scripted(["```sql\nSELECT 1\n```"] * 5)
    evs = list(analyst.ask("loop", [], fake_state(), d, "m", 30, runner=runner))
    assert evs[-1]["type"] == "done" and len(calls) == analyst.MAX_ROUNDS + 1   # the last round must answer
    def broken(prompt, system, model, timeout):
        yield {"type": "error", "text": "claude CLI not found"}
    evs = list(analyst.ask("hi", [], fake_state(), d, "m", 30, runner=broken))
    assert evs[-1]["type"] == "error" and "not found" in evs[-1]["text"]


def test_cli_resolver(monkeypatch, tmp_path):
    fake = tmp_path / "claude"; fake.write_text("#!/bin/sh\necho 9.9.9\n"); fake.chmod(0o755)
    monkeypatch.setenv("CLAUDE_BIN", str(fake))
    assert cli.resolve() == str(fake) and cli.status()["available"] is True and cli.status()["version"] == "9.9.9"
    monkeypatch.setenv("CLAUDE_BIN", str(tmp_path / "missing"))
    assert cli.resolve() is None and cli.status()["hint"]
    assert "/opt/homebrew/bin" in cli.env()["PATH"].split(":")
