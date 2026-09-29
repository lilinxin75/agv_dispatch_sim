"""缩尺快速回归：漫游、零碰撞、均匀分配、循环复用、死锁恢复 (T012/T017/T025/T031/T036)。

与全量共享引擎代码，用 Config.scaled() 缩尺（40×40 / 10 车 / 加速时钟）。
"""

from rcs_demo.config import Config
from rcs_demo.sim import entities as E
from rcs_demo.sim.engine import Engine
from rcs_demo.sim.grid import CellKind


def make_engine(seed=7):
    return Engine(Config.scaled(), seed=seed)


def run(eng, n):
    for _ in range(n):
        eng.step()


def sweep_occupancy(eng):
    seen = set()
    for r in range(eng.grid.size):
        for c in range(eng.grid.size):
            occ = eng.grid.occupant_at(r, c)
            if occ is not None:
                assert occ not in seen, f"duplicate occupant at ({r},{c})"
                seen.add(occ)


# ---------------------------------------------------------------- US1 漫游
def test_wander_moves_vehicles():
    eng = make_engine(11)
    before = {v.id: v.cell for v in eng.vehicles.values()}
    run(eng, 100)
    moved = sum(
        1 for v in eng.vehicles.values() if v.cell != before[v.id]
    )
    assert moved >= len(eng.vehicles) // 2
    for v in eng.vehicles.values():
        assert eng.grid.in_bounds(*v.cell)


# ---------------------------------------------------------------- US3 零碰撞
def test_zero_collision_high_density():
    eng = make_engine(5)
    for i in range(600):
        eng.step()
        if i % 10 == 0:
            sweep_occupancy(eng)
    assert eng.collision_count == 0


def test_intersection_single_winner():
    """两车同时瞄准同一空格：单 tick 内至多一车进入 (T029 确定性场景)。"""
    eng = make_engine(9)
    v1, v2 = eng.vehicles[1], eng.vehicles[2]
    # 摆位：(5,5) 与 (5,7)，共同目标 (5,6)
    eng.grid.set_occupant(*v1.cell, None)
    eng.grid.set_occupant(*v2.cell, None)
    v1.cell = (5, 5); v2.cell = (5, 7)
    eng.grid.set_occupant(5, 5, 1); eng.grid.set_occupant(5, 7, 2)
    v1.task_id = None; v2.task_id = None
    v1.path = [(5, 6)]; v2.path = [(5, 6)]
    v1.target = (5, 6); v2.target = (5, 6)
    eng.step()
    occupants = sum(
        1 for t in [eng.grid.occupant_at(5, 6)] if t is not None
    )
    assert occupants == 1  # 至多一车进入
    movers = [v for v in (v1, v2) if v.cell == (5, 6)]
    assert len(movers) == 1
    other = v1 if len(movers) == 1 and v1.cell != (5, 6) else v2
    assert other.state == E.V_WAITING


# ---------------------------------------------------------------- US2 均匀分配
def test_task_pairing_one_to_one():
    eng = make_engine(13)
    run(eng, 800)
    gen = eng.stats["tasks_generated"]
    assert gen["inbound"] == gen["outbound"], "FR-018: 1:1 成对生成"
    assert gen["inbound"] > 5, "缩尺运行应有可观测的任务生成量"


def test_fair_assignment():
    eng = make_engine(17)
    run(eng, 1500)
    totals = [v.assigned_total for v in eng.vehicles.values()]
    assert sum(totals) > 20, "应有一定任务量"
    mean = sum(totals) / len(totals)
    spread = max(totals) - min(totals)
    assert spread <= max(1, 0.2 * mean), (
        f"分配偏差 {spread} 超过 ±20% (mean={mean})"
    )


def test_tote_circulation():
    eng = make_engine(19)
    run(eng, 1500)
    assert eng.stats["tasks_done"]["outbound"] > 0, "应发生过出库"
    # 出库料箱循环回入库口 (FR-018)：引擎累计循环计数
    assert eng.stats["totes_recycled"] > 0, "出库后的料箱应循环回入库口"


def test_slot_single_tote():
    eng = make_engine(23)
    run(eng, 600)
    per_cell = {}
    for t in eng.totes.values():
        if t.state in (E.T_DOCKED, E.T_CYCLING):
            per_cell[t.location] = per_cell.get(t.location, 0) + 1
    assert all(n == 1 for n in per_cell.values()), "一格至多一箱"


# ---------------------------------------------------------------- US4 障碍物
def test_replan_avoids_new_obstacle():
    eng = make_engine(29)
    run(eng, 20)
    target = None
    for v in eng.vehicles.values():
        if len(v.path) >= 4:
            cand = v.path[2]
            if (
                eng.grid.is_passable(*cand)
                and eng.grid.occupant_at(*cand) is None
                and eng.grid.kind_at(*cand) == CellKind.FLOOR
            ):
                target = (v, cand)
                break
    assert target is not None, "应存在可用于验证的路径"
    v, cell = target
    assert eng.add_obstacle(*cell) is True
    run(eng, 1)
    assert cell not in v.path, "路径应立即绕开新障碍物"
    run(eng, 100)
    assert eng.grid.occupant_at(*cell) is None, "任何车不得驶入障碍物格"
    assert eng.collision_count == 0


def test_unblock_all_returns_tasks_to_queue():
    eng = make_engine(31)
    eng.add_obstacle(2, 2)
    task = next(iter(eng.tasks.values())) if eng.tasks else None
    if task is None:
        run(eng, 10)
        task = next(iter(eng.tasks.values()))
    eng.dispatcher._block(eng, task, eng.vehicles[1], "test")
    assert task.id in eng.blocked
    eng.remove_obstacle(2, 2)
    assert task.id not in eng.blocked
    assert task.state == E.K_QUEUED


# ---------------------------------------------------------------- US5 死锁
def test_head_on_resolves_without_permanent_stall():
    """对向会车：经让路/重规划后恢复移动，无永久卡死 (T036)。

    隔离任务生成（拉长 task_interval、清空队列），只考察移动层死锁。
    """
    eng = make_engine(37)
    eng.cfg.task_interval = 10 ** 9  # 关闭任务生成
    eng.task_queue = []
    v1, v2 = eng.vehicles[1], eng.vehicles[2]
    eng.grid.set_occupant(*v1.cell, None)
    eng.grid.set_occupant(*v2.cell, None)
    v1.cell = (5, 5); v2.cell = (5, 6)
    eng.grid.set_occupant(5, 5, 1); eng.grid.set_occupant(5, 6, 2)
    for v, tgt, pth in [
        (v1, (5, 8), [(5, 6), (5, 7), (5, 8)]),
        (v2, (5, 3), [(5, 5), (5, 4), (5, 3)]),
    ]:
        v.task_id = None
        v.target = tgt
        v.path = list(pth)
        v.stall_ticks = 0
    thr = eng.cfg.stall_threshold
    run(eng, 3)  # 少于级联让路阈值：双方应仍处于互相等待
    assert v1.state == E.V_WAITING and v2.state == E.V_WAITING, (
        "对向会车应先互相等待（路径重合→等待）"
    )
    assert v1.task_id is None and v2.task_id is None  # 隔离有效性
    # 级联让路/重规划后应在有限时间内解开
    resolved = False
    for _ in range(thr * 2):
        eng.step()
        if v1.cell != (5, 5) or v2.cell != (5, 6):
            resolved = True
            break
    assert resolved, "死锁恢复机制应在有限时间内解开对向会车"
    assert eng.collision_count == 0


def test_enclosed_vehicle_recovers_after_unblock():
    """围堵→解除：车辆重获移动能力 (T036 场景 2)。"""
    eng = make_engine(41)
    v = eng.vehicles[1]
    # 先把 v 摆到 (5,5)
    eng.grid.set_occupant(*v.cell, None)
    assert eng.grid.occupant_at(5, 5) is None or True
    eng.grid.set_occupant(5, 5, None)
    v.cell = (5, 5)
    eng.grid.set_occupant(5, 5, 1)
    # 把 v 围在 (5,5)，四周全部障碍
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if (dr, dc) == (0, 0):
                continue
            eng.add_obstacle(5 + dr, 5 + dc)
    # v 当前漫游路径可能已存在，清除后其无法获得新路径
    v.path = []
    v.target = None
    run(eng, 60)
    assert v.cell == (5, 5), "被围堵的车辆应停在原地"
    # 解除一侧围堵
    eng.remove_obstacle(5, 6)
    run(eng, 120)
    assert v.cell != (5, 5), "解除围堵后车辆应恢复移动"


# ------------------------------------------------- Phase 9 / FR-015 带箱避让
def test_carrying_path_avoids_docked_totes():
    """携带料箱的车辆：路径与所在格均不得含静置料箱格 (FR-015)。"""
    eng = make_engine(7)
    run(eng, 200)
    docked = {
        t.location for t in eng.totes.values() if t.state == E.T_DOCKED
    }
    for v in eng.vehicles.values():
        carrying = (
            v.task_id is not None
            and eng.tasks[v.task_id].state == E.K_IN_PROGRESS
        )
        if not carrying:
            continue
        assert v.cell not in docked, f"carrying vehicle {v.id} on tote cell"
        for cell in v.path:
            assert cell not in docked, (
                f"carrying vehicle {v.id} path crosses tote cell {cell}"
            )


def test_unladen_path_may_cross_totes():
    """无载车可驶入静置料箱格（取货必经语义）；带箱车同格规划被禁止。

    astar avoid 语义：avoid 集合格既不可作为途经点也不可作为目标。
    """
    from rcs_demo.sim.planner import astar
    eng = make_engine(3)
    tote_cell = next(
        t.location for t in eng.totes.values()
        if t.state == E.T_DOCKED and eng.grid.kind_at(*t.location) == CellKind.SLOT
    )
    r, c = tote_cell
    start = (r, c - 1)  # 相邻通道格
    assert eng.grid.is_passable(*start)
    path = astar(eng.grid, start, tote_cell)
    assert path is not None and path[-1] == tote_cell  # 无载：可驶入料箱格
    assert astar(eng.grid, start, tote_cell, avoid={tote_cell}) is None
    # 带箱：不可途经也不可以料箱格为目标


def test_carrying_vehicle_never_enters_tote_cell():
    """运行中任意时刻，带箱车不得位于静置料箱格 (移动层强制)。"""
    eng = make_engine(21)
    for _ in range(300):
        eng.step()
        docked = {
            t.location for t in eng.totes.values() if t.state == E.T_DOCKED
        }
        for v in eng.vehicles.values():
            carrying = (
                v.task_id is not None
                and eng.tasks[v.task_id].state == E.K_IN_PROGRESS
            )
            if carrying:
                assert v.cell not in docked, (
                    f"carrying vehicle {v.id} entered tote cell {v.cell}"
                )
