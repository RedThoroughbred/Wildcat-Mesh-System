"""Open the one radio connection from ``[radio]``.

Mirrors ``bbs/config_init.get_interface`` (which the v1 BBS/telemetry keep
using in ``source = "radio"`` mode) but takes the typed config. ``meshtastic``
is imported lazily so the rest of the package never needs it.
"""
from __future__ import annotations

import logging
from typing import Any, List, Tuple

from ..config import RadioConfig

log = logging.getLogger("wildcat.meshd.radio")


class RadioError(RuntimeError):
    pass


def serial_ports() -> List[Tuple[str, str]]:
    from serial.tools import list_ports  # type: ignore
    return [(p.device, p.description or "") for p in list_ports.comports()]


def open_interface(radio: RadioConfig) -> Any:
    """Return a connected meshtastic interface, or raise RadioError with a
    message the operator can act on. Connection *loss* later is reported via
    pubsub (``meshtastic.connection.lost``), not here."""
    import meshtastic.serial_interface  # type: ignore
    import meshtastic.tcp_interface  # type: ignore

    if radio.type == "tcp":
        log.info("connecting to node over tcp %s:%s", radio.host, radio.tcp_port)
        try:
            return meshtastic.tcp_interface.TCPInterface(hostname=radio.host, portNumber=radio.tcp_port)
        except Exception as e:  # the lib raises a grab-bag (socket errors, its own Exception)
            raise RadioError(f"tcp {radio.host}:{radio.tcp_port}: {e}") from e

    port = radio.port
    if not port:
        ports = serial_ports()
        if len(ports) == 1:
            port = ports[0][0]
        elif not ports:
            raise RadioError("no serial ports detected (set [radio].port or plug the node in)")
        else:
            raise RadioError("several serial ports: " + ", ".join(d for d, _ in ports) + " — set [radio].port")
    log.info("connecting to node over serial %s", port)
    try:
        return meshtastic.serial_interface.SerialInterface(port)
    except Exception as e:
        raise RadioError(f"serial {port}: {e}") from e
