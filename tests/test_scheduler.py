"""Scheduled posts: the next-run math, validation, storage, and the tick."""
from __future__ import annotations

import time

import pytest

from wildcat.observatory.scheduler import Scheduler, next_run, validate


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


def local(y, mo, d, h=0, mi=0):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def test_next_run_is_strictly_after_and_honours_weekdays():
    mon = local(2026, 9, 14, 6, 59)                      # 2026-09-14 is a Monday
    assert next_run(mon, 7, 0) == local(2026, 9, 14, 7, 0)
    assert next_run(local(2026, 9, 14, 7, 0), 7, 0) == local(2026, 9, 15, 7, 0)       # exactly on the slot → tomorrow
    assert next_run(mon, 7, 0, days=[5, 6]) == local(2026, 9, 19, 7, 0)                # Saturday
    assert next_run(local(2026, 9, 19, 8, 0), 7, 0, days=[5, 6]) == local(2026, 9, 20, 7, 0)
    assert next_run(local(2026, 9, 20, 8, 0), 7, 0, days=[0]) == local(2026, 9, 21, 7, 0)


def test_validate_shapes_each_kind():
    assert validate("digest_bulletin", 7, 0, None, {}) == {"board": "General", "hours": 24}
    assert validate("bulletin", 7, 0, [0, 2], {"board": " Net ", "subject": "Net tonight", "content": "8pm on ch0"}) == {"board": "Net", "subject": "Net tonight", "content": "8pm on ch0"}
    assert validate("broadcast", 20, 0, None, {"text": "  Net starts  in 10 ", "channel": "1"}) == {"text": "Net starts in 10", "channel": 1}
    for args in (("nope", 7, 0, None, {}), ("bulletin", 24, 0, None, {}), ("bulletin", 7, 60, None, {}), ("bulletin", 7, 0, [7], {}),
                 ("bulletin", 7, 0, None, {"subject": "x"}), ("broadcast", 7, 0, None, {"text": "x" * 201}), ("broadcast", 7, 0, None, {"text": "x", "channel": 9}),
                 ("digest_bulletin", 7, 0, None, {"hours": 0})):
        with pytest.raises(ValueError):
            validate(*args)


def make(tmp_path, t):
    ran = []
    def digest_action(p): ran.append(("digest", p)); return {"digest_id": 7, "board": p["board"]}
    def bulletin_action(p): ran.append(("bulletin", p)); return {"unique_id": "u"}
    def broadcast_action(p):
        ran.append(("broadcast", p))
        if p["text"] == "fail":
            raise RuntimeError("the bus is off")
        return {"id": "obs-1"}
    clock = Clock(t)
    s = Scheduler(str(tmp_path / "s.db"), {"digest_bulletin": digest_action, "bulletin": bulletin_action, "broadcast": broadcast_action}, clock=clock)
    return s, ran, clock


def test_add_list_update_remove(tmp_path):
    t = local(2026, 9, 14, 6, 0)
    s, ran, clock = make(tmp_path, t)
    j = s.add("digest_bulletin", "Morning digest", 7, 0, {"board": "Digest"})
    assert j["id"] == 1 and j["enabled"] and j["next_run"] == local(2026, 9, 14, 7, 0) and j["payload"] == {"board": "Digest", "hours": 24}
    b = s.add("broadcast", "", 20, 30, {"text": "Net at 8", "channel": 0}, days=[1])
    assert b["name"] == "broadcast 20:30" and b["days"] == [1] and b["next_run"] == local(2026, 9, 15, 20, 30)
    assert [x["id"] for x in s.list()] == [1, 2]
    u = s.update(1, hour=8, enabled=False, payload={"board": "News", "hours": 48})
    assert u["hour"] == 8 and u["enabled"] is False and u["payload"]["board"] == "News" and u["next_run"] == local(2026, 9, 14, 8, 0)
    with pytest.raises(ValueError):
        s.update(2, payload={"text": ""})
    with pytest.raises(KeyError):
        s.update(99, hour=1)
    assert s.remove(2) is True and s.remove(2) is False and len(s.list()) == 1


def test_tick_runs_due_jobs_once_and_reschedules(tmp_path):
    t = local(2026, 9, 14, 6, 0)
    s, ran, clock = make(tmp_path, t)
    s.add("digest_bulletin", "Morning digest", 7, 0, {"board": "Digest"})
    s.add("bulletin", "Net reminder", 7, 0, {"board": "Net", "subject": "Net tonight", "content": "8pm"}, days=[6])   # Sundays only
    s.add("broadcast", "off", 7, 0, {"text": "never"}, enabled=False)
    assert s.tick(clock.t) == []
    clock.t = local(2026, 9, 14, 7, 0, ) + 30
    res = s.tick()
    assert [r["name"] for r in res] == ["Morning digest"] and res[0]["ok"] and res[0]["digest_id"] == 7
    assert ran == [("digest", {"board": "Digest", "hours": 24})]
    j = s.get(1)
    assert j["last_run"] == int(clock.t) and j["last_result"]["ok"] and j["next_run"] == local(2026, 9, 15, 7, 0)
    assert s.tick() == []                                    # same slot never runs twice
    clock.t = local(2026, 9, 16, 12, 0)                      # the Den was off for a day: one catch-up run, not two
    assert [r["name"] for r in s.tick()] == ["Morning digest"] and s.get(1)["next_run"] == local(2026, 9, 17, 7, 0)
    clock.t = local(2026, 9, 20, 7, 1)                       # Sunday: both
    assert sorted(r["name"] for r in s.tick()) == ["Morning digest", "Net reminder"]


def test_failures_are_recorded_and_the_job_still_reschedules(tmp_path):
    t = local(2026, 9, 14, 6, 0)
    s, ran, clock = make(tmp_path, t)
    s.add("broadcast", "bad", 7, 0, {"text": "fail"})
    clock.t = local(2026, 9, 14, 7, 5)
    res = s.tick()
    assert res[0]["ok"] is False and "bus is off" in res[0]["error"]
    j = s.get(1)
    assert j["last_result"]["ok"] is False and j["next_run"] == local(2026, 9, 15, 7, 0)
    r = s.run(1, manual=True)
    assert r["ok"] is False and r["manual"] is True
    with pytest.raises(KeyError):
        s.run(9)
