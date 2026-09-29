"""启动自检冒烟测试 (T003/T010)。"""

import sys

from rcs_demo.__main__ import main


def test_check_passes(capsys):
    rc = main(["--check"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out
    assert "layout ok" in out
    assert "planner ok" in out
    assert "reservations ok" in out
    assert "engine ok" in out


def test_check_with_seed():
    assert main(["--check", "--seed", "42"]) == 0
    assert "rcs_demo" not in sys.argv  # sanity: invoked via main()
