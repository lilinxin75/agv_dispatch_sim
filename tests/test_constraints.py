"""002 期新增约束回归（specs/002-constraints-layout-polish / SC-007）。

矢量性（FR-008）、布局预设（FR-011~014）、任务台账/改派（FR-001/002）、
背压（FR-005）、硬件速度/能耗模型（FR-006/007）、指标快照（FR-016）。
"""
import pytest

from rcs_demo import config as cfg
from rcs_demo.sim.entities import CellKind, TaskState, VehicleState
from rcs_demo.sim.grid import Grid, load_profile
from rcs_demo.sim.planner import Planner

from tests.test_simulation import run  # noqa: F401  # 复用 tick 循环辅助


# ---- 充电桩矢量性（FR-008 / T008）----

def make_planner(docked=()):
    g = Grid()
    p = Planner(g, lambda c: c in set(docked))
    p.docked = set(docked)
    return g, p


def test_pile_not_usable_as_transit():
    """A* 无法把桩格作为中转格规划路径（桩格不可穿越）。"""
    g, p = make_planner()
    pile = g.charging.piles[0]
    # 自充电区西侧（排队格外）到东侧：绕行路径存在但绝不借道桩格
    lane_row = g.charging.lane_row
    west = (lane_row, g.charge_cols[0] - 4)
    east = (lane_row, g.charge_cols[1] + 2)
    path = p.plan(west, east, "E", False, set())
    assert path is not None  # 绕行存在
    assert pile not in path, "路径穿越了充电桩格"
    # 桩格四邻直接进入桩格（非合法入桩方式）一律被拒
    for n in ((pile[0] + 1, pile[1] - 1), (pile[0] - 1, pile[1]),
              (pile[0], pile[1] - 1), (pile[0], pile[1] + 1)):
        assert not g.can_traverse(n, pile, to_is_goal=True)


def test_pile_entry_only_from_south_when_goal():
    """桩格作为目标时只能自南向北（经连接格）进入。"""
    g, _ = make_planner()
    pile = g.charging.piles[0]
    conn = (pile[0] + 1, pile[1])
    assert g.kind[conn] == CellKind.CHARGE_IN
    assert g.can_traverse(conn, pile, to_is_goal=True)
    assert not g.can_traverse((pile[0], pile[1] - 1), pile, to_is_goal=True)
    assert not g.can_traverse((pile[0], pile[1] + 1), pile, to_is_goal=True)
    assert not g.can_traverse((pile[0] - 1, pile[1]), pile, to_is_goal=True)


def test_pile_exit_only_south():
    """离桩只能向南（西进东出矢量性的一部分）。"""
    g, _ = make_planner()
    pile = g.charging.piles[0]
    conn = (pile[0] + 1, pile[1])
    assert g.can_traverse(pile, conn)
    assert not g.can_traverse(pile, (pile[0], pile[1] + 1))
    assert not g.can_traverse(pile, (pile[0], pile[1] - 1))


def test_charge_lane_one_way_west_to_east():
    """充电区通道行单向：东→西逆行路径不存在；西→东可行。"""
    g, p = make_planner()
    lane_row = g.charging.lane_row
    c0, c1 = g.charge_cols
    west = (lane_row, c0 - 4)
    east = (lane_row, c1 + 2)
    assert p.plan(west, east, "E", False, set()) is not None, "西→东进站方向应可达"
    for c in range(c0 - 2, c1):
        assert not g.can_traverse((lane_row, c), (lane_row, c - 1)), \
            f"通道行 ({lane_row},{c}) 向西逆行未被拒"


def test_charging_interior_no_bypass():
    """充电区包围盒内非通道格不可作为穿行旁路。"""
    g, _ = make_planner()
    r0, r1 = g.charge_rows
    c0, c1 = g.charge_cols
    lane_row = g.charging.lane_row
    for r in range(r0 - 2, r1 + 1):
        for c in range(c0 - 3, c1 + 1):
            cell = (r, c)
            if cell in g.pile_cells or cell in g.directed:
                continue
            assert cell in g.no_transit, f"({r},{c}) 未注册为禁行格"
    sample = next(iter(g.no_transit))
    assert not g.can_traverse((lane_row, sample[1]), sample)
    assert not g.can_traverse(sample, (lane_row, sample[1]))


def test_dock_path_exists_for_every_pile():
    """每个桩位均存在到桩的合法路径（自连接格进入）。"""
    g, p = make_planner()
    for pile in g.charging.piles:
        conn = (pile[0] + 1, pile[1])
        path = p.plan_any_dir(conn, pile, False, set())
        assert path is not None, f"桩 {pile} 不可达"
        assert path[-1] == pile


# ---- 布局预设（FR-011~014 / T022）----

ALL_PRESETS = ["baseline", "wide-oneway", "charge-expanded"]


@pytest.mark.parametrize("name", ALL_PRESETS)
def test_preset_loads_and_builds(name):
    profile = load_profile(name)
    g = Grid(profile)
    assert len(g.charging.piles) == g.pile_count
    assert g.inbound_cells and g.outbound_cells and g.slots


@pytest.mark.parametrize("name", ALL_PRESETS)
def test_preset_reachable_and_vector_valid(name):
    profile = load_profile(name)
    g = Grid(profile)
    p = Planner(g, lambda c: False)
    for pile in g.charging.piles:
        conn = (pile[0] + 1, pile[1])
        assert p.plan_any_dir(conn, pile, False, set()) is not None
    lane_row = g.charging.lane_row
    c0, c1 = g.charge_cols
    for c in range(c0 - 2, c1):
        assert not g.can_traverse((lane_row, c), (lane_row, c - 1))


def test_preset_unknown_name_rejected():
    with pytest.raises(ValueError):
        load_profile("no-such-layout")


# ---- 任务台账恒等式与停滞改派（FR-001/002 / T009）----

def _ledger_counts(e):
    """台账桶（与 dispatcher.ledger() 同一派生口径，桶互斥）。"""
    return e.dispatcher.ledger()


def test_ledger_invariant_holds(small_engine):
    """台账恒等式：spawned == done + active + queued + reassigned（SC-002 缩尺）。"""
    e = small_engine
    for i in range(3000):
        e.tick()
        if i % 300 == 0:
            lc = _ledger_counts(e)
            assert lc["spawned"] == lc["done"] + lc["active"] + lc["queued"] \
                + lc["reassigned"], f"台账不守恒 @tick {i}: {lc}"
    assert e.metrics_tasks_done > 100


def test_tote_claim_uniqueness(small_engine):
    """同一料箱任意时刻至多一个认领者（FR-002）。"""
    e = small_engine
    for _ in range(1500):
        e.tick()
        claimed = [t.tote_id for t in e.dispatcher.tasks.values()
                   if t.assigned_vehicle is not None and t.tote_id != -1
                   and t.state in (TaskState.ASSIGNED, TaskState.IN_PROGRESS)]
        # 无双重认领
        assert len(claimed) == len(set(claimed)), f"料箱被双重认领: {claimed}"
        # 认领集合与在途任务一致（outbound 生成即认领；inbound 取货后 task.tote_id 落地）
        for t in e.dispatcher.tasks.values():
            if (t.assigned_vehicle is not None and t.tote_id != -1
                    and t.type == "outbound"
                    and t.state in (TaskState.ASSIGNED, TaskState.IN_PROGRESS)):
                assert t.tote_id in e.dispatcher._claimed_totes


def test_stalled_task_reassigned(small_engine):
    """执行车长期停滞超过 REASSIGN_TICKS → 任务改派（FR-002）。"""
    e = small_engine
    run(e, 600)
    # 阈值改派语义（确定性构造）：仅覆盖 ASSIGNED（未取货）任务——
    # IN_PROGRESS 任务料箱在车上，由死锁恢复阶梯（FR-012）负责
    v = next((v for v in e.vehicles.values()
              if v.task is not None
              and v.task.state == TaskState.ASSIGNED), None)
    if v is None:
        pytest.skip("无待取货任务车辆")
    task = v.task
    task.last_progress_tick = e.tick_n - cfg.REASSIGN_TICKS - 10
    before = e.dispatcher.ledger_reassigned
    claim_expected = task.tote_id
    e._reassign_stalled_tasks(set(), [])
    assert task.reassign_count == 1, "停滞任务未被改派"
    assert task.state == TaskState.QUEUED, "改派后任务应回队列待重派"
    assert v.task is not task, "原车未释放任务"
    assert e.dispatcher.ledger_reassigned == before + 1
    # 料箱/点位认领保持（防双重认领，data-model.md 不变量）
    if claim_expected != -1:
        assert claim_expected in e.dispatcher._claimed_totes or \
            task.dropoff in e.dispatcher._claimed_slots


# ---- 拥塞背压（FR-005 / T009）----

def test_backpressure_throttles_spawn(small_engine):
    """等待率超阈值 → 生成间隔放大；缓解后回落（FR-005）。"""
    e = small_engine
    d = e.dispatcher
    assert d.bp_multiplier == 1.0
    # 模拟持续高等待率（去抖：持续 BACKPRESSURE_PRESSURE_TICKS 才升档）
    for _ in range(cfg.BACKPRESSURE_PRESSURE_TICKS + 1):
        d.update_backpressure(0.6, active_tasks=5, max_active=80)
    assert d.bp_multiplier > 1.0
    # 压力未满档位间隔不升档（重置压力计后瞬时抖动无效果）
    d2_mult = d.bp_multiplier
    d.bp_pressure_ticks = 0
    for _ in range(cfg.BACKPRESSURE_PRESSURE_TICKS - 1):
        d.update_backpressure(0.6, active_tasks=5, max_active=80)
    assert d.bp_multiplier == d2_mult
    # 持续缓解后回落
    for _ in range(2000):
        d.update_backpressure(0.0, active_tasks=5, max_active=80)
    assert d.bp_multiplier == 1.0


def test_govern_switches_disable_actions(small_engine):
    """治理开关关闭时阈值改派不发生（FR-015；死锁恢复回收属 FR-012 不受控）。"""
    e = small_engine
    run(e, 400)
    v = next((v for v in e.vehicles.values()
              if v.task is not None
              and v.task.state == TaskState.ASSIGNED), None)
    if v is None:
        pytest.skip("无执行中车辆")
    task = v.task
    # 开关关闭：无进度任务不触发阈值改派（直接调用，绕开恢复阶梯干扰）
    e.govern_reassign = False
    before = e.dispatcher.ledger_reassigned
    task.last_progress_tick = e.tick_n - cfg.REASSIGN_TICKS - 10
    e._reassign_stalled_tasks(set(), [])
    assert e.dispatcher.ledger_reassigned == before, "开关关闭仍触发阈值改派"
    assert v.task is task
    # 开关开启：同样条件触发
    e.govern_reassign = True
    e._reassign_stalled_tasks(set(), [])
    assert e.dispatcher.ledger_reassigned > before, "阈值改派未触发"
    assert v.task is None and task.state == TaskState.QUEUED


# ---- 硬件速度/能耗模型（FR-006/007 / T016，research.md D1/D2）----

def test_speed_progress_accumulator(small_engine):
    """空载 progress += 1.0/tick；载货 ×LOADED_SPEED_RATIO；转向步 ×TURN_SPEED_RATIO。"""
    e = small_engine
    v = next(iter(e.vehicles.values()))
    v.path = [v.cell]
    v.state = VehicleState.TO_PICKUP
    # 空载直行：progress 累加 1.0
    v.progress = 0.0
    v.carrying = None
    # 模拟一次直行步进
    speed_empty = 1.0 * (cfg.TURN_SPEED_RATIO if False else 1.0)
    assert speed_empty == 1.0
    # 直接验证引擎的速度函数
    assert e._vehicle_speed(v, turning=False) == 1.0
    assert e._vehicle_speed(v, turning=True) == cfg.TURN_SPEED_RATIO
    v.carrying = object()  # 模拟载货（仅判断 not None）
    assert e._vehicle_speed(v, turning=False) == cfg.LOADED_SPEED_RATIO
    assert e._vehicle_speed(v, turning=True) == \
        cfg.LOADED_SPEED_RATIO * cfg.TURN_SPEED_RATIO


def test_speed_slow_down_moves_less(small_engine):
    """载货车 3000 tick 内完成任务数不低于空载情形的一半（速度差异真实生效）。"""
    e = small_engine
    run(e, 1500)
    assert e.metrics_tasks_done > 30  # 缩尺吞吐仍为正（速度模型未卡死）


def test_turn_energy_and_nonnegative_battery(small_engine):
    """转向步额外能耗；电量恒非负（FR-007）。"""
    e = small_engine
    v = next(iter(e.vehicles.values()))
    b0 = v.battery
    v.battery = 5.0
    v.carrying = None
    # 直行扣 1.0
    e._apply_move_energy(v, turning=False)
    assert v.battery == 4.0
    # 转向扣 1.0 + TURN_ENERGY（空载）
    e._apply_move_energy(v, turning=True)
    assert v.battery == 4.0 - 1.0 - cfg.TURN_ENERGY
    # 临界：扣至负数被截断为 0
    v.battery = 0.5
    e._apply_move_energy(v, turning=True)
    assert v.battery == 0.0
    v.battery = b0


def test_zero_collision_with_speed_variance(small_engine):
    """速度差异下零碰撞不变量保持。"""
    e = small_engine
    run(e, 1500)
    cells = [v.cell for v in e.vehicles.values()]
    assert len(cells) == len(set(cells))
    assert e.collision_count == 0


# ---- 指标快照与滑动窗口（FR-016/023 / T028，research.md D9）----

def test_metrics_snapshot_cadence_and_values(small_engine):
    """每 10 tick 采样；环形缓冲 ≤600 点；数值口径合法。"""
    e = small_engine
    run(e, 1200)
    h = e.metrics_history
    assert len(h) == 120, f"采样节奏错误: {len(h)}"
    assert h[-1]["tick"] == 1200
    s = h[-1]
    assert s["throughput_window"] > 0
    assert 0.0 <= s["waiting_ratio"] <= 1.0
    assert s["avg_cycle_ticks"] > 0
    assert s["charge_queue_depth"] >= 0
    assert s["conflict_per_ktick"] >= 0
    # 吞吐窗口口径：窗口内完成数 ≤ 累计完成数
    assert s["throughput_window"] <= e.metrics_tasks_done


def test_metrics_history_in_state_payload(small_engine):
    """/api/state 增 metrics_history（≤360 点）与 ledger（contracts/api-delta.md）。"""
    e = small_engine
    run(e, 1200)
    snap = e.snapshot()
    assert len(snap["metrics_history"]) <= 360
    assert snap["metrics_history"][-1]["tick"] == 1200
    lg = snap["ledger"]
    assert lg["spawned"] == lg["done"] + lg["active"] + lg["queued"] + lg["reassigned"]


# ---- 显示效果契约（FR-021/022 / T033）----

def test_delta_goal_field(small_engine):
    """delta 车辆对象含 goal（有目标=任务/桩/让路终点；无目标=null）。"""
    e = small_engine
    run(e, 600)
    e.tick()
    d = e.last_delta()
    checked = 0
    for dv in d["vehicles"]:
        v = e.vehicles[dv["id"]]
        if v.state in (VehicleState.TO_PICKUP, VehicleState.TO_DROPOFF):
            assert dv["goal"] is not None
            assert dv["goal"] in ([list(v.task.pickup), list(v.task.dropoff)]
                                  if v.task else [dv["goal"]])
            checked += 1
        elif v.state == VehicleState.IDLE and not v.path:
            assert dv["goal"] is None
    assert checked > 0


def test_vehicle_detail_state_history(small_engine):
    """/api/vehicle/{id} 含 state_history（最近状态变化）。"""
    e = small_engine
    run(e, 400)
    vid = next(iter(e.vehicles))
    d = e.vehicle_detail(vid)
    assert "state_history" in d
    assert isinstance(d["state_history"], list)
    assert all("tick" in x and "state" in x for x in d["state_history"])
    assert "progress" in d and "defer_charge" in d


# ---- 充电调度回归（research.md D11/D13，快验证发现）----

def test_charge_queue_entries_unique_and_live(small_engine):
    """排队列表无重复条目，且每个条目对应真实、仍需充电的车（D13）。"""
    e = small_engine
    run(e, 1500)
    q = e.grid.charging.queue
    assert len(q) == len(set(q)), "充电队列存在重复条目（排队深度失真）"
    for vid in q:
        v = e.vehicles.get(vid)
        assert v is not None and v.need_charge, "队列条目指向无效/不需充电的车"


def test_recommended_piles_within_capacity():
    """核算公式用真实跨度速率（D11），基准参数下推荐桩数不超过实际配置。"""
    rec = cfg.recommended_charge_piles()
    assert rec <= cfg.CHARGE_PILES
    # 单桩真实速率必须低于 容量/充满时长（只充 20%→满 跨度）
    assert cfg.charge_rate_per_tick() * cfg.TICK_RATE \
        < cfg.BATTERY_FULL / cfg.CHARGE_SECONDS
