"""SOS broadcast: the controller (pure), the bridge wiring, and the routes."""
from __future__ import annotations

import pytest

from wildcat.observatory import sos as S
from wildcat.observatory.bridge import State
from wildcat.observatory.sos import SosController, compose, is_distress


class Clock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t


def make(position=None, fail=False):
    sent, published = [], []
    def sender(msg, ch):
        if fail:
            raise RuntimeError("the bus is off")
        sent.append((msg, ch)); return {"id": f"sos-{len(sent)}"}
    clock = Clock()
    c = SosController(sender, clock=clock, publish=published.append, name_fn=lambda: "6b18", position_fn=lambda: position)
    return c, sent, published, clock


def test_compose_marks_names_positions_counts_and_fits_one_packet():
    m = compose("6b18", "Tree down on Route 8, need help", 1, 1, {"lat": 38.879581, "lon": -84.616921})
    assert m == "🆘 SOS from 6b18: Tree down on Route 8, need help @ 38.87958,-84.61692"
    assert compose("6b18", "x", 2, 6, None) == "🆘 SOS from 6b18: x [2/6]"
    assert compose("6b18", "x", 3, 0, None) == "🆘 SOS from 6b18: x [3]"        # until stopped: plain counter
    long = "é" * 300
    m = compose("a-long-den-name", long, 12, 48, {"lat": 1, "lon": 2})
    assert len(m.encode("utf-8")) <= 200 and m.endswith(" @ 1.00000,2.00000 [12/48]")


def test_distress_detection_is_prefix_based_and_case_insensitive():
    assert is_distress("🆘 SOS from GO: hurt") and is_distress("SOS need help") and is_distress("  mayday mayday")
    assert is_distress("EMERGENCY at the lake") and is_distress("!!! SOS")
    assert not is_distress("no sos here") and not is_distress("sosad") and not is_distress(None) and not is_distress("M")


def test_once_sends_immediately_and_completes():
    c, sent, pub, clock = make(position={"lat": 38.88, "lon": -84.62})
    st = c.start("Flooding at the creek", channel=0, repeat=1)
    assert len(sent) == 1 and sent[0][0].startswith("🆘 SOS from 6b18: Flooding at the creek @ 38.88000,-84.62000") and sent[0][1] == 0
    assert st["active"] is False and st["ended"] == "completed" and st["sent"] == 1 and st["last_tx_id"] == "sos-1"
    assert c.tick(clock.t + 9999) is False and len(sent) == 1
    assert pub[-1]["ended"] == "completed"


def test_repeats_on_the_interval_then_completes():
    c, sent, pub, clock = make()
    st = c.start("Hiker missing near the ridge", interval=120, repeat=3, include_position=False)
    assert st["active"] and st["sent"] == 1 and st["next_at"] == clock.t + 120 and sent[0][0].endswith(" [1/3]")
    assert c.tick(clock.t + 119) is False
    assert c.tick(clock.t + 120) is True and sent[1][0].endswith(" [2/3]")
    assert c.status()["next_at"] == clock.t + 240
    assert c.tick(clock.t + 500) is True and sent[2][0].endswith(" [3/3]")
    st = c.status()
    assert st["active"] is False and st["ended"] == "completed" and st["sent"] == 3 and st["next_at"] is None
    assert c.tick(clock.t + 1000) is False and len(sent) == 3


def test_until_stopped_keeps_going_and_stop_ends_it():
    c, sent, pub, clock = make()
    c.start("Test drill", interval=60, repeat=0)
    for i in range(5):
        clock.t += 60; assert c.tick() is True
    assert len(sent) == 6 and sent[-1][0].endswith(" [6]") and c.status()["active"]
    st = c.stop(by="operator")
    assert st["active"] is False and st["ended"] == "stopped" and st["stopped_by"] == "operator" and st["next_at"] is None
    clock.t += 600
    assert c.tick() is False and len(sent) == 6
    assert c.stop()["ended"] == "stopped"                   # idempotent


def test_validation():
    c, sent, pub, clock = make()
    for kw in ({"text": "  "}, {"text": "x" * 121}, {"text": "x", "channel": 9}, {"text": "x", "repeat": 49},
               {"text": "x", "repeat": 3, "interval": 5}, {"text": "x", "repeat": 0, "interval": 99999}):
        with pytest.raises(ValueError):
            c.start(**kw)
    assert sent == []
    c.start("once ignores the interval", repeat=1, interval=1)      # interval irrelevant for a single send
    assert len(sent) == 1


def test_a_failed_send_ends_the_sos_visibly():
    c, sent, pub, clock = make(fail=True)
    with pytest.raises(RuntimeError):
        c.start("help")
    st = c.status()
    assert st["active"] is False and st["ended"] == "failed" and "bus is off" in st["error"] and pub[-1]["ended"] == "failed"


def test_state_flags_incoming_distress_and_sos_sends():
    st = State()
    st.apply_roster({"my_id": "!9e766b18", "roster": {"!9e766b18": {"id": "!9e766b18", "short_name": "6b18"}}})
    ev = st.apply_packet({"kind": "text", "from": "!716c668c", "broadcast": True, "rx": {"snr": 5, "rssi": -80, "hops": 0},
                          "text": "SOS truck in the ditch at mile 3", "received_at": 5.0}, now=5.0)
    assert ev["packet"]["sos"] is True
    ev = st.apply_packet({"kind": "text", "from": "!716c668c", "broadcast": True, "rx": {}, "text": "all good", "received_at": 6.0}, now=6.0)
    assert "sos" not in ev["packet"]
    rec = st.new_tx("sos-1", "^all", None, "🆘 SOS from 6b18: x", 0, 7.0, True, sos=True)
    assert rec["sos"] is True and st.packets[-1]["sos"] is True and st.packets[-1]["sent"] is True
    rec = st.new_tx("obs-2", "^all", None, "hello", 0, 8.0, True)
    assert rec["sos"] is False and "sos" not in st.packets[-1]
    assert st.snapshot(9.0)["sos"] == {"active": False, "sent": 0, "ended": None}
