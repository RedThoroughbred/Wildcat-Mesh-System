"""Bobcat Part B — the mesh responder against the in-memory bus with fake providers.
Nothing here touches a radio, the claude CLI, or the network."""
from __future__ import annotations

import sqlite3

import pytest

from wildcat.bus import MemoryBus
from wildcat.config import build
from wildcat.brain import responder as R
from wildcat.brain.responder import Limits, Responder, ProviderError, tidy

BASE, BASE_NUM = "!000000de", 222
GO, STAY = "!0000006f", "!00000070"

BASE_CFG = {"radio": {"type": "tcp", "host": "10.0.0.5"}, "mqtt": {"enabled": True},
            "bbs": {"source": "bus"}, "telemetry": {"source": "bus"}}


def cfg(**brain):
    d = {**BASE_CFG, "brain": {"enabled": True, **brain}}
    return build(d)


class Clock:
    def __init__(self, t=1_000_000.0): self.t = t
    def __call__(self): return self.t


def seed(bus, util=None):
    bus.publish("meshd/status", {"state": "connected", "myNodeNum": BASE_NUM}, retain=True)
    me = {"id": BASE, "short_name": "Den", "hops_away": 0}
    if util is not None:
        me["channel_util"] = util
    bus.publish("nodes", {"myNodeNum": BASE_NUM, "my_id": BASE, "count": 2, "nodes": {},
                          "roster": {BASE: me, GO: {"id": GO, "short_name": "GO", "hops_away": 0, "snr": 6.5}}}, retain=True)


def dm(text, frm=GO, to=BASE, pid=None, broadcast=False, channel=0):
    return {"v": 1, "proto": "meshtastic", "kind": "text", "from": frm, "to": None if broadcast else to, "broadcast": broadcast,
            "channel": channel, "rx": {"time": 1, "snr": 6.5, "rssi": -80, "hops": 0}, "text": text, "id": pid, "received_at": 1.0}


def fake(reply="The mesh is fine.", cost=0.01):
    calls = []
    def p(prompt, system):
        calls.append((prompt, system)); return reply, cost
    return p, calls


def make(bus=None, util=None, providers=None, clock=None, db=None, **brain):
    bus = bus or MemoryBus(); seed(bus, util)
    c = cfg(**brain)
    p, calls = fake() if providers is None else (None, None)
    r = Responder(c, bus, clock=clock or Clock(), providers=providers if providers is not None else [("fake", p)],
                  db_path=db or "", sync=True)
    r.wire()
    return r, bus, calls


def txs(bus):
    return [p for t, p in bus.published if t == "wildcat/tx"]


def exchanges(bus):
    return [p for t, p in bus.published if t == "wildcat/brain/exchange"]


# ---------------------------------------------------------------------------- the hard switch
def test_off_by_default_never_transmits_or_calls_a_provider():
    bus = MemoryBus(); seed(bus)
    p, calls = fake()
    r = Responder(build(BASE_CFG), bus, providers=[("fake", p)], db_path="", sync=True); r.wire()
    assert r.enabled is False
    bus.publish("rx/text", dm("?what's up", pid=1))
    assert txs(bus) == [] and calls == []
    x = exchanges(bus)
    assert len(x) == 1 and x[0]["status"] == "off" and x[0]["prompt"] == "what's up" and x[0]["chunks"] == 0
    st = bus.last("brain/status")
    assert st["enabled"] is False and st["running"] is True and st["node"] == BASE


def test_status_is_republished_when_the_node_id_arrives_late():
    bus = MemoryBus()                                   # no retained snapshots yet
    p, calls = fake()
    r = Responder(cfg(), bus, providers=[("fake", p)], db_path="", sync=True); r.wire()
    assert bus.last("brain/status")["node"] is None
    seed(bus)
    assert bus.last("brain/status")["node"] == BASE and r.my_id == BASE


def test_operator_control_message_toggles_at_runtime():
    r, bus, calls = make()
    bus.publish("brain/control", {"enabled": False, "by": "observatory"}, retain=True)
    assert r.enabled is False and bus.last("brain/status")["enabled"] is False
    bus.publish("rx/text", dm("?hi", pid=2))
    assert txs(bus) == []
    bus.publish("brain/control", {"enabled": True, "by": "observatory"}, retain=True)
    bus.publish("rx/text", dm("?hi", pid=3))
    assert len(txs(bus)) == 1


def test_retained_control_is_honoured_by_a_late_starting_responder():
    bus = MemoryBus(); seed(bus)
    bus.publish("brain/control", {"enabled": True, "by": "observatory"}, retain=True)
    p, calls = fake()
    r = Responder(build(BASE_CFG), bus, providers=[("fake", p)], db_path="", sync=True); r.wire()
    assert r.enabled is True


# ---------------------------------------------------------------------------- the happy path
def test_question_is_answered_in_numbered_chunks_over_the_tx_path(tmp_path):
    db = tmp_path / "m.db"; c = sqlite3.connect(db)
    c.execute("CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, sender_short_name TEXT, to_id INTEGER, channel_index INTEGER, message TEXT, snr REAL, rssi INTEGER, hop_limit INTEGER)")
    c.commit(); c.close()
    long = " ".join(f"word{i}" for i in range(80))          # ~560 chars → capped at 320 → 2 packets
    p, calls = fake(long)
    r, bus, _ = make(providers=[("fake", p)], db=str(db))
    bus.publish("rx/text", dm("?tell me everything", pid=10))
    assert len(calls) == 1
    prompt, system = calls[0]
    assert "tell me everything" in prompt and "MESH BRIEF" in prompt and "GO" in prompt
    assert "under 320 characters" in system and "Bobcat" in system
    t = txs(bus)
    assert len(t) == 1 and t[0]["to"] == GO and t[0]["wantAck"] is True and t[0]["id"].startswith("brain-")
    chunks = t[0]["chunks"]
    assert 1 < len(chunks) <= 3 and chunks[0].startswith("1/") and all(len(ch.encode()) <= 230 for ch in chunks)
    x = exchanges(bus)[-1]
    assert x["status"] == "sent" and x["provider"] == "fake" and x["chunks"] == len(chunks) and x["node"] == GO
    assert x["reply"].endswith("…") and len(x["reply"]) <= 320 and x["cost_usd"] == 0.01 and x["tx_id"] == t[0]["id"]
    rows = sqlite3.connect(db).execute("SELECT sender_id, to_id, message FROM message_logs ORDER BY id").fetchall()
    assert rows[0][0] == GO and rows[0][1] == BASE_NUM and rows[0][2] == "?tell me everything"
    assert rows[1][0] == BASE and rows[1][1] == 111 and rows[1][2] == x["reply"]
    st = bus.last("brain/status")
    assert st["answered"] == 1 and st["counts"] == {"hour": 1, "day": 1}


def test_short_answer_is_one_unnumbered_packet():
    r, bus, calls = make()
    bus.publish("rx/text", dm("?ok?", pid=11))
    assert txs(bus)[0]["chunks"] == ["The mesh is fine."]


# ---------------------------------------------------------------------------- what is ignored
def test_ignores_broadcasts_other_recipients_own_messages_and_unprefixed_text():
    r, bus, calls = make()
    bus.publish("rx/text", dm("?hi", broadcast=True, pid=20))
    bus.publish("rx/text", dm("?hi", to=STAY, pid=21))
    bus.publish("rx/text", dm("?hi", frm=BASE, pid=22))
    bus.publish("rx/text", dm("hello there", pid=23))
    bus.publish("rx/text", dm("M", pid=24))
    assert txs(bus) == [] and calls == [] and exchanges(bus) == []


def test_duplicate_packet_ids_are_answered_once():
    r, bus, calls = make()
    bus.publish("rx/text", dm("?hi", pid=30))
    bus.publish("rx/text", dm("?hi", pid=30))           # the mesh re-delivers
    assert len(txs(bus)) == 1 and len(calls) == 1


def test_custom_prefix():
    r, bus, calls = make(trigger_prefix="cat:")
    bus.publish("rx/text", dm("?hi", pid=40))
    bus.publish("rx/text", dm("cat: hi", pid=41))
    assert len(txs(bus)) == 1 and exchanges(bus)[-1]["prompt"] == "hi"


# ---------------------------------------------------------------------------- built-ins are free
def test_help_and_status_never_call_a_provider():
    r, bus, calls = make(util=7.0)
    bus.publish("rx/text", dm("?", pid=50))
    bus.publish("rx/text", dm("?help", pid=51))
    bus.publish("rx/text", dm("?status", pid=52))
    assert calls == [] and len(txs(bus)) == 3
    assert "?your question" in txs(bus)[0]["chunks"][0] and "3 packets" in txs(bus)[0]["chunks"][0]
    st = txs(bus)[2]["chunks"][0]
    assert "radio connected" in st and "2 nodes known" in st and "util 7%" in st
    assert all(x["provider"] == "builtin" and x["cost_usd"] == 0.0 for x in exchanges(bus))


# ---------------------------------------------------------------------------- the cascade
def test_cascade_falls_through_failures_to_the_next_provider():
    def broken(prompt, system): raise ProviderError("cli missing")
    def crashy(prompt, system): raise RuntimeError("boom")
    def empty(prompt, system): return "   ", None
    ok, calls = fake("Second opinion.")
    r, bus, _ = make(providers=[("a", broken), ("b", crashy), ("c", empty), ("d", ok)])
    bus.publish("rx/text", dm("?why", pid=60))
    x = exchanges(bus)[-1]
    assert x["provider"] == "d" and x["reply"] == "Second opinion." and len(calls) == 1


def test_all_providers_failing_sends_the_canned_line():
    def broken(prompt, system): raise ProviderError("nope")
    r, bus, _ = make(providers=[("a", broken)])
    bus.publish("rx/text", dm("?why", pid=61))
    x = exchanges(bus)[-1]
    assert x["provider"] == "none" and x["reply"] == R.CANNED_UNAVAILABLE and x["status"] == "sent"
    assert txs(bus)[0]["chunks"] == [R.CANNED_UNAVAILABLE]


def test_build_providers_follows_config_order_and_skips_ollama_without_a_url():
    names = [n for n, _ in R.build_providers(cfg(providers=["canned", "anthropic", "ollama"]))]
    assert names == ["canned", "claude-cli"]
    names = [n for n, _ in R.build_providers(cfg(providers=["ollama", "canned"], ollama_url="http://localhost:11434"))]
    assert names == ["ollama", "canned"]


# ---------------------------------------------------------------------------- rate limits
def test_per_node_hourly_limit_one_notice_then_silence():
    clock = Clock()
    r, bus, calls = make(clock=clock, per_node_per_hour=2, per_node_per_day=30)
    for i in range(2):
        bus.publish("rx/text", dm("?q", pid=70 + i))
    assert len(txs(bus)) == 2
    bus.publish("rx/text", dm("?q", pid=72))
    assert len(txs(bus)) == 3 and "limit" in txs(bus)[2]["chunks"][0] and len(txs(bus)[2]["chunks"]) == 1
    x = exchanges(bus)[-1]
    assert x["status"] == "limited" and x["rate_limited"] and "2/h" in x["reason"] and x["chunks"] == 1
    bus.publish("rx/text", dm("?q", pid=73))
    assert len(txs(bus)) == 3 and exchanges(bus)[-1]["chunks"] == 0          # silent this time
    assert len(calls) == 2
    clock.t += 3601                                                          # the window slides
    bus.publish("rx/text", dm("?q", pid=74))
    assert exchanges(bus)[-1]["status"] == "sent"


def test_per_node_daily_and_global_limits():
    clock = Clock()
    r, bus, calls = make(clock=clock, per_node_per_hour=100, per_node_per_day=3, global_per_hour=5)
    for i in range(3):
        bus.publish("rx/text", dm("?q", pid=80 + i))
    bus.publish("rx/text", dm("?q", pid=83))
    assert exchanges(bus)[-1]["status"] == "limited" and "3/day" in exchanges(bus)[-1]["reason"]
    for i in range(2):
        bus.publish("rx/text", dm("?q", frm=STAY, pid=90 + i))              # another node still fine
    assert exchanges(bus)[-1]["status"] == "sent"
    bus.publish("rx/text", dm("?q", frm="!00000071", pid=95))
    assert exchanges(bus)[-1]["status"] == "limited" and "global" in exchanges(bus)[-1]["reason"]


def test_admin_nodes_skip_per_node_limits_but_not_the_global_one():
    r, bus, calls = make(per_node_per_hour=1, global_per_hour=3, admin_nodes=[GO])
    for i in range(3):
        bus.publish("rx/text", dm("?q", pid=100 + i))
    assert all(x["status"] == "sent" for x in exchanges(bus))
    bus.publish("rx/text", dm("?q", pid=103))
    assert exchanges(bus)[-1]["status"] == "limited" and "global" in exchanges(bus)[-1]["reason"]


def test_limits_window_math():
    L = Limits(2, 3, 10)
    assert L.check("a", 0) is None
    L.record("a", 0); L.record("a", 10)
    assert L.check("a", 20) == "node limit 2/h"
    assert L.check("a", 3700) is None                    # hour passed, day has 2
    L.record("a", 3700)
    assert L.check("a", 3800) == "node limit 3/day"
    assert L.counts(3800) == {"hour": 1, "day": 3}
    assert L.check("a", 90000) is None                   # a day later everything has aged out


# ---------------------------------------------------------------------------- airtime brake
def test_airtime_brake_from_the_nodes_snapshot_and_live_telemetry():
    r, bus, calls = make(util=35.0, max_channel_util_pct=20)
    bus.publish("rx/text", dm("?q", pid=110))
    x = exchanges(bus)[-1]
    assert x["status"] == "braked" and x["rate_limited"] and "35%" in x["reason"] and txs(bus) == [] and calls == []
    assert bus.last("brain/status")["brake"] is True
    # our own telemetry says the air cleared
    bus.publish("rx/telemetry", {"v": 1, "proto": "meshtastic", "kind": "telemetry", "from": BASE, "to": None, "broadcast": True,
                                 "channel": 0, "rx": {}, "telemetry": {"channel_util": 5.0}, "received_at": 2.0, "id": 111})
    bus.publish("rx/text", dm("?q", pid=112))
    assert exchanges(bus)[-1]["status"] == "sent" and bus.last("brain/status")["brake"] is False


def test_admin_nodes_bypass_the_brake():
    r, bus, calls = make(util=90.0, admin_nodes=[GO])
    bus.publish("rx/text", dm("?q", pid=120))
    assert exchanges(bus)[-1]["status"] == "sent"
    bus.publish("rx/text", dm("?q", frm=STAY, pid=121))
    assert exchanges(bus)[-1]["status"] == "braked"


# ---------------------------------------------------------------------------- text shaping
def test_tidy_strips_markdown_and_caps_at_a_word_boundary():
    assert tidy("**Bold** and `code`\n\n# Heading\n- item one\n- item two", 500) == "Bold and code Heading item one item two"
    t = tidy("alpha beta gamma delta epsilon zeta", 20)
    assert t == "alpha beta gamma…" and len(t) <= 20
    assert tidy("", 100) == "" and tidy("x" * 50, 10).endswith("…") and len(tidy("x" * 50, 10)) <= 10


def test_max_chunks_truncates_with_a_marker():
    p, calls = fake(" ".join(f"w{i}" for i in range(300)))
    r, bus, _ = make(providers=[("fake", p)], max_reply_chars=1000, max_chunks=2)
    bus.publish("rx/text", dm("?long", pid=130))
    chunks = txs(bus)[0]["chunks"]
    assert len(chunks) == 2 and chunks[1].endswith("…(ask for more)") and chunks[1].startswith("2/2 ")


# ---------------------------------------------------------------------------- async worker
def test_worker_thread_processes_the_queue():
    import time as _t
    bus = MemoryBus(); seed(bus)
    p, calls = fake("Async answer.")
    r = Responder(cfg(), bus, providers=[("fake", p)], db_path="", sync=False)
    r.start()
    try:
        bus.publish("rx/text", dm("?q", pid=140))
        for _ in range(100):
            if txs(bus):
                break
            _t.sleep(0.02)
        assert txs(bus) and txs(bus)[0]["chunks"] == ["Async answer."]
    finally:
        r.stop()
    assert bus.last("brain/status")["running"] is False
