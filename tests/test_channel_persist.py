"""Received channel messages must reach message_logs, or the per-channel view never shows them."""
from __future__ import annotations

import sqlite3
import time

import pytest

pytest.importorskip("flask")
flask_socketio = pytest.importorskip("flask_socketio")
import flask

from wildcat.config import build
from wildcat.observatory import queries as Q
from wildcat.observatory.bridge import Bridge

SCHEMA = ("CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, sender_short_name TEXT,"
          " to_id INTEGER, channel_index INTEGER, message TEXT, snr REAL, rssi INTEGER, hop_limit INTEGER)")
DEN = "!ba5a3f4b"


def _bridge(tmp_path, bbs_node_id="!9e766b18", with_table=True):
    (tmp_path / "content").mkdir()
    db = tmp_path / "b.db"
    c = sqlite3.connect(db)
    if with_table:
        c.execute(SCHEMA)
    c.commit(); c.close()
    cfg = build({"radio": {"type": "serial"}, "database": {"path": str(db)}, "bbs": {"content_dir": str(tmp_path / "content")},
                 "observatory": {"bbs_node_id": bbs_node_id}})
    b = Bridge(cfg, flask_socketio.SocketIO(flask.Flask("t"), async_mode="threading"))
    b.state.apply_roster({"my_id": DEN, "roster": {"!716c668c": {"id": "!716c668c", "short_name": "GO"}}})
    return b, str(db)


def _text(text="hello channel", channel=1, frm="!716c668c", broadcast=True, via_mqtt=False, at=None):
    return {"from": frm, "to": "^all" if broadcast else DEN, "broadcast": broadcast, "kind": "text", "channel": channel,
            "text": text, "proto": "meshtastic", "received_at": int(at or time.time()),
            "rx": {"snr": 6.25, "rssi": -30, "hops": 0, "via_mqtt": via_mqtt}}


def _rows(db):
    return sqlite3.connect(db).execute("SELECT sender_id, sender_short_name, to_id, channel_index, message, snr, rssi FROM message_logs").fetchall()


def test_received_channel_message_is_logged_and_shows_in_the_channel_view(tmp_path):
    b, db = _bridge(tmp_path)
    b._on_rx("wildcat/rx/x", _text())
    assert _rows(db) == [("!716c668c", "GO", 4294967295, 1, "hello channel", 6.25, -30)]
    m = Q.channel_messages(db, 1, 24)
    assert [x["text"] for x in m] == ["hello channel"] and m[0]["short_name"] == "GO"
    assert Q.channel_messages(db, 0, 24) == []
    assert Q.channel_details(db, 24)[0]["channel"] == 1


def test_same_message_is_not_doubled_but_a_later_repeat_is_kept(tmp_path):
    b, db = _bridge(tmp_path)
    now = time.time()
    b._on_rx("t", _text(at=now)); b._on_rx("t", _text(at=now + 3))          # duplicate delivery / BBS twin
    assert len(_rows(db)) == 1
    b._on_rx("t", _text(at=now + 60))                                       # genuinely sent again a minute later
    assert len(_rows(db)) == 2


def test_dms_own_messages_and_non_text_are_not_logged_here(tmp_path):
    b, db = _bridge(tmp_path)
    b._on_rx("t", _text(broadcast=False))                                   # DMs have their own path (BBS / quiet-node log)
    b._on_rx("t", _text(frm=DEN))                                           # what we sent is logged when sent
    e = _text(); e["kind"] = "telemetry"; b._on_rx("t", e)
    assert _rows(db) == []


def test_internet_bridged_text_is_logged_without_radio_measurements(tmp_path):
    b, db = _bridge(tmp_path)
    b._on_rx("t", _text(via_mqtt=True))
    assert _rows(db)[0][5:] == (None, None)


def test_when_the_den_node_is_the_bbs_node_the_bbs_logs_so_we_do_not(tmp_path):
    b, db = _bridge(tmp_path, bbs_node_id=DEN)
    b._on_rx("t", _text())
    assert _rows(db) == []


def test_missing_database_or_table_never_breaks_receive(tmp_path):
    b, db = _bridge(tmp_path, with_table=False)
    b._on_rx("t", _text())                                                  # no table: logged as a warning, packet still folded
    assert b.state.total == 1
    (tmp_path / "b.db").unlink()
    b._on_rx("t", _text("again"))
    assert b.state.total == 2
