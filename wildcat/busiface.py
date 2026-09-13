"""``BusInterface`` — the meshtastic-interface-shaped object the BBS and the
telemetry logger use when ``source = "bus"``.

The v1 code was written against ``meshtastic``'s interface: it reads
``interface.nodes``, ``interface.myInfo.my_node_num``, calls
``interface.getMyNodeInfo()`` and ``interface.sendText(...)``, and hangs a few
attributes on it (``bbs_nodes``, ``allowed_nodes``, ``request_timestamp``).
This class provides exactly that surface, backed by the bus:

* ``nodes`` / ``myInfo`` come from meshd's retained ``wildcat/nodes`` and
  ``wildcat/meshd/status`` snapshots (kept current as they're republished);
* ``sendText`` publishes a ``wildcat/tx`` request (meshd owns pacing + the radio)
  and returns an object with an ``.id`` like the real one;
* ``start(on_packet)`` subscribes to ``wildcat/rx/+`` and hands each consumer
  the ORIGINAL packet dict (bytes restored), so ``message_processing.on_receive``
  and ``telemetry_logger.on_receive`` run unchanged.

So the BBS's 1300 lines of handlers don't know the radio moved. That is the
whole point of the adapter (docs/MESHD_MIGRATION.md).
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from .bus import Bus
from .config import WildcatConfig
from .meshd import packets

log = logging.getLogger("wildcat.busiface")


class BusInterface:
    def __init__(self, bus: Bus, cfg: WildcatConfig, client_name: str = "bbs") -> None:
        self.bus = bus
        self.cfg = cfg
        self.client_name = client_name
        self.nodes: Dict[str, Any] = {}
        self.myInfo = SimpleNamespace(my_node_num=None)
        self.meshd_state: str = "unknown"
        # v1 attributes the BBS sets on the interface object
        self.bbs_nodes: list = []
        self.allowed_nodes: list = []
        self.request_timestamp: Optional[int] = None
        self._ready = threading.Event()
        self._seq = itertools.count(1)
        self._on_packet: Optional[Callable[[Dict[str, Any], "BusInterface"], None]] = None
        bus.subscribe("nodes", self._on_nodes)
        bus.subscribe("meshd/status", self._on_status)

    # ---- snapshots ------------------------------------------------------------------
    def _on_nodes(self, topic: str, payload: Dict[str, Any]) -> None:
        nodes = payload.get("nodes")
        if isinstance(nodes, dict):
            self.nodes = packets.restore(nodes)
        if payload.get("myNodeNum") is not None:
            self.myInfo.my_node_num = payload["myNodeNum"]
        self._check_ready()

    def _on_status(self, topic: str, payload: Dict[str, Any]) -> None:
        self.meshd_state = str(payload.get("state", "unknown"))
        if payload.get("myNodeNum") is not None:
            self.myInfo.my_node_num = payload["myNodeNum"]
        if self.meshd_state != "connected":
            log.warning("meshd reports radio %s%s", self.meshd_state,
                        f" ({payload['error']})" if payload.get("error") else "")
        self._check_ready()

    def _check_ready(self) -> None:
        if self.meshd_state == "connected" and self.myInfo.my_node_num is not None:
            self._ready.set()

    def wait_ready(self, timeout: float) -> bool:
        """True once meshd says the radio is connected and we know our node num."""
        return self._ready.wait(timeout)

    # ---- meshtastic-interface surface --------------------------------------------------
    def getMyNodeInfo(self) -> Dict[str, Any]:
        my = self.myInfo.my_node_num
        for node in self.nodes.values():
            if isinstance(node, dict) and node.get("num") == my:
                return node
        return {"num": my, "user": {"id": f"!{my:08x}" if isinstance(my, int) else "unknown", "shortName": "BBS"}}

    def sendText(self, text: str, destinationId: Any = "^all", wantAck: bool = False, wantResponse: bool = False,
                 channelIndex: Optional[int] = None, **_: Any) -> SimpleNamespace:
        req_id = f"{self.client_name}-{int(time.time())}-{next(self._seq)}"
        self.bus.publish("tx", {
            "to": destinationId, "text": text, "wantAck": bool(wantAck),
            "channel": self.cfg.radio.channel_index if channelIndex is None else channelIndex,
            "id": req_id, "chunks": [text],      # v1 callers already chunk at 200; don't re-slice
        })
        return SimpleNamespace(id=req_id)

    def close(self) -> None:
        pass

    # ---- inbound -------------------------------------------------------------------------
    def start(self, on_packet: Callable[[Dict[str, Any], "BusInterface"], None]) -> None:
        self._on_packet = on_packet
        self.bus.subscribe("rx/+", self._on_rx)

    def _on_rx(self, topic: str, env: Dict[str, Any]) -> None:
        if self._on_packet is None:
            return
        try:
            packet = packets.packet_from_envelope(env)
        except Exception:
            log.exception("bad envelope on %s", topic)
            return
        try:
            self._on_packet(packet, self)
        except Exception:
            log.exception("packet handler failed")
