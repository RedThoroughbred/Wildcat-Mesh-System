from __future__ import annotations

import pytest

from wildcat.meshd.txqueue import (BROADCAST_NUM, BadRequest, TxQueue, chunk_raw, chunk_words,
                                   parse_destination)


def test_parse_destination():
    assert parse_destination(123) == 123
    assert parse_destination("!9e766b18") == 0x9E766B18
    assert parse_destination("^all") == BROADCAST_NUM
    assert parse_destination("4294967295") == BROADCAST_NUM
    for bad in (None, -1, True, "zz", "!123", 2**33):
        with pytest.raises(BadRequest):
            parse_destination(bad)


def test_chunk_raw_matches_v1_send_message():
    text = "x" * 450
    assert chunk_raw(text, 200) == [text[0:200], text[200:400], text[400:450]]
    assert chunk_raw("", 200) == []


def test_chunk_words_numbered_and_bounded():
    text = " ".join(f"word{i}" for i in range(80))          # ~560 chars
    chunks = chunk_words(text, 190)
    assert all(len(c) <= 190 for c in chunks)
    assert chunks[0].startswith("1/") and chunks[-1].startswith(f"{len(chunks)}/")
    assert " ".join(c.split(" ", 1)[1] for c in chunks) == text   # nothing lost
    assert chunk_words("short", 190) == ["short"]                  # single chunk: no prefix


def test_chunk_words_cap_and_long_token():
    text = " ".join(f"w{i}" for i in range(300))
    chunks = chunk_words(text, 100, max_chunks=3)
    assert len(chunks) == 3 and chunks[-1].endswith("…(ask for more)") and all(len(c) <= 100 for c in chunks)
    long = "a" * 250
    assert [len(c) <= 100 for c in chunk_words(long, 100)] and "".join(
        c.split(" ", 1)[1] for c in chunk_words(long, 100)) == long


def test_queue_normalizes_and_validates():
    q = TxQueue(max_chunk_chars=200, pacing_seconds=2.0, default_channel=0)
    req = q.put({"to": "!9e766b18", "text": "hello", "id": "r1"})
    assert req.dest == 0x9E766B18 and req.chunks == ["hello"] and req.want_ack is True and req.channel == 0
    req = q.put({"to": "^all", "text": "hey"})
    assert req.want_ack is False, "broadcasts don't ask for acks by default"
    for bad in ({"to": "!9e766b18"}, {"to": "!9e766b18", "text": ""}, {"to": "!9e766b18", "text": "x", "channel": 9},
                {"to": "!9e766b18", "text": "x", "priority": 10}, {"to": "!9e766b18", "chunks": ["x" * 231]},
                {"to": "!9e766b18", "chunks": []}, "nope"):
        with pytest.raises(BadRequest):
            q.put(bad)


def test_queue_priority_order_then_fifo_and_pacing():
    q = TxQueue(max_chunk_chars=5, pacing_seconds=2.0)
    q.put({"to": 1, "text": "aaaaabbbbb", "priority": 5, "id": "low"})   # 2 chunks
    q.put({"to": 1, "text": "zz", "priority": 0, "id": "urgent"})
    q.put({"to": 1, "text": "yy", "priority": 5, "id": "low2"})
    sent = []
    now = 100.0
    wait = q.pump(sent.append, now)
    assert [s["id"] for s in sent] == ["urgent"] and wait == 0.0
    assert q.pump(sent.append, now) == pytest.approx(2.0)          # pacing: nothing sent
    assert len(sent) == 1
    q.pump(sent.append, now + 2.0)
    q.pump(sent.append, now + 4.0)
    q.pump(sent.append, now + 6.0)
    assert [(s["id"], s["text"], s["index"], s["count"]) for s in sent] == [
        ("urgent", "zz", 0, 1), ("low", "aaaaa", 0, 2), ("low", "bbbbb", 1, 2), ("low2", "yy", 0, 1)]
    assert len(q) == 0 and q.sent == 4
    assert q.pump(sent.append, now + 8.0) == 2.0                    # idle → sleep a pacing interval


def test_queue_full_drops():
    q = TxQueue(max_depth=2)
    q.put({"to": 1, "text": "a"}); q.put({"to": 1, "text": "b"})
    with pytest.raises(BadRequest):
        q.put({"to": 1, "text": "c"})
    assert q.dropped == 1


def test_send_failure_propagates_and_counts():
    q = TxQueue()
    q.put({"to": 1, "text": "a"})
    def boom(item): raise OSError("radio gone")
    with pytest.raises(OSError):
        q.pump(boom, 0.0)
    assert q.sent == 0
