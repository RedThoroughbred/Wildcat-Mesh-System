"""The meshd service: radio ↔ bus.

    radio ──(pubsub meshtastic.receive)──► envelope ──► bus  wildcat/rx/<kind>
    bus  wildcat/tx ──► TxQueue (priority + pacing) ──► interface.sendText ──► bus wildcat/tx/result
    every N s:  bus wildcat/nodes  (retained snapshot of interface.nodes)
    on connect / lost:  bus wildcat/meshd/status  (retained), reconnect with backoff

Everything the outside world provides is injected (bus, pubsub module, the
``open_interface`` factory, clock/sleep) so the whole loop is unit-tested with
a fake radio. ``wildcat meshd`` (cli) wires the real ones.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from ..bus import Bus
from ..config import WildcatConfig
from . import packets
from .txqueue import BadRequest, TxQueue

log = logging.getLogger("wildcat.meshd")

STATUS_TOPIC = "meshd/status"
NODES_TOPIC = "nodes"
CHANNELS_TOPIC = "meshd/channels"      # retained: the node's channel slots (names, roles, psk set?) — never the keys
TX_TOPIC = "tx"
TX_RESULT_TOPIC = "tx/result"


class MeshDaemon:
    def __init__(self, cfg: WildcatConfig, bus: Bus, open_interface: Callable[[], Any], pub: Any,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg = cfg
        self.bus = bus
        self.open_interface = open_interface
        self.pub = pub
        self.clock = clock
        self.sleep = sleep
        self.interface: Any = None
        self.queue = TxQueue(max_chunk_chars=cfg.meshd.max_chunk_chars,
                             pacing_seconds=cfg.meshd.tx_pacing_seconds,
                             default_channel=cfg.radio.channel_index)
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._next_port_check = 0.0
        self._next_nodes_at = 0.0
        self._backoff = cfg.meshd.reconnect_min_seconds
        self.rx_count = 0
        self.connected_at: Optional[float] = None

    # ---- wiring -----------------------------------------------------------------------
    def start(self) -> None:
        """Subscribe to pubsub + bus, connect the radio (blocking, with retries)."""
        self.pub.subscribe(self._on_receive, "meshtastic.receive")
        self.pub.subscribe(self._on_established, "meshtastic.connection.established")
        self.pub.subscribe(self._on_lost, "meshtastic.connection.lost")
        self.bus.subscribe(TX_TOPIC, self._on_tx)
        self._publish_status("starting")
        self.connect()

    def connect(self) -> None:
        """Open the radio, retrying with bounded exponential backoff until it works or stop()."""
        while not self._stop.is_set():
            try:
                self.interface = self.open_interface()
            except Exception as e:
                self._publish_status("disconnected", error=str(e))
                log.error("radio connect failed: %s — retry in %ss", e, self._backoff)
                self.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, self.cfg.meshd.reconnect_max_seconds)
                continue
            self._backoff = self.cfg.meshd.reconnect_min_seconds
            self._lost.clear()
            self.connected_at = self.clock()
            self._publish_status("connected")
            self.publish_nodes()
            return

    def stop(self) -> None:
        self._stop.set()
        self._publish_status("stopped")
        if self.interface is not None:
            try:
                self.interface.close()
            except Exception:
                pass

    # ---- radio → bus -----------------------------------------------------------------------
    def _on_receive(self, packet: Dict[str, Any], interface: Any = None, **_: Any) -> None:
        try:
            kind, env = packets.envelope(packet, self.clock())
        except Exception:
            log.exception("could not envelope packet")
            return
        self.rx_count += 1
        self.bus.publish(f"rx/{kind}", env)

    def _on_established(self, interface: Any = None, **_: Any) -> None:
        log.info("radio connection established")

    def _on_lost(self, interface: Any = None, **_: Any) -> None:
        log.warning("radio connection lost")
        self._lost.set()
        self._publish_status("disconnected", error="connection lost")

    def publish_nodes(self) -> None:
        if self.interface is None:
            return
        try:
            nodes = packets.json_safe(getattr(self.interface, "nodes", {}) or {})
            my = getattr(getattr(self.interface, "myInfo", None), "my_node_num", None)
        except Exception:
            log.exception("could not snapshot nodes")
            return
        self.bus.publish(NODES_TOPIC, {"ts": self.clock(), "myNodeNum": my, "my_id": packets.node_id(my),
                                       "count": len(nodes), "roster": packets.roster(nodes), "nodes": nodes},
                         retain=True)
        try:
            table = packets.channel_table(getattr(getattr(self.interface, "localNode", None), "channels", None))
            self.bus.publish(CHANNELS_TOPIC, {"ts": self.clock(), "myNodeNum": my, "channels": table}, retain=True)
        except Exception:
            log.exception("could not publish the channel table")
        self._next_nodes_at = self.clock() + self.cfg.meshd.nodes_publish_interval
        self._publish_status("connected")          # keep rxCount/txSent/txQueued fresh on the retained topic

    def _publish_status(self, state: str, error: Optional[str] = None) -> None:
        my = None
        if self.interface is not None:
            my = getattr(getattr(self.interface, "myInfo", None), "my_node_num", None)
        payload: Dict[str, Any] = {"state": state, "ts": self.clock(), "myNodeNum": my,
                                   "radio": f"{self.cfg.radio.type}:{self.cfg.radio.host or self.cfg.radio.port or 'auto'}",
                                   "rxCount": self.rx_count, "txSent": self.queue.sent, "txQueued": len(self.queue)}
        if error:
            payload["error"] = error
        self.bus.publish(STATUS_TOPIC, payload, retain=True)

    # ---- bus → radio ------------------------------------------------------------------------
    def _on_tx(self, topic: str, payload: Dict[str, Any]) -> None:
        try:
            req = self.queue.put(payload)
        except BadRequest as e:
            log.warning("rejected tx request: %s", e)
            self.bus.publish(TX_RESULT_TOPIC, {"id": (payload or {}).get("id") if isinstance(payload, dict) else None,
                                               "ok": False, "error": str(e)})
            return
        log.debug("queued %d chunk(s) for %s (id=%s)", len(req.chunks), req.dest, req.id)

    def _send(self, item: Dict[str, Any]) -> None:
        try:
            r = self.interface.sendText(text=item["text"], destinationId=item["dest"],
                                        wantAck=item["wantAck"], wantResponse=False,
                                        channelIndex=item["channel"])
        except Exception as e:
            self.bus.publish(TX_RESULT_TOPIC, {"id": item["id"], "index": item["index"], "count": item["count"],
                                               "ok": False, "error": str(e)})
            raise
        log.info("SENT %s/%s to !%08x (id=%s packet=%s): %r", item["index"] + 1, item["count"], item["dest"],
                 item["id"], getattr(r, "id", None), item["text"][:60])
        self.bus.publish(TX_RESULT_TOPIC, {"id": item["id"], "index": item["index"], "count": item["count"],
                                           "ok": True, "packetId": getattr(r, "id", None), "to": item["dest"]})

    def pump_once(self, now: Optional[float] = None) -> float:
        """One scheduler tick: reconnect if lost, send one paced chunk, snapshot nodes
        when due. Returns how long the loop may sleep."""
        now = self.clock() if now is None else now
        # Serial watchdog: an unplugged USB node makes its device path vanish long before the
        # library notices (if it ever does). Check every 2 s; the reconnect loop then waits
        # for the path to come back and re-opens it.
        if (self.interface is not None and not self._lost.is_set() and self.cfg.radio.type == "serial" and self.cfg.radio.port
                and now >= self._next_port_check):
            self._next_port_check = now + 2
            if not os.path.exists(self.cfg.radio.port):
                log.warning("serial device %s disappeared — treating the radio as lost", self.cfg.radio.port)
                self._on_lost()
        if self._lost.is_set() and not self._stop.is_set():
            try:
                self.interface.close()
            except Exception:
                pass
            self.interface = None
            self.connect()
            return 0.0
        wait = self.queue.pacing
        if self.interface is not None:
            try:
                wait = self.queue.pump(self._send, now)
            except Exception:
                # sendText raising usually means the socket is gone; the lost event
                # follows shortly. Don't spin.
                wait = 1.0
            if now >= self._next_nodes_at:
                self.publish_nodes()
        return max(0.0, min(wait, 0.5))

    def run(self) -> None:
        """Blocking main loop (until stop())."""
        self.start()
        try:
            while not self._stop.is_set():
                self.sleep(self.pump_once())
        finally:
            self.stop()
