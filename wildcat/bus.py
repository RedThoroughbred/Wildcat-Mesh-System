"""wildcat.bus — the local message bus every Den service talks over.

Two implementations of one tiny interface:

* :class:`MqttBus` — paho-mqtt against the Pi's Mosquitto. JSON payloads,
  topic prefix applied for you (``publish("rx/text", …)`` → ``wildcat/rx/text``),
  subscriptions re-applied after every reconnect, automatic reconnect with
  backoff (paho's own loop thread), optional last-will.
* :class:`MemoryBus` — synchronous in-process twin with real MQTT wildcard
  matching. Used by the tests and by ``wildcat meshd --dry-run``.

Topic tree (see docs/MESHD_MIGRATION.md):

    wildcat/rx/text | position | telemetry | neighborinfo | nodeinfo | routing | other
    wildcat/nodes            (retained snapshot of interface.nodes, every N s)
    wildcat/meshd/status     (retained: {"state": "connected"|"disconnected", "myNodeNum", …})
    wildcat/tx               (requests: {"to", "text", "channel"?, "wantAck"?, "priority"?, "id"?})
    wildcat/tx/result        ({"id", "ok", "packetIds": [...], "error"?})
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import MqttConfig

log = logging.getLogger("wildcat.bus")

Handler = Callable[[str, Dict[str, Any]], None]


def mqtt_match(filt: str, topic: str) -> bool:
    """MQTT topic-filter matching: ``+`` one level, ``#`` the rest (must be last)."""
    f, t = filt.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return i == len(f) - 1
        if i >= len(t):
            return False
        if part != "+" and part != t[i]:
            return False
    return len(f) == len(t)


class Bus:
    """The interface. Topics are RELATIVE to the configured prefix."""

    def publish(self, topic: str, payload: Dict[str, Any], retain: bool = False) -> None:
        raise NotImplementedError

    def subscribe(self, topic_filter: str, handler: Handler) -> None:
        raise NotImplementedError

    def start(self) -> None:  # pragma: no cover - trivial
        pass

    def stop(self) -> None:  # pragma: no cover - trivial
        pass


class MemoryBus(Bus):
    """In-process bus: publish dispatches synchronously to matching handlers.

    Retained messages are remembered and replayed to late subscribers, like a
    real broker, so the ``wildcat/nodes`` snapshot pattern works in tests too.
    """

    def __init__(self, prefix: str = "wildcat") -> None:
        self.prefix = prefix
        self._subs: List[Tuple[str, Handler]] = []
        self._retained: Dict[str, Dict[str, Any]] = {}
        self.published: List[Tuple[str, Dict[str, Any]]] = []   # (full topic, payload) log for tests
        self._lock = threading.RLock()

    def full(self, topic: str) -> str:
        return f"{self.prefix}/{topic}" if self.prefix else topic

    def publish(self, topic: str, payload: Dict[str, Any], retain: bool = False) -> None:
        ft = self.full(topic)
        payload = json.loads(json.dumps(payload))      # enforce JSON-serializable, decouple from caller
        with self._lock:
            self.published.append((ft, payload))
            if retain:
                self._retained[ft] = payload
            targets = [h for f, h in self._subs if mqtt_match(f, ft)]
        for h in targets:
            try:
                h(ft, payload)
            except Exception:
                log.exception("handler for %s failed", ft)

    def subscribe(self, topic_filter: str, handler: Handler) -> None:
        ff = self.full(topic_filter)
        with self._lock:
            self._subs.append((ff, handler))
            replay = [(t, p) for t, p in self._retained.items() if mqtt_match(ff, t)]
        for t, p in replay:
            handler(t, p)

    def last(self, topic: str) -> Optional[Dict[str, Any]]:
        """Most recent payload published on a full-or-relative topic (tests)."""
        ft = self.full(topic) if not topic.startswith(self.prefix + "/") else topic
        for t, p in reversed(self.published):
            if t == ft:
                return p
        return None


class MqttBus(Bus):
    """paho-mqtt wrapper. ``import paho`` happens here, lazily, so the rest of
    the package (config, doctor) never needs it installed."""

    def __init__(self, cfg: MqttConfig, client_id: str, will: Optional[Tuple[str, Dict[str, Any]]] = None) -> None:
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError as e:  # pragma: no cover - environment
            raise RuntimeError("paho-mqtt is not installed: pip install -e '.[den]'") from e
        self.cfg = cfg
        self.prefix = cfg.topic_prefix
        self._subs: List[Tuple[str, Handler]] = []
        self._lock = threading.RLock()
        self.connected = threading.Event()
        try:  # paho 2.x
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
        except AttributeError:  # paho 1.x
            self._client = mqtt.Client(client_id=client_id, clean_session=True)
        if cfg.username:
            self._client.username_pw_set(cfg.username, cfg.password)
        if will:
            self._client.will_set(self.full(will[0]), json.dumps(will[1]), qos=1, retain=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def full(self, topic: str) -> str:
        return f"{self.prefix}/{topic}" if self.prefix else topic

    # paho callbacks (signatures differ between 1.x and 2.x → *args)
    def _on_connect(self, client, userdata, flags, rc, *args) -> None:
        code = getattr(rc, "value", rc)
        if code not in (0, "Success"):
            log.error("MQTT connect refused: %s", rc)
            return
        log.info("MQTT connected to %s:%s", self.cfg.host, self.cfg.port)
        self.connected.set()
        with self._lock:
            for f, _ in self._subs:
                client.subscribe(f, qos=1)

    def _on_disconnect(self, client, userdata, *args) -> None:
        self.connected.clear()
        log.warning("MQTT disconnected; paho will reconnect with backoff")

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8")) if msg.payload else {}
        except ValueError:
            log.warning("non-JSON message on %s ignored", msg.topic)
            return
        with self._lock:
            targets = [h for f, h in self._subs if mqtt_match(f, msg.topic)]
        for h in targets:
            try:
                h(msg.topic, payload)
            except Exception:
                log.exception("handler for %s failed", msg.topic)

    # Bus interface
    def start(self) -> None:
        self._client.connect_async(self.cfg.host, self.cfg.port, keepalive=30)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        try:
            self._client.disconnect()
        except Exception:
            pass

    def wait_connected(self, timeout: float) -> bool:
        return self.connected.wait(timeout)

    def publish(self, topic: str, payload: Dict[str, Any], retain: bool = False) -> None:
        self._client.publish(self.full(topic), json.dumps(payload, separators=(",", ":")), qos=1, retain=retain)

    def subscribe(self, topic_filter: str, handler: Handler) -> None:
        ff = self.full(topic_filter)
        with self._lock:
            self._subs.append((ff, handler))
        if self.connected.is_set():
            self._client.subscribe(ff, qos=1)


def make_bus(cfg: MqttConfig, client_id: str, will: Optional[Tuple[str, Dict[str, Any]]] = None) -> MqttBus:
    return MqttBus(cfg, client_id=client_id, will=will)
