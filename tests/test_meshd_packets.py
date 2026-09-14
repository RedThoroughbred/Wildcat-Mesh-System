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
    assert env["text"] == "M" and env["from"] == "!9e766b18" and env["broadcast"] is False
    assert env["rx"]["snr"] == 5.5 and env["hopLimit"] == 3 and env["channel"] == 0
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


# ---- the protocol-neutral fields (docs/OBSERVATORY_V2.md §2c) ------------------------

def test_neutral_text_fields():
    _, env = packets.envelope({**TEXT, "hopStart": 3, "hopLimit": 2}, 0)
    assert env["v"] == 2 and env["proto"] == "meshtastic" and env["kind"] == "text"
    assert env["from"] == "!9e766b18" and env["to"] == "!9e69a9d8" and env["broadcast"] is False
    assert env["rx"] == {"time": 1700000000, "snr": 5.5, "rssi": -86, "hops": 1, "via_mqtt": False}
    assert env["text"] == "M"


def test_neutral_broadcast_to_is_null_and_hops_zero():
    _, env = packets.envelope(POS, 0)
    assert env["to"] is None and env["broadcast"] is True
    assert env["rx"]["hops"] is None                     # no hopStart/hopLimit → unknown
    _, env = packets.envelope({**POS, "hopStart": 3, "hopLimit": 3}, 0)
    assert env["rx"]["hops"] == 0                        # heard directly


def test_neutral_position_telemetry_neighbors_nodeinfo():
    _, env = packets.envelope(POS, 0)
    assert env["position"] == {"lat": 39.0, "lon": -84.6, "alt": 250, "sats": 7, "time": None}
    tel = {"from": 1, "to": 0xFFFFFFFF, "fromId": "!00000001", "decoded": {"portnum": "TELEMETRY_APP", "telemetry": {
        "deviceMetrics": {"batteryLevel": 101, "voltage": 4.32, "channelUtilization": 4.4, "airUtilTx": 0.9, "uptimeSeconds": 12},
        "environmentMetrics": {"temperature": 21.5}}}}
    kind, env = packets.envelope(tel, 0)
    assert kind == "telemetry" and env["telemetry"]["battery"] == 101 and env["telemetry"]["temperature"] == 21.5
    assert env["telemetry"]["humidity"] is None
    ni = {"from": 1, "to": 0xFFFFFFFF, "fromId": "!00000001", "decoded": {"portnum": "NEIGHBORINFO_APP",
          "neighborinfo": {"neighbors": [{"nodeId": 2688059520, "snr": 7.25}, {"nodeId": "junk"}]}}}
    kind, env = packets.envelope(ni, 0)
    assert kind == "neighbors" and env["neighbors"] == [{"id": "!a0388880", "snr": 7.25, "last_heard": None}]
    info = {"from": 2688059520, "to": 0xFFFFFFFF, "decoded": {"portnum": "NODEINFO_APP",
            "user": {"id": "!a0388880", "shortName": "STAY", "longName": "Wildcat Stay", "hwModel": "STATION_G2", "role": "CLIENT_BASE"}}}
    kind, env = packets.envelope(info, 0)
    assert kind == "nodeinfo" and env["from"] == "!a0388880" and env["nodeinfo"]["hw"] == "STATION_G2"


def test_roster_from_interface_nodes():
    nodes = {"!a0388880": {"num": 2688059520, "user": {"id": "!a0388880", "shortName": "STAY", "hwModel": "STATION_G2", "role": "CLIENT_BASE"},
                           "position": {"latitudeI": 388795834, "longitudeI": -846169218, "altitude": 280},
                           "deviceMetrics": {"batteryLevel": 100, "voltage": 4.1}, "lastHeard": 1700000000, "hopsAway": 0, "snr": 7.25},
             "!00000002": {"num": 2, "user": {"id": "!00000002", "shortName": "nopos"}},
             "junk": "not a node"}
    r = packets.roster(nodes)
    assert set(r) == {"!a0388880", "!00000002"}
    stay = r["!a0388880"]
    assert stay["position"] == {"lat": 38.8795834, "lon": -84.6169218, "alt": 280}
    assert stay["battery"] == 100 and stay["hops_away"] == 0 and stay["role"] == "CLIENT_BASE" and stay["proto"] == "meshtastic"
    assert r["!00000002"]["position"] is None
    assert packets.node_id(2688059520) == "!a0388880" and packets.node_id(packets.BROADCAST_NUM) is None


def test_via_mqtt_is_explicit_on_meshtastic_envelopes_and_roster_rows():
    from wildcat.meshd.packets import ENVELOPE_VERSION, envelope, roster
    base = {"from": 111, "to": 4294967295, "fromId": "!0000006f", "channel": 0, "id": 9, "rxTime": 1,
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"hi", "text": "hi"}}
    assert ENVELOPE_VERSION == 2
    _, rf = envelope(dict(base), 1.0)
    assert rf["v"] == 2 and rf["rx"]["via_mqtt"] is False                       # Meshtastic omits a false flag → radio
    _, mq = envelope({**base, "viaMqtt": True, "hopStart": 3, "hopLimit": 3}, 1.0)
    assert mq["rx"]["via_mqtt"] is True and mq["rx"]["hops"] == 0                # hops mean nothing here; the flag says so
    r = roster({"!a": {"num": 10, "user": {"id": "!0000000a", "shortName": "A"}, "viaMqtt": True, "lastHeard": 5},
                "!b": {"num": 11, "user": {"id": "!0000000b", "shortName": "B"}, "lastHeard": 6}})
    assert r["!0000000a"]["via_mqtt"] is True and r["!0000000b"]["via_mqtt"] is False
