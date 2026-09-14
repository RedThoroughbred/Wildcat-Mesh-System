"""Emergency / SOS broadcast — the operator's distress button.

An SOS is a clearly-marked broadcast (``🆘 SOS from <Den>: …``) sent at the highest
TX priority, optionally repeated on an interval until a count is reached or the
operator stops it. Protocol-agnostic: it is text on ``wildcat/tx`` like any other
send; the marker lives in the text so every radio, app and human sees it as what it
is. The Den's own position is appended when known (rescuers need a place).

Incoming distress is recognised the same way — any text that starts with the SOS
marker, "SOS", "MAYDAY" or "EMERGENCY" is flagged ``sos`` in the feed and chimed.

The controller is pure (injected sender/clock/publisher) so it is fully testable;
the bridge owns one and ticks it from a thread. State is in-process: if the
Observatory restarts mid-SOS the repeats stop (the retained ``wildcat/alert/sos``
still says what was last sent).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Dict, Optional

MARK = "🆘 SOS"
MAX_PACKET_BYTES = 200
MAX_TEXT_CHARS = 120
MIN_INTERVAL, MAX_INTERVAL, DEFAULT_INTERVAL = 60, 3600, 300
MAX_REPEAT = 48                                  # 0 = until stopped, 1 = once, n = n sends in total

DISTRESS = re.compile(r"^\W*(?:🆘|SOS\b|MAYDAY\b|EMERGENCY\b)", re.IGNORECASE)


def is_distress(text: Any) -> bool:
    return isinstance(text, str) and bool(DISTRESS.search(text))


def compose(name: str, text: str, n: int, total: int, position: Optional[Dict[str, Any]]) -> str:
    """One packet, always ≤ 200 bytes: marker, sender, message, position, repeat counter."""
    head = f"{MARK} from {name}: "
    pos = ""
    if position and position.get("lat") is not None and position.get("lon") is not None:
        pos = f" @ {float(position['lat']):.5f},{float(position['lon']):.5f}"
    tail = f" [{n}/{total}]" if total > 1 else (f" [{n}]" if n > 1 else "")
    budget = MAX_PACKET_BYTES - len((head + pos + tail).encode("utf-8"))
    body = " ".join(text.split())
    while body and len(body.encode("utf-8")) > budget:
        body = body[:-1]
    return head + body + pos + tail


class SosController:
    def __init__(self, sender: Callable[[str, int], Dict[str, Any]], clock: Callable[[], float] = time.time,
                 publish: Optional[Callable[[Dict[str, Any]], None]] = None,
                 name_fn: Callable[[], str] = lambda: "the Den",
                 position_fn: Callable[[], Optional[Dict[str, Any]]] = lambda: None) -> None:
        self._send, self.clock, self._publish = sender, clock, publish
        self._name, self._position = name_fn, position_fn
        self.lock = threading.RLock()
        self.st: Dict[str, Any] = {"active": False, "sent": 0, "ended": None}

    # ---- operator actions ----------------------------------------------------------------
    def start(self, text: str, channel: int = 0, interval: int = DEFAULT_INTERVAL, repeat: int = 1,
              include_position: bool = True, by: str = "operator") -> Dict[str, Any]:
        text = " ".join((text or "").split())
        if not text:
            raise ValueError("say what the emergency is")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"message must be ≤ {MAX_TEXT_CHARS} characters (the marker and position need room)")
        if not isinstance(channel, int) or not 0 <= channel <= 7:
            raise ValueError("channel must be 0–7")
        if not isinstance(repeat, int) or not 0 <= repeat <= MAX_REPEAT:
            raise ValueError(f"repeat must be 0 (until stopped) or 1–{MAX_REPEAT}")
        if repeat != 1 and (not isinstance(interval, int) or not MIN_INTERVAL <= interval <= MAX_INTERVAL):
            raise ValueError(f"interval must be {MIN_INTERVAL}–{MAX_INTERVAL} seconds")
        now = self.clock()
        with self.lock:
            self.st = {"active": True, "text": text, "channel": channel, "interval": interval if repeat != 1 else None,
                       "repeat": repeat, "include_position": bool(include_position), "by": by, "started": now,
                       "sent": 0, "next_at": None, "last_at": None, "last_tx_id": None, "message": None, "ended": None,
                       "stopped_at": None, "error": None}
            self._fire(now)
            return dict(self.st)

    def stop(self, by: str = "operator") -> Dict[str, Any]:
        with self.lock:
            if self.st.get("active"):
                self.st.update({"active": False, "next_at": None, "ended": "stopped", "stopped_at": self.clock(), "stopped_by": by})
                self._announce()
            return dict(self.st)

    def tick(self, now: Optional[float] = None) -> bool:
        """Send the next repeat if it is due. Returns True when something was sent."""
        now = self.clock() if now is None else now
        with self.lock:
            if not self.st.get("active") or self.st.get("next_at") is None or now < self.st["next_at"]:
                return False
            self._fire(now)
            return True

    def status(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.st)

    # ---- internals -------------------------------------------------------------------------
    def _fire(self, now: float) -> None:
        st = self.st
        n = st["sent"] + 1
        msg = compose(self._name(), st["text"], n, st["repeat"], self._position() if st["include_position"] else None)
        try:
            rec = self._send(msg, st["channel"])
        except Exception as e:          # the bus is off, or meshd refused — the operator must see it
            st.update({"active": False, "ended": "failed", "error": str(e), "next_at": None})
            self._announce()
            raise
        st.update({"sent": n, "last_at": now, "last_tx_id": (rec or {}).get("id"), "message": msg})
        done = st["repeat"] and n >= st["repeat"]
        if done:
            st.update({"active": False, "next_at": None, "ended": "completed"})
        else:
            st["next_at"] = now + st["interval"]
        self._announce()

    def _announce(self) -> None:
        if self._publish:
            try:
                self._publish(dict(self.st))
            except Exception:
                pass
