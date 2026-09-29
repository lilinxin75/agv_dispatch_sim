"""互堵冲突事件检测与记录测试 (Phase 9 / FR-022)。"""

from rcs_demo.config import Config
from rcs_demo.sim.conflicts import find_mutual_blocks
from rcs_demo.sim.engine import Engine
from rcs_demo.sim import entities as E


def isolated_engine(seed=3, vehicle_count=2):
    """缩尺双车引擎：任务生成隔离、关闭拥堵优化，专用于冲突场景构造。"""
    cfg = Config.scaled()
    cfg.vehicle_count = vehicle_count
    cfg.conflict_optimization = False
    eng = Engine(cfg, seed=seed)
    eng.cfg.task_interval = 10 ** 9  # 隔离任务生成
    eng.task_queue = []
    return eng


def place(eng, vid, cell):
    """把车辆瞬移到指定格（清空原占用）。"""
    v = eng.vehicles[vid]
    eng.grid.set_occupant(*v.cell, None)
    eng.grid.set_occupant(*cell, vid)
    v.cell = cell
    v.path = []
    v.target = None
    v.state = E.V_IDLE
    v.stall_ticks = 0


# ------------------------------------------------------------- 纯函数
def test_find_mutual_blocks_cycle():
    assert find_mutual_blocks({1: 2, 2: 1}) == {1, 2}


def test_find_mutual_blocks_three_cycle():
    assert find_mutual_blocks({1: 2, 2: 3, 3: 1}) == {1, 2, 3}


def test_find_mutual_blocks_chain_no_cycle():
    # 排队链（末端无环）不算互堵
    assert find_mutual_blocks({1: 2, 2: 3}) == set()


def test_find_mutual_blocks_empty():
    assert find_mutual_blocks({}) == set()


# ------------------------------------------------------------- 集成：记录
def test_mutual_block_records_event():
    eng = isolated_engine()
    rec = eng.recorder
    # 车1、车2 相向对峙：互为对方路径上的占用者
    place(eng, 1, (5, 10))
    place(eng, 2, (5, 11))
    eng.vehicles[1].path = [(5, 11)]
    eng.vehicles[1].target = (5, 11)
    eng.vehicles[2].path = [(5, 10)]
    eng.vehicles[2].target = (5, 10)

    for _ in range(4):  # 阈值 5 之前，让路机制尚未介入
        eng.step()
        assert eng.vehicles[1].state == E.V_WAITING
        assert eng.vehicles[2].state == E.V_WAITING

    assert rec.open_event is not None, "互堵应开启事件"
    assert rec.open_event["vehicles"] == {1, 2}

    # 继续运行让既有让路机制解堵 → 事件闭合落盘
    for _ in range(20):
        eng.step()
    closed = rec.closed_events
    assert len(closed) >= 1, "解堵后事件应闭合"
    ev = closed[0]
    assert set(v["id"] for v in ev["vehicles"]) == {1, 2}
    assert ev["end_tick"] > ev["start_tick"] >= 1
    assert ev["duration"] >= 4
    assert ev["cells"], "事件必须记录互堵位置"


def test_mutual_block_records_carrying_flag():
    eng = isolated_engine()
    rec = eng.recorder
    place(eng, 1, (5, 10))
    place(eng, 2, (5, 11))
    # 给车2 挂一个进行中任务（携带料箱）
    task = E.Task(id=999, type=E.OUTBOUND, pickup=(5, 11), dropoff=(35, 36),
                  tote_id=1)
    task.state = E.K_IN_PROGRESS
    eng.tasks[999] = task
    eng.vehicles[2].task_id = 999

    eng.vehicles[1].path = [(5, 11)]
    eng.vehicles[1].target = (5, 11)
    eng.vehicles[2].path = [(5, 10)]
    eng.vehicles[2].target = (5, 10)

    for _ in range(4):
        eng.step()
    assert rec.open_event is not None
    carrying = rec.open_event["carrying"]
    assert carrying[1] is False
    assert carrying[2] is True


def test_single_queue_not_recorded():
    """单车排队（阻挡者不在等待）不算互堵事件。"""
    eng = isolated_engine()
    rec = eng.recorder
    place(eng, 1, (5, 10))
    place(eng, 2, (5, 9))
    # 车1 停在原地（无路径、非等待）；车2 想进入车1 的格但该格被他车"预约"
    # 用预约表模拟前方车流：预约者不在等待集合 → 不构成环
    for t in range(eng.tick + 1, eng.tick + 8):
        eng.reservations.claim(5, 10, t, 999)
    eng.vehicles[2].path = [(5, 10)]
    eng.vehicles[2].target = (5, 10)
    # 车1 给一个远离的长路径，使其处于移动态而非等待态
    eng.vehicles[1].path = [(4, 10), (3, 10), (3, 11), (3, 12)]
    eng.vehicles[1].target = (3, 12)

    for _ in range(5):
        eng.step()
    assert rec.open_event is None, "单车排队不应开启互堵事件"
    assert rec.closed_events == []


# ------------------------------------------------- SC-008/SC-009 验收
def test_optimization_reduces_conflicts():
    """同 seed 等长 A/B（3 seed 聚合）：互堵频次与平均时长双降 ≥30%，
    且零碰撞、公平性 ±20%、吞吐不明显回退均不破 (SC-008/SC-009)。

    单 seed 结果对初始条件混沌敏感，故按聚合口径断言。
    """
    ticks = 1500
    seeds = (42, 7, 3)

    def run(opt, seed):
        cfg = Config.scaled()
        cfg.conflict_optimization = opt
        eng = Engine(cfg, seed=seed)
        for _ in range(ticks):
            eng.step()
        evs = eng.recorder.closed_events
        dur = sum(e["duration"] for e in evs)  # 加权聚合用
        return {
            "eng": eng, "events": len(evs), "dur_sum": dur,
            "done": sum(eng.stats["tasks_done"].values()),
        }

    bases = [run(False, s) for s in seeds]
    opts = [run(True, s) for s in seeds]
    assert all(b["eng"].collision_count == 0 for b in bases)
    assert all(o["eng"].collision_count == 0 for o in opts)

    n_ev0 = sum(b["events"] for b in bases)
    n_ev1 = sum(o["events"] for o in opts)
    d0 = sum(b["dur_sum"] for b in bases) / max(1, n_ev0)
    d1 = sum(o["dur_sum"] for o in opts) / max(1, n_ev1)

    # 公平性不回退 (FR-004/SC-003)：按任务分配数口径
    per = [v.assigned_total for v in opts[0]["eng"].vehicles.values()]
    mean = sum(per) / len(per)
    assert max(per) - min(per) <= max(1, 0.2 * mean)

    # 吞吐不明显回退（≥0.9×）
    done0 = sum(b["done"] for b in bases)
    done1 = sum(o["done"] for o in opts)
    assert done1 >= 0.9 * done0, f"吞吐回退: {done0}→{done1}"

    # 双指标 ≥30%（聚合）
    assert n_ev1 <= 0.7 * n_ev0, f"频次降幅不足: {n_ev0}→{n_ev1}"
    assert d1 <= 0.7 * d0, f"时长降幅不足: {d0:.1f}→{d1:.1f} tick"
