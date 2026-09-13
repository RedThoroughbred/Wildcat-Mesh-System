from __future__ import annotations

from wildcat.meshd import packets

TEXT = {
    "from": 2658765568, "to": 2657904408, "channel": 0, "id": 12345, "rxTime": 1700000000,
    "rxSnr": 5.5, "rxRssi": -86, "hopLimit": 3, "hopStart": 3,
    "fromId": "!9e766b18", "toId": "!9e69a9d8",
    "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"M", "text": "M"},
    "raw": "<protobuf str>",
}
POS = {
    "from": 1, "to": 0xFFFFFFFF, "fromId": "!00000001", "toId": "^all", "rxTime": 1,
    "decoded": {"portnum": "POSITION_APP", "payload": b"\x01\x02",
                "position": {"latitude": 39.0, "longitude": -84.6, "altitude": 250, "satsInView": 7}},
}


def test_json_safe_restore_roundtrip():
    safe = packets.json_safe(TEXT)
    import json
    json.dumps(safe)                          # serializable
    assert safe["decoded"]["payload"] == {"__bytes_b64__": "TQ=="}
    assert packets.restore(safe) == TEXT


def test_text_envelope():
    kind, env = packets.envelope(TEXT, received_at=1700000001.5)
    assert kind == "text"
    assert env["text"] == "M" and env["fromId"] == "!9e766b18" and env["broadcast"] is False
    assert env["rxSnr"] == 5.5 and env["hopLimit"] == 3 and env["channel"] == 0
    assert packets.packet_from_envelope(env) == TEXT


def test_text_without_decoded_text_field_decodes_payload():
    p = dict(TEXT); p["decoded"] = {"portnum": "TEXT_MESSAGE_APP", "payload": "héllo".encode()}
    _, env = packets.envelope(p, 0)
    assert env["text"] == "héllo"


def test_position_and_broadcast_flag():
    kind, env = packets.envelope(POS, 0)
    assert kind == "position" and env["broadcast"] is True
    assert packets.packet_from_envelope(env)["decoded"]["position"]["satsInView"] == 7


def test_unknown_and_encrypted_kinds():
    assert packets.kind_of({"decoded": {"portnum": "PAXCOUNTER_APP"}}) == "other"
    assert packets.kind_of({"from": 1, "encrypted": b"..."}) == "encrypted"
    kind, env = packets.envelope({"from": 1, "to": 2, "encrypted": b"xx"}, 0)
    assert kind == "encrypted" and env["portnum"] is None and "text" not in env


def test_non_json_objects_stringified():
    class Enum:
        def __str__(self): return "ROUTER"
    assert packets.json_safe({"role": Enum()}) == {"role": "ROUTER"}
