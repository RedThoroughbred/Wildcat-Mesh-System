"""The daily digest: deterministic facts, a scripted CLI summary, the fallback, storage, and posting as a bulletin."""
from __future__ import annotations

import sqlite3
import time

from wildcat.brain import digest as D
from wildcat.observatory.bridge import State

BASE, GO, STAY, NEW = "!9e766b18", "!716c668c", "!8880aaaa", "!00000011"


def db(tmp_path, now):
    p = tmp_path / "d.db"; c = sqlite3.connect(p)
    c.execute("CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, sender_short_name TEXT, to_id INTEGER, channel_index INTEGER, message TEXT, snr REAL, rssi INTEGER, hop_limit INTEGER)")
    c.execute("CREATE TABLE node_info (node_id TEXT, long_name TEXT, hw_model TEXT, role TEXT)")
    c.execute("CREATE TABLE telemetry_logs (id INTEGER PRIMARY KEY, node_id TEXT, timestamp INTEGER, battery_level INTEGER, voltage REAL)")
    c.execute("CREATE TABLE bulletins (id INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT NOT NULL, sender_short_name TEXT NOT NULL, date TEXT NOT NULL, subject TEXT NOT NULL, content TEXT NOT NULL, unique_id TEXT NOT NULL)")
    rows = [(now - 3600 * 3, GO, "GO", 4294967295, 0, "morning all", 6.0, -80), (now - 3600 * 2, GO, "GO", 4294967295, 0, "SOS truck in ditch mile 3", 5.5, -82),
            (now - 3600, STAY, "STAY", 4294967295, 1, "copy", 2.0, -95), (now - 1800, NEW, "NEW1", 4294967295, 0, "hello mesh", 1.0, -100),
            (now - 5 * 86400, STAY, "STAY", 4294967295, 0, "old", 3.0, -90), (now - 9 * 86400, GO, "GO", 4294967295, 0, "older", 3.0, -90)]
    c.executemany("INSERT INTO message_logs (timestamp,sender_id,sender_short_name,to_id,channel_index,message,snr,rssi) VALUES (?,?,?,?,?,?,?,?)", rows)
    c.executemany("INSERT INTO telemetry_logs (node_id,timestamp,battery_level,voltage) VALUES (?,?,?,?)", [(STAY, now - 600, 12, 3.4), (GO, now - 600, 90, 4.1)])
    c.execute("INSERT INTO bulletins (board,sender_short_name,date,subject,content,unique_id) VALUES (?,?,?,?,?,?)",
              ("General", "GO", time.strftime("%Y-%m-%d %H:%M", time.localtime(now - 7200)), "Meetup Saturday", "at the park", "u1"))
    c.execute("INSERT INTO bulletins (board,sender_short_name,date,subject,content,unique_id) VALUES (?,?,?,?,?,?)",
              ("General", "GO", "2020-01-01 00:00", "ancient", "x", "u0"))
    c.commit(); c.close(); return str(p)


def state(now):
    s = State()
    s.apply_roster({"my_id": BASE, "roster": {BASE: {"id": BASE, "short_name": "6b18", "last_heard": now},
                                              GO: {"id": GO, "short_name": "GO", "last_heard": now - 1800},
                                              STAY: {"id": STAY, "short_name": "STAY", "last_heard": now - 8 * 3600},
                                              NEW: {"id": NEW, "short_name": "NEW1", "last_heard": now - 1800}}})
    s.apply_brain({"node": GO, "prompt": "hi", "reply": "yo", "status": "sent", "ts": now - 100}, now)
    s.apply_brain({"node": GO, "prompt": "again", "reply": "", "status": "limited", "ts": now - 50}, now)
    return s


def test_gather_is_deterministic_and_complete(tmp_path):
    now = time.time()
    f = D.gather(db(tmp_path, now), state(now), {"score": 72, "level": "warn", "alerts": [{"text": "STAY quiet 8h"}]}, hours=24, now=now)
    assert f["den"]["name"] == "6b18" and f["nodes_known"] == 4 and f["heard_in_window"] == 4
    assert f["messages"] == 4 and f["senders"] == 3
    assert [t["name"] for t in f["top_senders"]][0] == "GO" and f["top_senders"][0]["n"] == 2
    assert {c["channel"]: c["n"] for c in f["channels"]} == {0: 3, 1: 1}
    assert [n["name"] for n in f["new_nodes"]] == ["NEW1"]
    assert f["low_battery"] == [{"id": STAY, "name": "STAY", "battery": 12}]
    assert f["distress"][0]["from"] == "GO" and f["distress"][0]["text"].startswith("SOS truck")
    assert [b["subject"] for b in f["bulletins"]] == ["Meetup Saturday"]
    assert f["bobcat"] == {"questions": 2, "answered": 1}
    assert [q["name"] for q in f["went_quiet"]] == ["STAY"]
    assert f["health"]["score"] == 72 and f["health"]["alerts"] == ["STAY quiet 8h"]
    text = D.facts_text(f)
    for needle in ("4 known", "Messages: 4 from 3 senders", "Most active: GO (2)", "ch0 3, ch1 1", "New voices: NEW1", "Went quiet", "STAY 12%",
                   "DISTRESS messages", "Meetup Saturday", "Bobcat: 2 question(s) asked, 1 answered", "score 72 (warn)"):
        assert needle in text, needle


def test_gather_survives_a_missing_database(tmp_path):
    now = time.time()
    f = D.gather(str(tmp_path / "nope.db"), state(now), None, now=now)
    assert f["nodes_known"] == 4 and f["messages"] == 0 and f["senders"] == 0 and f["top_senders"] == [] and f["bulletins"] == []
    assert "Messages: 0 from 0 senders" in D.facts_text(f)


def scripted(text, error=None):
    calls = []
    def runner(prompt, system, model, timeout, max_budget_usd=None):
        calls.append({"prompt": prompt, "system": system, "model": model, "budget": max_budget_usd})
        if error:
            yield {"type": "error", "text": error}; return
        yield {"type": "delta", "text": text}
        yield {"type": "done", "text": text, "cost_usd": 0.02, "ms": 5, "model": model, "is_error": False}
    return runner, calls


def test_summarize_uses_the_facts_and_falls_back_to_them(tmp_path):
    now = time.time()
    f = D.gather(db(tmp_path, now), state(now), None, now=now)
    runner, calls = scripted("Quiet day.\n\nGO led with 2 messages; NEW1 joined.")
    rec = D.summarize(f, "test-model", 30, runner=runner)
    assert rec["text"] == "Quiet day. GO led with 2 messages; NEW1 joined." and rec["model"] == "test-model" and rec["cost_usd"] == 0.02
    assert not rec["fallback"] and rec["facts"] is f and "Most active: GO (2)" in calls[0]["prompt"] and "last 24 hours" in calls[0]["system"]
    assert calls[0]["budget"] == 0.15
    runner, calls = scripted("", error="claude CLI not found")
    rec = D.summarize(f, "test-model", 30, runner=runner)
    assert rec["fallback"] and rec["model"] is None and rec["text"] == D.facts_text(f) and rec["error"] == "claude CLI not found" and rec["cost_usd"] == 0.0


def test_store_latest_get_and_post_as_bulletin(tmp_path):
    now = time.time()
    d = db(tmp_path, now)
    f = D.gather(d, state(now), None, now=now)
    runner, _ = scripted("A fine day on the mesh.")
    rec = D.summarize(f, "m", 30, runner=runner)
    i1 = D.save(d, rec); i2 = D.save(d, {**rec, "text": "Second."}, source="schedule")
    got = D.latest(d)
    assert [g["id"] for g in got] == [i2, i1] and got[0]["source"] == "schedule" and got[1]["facts"]["messages"] == 4 and got[1]["fallback"] is False
    assert D.get(d, i1)["text"] == "A fine day on the mesh." and D.get(d, 999) is None
    out = D.post_bulletin(d, i1, "Announcements", "6b18")
    assert out["board"] == "Announcements" and out["subject"].startswith("Mesh digest ")
    c = sqlite3.connect(d)
    row = c.execute("SELECT board, sender_short_name, subject, content, unique_id FROM bulletins ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ("Announcements", "6b18", out["subject"], "A fine day on the mesh.", out["unique_id"])
    assert D.get(d, i1)["bulletin_id"] == out["unique_id"]
    try:
        D.post_bulletin(d, 999, "General", "6b18"); assert False
    except KeyError:
        pass
