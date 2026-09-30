"""缩尺测试夹具（research.md R8：缩尺 40×40 / 10 车 / 与全量共享引擎代码）。"""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rcs_demo.config as cfg  # noqa: E402
import rcs_demo.sim.engine as engine_mod  # noqa: E402
from rcs_demo.sim.engine import Engine  # noqa: E402


@pytest.fixture
def small_engine(tmp_path, monkeypatch):
    """缩尺引擎：40×40、10 车、4 充电位、加速时钟逻辑。"""
    monkeypatch.setattr(cfg, "GRID", 40)
    monkeypatch.setattr(cfg, "SHELF_ROW_RANGE", (10, 29))
    monkeypatch.setattr(cfg, "SHELF_COL_RANGE", (10, 29))
    monkeypatch.setattr(cfg, "INBOUND_ROW_RANGE", (14, 23))
    monkeypatch.setattr(cfg, "INBOUND_COL_RANGE", (0, 4))
    monkeypatch.setattr(cfg, "OUTBOUND_ROW_RANGE", (14, 23))
    monkeypatch.setattr(cfg, "OUTBOUND_COL_RANGE", (35, 39))
    monkeypatch.setattr(cfg, "CHARGE_ROW_RANGE", (32, 35))
    monkeypatch.setattr(cfg, "CHARGE_COL_RANGE", (12, 26))
    monkeypatch.setattr(cfg, "CHARGE_PILES", 6)
    monkeypatch.setattr(cfg, "VEHICLES", 10)
    monkeypatch.setattr(cfg, "STALL_TICKS", 50)
    monkeypatch.setattr(cfg, "TASKS_PER_SECOND", 1)  # 与规格核算基准一致（1 任务/秒）
    monkeypatch.setattr(engine_mod, "INBOUND_POOL_SIZE", 6)
    e = Engine(seed=7, conflict_log_dir=str(tmp_path))
    yield e
    e.close()
