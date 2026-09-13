"""The ONE outbound queue for the radio: priorities, chunking, airtime pacing.

Pure — the daemon injects ``send`` and the clock, so this is fully testable
with no hardware.

A TX request (what arrives on ``wildcat/tx``)::

    {"to": 123456789 | "!075bcd15" | "^all",   # node num, node id, or broadcast
     "text": "…",
     "channel": 0,          # optional, default [radio].channel_index
     "wantAck": true,       # optional, default true for DMs, false for broadcast
     "priority": 5,         # optional, 0 = most urgent … 9; default 5
     "id": "bbs-…",         # optional correlation id echoed on wildcat/tx/result
     "chunks": ["…", "…"]}  # optional: pre-split; else split raw at max_chunk_chars

Chunking default is a RAW slice at ``max_chunk_chars`` (200) — byte-for-byte
what the v1 BBS's ``send_message`` did, so menus render identically. Phase 2's
brain uses :func:`chunk_words` (word boundaries + ``1/3`` prefixes) itself and
sends ``chunks``.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("wildcat.meshd.tx")

BROADCAST_NUM = 0xFFFFFFFF
BROADCAST_ALIASES = ("^all", "broadcast", "all", str(BROADCAST_NUM))
_NODE_ID = re.compile(r"^!([0-9a-fA-F]{8})$")


class BadRequest(ValueError):
    pass


def parse_destination(to: Any) -> int:
    """node num (int), "!hex" id, or a broadcast alias → node num."""
    if isinstance(to, bool):
        raise BadRequest(f"bad 'to': {to!r}")
    if isinstance(to, int):
        if to < 0 or to > BROADCAST_NUM:
            raise BadRequest(f"bad 'to': {to!r}")
        return to
    if isinstance(to, str):
        s = to.strip()
        if s.lower() in BROADCAST_ALIASES:
            return BROADCAST_NUM
        m = _NODE_ID.match(s)
        if m:
            return int(m.group(1), 16)
        if s.isdigit():
            return int(s)
    raise BadRequest(f"bad 'to': {to!r} (want a node num, '!xxxxxxxx', or '^all')")


def chunk_raw(text: str, max_chars: int) -> List[str]:
    """v1-compatible: plain slices."""
    if not text:
        return []
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]


def chunk_words(text: str, max_chars: int, max_chunks: Optional[int] = None, numbered: bool = True) -> List[str]:
    """Split at word boundaries; ``numbered`` prefixes ``i/n `` when there is
    more than one chunk (the prefix counts toward ``max_chars``). ``max_chunks``
    truncates with an ellipsis marker. Long single words are hard-split."""
    text = " ".join(text.split())
    if not text:
        return []
    # worst-case prefix "9/9 " = 4 chars; recompute after we know n
    budget = max_chars - (4 if numbered else 0)
    if budget < 8:
        raise ValueError("max_chars too small")
    words, chunks, cur = text.split(" "), [], ""
    for w in words:
        while len(w) > budget:            # a single over-long token
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(w[:budget]); w = w[budget:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= budget:
            cur += " " + w
        else:
            chunks.append(cur); cur = w
    if cur:
        chunks.append(cur)
    if max_chunks and len(chunks) > max_chunks:
        chunks = chunks[:max_chunks]
        last = chunks[-1]
        tail = " …(ask for more)"
        chunks[-1] = (last[: budget - len(tail)] + tail) if len(last) + len(tail) > budget else last + tail
    if numbered and len(chunks) > 1:
        n = len(chunks)
        chunks = [f"{i}/{n} {c}" for i, c in enumerate(chunks, 1)]
    return chunks


@dataclass(order=True)
class _Item:
    priority: int
    seq: int
    req: Dict[str, Any] = field(compare=False)


@dataclass
class TxRequest:
    dest: int
    chunks: List[str]
    channel: int
    want_ack: bool
    priority: int
    id: Optional[str]


class TxQueue:
    """Thread-safe priority queue + pacer. ``pump()`` sends at most one chunk
    and tells you how long to wait before calling it again."""

    def __init__(self, max_chunk_chars: int = 200, pacing_seconds: float = 2.0, default_channel: int = 0,
                 max_depth: int = 500) -> None:
        self.max_chunk_chars = max_chunk_chars
        self.pacing = pacing_seconds
        self.default_channel = default_channel
        self.max_depth = max_depth
        self._heap: List[_Item] = []
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._last_send: Optional[float] = None
        self.sent = 0
        self.dropped = 0

    # ---- intake --------------------------------------------------------------------
    def normalize(self, raw: Dict[str, Any]) -> TxRequest:
        if not isinstance(raw, dict):
            raise BadRequest("tx request must be an object")
        dest = parse_destination(raw.get("to"))
        chunks = raw.get("chunks")
        if chunks is None:
            text = raw.get("text")
            if not isinstance(text, str) or not text:
                raise BadRequest("tx request needs non-empty 'text' (or 'chunks')")
            chunks = chunk_raw(text, self.max_chunk_chars)
        elif not (isinstance(chunks, list) and chunks and all(isinstance(c, str) and c for c in chunks)):
            raise BadRequest("'chunks' must be a non-empty list of non-empty strings")
        for c in chunks:
            if len(c.encode("utf-8")) > 230:
                raise BadRequest(f"chunk exceeds the LoRa payload limit: {len(c.encode('utf-8'))} bytes")
        channel = raw.get("channel", self.default_channel)
        if not isinstance(channel, int) or isinstance(channel, bool) or not 0 <= channel <= 7:
            raise BadRequest(f"bad 'channel': {channel!r}")
        want_ack = raw.get("wantAck", dest != BROADCAST_NUM)
        priority = raw.get("priority", 5)
        if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 9:
            raise BadRequest(f"bad 'priority': {priority!r}")
        rid = raw.get("id")
        return TxRequest(dest, list(chunks), channel, bool(want_ack), priority, str(rid) if rid is not None else None)

    def put(self, raw: Dict[str, Any]) -> TxRequest:
        req = self.normalize(raw)
        with self._lock:
            if len(self._heap) >= self.max_depth:
                self.dropped += 1
                raise BadRequest(f"tx queue full ({self.max_depth}) — dropping")
            for i, chunk in enumerate(req.chunks):
                heapq.heappush(self._heap, _Item(req.priority, next(self._seq), {
                    "dest": req.dest, "text": chunk, "channel": req.channel, "wantAck": req.want_ack,
                    "id": req.id, "index": i, "count": len(req.chunks)}))
        return req

    def __len__(self) -> int:
        with self._lock:
            return len(self._heap)

    # ---- output --------------------------------------------------------------------
    def pump(self, send: Callable[[Dict[str, Any]], Any], now: float) -> float:
        """Send one chunk if the pacer allows. Returns seconds until the next
        useful call (0 = call again immediately, i.e. more queued and pacing
        satisfied). ``send`` gets the chunk dict and returns whatever the radio
        returns (its ``.id`` is forwarded on the result topic by the daemon)."""
        with self._lock:
            if not self._heap:
                return self.pacing
            if self._last_send is not None and now - self._last_send < self.pacing:
                return self.pacing - (now - self._last_send)
            item = heapq.heappop(self._heap)
            self._last_send = now
        try:
            send(item.req)
            self.sent += 1
        except Exception:
            log.exception("send failed for %s", item.req.get("id"))
            raise
        with self._lock:
            return 0.0 if self._heap else self.pacing
