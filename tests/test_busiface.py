"""BusInterface: the meshtastic-shaped adapter over the bus, and the end-to-end
proof — a DM of 'M' reaching the REAL BBS handlers over the bus and the menu
coming back out to a fake radio, with no hardware anywhere."""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from wildcat.bus import MemoryBus
from wildcat.busiface import BusInterface
from wildcat.config import build, load
from wildcat.meshd import packets
from wildcat.meshd.daemon import MeshDaemon

from test_meshd_daemon import TEXT, FakePub, FakeRadio, Clock  # noqa: E402  (shared fakes)

CFG = {"radio": {"type": "tcp", "host": "10.0.0.5"}, "mqtt": {"enabled": True},
       "bbs": {"source": "bus"}, "telemetry": {"source": "bus"}}


def _snapshot(bus, state="connected"):
    bus.publish("meshd/status", {"state": state, "myNodeNum": 222}, retain=True)
    bus.publish("nodes", {"myNodeNum": 222, "count": 1, "nodes": {
        "!000000de": {"num": 222, "user": {"id": "!000000de", "shortName": "BBS", "longName": "Wildcat BBS"}}}}, retain=True)


def test_ready_from_retained_snapshots_even_if_late():
    bus = MemoryBus(); _snapshot(bus)
    iface = BusInterface(bus, build(CFG))
    assert iface.wait_ready(0) is True
    assert iface.myInfo.my_node_num == 222
    assert iface.nodes["!000000de"]["user"]["shortName"] == "BBS"
    assert iface.getMyNodeInfo()["user"]["longName"] == "Wildcat BBS"


def test_not_ready_while_meshd_disconnected():
    bus = MemoryBus(); _snapshot(bus, state="disconnected")
    iface = BusInterface(bus, build(CFG))
    assert iface.wait_ready(0) is False
    bus.publish("meshd/status", {"state": "connected", "myNodeNum": 222}, retain=True)
    assert iface.wait_ready(0) is True


def test_sendtext_publishes_tx_request():
    bus = MemoryBus(); _snapshot(bus)
    iface = BusInterface(bus, build(CFG), client_name="bbs")
    r = iface.sendText(text="hello", destinationId=111, wantAck=True, wantResponse=False)
    tx = bus.last("tx")
    assert tx["to"] == 111 and tx["chunks"] == ["hello"] and tx["wantAck"] is True and tx["channel"] == 0
    assert tx["id"] == r.id and r.id.startswith("bbs-")


def test_rx_delivers_original_packet_with_bytes_restored():
    bus = MemoryBus(); _snapshot(bus)
    iface = BusInterface(bus, build(CFG))
    got = []
    iface.start(lambda packet, i: got.append((packet, i)))
    _, env = packets.envelope(TEXT, 0)
    bus.publish("rx/text", env)
    assert got[0][0] == TEXT and got[0][0]["decoded"]["payload"] == b"M" and got[0][1] is iface


def test_end_to_end_menu_over_the_bus(write, tmp_path, monkeypatch):
    """DM 'M' → meshd → bus → real BBS handlers → bus → meshd → radio.sendText(menu)."""
    db = tmp_path / "shared" / "bulletins.db"
    cfg_path = write("c.toml", f'''
[radio]
type = "tcp"
host = "10.0.0.5"
[mqtt]
enabled = true
[bbs]
source = "bus"
[telemetry]
source = "bus"
[database]
path = "{db}"
''')
    cfg = load(str(cfg_path))
    for name in ("message_processing", "command_handlers", "db_operations", "utils", "js8call_integration"):
        sys.modules.pop(name, None)
    db_operations = importlib.import_module("db_operations")
    db_operations.initialize_database()
    utils = importlib.import_module("utils")
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)      # v1 paces 2 s/chunk itself
    mp = importlib.import_module("message_processing")

    # the Den, in one process: meshd on a fake radio + the BBS behind BusInterface
    bus, pub, clock = MemoryBus(), FakePub(), Clock()
    radio = FakeRadio()
    daemon = MeshDaemon(cfg, bus, lambda: radio, pub, clock=clock.now, sleep=clock.sleep)
    daemon.start()
    bbs = BusInterface(bus, cfg, client_name="bbs")
    assert bbs.wait_ready(0)
    bbs.bbs_nodes, bbs.allowed_nodes = [], []
    bbs.start(lambda packet, iface: mp.on_receive(packet, iface))

    # GO (111) DMs the BBS node (222) the letter M — in this fork that's the Mail menu
    pub.send("meshtastic.receive", packet=TEXT, interface=radio)
    tx = bus.last("tx")
    assert tx["to"] == 111 and "Mail Menu" in tx["chunks"][0]
    # … and meshd put it on the (fake) air
    daemon.pump_once()
    assert radio.sent and radio.sent[0]["destinationId"] == 111 and "Mail Menu" in radio.sent[0]["text"]
    assert bus.last("tx/result")["ok"] is True

    # any unrecognized text (X resets first) → the MAIN menu, with Seth's items
    pub.send("meshtastic.receive", packet={**TEXT, "id": 8, "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"x", "text": "x"}}, interface=radio)
    tx = bus.last("tx")
    assert "Wildcat" in tx["chunks"][0] and "[B]ulletins" in tx["chunks"][0] and "[W]eather" in tx["chunks"][0]
    clock.t += 2.0
    daemon.pump_once()
    assert "[B]ulletins" in radio.sent[-1]["text"]
    # and the inbound DM was logged to the shared DB by the unchanged v1 code
    conn = db_operations.get_db_connection()
    rows = conn.execute("SELECT sender_id, message FROM message_logs").fetchall()
    assert ("!0000006f", "M") in rows


# ---------------------------------------------------------------------------- Bobcat hand-off
def _text_dm(text, frm="!0000006f", to="!000000de"):
    return {"v": 1, "proto": "meshtastic", "kind": "text", "from": frm, "to": to, "broadcast": False, "channel": 0,
            "rx": {"snr": 6.0, "rssi": -80, "hops": 0}, "text": text, "id": 77, "received_at": 1.0,
            "packet": {"from": 111, "to": 222, "fromId": frm, "toId": to, "channel": 0, "id": 77,
                       "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": {"__bytes_b64__": "TQ=="}, "text": text}}}


def test_brain_questions_bypass_the_bbs_only_while_bobcat_reports_on():
    bus = MemoryBus(); _snapshot(bus)
    iface = BusInterface(bus, build(CFG))
    got = []
    iface.start(lambda packet, i: got.append(packet["decoded"]["text"]))
    bus.publish("rx/text", _text_dm("?who is online"))          # Bobcat not running → the BBS sees it (v1 behaviour)
    assert got == ["?who is online"]
    bus.publish("brain/status", {"enabled": True, "running": True}, retain=True)
    bus.publish("rx/text", _text_dm("?who is online"))          # Bobcat on → handed to wildcat brain, not the BBS
    bus.publish("rx/text", _text_dm("M"))                       # ordinary DMs still reach the BBS
    bus.publish("rx/text", {**_text_dm("?bcast"), "to": None, "broadcast": True})
    assert got == ["?who is online", "M", "?bcast"]
    bus.publish("brain/status", {"enabled": False, "running": True}, retain=True)
    bus.publish("rx/text", _text_dm("?again"))
    assert got[-1] == "?again"
    bus.publish("brain/status", {"enabled": True, "running": False}, retain=True)   # process died → BBS takes over
    bus.publish("rx/text", _text_dm("?dead"))
    assert got[-1] == "?dead"


def test_quiet_nodes_are_logged_but_never_answered(tmp_path):
    import sqlite3
    db = tmp_path / "q.db"; c = sqlite3.connect(db)
    c.execute("CREATE TABLE message_logs (id INTEGER PRIMARY KEY, timestamp INTEGER, sender_id TEXT, sender_short_name TEXT, to_id INTEGER, channel_index INTEGER, message TEXT, snr REAL, rssi INTEGER, hop_limit INTEGER)")
    c.commit(); c.close()
    bus = MemoryBus(); _snapshot(bus)
    iface = BusInterface(bus, build({**CFG, "bbs": {"source": "bus", "quiet_nodes": ["!0000006f"]}, "database": {"path": str(db)}}))
    got = []
    iface.start(lambda packet, i: got.append(packet["decoded"]["text"]))
    bus.publish("rx/text", _text_dm("hey den, it's me"))                 # quiet node → not handed to the BBS
    bus.publish("rx/text", _text_dm("M", frm="!00000070"))                # anyone else → BBS as usual
    bus.publish("rx/text", {**_text_dm("all hear this"), "to": None, "broadcast": True})   # broadcasts untouched
    assert got == ["M", "all hear this"]
    rows = sqlite3.connect(db).execute("SELECT sender_id, to_id, message, snr FROM message_logs").fetchall()
    assert rows == [("!0000006f", 222, "hey den, it's me", 6.0)]
