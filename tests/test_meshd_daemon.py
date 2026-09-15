"""MeshDaemon with a fake radio, fake pubsub, MemoryBus, and a fake clock."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from wildcat.bus import MemoryBus
from wildcat.config import build
from wildcat.meshd.daemon import MeshDaemon
from wildcat.meshd.radio import RadioError

TEXT = {"from": 111, "to": 222, "fromId": "!0000006f", "toId": "!000000de", "channel": 0, "id": 7,
        "rxTime": 1000, "rxSnr": 6.0, "rxRssi": -80, "hopLimit": 3,
        "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"M", "text": "M"}}


class FakePub:
    def __init__(self): self.subs = {}
    def subscribe(self, fn, topic): self.subs.setdefault(topic, []).append(fn)
    def send(self, topic, **kw):
        for fn in self.subs.get(topic, []): fn(**kw)


class FakeRadio:
    def __init__(self):
        self.nodes = {"!000000de": {"num": 222, "user": {"id": "!000000de", "shortName": "BBS"}},
                      "!0000006f": {"num": 111, "user": {"id": "!0000006f", "shortName": "GO"}}}
        self.myInfo = SimpleNamespace(my_node_num=222)
        self.sent = []; self.closed = False; self._n = 0
    def sendText(self, **kw):
        self._n += 1; self.sent.append(kw); return SimpleNamespace(id=self._n)
    def close(self): self.closed = True


class Clock:
    def __init__(self, t=1000.0): self.t = t; self.sleeps = []
    def now(self): return self.t
    def sleep(self, s): self.sleeps.append(s); self.t += s


def make(fail_first=0, **cfg_over):
    cfg = build({"radio": {"type": "tcp", "host": "10.0.0.5"}, "mqtt": {"enabled": True},
                 "bbs": {"source": "bus"}, "telemetry": {"source": "bus"},
                 "meshd": {"reconnect_min_seconds": 1, "reconnect_max_seconds": 4, "tx_pacing_seconds": 2.0,
                           "nodes_publish_interval": 60, **cfg_over}})
    bus, pub, clock = MemoryBus(), FakePub(), Clock()
    radios, attempts = [], {"n": 0}
    def open_interface():
        attempts["n"] += 1
        if attempts["n"] <= fail_first:
            raise RadioError("tcp 10.0.0.5:4403: refused")
        r = FakeRadio(); radios.append(r); return r
    d = MeshDaemon(cfg, bus, open_interface, pub, clock=clock.now, sleep=clock.sleep)
    return d, bus, pub, clock, radios, attempts


def test_start_publishes_status_and_nodes():
    d, bus, pub, clock, radios, _ = make()
    d.start()
    st = bus.last("meshd/status")
    assert st["state"] == "connected" and st["myNodeNum"] == 222 and st["radio"] == "tcp:10.0.0.5"
    nodes = bus.last("nodes")
    assert nodes["count"] == 2 and nodes["nodes"]["!0000006f"]["user"]["shortName"] == "GO"
    assert set(pub.subs) == {"meshtastic.receive", "meshtastic.connection.established", "meshtastic.connection.lost"}


def test_rx_is_enveloped_onto_the_bus():
    d, bus, pub, clock, radios, _ = make(); d.start()
    pub.send("meshtastic.receive", packet=TEXT, interface=radios[0])
    env = bus.last("rx/text")
    assert env["text"] == "M" and env["from"] == "!0000006f" and env["from_num"] == 111 and env["received_at"] == clock.now()
    assert d.rx_count == 1


def test_tx_request_is_sent_with_pacing_and_results():
    d, bus, pub, clock, radios, _ = make(); d.start()
    bus.publish("tx", {"to": "!0000006f", "chunks": ["one", "two"], "id": "r1"})
    assert len(d.queue) == 2
    d.pump_once()
    assert [s["text"] for s in radios[0].sent] == ["one"]
    assert radios[0].sent[0] == {"text": "one", "destinationId": 111, "wantAck": True, "wantResponse": False, "channelIndex": 0}
    res = bus.last("tx/result")
    assert res == {"id": "r1", "index": 0, "count": 2, "ok": True, "packetId": 1, "to": 111}
    d.pump_once()                                   # same instant: pacing blocks
    assert len(radios[0].sent) == 1
    clock.t += 2.0
    d.pump_once()
    assert [s["text"] for s in radios[0].sent] == ["one", "two"]


def test_bad_tx_request_gets_an_error_result():
    d, bus, pub, clock, radios, _ = make(); d.start()
    bus.publish("tx", {"to": "nowhere", "text": "x", "id": "bad"})
    res = bus.last("tx/result")
    assert res["ok"] is False and res["id"] == "bad" and "bad 'to'" in res["error"]
    assert len(d.queue) == 0


def test_connect_retries_with_backoff():
    d, bus, pub, clock, radios, attempts = make(fail_first=3)
    d.start()
    assert attempts["n"] == 4 and clock.sleeps == [1, 2, 4]
    assert bus.last("meshd/status")["state"] == "connected"
    errs = [p for t, p in bus.published if t.endswith("meshd/status") and p["state"] == "disconnected"]
    assert len(errs) == 3 and "refused" in errs[0]["error"]


def test_connection_lost_reconnects_and_resets_backoff():
    d, bus, pub, clock, radios, attempts = make(fail_first=1)
    d.start()
    assert clock.sleeps == [1]
    pub.send("meshtastic.connection.lost", interface=radios[0])
    assert bus.last("meshd/status")["state"] == "disconnected"
    d.pump_once()
    assert radios[0].closed and len(radios) == 2 and d.interface is radios[1]
    assert bus.last("meshd/status")["state"] == "connected"
    assert d._backoff == 1


def test_nodes_snapshot_republished_on_interval():
    d, bus, pub, clock, radios, _ = make(); d.start()
    n0 = sum(1 for t, _ in bus.published if t.endswith("/nodes"))
    d.pump_once(); assert sum(1 for t, _ in bus.published if t.endswith("/nodes")) == n0
    clock.t += 61
    d.pump_once(); assert sum(1 for t, _ in bus.published if t.endswith("/nodes")) == n0 + 1


def test_send_failure_does_not_spin():
    d, bus, pub, clock, radios, _ = make(); d.start()
    def boom(**kw): raise OSError("socket closed")
    radios[0].sendText = boom
    bus.publish("tx", {"to": 111, "text": "x", "id": "r"})
    wait = d.pump_once()
    assert wait == 0.5 and bus.last("tx/result")["ok"] is False


def test_stop_closes_radio_and_publishes():
    d, bus, pub, clock, radios, _ = make(); d.start(); d.stop()
    assert radios[0].closed and bus.last("meshd/status")["state"] == "stopped"


def test_serial_watchdog_notices_an_unplugged_node(tmp_path):
    port = tmp_path / "cu.usbserial-0001"; port.write_text("")
    cfg = build({"radio": {"type": "serial", "port": str(port)}, "mqtt": {"enabled": True}, "bbs": {"source": "bus"}, "telemetry": {"source": "bus"},
                 "meshd": {"reconnect_min_seconds": 1, "reconnect_max_seconds": 4, "tx_pacing_seconds": 2.0, "nodes_publish_interval": 60}})
    bus, pub, clock = MemoryBus(), FakePub(), Clock()
    radios = []
    def open_interface():
        if not port.exists():
            raise RadioError(f"serial {port}: no such device")
        r = FakeRadio(); radios.append(r); return r
    d = MeshDaemon(cfg, bus, open_interface, pub, clock=clock.now, sleep=clock.sleep)
    d.start(); assert len(radios) == 1 and bus.last("meshd/status")["state"] == "connected"
    d.pump_once(); assert not d._lost.is_set()                         # plugged in: nothing happens
    port.unlink()                                                      # yank the cable
    # the reconnect loop blocks until the device is back: simulate the cable returning after one failed attempt
    real_sleep = clock.sleep
    def sleep_and_replug(secs):
        real_sleep(secs); port.write_text("")
    d.sleep = sleep_and_replug
    clock.t += 3; d.pump_once()
    states = [p["state"] for t, p in bus.published if t == "wildcat/meshd/status"]
    assert "disconnected" in states[-3:] and radios[0].closed
    assert len(radios) == 2 and d.interface is radios[1] and bus.last("meshd/status")["state"] == "connected"
    assert clock.sleeps[-1] == 1                                       # one backoff step, then it was back
