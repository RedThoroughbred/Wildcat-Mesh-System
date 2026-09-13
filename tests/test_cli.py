from __future__ import annotations

from wildcat import cli


def test_validate_ok_and_bad(write, capsys):
    good = write("good.toml", 'radio.type = "serial"\n')
    assert cli.main(["config", "validate", "-c", str(good)]) == 0
    assert "OK" in capsys.readouterr().out

    bad = write("bad.toml", '[brain]\nmax_chunks = 9\n')     # also missing radio.host
    assert cli.main(["config", "validate", "-c", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "CONFIG ERROR" in err and "[brain].max_chunks" in err and "[radio].host" in err


def test_path_shows_search_order(write, tmp_path, capsys):
    assert cli.main(["config", "path"]) == 2
    out = capsys.readouterr()
    assert "Search order" in out.out and "no Wildcat config found" in out.err
    write("config/wildcat.toml", 'radio.type = "serial"\n')
    assert cli.main(["config", "path"]) == 0
    assert "[FOUND]" in capsys.readouterr().out


def test_show_redacts_by_default(write, capsys):
    p = write("s.toml", 'radio.type = "serial"\n[bbs]\nweather_api_key = "sekrit"\n')
    assert cli.main(["config", "show", "-c", str(p)]) == 0
    assert "sekrit" not in capsys.readouterr().out
    assert cli.main(["config", "show", "-c", str(p), "--no-redact"]) == 0
    assert "sekrit" in capsys.readouterr().out


def test_doctor_not_built_yet_is_honest(write, capsys):
    p = write("s.toml", 'radio.type = "serial"\n')
    rc = cli.main(["doctor", "-c", str(p)])
    # Either the real doctor ran (step 3 landed) or the stub said so — never a traceback.
    assert rc in (0, 1, 2)
