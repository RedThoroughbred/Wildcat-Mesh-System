"""wildcat doctor — every check with injected probes; no hardware, no systemd."""
from __future__ import annotations

import os
import socket
import sqlite3
import threading
import time

import pytest

from wildcat import cli, doctor
from wildcat.config import build
from wildcat.doctor import FAIL, OK, SKIP, WARN, Report

SERIAL = {"radio": {"type": "serial"}}


def statuses(r: Report, name: str):
    return [(c.status, c.detail, c.hint) for c in r.checks if c.name == name]


# ---- real TCP probe on loopback -------------------------------------------------

@pytest.fixture
def listener():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(5)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = srv.accept(); c.close()
            except socket.timeout:
                pass
    t = threading.Thread(target=accept, daemon=True); t.start()
    yield port
    stop.set(); t.join(); srv.close()


def test_probe_tcp_real(listener):
    ok, err = doctor.probe_tcp("127.0.0.1", listener, 1.0)
    assert ok and err == ""
    ok, err = doctor.probe_tcp("127.0.0.1", 1, 0.5)      # nothing listens on tcp/1
    assert not ok and err


def test_scan_lan_refuses_loopback_and_public():
    assert doctor.scan_lan("127.0.0.1", 4403) == []
    assert doctor.scan_lan("8.8.8.8", 4403) == []
    assert doctor.scan_lan("not-an-ip", 4403) == []


# ---- radio ------------------------------------------------------------------------

def test_radio_tcp_ok(listener):
    cfg = build({"radio": {"type": "tcp", "host": "127.0.0.1", "tcp_port": listener}})
    r = Report(); doctor.check_radio(r, cfg, resolve=lambda h: h, scan=lambda ip, p: [])
    assert statuses(r, "radio")[0][0] == OK


def test_radio_tcp_down_with_scan_hit():
    cfg = build({"radio": {"type": "tcp", "host": "192.168.86.37"}})
    r = Report()
    doctor.check_radio(r, cfg, probe=lambda h, p, t: (False, "Connection refused"),
                       resolve=lambda h: h, scan=lambda ip, p: ["192.168.86.40"])
    st, detail, hint = statuses(r, "radio")[0]
    assert st == FAIL and "Connection refused" in detail
    assert "192.168.86.40" in hint and "[radio].host" in hint


def test_radio_tcp_down_no_scan_hit_mentions_single_client():
    cfg = build({"radio": {"type": "tcp", "host": "192.168.86.37"}})
    r = Report()
    doctor.check_radio(r, cfg, probe=lambda h, p, t: (False, "timed out"), resolve=lambda h: h, scan=lambda ip, p: [])
    st, _, hint = statuses(r, "radio")[0]
    assert st == FAIL and "ONE API client" in hint


def test_radio_tcp_unresolvable_and_no_network():
    cfg = build({"radio": {"type": "tcp", "host": "nope.local"}})
    r = Report(); doctor.check_radio(r, cfg, resolve=lambda h: None)
    assert statuses(r, "radio")[0][0] == FAIL
    r = Report(); doctor.check_radio(r, cfg, network=False)
    assert statuses(r, "radio")[0][0] == SKIP


def test_radio_serial_autodetect(tmp_path):
    cfg = build(SERIAL)
    r = Report(); doctor.check_radio(r, cfg, ports=lambda: [("/dev/ttyUSB0", "CP210x")])
    assert statuses(r, "radio")[0] [0] == OK
    r = Report(); doctor.check_radio(r, cfg, ports=lambda: [])
    assert statuses(r, "radio")[0][0] == FAIL
    r = Report(); doctor.check_radio(r, cfg, ports=lambda: [("/dev/ttyUSB0", ""), ("/dev/ttyACM0", "")])
    st, _, hint = statuses(r, "radio")[0]
    assert st == FAIL and "[radio].port" in hint


def test_radio_serial_explicit_port(tmp_path):
    dev = tmp_path / "ttyUSB0"; dev.write_text("")
    cfg = build({"radio": {"type": "serial", "port": str(dev)}})
    r = Report(); doctor.check_radio(r, cfg)
    assert statuses(r, "radio")[0][0] == OK
    cfg = build({"radio": {"type": "serial", "port": str(tmp_path / "missing")}})
    r = Report(); doctor.check_radio(r, cfg, ports=lambda: [(str(dev), "")])
    st, detail, hint = statuses(r, "radio")[0]
    assert st == FAIL and "does not exist" in detail and str(dev) in hint


# ---- mqtt ---------------------------------------------------------------------------

def test_mqtt_skipped_when_disabled_and_probed_when_enabled():
    r = Report(); doctor.check_mqtt(r, build(SERIAL))
    assert statuses(r, "mqtt")[0][0] == SKIP
    cfg = build({**SERIAL, "mqtt": {"enabled": True}, "bbs": {"source": "bus"}, "telemetry": {"source": "bus"}})
    r = Report(); doctor.check_mqtt(r, cfg, probe=lambda h, p, t: (True, ""))
    assert statuses(r, "mqtt")[0][0] == OK
    r = Report(); doctor.check_mqtt(r, cfg, probe=lambda h, p, t: (False, "refused"))
    st, _, hint = statuses(r, "mqtt")[0]
    assert st == FAIL and "mosquitto" in hint


# ---- deps ---------------------------------------------------------------------------

def test_deps_reports_missing_with_install_hint():
    def importer(mod, dist):
        return (False, "No module named 'flask'") if mod == "flask" else (True, "1.0")
    r = Report(); doctor.check_deps(r, build(SERIAL), importer=importer)
    st = statuses(r, "deps")
    assert any(s == FAIL and "flask" in d for s, d, _ in st)
    assert any(s == OK and "meshtastic 1.0" in d for s, d, _ in st)
    assert any(s == SKIP and "paho" in d for s, d, _ in st)


def test_deps_includes_paho_when_mqtt_enabled():
    seen = []
    def importer(mod, dist):
        seen.append(dist); return True, "x"
    doctor.check_deps(Report(), build({**SERIAL, "mqtt": {"enabled": True}, "bbs": {"source": "bus"}, "telemetry": {"source": "bus"}}), importer=importer)
    assert "paho-mqtt" in seen


# ---- database ------------------------------------------------------------------------

def test_database_missing_file_is_warn_when_dir_writable(tmp_path):
    cfg = build({**SERIAL, "database": {"path": str(tmp_path / "x.db")}})
    r = Report(); doctor.check_database(r, cfg)
    assert statuses(r, "database")[0][0] == WARN


def test_database_missing_dir_under_writable_ancestor_is_warn(tmp_path):
    # db_operations mkdirs the parent on first start, so this is not a failure
    cfg = build({**SERIAL, "database": {"path": str(tmp_path / "nope" / "x.db")}})
    r = Report(); doctor.check_database(r, cfg)
    st, detail, _ = statuses(r, "database")[0]
    assert st == WARN and "first BBS start" in detail


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_database_missing_dir_under_unwritable_ancestor_is_fail(tmp_path):
    ro = tmp_path / "ro"; ro.mkdir(); ro.chmod(0o500)
    try:
        cfg = build({**SERIAL, "database": {"path": str(ro / "sub" / "x.db")}})
        r = Report(); doctor.check_database(r, cfg)
        st, _, hint = statuses(r, "database")[0]
        assert st == FAIL and "mkdir" in hint
    finally:
        ro.chmod(0o700)


def test_database_healthy_with_recent_packet(tmp_path):
    db = tmp_path / "b.db"
    conn = sqlite3.connect(db)
    for t in doctor.BBS_TABLES + doctor.OBS_TABLES:
        conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, timestamp INTEGER)")
    conn.execute("INSERT INTO message_logs (timestamp) VALUES (?)", (int(time.time()) - 120,))
    conn.commit()
    conn.execute("PRAGMA journal_mode=WAL")      # must be outside a transaction or SQLite ignores it
    conn.close()
    cfg = build({**SERIAL, "database": {"path": str(db)}})
    r = Report(); doctor.check_database(r, cfg)
    st = statuses(r, "database")
    assert st[0][0] == OK and "journal_mode=wal" in st[0][1]
    assert not any(s == WARN for s, _, _ in st)
    assert any("last packet logged 2m" in d for _, d, _ in st)


def test_database_warns_on_missing_tables_and_non_wal(tmp_path):
    db = tmp_path / "b.db"
    sqlite3.connect(db).execute("CREATE TABLE bulletins (id)").connection.commit()
    cfg = build({**SERIAL, "database": {"path": str(db)}})
    r = Report(); doctor.check_database(r, cfg)
    st = statuses(r, "database")
    assert st[0][0] == OK
    assert any("switches it to WAL" in d for _, d, _ in st)
    assert any("tables not created yet" in d and "message_logs" in d for _, d, _ in st)


# ---- content -----------------------------------------------------------------------------

def test_content_real_repo_dir(repo_root):
    cfg = build({**SERIAL, "bbs": {"content_dir": str(repo_root / "bbs")}})
    r = Report(); doctor.check_content(r, cfg)
    st = statuses(r, "content")
    assert st[0][0] == OK and "messages.json" in st[0][1]
    assert any(s == WARN and "fortunes.txt" in d for s, d, _ in st)   # git-ignored, so absent here


# ---- systemd -------------------------------------------------------------------------------

def _units(**over):
    def show(unit):
        if unit in over:
            return over[unit]
        return {"LoadState": "not-found", "ActiveState": "inactive", "UnitFileState": ""}
    return show


def test_systemd_absent_is_skip():
    r = Report(); doctor.check_systemd(r, show=lambda u: {})
    assert statuses(r, "systemd")[0][0] == SKIP


def test_systemd_legacy_only_is_fine():
    r = Report()
    doctor.check_systemd(r, show=_units(**{"mesh-bbs.service": {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled"}}))
    st = statuses(r, "systemd")
    assert any(s == WARN and "no wildcat-* units" in d for s, d, _ in st)
    assert any(s == OK and "legacy mesh-bbs" in d for s, d, _ in st)


def test_systemd_legacy_alongside_v2_is_fail():
    r = Report()
    doctor.check_systemd(r, show=_units(**{
        "wildcat-bbs.service": {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled"},
        "wildcat-telemetry.service": {"LoadState": "loaded", "ActiveState": "failed", "UnitFileState": "enabled"},
        "mesh-bbs.service": {"LoadState": "loaded", "ActiveState": "inactive", "UnitFileState": "enabled"},
    }))
    st = statuses(r, "systemd")
    assert any(s == OK and "wildcat-bbs.service active" in d for s, d, _ in st)
    assert any(s == FAIL and "wildcat-telemetry.service FAILED" in d for s, d, _ in st)
    assert any(s == FAIL and "legacy mesh-bbs" in d and "disable --now" in h for s, d, h in st)


# ---- end to end ------------------------------------------------------------------------------

def test_cli_doctor_exit_codes(write, capsys):
    good = write("good.toml", 'radio.type = "serial"\n')
    rc = cli.main(["doctor", "-c", str(good), "--no-network", "--no-color"])
    out = capsys.readouterr().out
    assert "wildcat doctor" in out and "config" in out
    assert rc in (0, 1)          # radio/serial autodetect depends on this machine's USB devices
    bad = write("bad.toml", '[radio]\ntype = "tcp"\n')
    assert cli.main(["doctor", "-c", str(bad), "--no-network"]) == 2
    assert "cannot load" in capsys.readouterr().out


def test_render_shape():
    r = Report(); r.add("a", OK, "fine"); r.add("b", FAIL, "bad", "do this\nthen that")
    text = r.render()
    assert "✔ a" in text and "✖ b" in text and "→ do this" in text and "→ then that" in text
    assert text.endswith("1 ok · 0 warn · 1 fail · 0 skipped")
