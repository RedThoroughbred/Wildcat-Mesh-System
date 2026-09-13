"""deploy/: the systemd units are hardened the way the design doc says, the
installer substitutes every placeholder, and `wildcat db backup` works."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from wildcat import cli

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
UNITS = sorted((DEPLOY / "systemd").glob("wildcat-*.service"))
PLACEHOLDER = re.compile(r"@([A-Z]+)@")


def _keys(text: str):
    """{key: [values]} over all sections (systemd allows repeated keys)."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        k, _, v = line.partition("=")
        out.setdefault(k.strip(), []).append(v.strip())
    return out


@pytest.mark.parametrize("unit", UNITS, ids=[u.name for u in UNITS])
def test_service_units_are_hardened(unit):
    k = _keys(unit.read_text())
    if unit.name == "wildcat-backup.service":
        assert k["Type"] == ["oneshot"]
        return
    assert k["Restart"] == ["on-failure"]
    assert any("network-online.target" in a for a in k["After"])
    assert k["Wants"] == ["network-online.target"]
    assert k["StartLimitIntervalSec"] == ["0"]
    assert k["WorkingDirectory"] == ["@REPO@"]
    assert k["PartOf"] == ["wildcat.target"]
    assert k["WantedBy"] == ["wildcat.target"]
    assert k["RestartPreventExitStatus"] == ["2"], "config errors must not restart-loop"
    assert k["KillSignal"] == ["SIGINT"]
    assert any("wildcat config validate" in v for v in k["ExecStartPre"])
    assert any("WILDCAT_CONFIG=@REPO@/config/wildcat.toml" in v for v in k["Environment"])
    assert "RuntimeMaxSec" not in k, "the legacy self-kill is replaced by exit-on-connection-lost"


def test_target_wants_every_service():
    target = _keys((DEPLOY / "systemd" / "wildcat.target").read_text())
    wanted = " ".join(target["Wants"]).split()
    for u in UNITS:
        if u.name != "wildcat-backup.service":
            assert u.name in wanted, u.name
    assert "wildcat-backup.timer" in wanted
    assert target["WantedBy"] == ["multi-user.target"]


def test_installer_substitutes_every_placeholder():
    script = (DEPLOY / "install.sh").read_text()
    used = set()
    for f in (DEPLOY / "systemd").iterdir():
        used |= set(PLACEHOLDER.findall(f.read_text()))
    assert used == {"REPO", "VENV", "USER"}
    for name in used:
        assert f"s|@{name}@|" in script, f"install.sh does not substitute @{name}@"
    assert "disable --now" in script and "mesh-bbs" in script, "legacy units must be retired"
    assert "systemctl enable wildcat.target" in script


def test_timer_is_persistent():
    k = _keys((DEPLOY / "systemd" / "wildcat-backup.timer").read_text())
    assert k["Persistent"] == ["true"] and k["OnCalendar"]


def test_db_backup_command(write, tmp_path, capsys):
    db = tmp_path / "shared" / "bulletins.db"; db.parent.mkdir()
    c = sqlite3.connect(db); c.execute("CREATE TABLE t (x)"); c.execute("INSERT INTO t VALUES (1)"); c.commit(); c.close()
    cfg = write("c.toml", f'radio.type = "serial"\n[database]\npath = "{db}"\n')
    out = tmp_path / "backups"
    for _ in range(3):
        assert cli.main(["db", "backup", "-c", str(cfg), "--dir", str(out), "--keep", "2"]) == 0
    files = sorted(out.glob("bulletins-*.db"))
    assert len(files) == 2, "prune keeps the newest N"
    assert sqlite3.connect(files[-1]).execute("SELECT x FROM t").fetchone()[0] == 1
    assert "backup ok" in capsys.readouterr().out
    missing = write("m.toml", f'radio.type = "serial"\n[database]\npath = "{tmp_path / "none.db"}"\n')
    assert cli.main(["db", "backup", "-c", str(missing), "--dir", str(out)]) == 1
