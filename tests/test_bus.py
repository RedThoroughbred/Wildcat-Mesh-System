from __future__ import annotations

import pytest

from wildcat.bus import MemoryBus, mqtt_match


@pytest.mark.parametrize("filt,topic,ok", [
    ("wildcat/rx/text", "wildcat/rx/text", True),
    ("wildcat/rx/+", "wildcat/rx/text", True),
    ("wildcat/rx/+", "wildcat/rx/text/extra", False),
    ("wildcat/#", "wildcat/rx/text/extra", True),
    ("wildcat/#", "wildcat", True),          # MQTT spec: "#" also matches the parent level
    ("#", "anything/at/all", True),
    ("wildcat/rx/text", "wildcat/rx/position", False),
    ("wildcat/+/status", "wildcat/meshd/status", True),
])
def test_mqtt_match(filt, topic, ok):
    assert mqtt_match(filt, topic) is ok


def test_memory_bus_dispatch_and_prefix():
    bus = MemoryBus("wildcat")
    got = []
    bus.subscribe("rx/+", lambda t, p: got.append((t, p)))
    bus.subscribe("tx", lambda t, p: got.append(("TX", p)))
    bus.publish("rx/text", {"text": "hi"})
    bus.publish("nodes", {"n": 1})
    assert got == [("wildcat/rx/text", {"text": "hi"})]
    assert bus.last("rx/text") == {"text": "hi"} and bus.last("wildcat/nodes") == {"n": 1}


def test_memory_bus_retained_replays_to_late_subscriber():
    bus = MemoryBus()
    bus.publish("nodes", {"snapshot": 1}, retain=True)
    bus.publish("nodes", {"snapshot": 2}, retain=True)
    got = []
    bus.subscribe("nodes", lambda t, p: got.append(p))
    assert got == [{"snapshot": 2}]


def test_memory_bus_requires_json_and_isolates_handler_errors():
    bus = MemoryBus()
    bus.subscribe("x", lambda t, p: 1 / 0)
    seen = []
    bus.subscribe("x", lambda t, p: seen.append(p))
    bus.publish("x", {"a": [1, 2]})             # first handler raises, second still runs
    assert seen == [{"a": [1, 2]}]
    with pytest.raises(TypeError):
        bus.publish("x", {"b": b"bytes"})
