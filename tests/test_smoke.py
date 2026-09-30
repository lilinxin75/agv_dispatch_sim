"""启动自检（章程 V / quickstart.md / T010）。"""
import rcs_demo.__main__ as main_mod


def test_check_selftest_passes(capsys):
    # --check 等价自检：布局、A* 抽检、预约互斥、端口绑定（port 0 = 临时端口）
    assert main_mod._check(port=0) == 0
    out = capsys.readouterr().out
    assert "OK: grid layout / planner / engine self-test passed" in out
