"""仿真引擎：tick 循环、移动执行、死锁检测恢复、冲突事件、指标快照。

单进程 asyncio 驱动（research.md R5），10 tick/s；每 tick：
任务生成 → 派单 → 规划 → 预约仲裁与移动 → 到达处理 → 充电推进 →
排队调度 → 死锁检测/恢复 → 冲突事件 → 料箱循环 → 指标。
"""
from __future__ import annotations

import json
import os
import random
import time
from collections import deque
from typing import Optional

from .. import config as cfg
from .charging import ChargingManager
from .dispatcher import Dispatcher
from .entities import (Cell, CellKind, ConflictEvent, Task, TaskState, Tote,
                       ToteState, Vehicle, VehicleState)
from .grid import Grid
from .planner import Planner, ReservationTable

INBOUND_POOL_SIZE = 30  # 初始置于入库口的循环料箱数（1:1 成对生成的启动池）


class Engine:
    def __init__(self, seed: int | None = None, conflict_log_dir: str | None = None,
                 layout: dict | None = None):
        self.rng = random.Random(seed)
        self.grid = Grid(layout)  # layout=None → config 常量（baseline 缺省）
        self.planner = Planner(self.grid, self._tote_at)
        self.reservations = ReservationTable()
        self.dispatcher = Dispatcher(self.grid, self.rng)
        self.charging = ChargingManager(self.grid.charging)

        self.tick_n = 0
        self.paused = False
        self.collision_count = 0
        # 拥塞治理开关（FR-015，默认取 config，可运行时独立关闭以便对比）
        self.govern_backpressure = cfg.GOVERN_BACKPRESSURE
        self.govern_reassign = cfg.GOVERN_REASSIGN
        self.govern_charge_defer = cfg.GOVERN_CHARGE_DEFER

        self.vehicles: dict[int, Vehicle] = {}
        self.totes: dict[int, Tote] = {}
        self.occupant: dict[Cell, int] = {}          # cell -> vehicle_id
        self.docked_cells: set[Cell] = set()         # 静置料箱所在格（O(1) 查询）
        self.planner.docked = self.docked_cells      # O(1) 热路径引用
        self.dispatcher.docked_cells = self.docked_cells  # O(1) 空点位查询
        self.inbound_pool: deque[int] = deque()      # 在入库口等待入库的料箱 id
        self.cycling: list[tuple[int, int]] = []     # (tote_id, ready_tick) 出库口→入库口

        self.metrics_tasks_done = 0
        self.conflict_events: list[ConflictEvent] = []
        self._active_conflicts: list[ConflictEvent] = []
        self._wait_graph: dict[int, int] = {}
        self._prev_batt: dict[int, int] = {}
        self._recover_cooldown: dict[int, int] = {}
        self._plan_fail_tick: dict[int, int] = {}
        self._charge_try_tick: dict[int, int] = {}  # 充电尝试冷却（30 tick）
        self._charge_retry_tick: dict[int, int] = {}
        self._park_try_tick: dict[int, int] = {}
        self._parked_ok: set[Cell] = set()  # 已靠边停好的车所在格
        self._delta: dict = {"tick": 0, "vehicles": [], "totes": [], "metrics": {}}
        self._pending_events: list[dict] = []
        self._state_hist: dict[int, deque] = {}  # vid -> [(tick, state)] 最近 8 条
        # ---- 002 期：指标快照滑动窗口（FR-016/023，research.md D9）----
        self.metrics_history: deque = deque(maxlen=600)   # ≤600 点 = 1 小时 @10tick/s
        self._done_cum_log: deque = deque()               # (tick, 累计完成数)
        self._done_base_cum = 0                           # 已修剪部分窗口基线
        self._cycle_log: deque = deque()                  # (done_tick, 周期)
        self._conflict_end_log: deque = deque()           # 冲突事件 end_tick

        self._init_world()
        self._open_conflict_log(conflict_log_dir)

    # ---- 世界初始化（FR-002/FR-017）----

    def _init_world(self):
        # 半仓料箱（FR-017）+ 入库口启动池（保证 1:1 成对生成可启动）
        slots = self.grid.slots[:]
        self.rng.shuffle(slots)
        n_half = len(slots) // 2
        n_pool = min(INBOUND_POOL_SIZE, n_half)
        tid = 1
        for slot in slots[:n_half - n_pool]:
            self.totes[tid] = Tote(id=tid, location=slot, state=ToteState.DOCKED)
            self.docked_cells.add(slot)
            tid += 1
        for _ in range(n_pool):
            self.totes[tid] = Tote(id=tid, location="inbound", state=ToteState.DOCKED)
            self.inbound_pool.append(tid)
            tid += 1

        # 100 台车随机分布（不在围墙/充电区/站点格）
        valid = [c for c, k in self.grid.kind.items()
                 if k == CellKind.FLOOR and c not in self.grid.obstacles
                 and not self.grid.in_charging_area(c)]
        self.rng.shuffle(valid)
        # 002 修正（快验证发现）：初始电量 50%–100% 错相分布——全队满电出发时
        # drain 速率相近，会在低电阈值处形成同步充电波（tick ~2200 起数十台同时
        # 排队，队尾车在等待中耗尽电量）。错相后充电需求在时间上摊开（SC-006）
        for i in range(cfg.VEHICLES):
            cell = valid[i % len(valid)]
            battery = cfg.BATTERY_FULL * self.rng.uniform(0.5, 1.0)
            v = Vehicle(id=i + 1, cell=cell, render_from=cell,
                        battery=battery)
            v.facing = self.rng.choice(["N", "E", "S", "W"])
            self.vehicles[v.id] = v
            self.occupant[cell] = v.id

    def _open_conflict_log(self, log_dir: str | None):
        """FR-023：每次启动一个批次文件（文件名含启动时间戳）。"""
        d = log_dir or cfg.CONFLICT_LOG_DIR
        os.makedirs(d, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        self.conflict_log_path = os.path.join(d, f"conflicts_{ts}.jsonl")
        self._conflict_fh = open(self.conflict_log_path, "a", encoding="utf-8")

    def close(self):
        if getattr(self, "_conflict_fh", None):
            self._conflict_fh.close()

    # ---- 查询 ----

    def _tote_at(self, cell: Cell) -> bool:
        return cell in self.docked_cells

    def occupied_by_others(self, vid: int) -> set[Cell]:
        return {c for c, v in self.occupant.items() if v != vid}

    # ---- 主循环 ----

    def tick(self):
        self.tick_n += 1
        self.planner.new_tick()  # 重置每 tick 规划节点预算（002 性能护栏）
        self._wait_graph.clear()
        changed_v: set[int] = set()
        changed_t: set[int] = set()
        events: list[dict] = []

        # 拥塞背压（FR-005）：按上一 tick 等待率调节任务生成节奏
        if self.govern_backpressure:
            waiting_now = sum(1 for v in self.vehicles.values()
                              if v.state in (VehicleState.WAITING,
                                             VehicleState.STALLED))
            active_now = sum(1 for t in self.dispatcher.tasks.values()
                             if t.state in (TaskState.ASSIGNED,
                                            TaskState.IN_PROGRESS))
            self.dispatcher.update_backpressure(
                waiting_now / max(1, len(self.vehicles)), active_now,
                cfg.MAX_ACTIVE_TASKS)
        self.dispatcher.maybe_generate(self.tick_n, self.totes, self.inbound_pool)
        # 002 FR-027 监控发现：充电出队调度原先排在任务规划/重规划之后——全局
        # 节点预算已被大预算消费者（任务规划 60k×3、重规划 50k×8）吃穿，充电
        # 行程规划永远拿不到预算 → 空闲桩无人充电、滞零饿死（tick_cost 异常的
        # 深层根因）。提前到 tick 最前：预算新鲜时优先服务充电调度
        self._charge_queue_scheduling(changed_v, events)
        self._assign_tasks(changed_v, events)
        self._plan_paths(changed_v)
        self._move_vehicles(changed_v, changed_t, events)
        self._handle_arrivals(changed_v, changed_t, events)
        self._charge_progress(changed_v, changed_t, events)
        self._cycling_advance(changed_t)
        self._need_charge_flags(changed_v, events)
        self._deadlock_detection(changed_v, events)
        self._reassign_stalled_tasks(changed_v, events)
        self._conflict_detection()
        self._finalize_conflicts()
        self._decay_hotspots()
        self._sample_metrics()
        self._build_delta(changed_v, changed_t, events)

    # ---- 指标快照（002 FR-016/023，research.md D9）----

    def _sample_metrics(self):
        """每 10 tick 采样滑动窗口指标入环形缓冲（600 点 = 1 小时 @10tick/s）。"""
        if self.tick_n % 10 != 0:
            return
        w = cfg.METRICS_WINDOW_TICKS
        t0 = self.tick_n - w
        # 窗口吞吐：累计完成数 - 窗口起点累计数
        base = self._done_base_cum
        for tick0, cum0 in self._done_cum_log:
            if tick0 < t0:
                base = cum0
            else:
                break
        throughput = self.metrics_tasks_done - base
        # 窗口内完成任务的平均周期
        cycles = [c for tk, c in self._cycle_log if tk >= t0]
        # 窗口内互堵事件频次（次/千 tick，001 口径事件）
        n_conf = sum(1 for tk in self._conflict_end_log if tk >= t0)
        waiting = sum(1 for v in self.vehicles.values()
                      if v.state in (VehicleState.WAITING, VehicleState.STALLED))
        self.metrics_history.append({
            "tick": self.tick_n,
            "throughput_window": throughput,
            "avg_cycle_ticks": round(sum(cycles) / len(cycles), 1) if cycles else 0,
            "waiting_ratio": round(waiting / max(1, len(self.vehicles)), 3),
            "charge_queue_depth": self._charge_queue_depth(),
            "conflict_per_ktick": round(n_conf * 1000.0 / w, 3),
        })
        # 日志修剪（窗口外条目定期清理）
        while self._done_cum_log and self._done_cum_log[0][0] < t0 - w:
            self._done_base_cum = self._done_cum_log.popleft()[1]
        while self._cycle_log and self._cycle_log[0][0] < t0 - w:
            self._cycle_log.popleft()
        while self._conflict_end_log and self._conflict_end_log[0] < t0 - w:
            self._conflict_end_log.popleft()

    # ---- 增量组装（契约 websocket.md）----

    def _build_delta(self, changed_v: set[int], changed_t: set[int],
                     events: list[dict]):
        vs = []
        for vid in changed_v:
            v = self.vehicles.get(vid)
            if v is None:
                continue
            d = {"id": v.id, "from": list(v.render_from), "to": list(v.cell),
                 "state": v.state.value}
            b = int(v.battery)
            if b != self._prev_batt.get(v.id):
                d["battery"] = b
                self._prev_batt[v.id] = b
            # 目标点（FR-021：目标连线绘制端点；无目标显式置空供前端清除）
            goal = self._current_goal(v)
            d["goal"] = list(goal) if goal is not None else None
            # 状态历史（FR-022：最近状态变化时间）
            h = self._state_hist.setdefault(v.id, deque(maxlen=8))
            if not h or h[-1][1] != v.state.value:
                h.append((self.tick_n, v.state.value))
            vs.append(d)
        ts = [{"id": tid, **self._tote_loc(self.totes[tid]),
               "state": self.totes[tid].state.value}
              for tid in changed_t if tid in self.totes]
        self._delta = {"tick": self.tick_n, "vehicles": vs, "totes": ts,
                       "metrics": self._metrics()}
        for e in events:
            self._pending_events.append({"type": "event", "tick": self.tick_n,
                                         **e})
        for kind, vid in self.charging.drain_events():
            self._pending_events.append({"type": "event", "tick": self.tick_n,
                                         "kind": kind, "vehicle_id": vid})
        for ev in self.dispatcher.drain_governance_events():
            self._pending_events.append({"type": "event", "tick": self.tick_n,
                                         **ev})

    def last_delta(self) -> dict:
        return self._delta

    def drain_ws_events(self) -> list[dict]:
        ev = self._pending_events
        self._pending_events = []
        return ev

    # ---- 派单（FR-004 / R4）----

    def _station_load(self, side: str) -> int:
        """工作站同时在场车辆数（FR-004）：在站车辆 + 已派任务指向该站的车。"""
        cells = set(self.grid.inbound_cells if side == "inbound"
                    else self.grid.outbound_cells)
        occ = sum(1 for v in self.vehicles.values() if v.cell in cells)
        targeting = 0
        for t in self.dispatcher.tasks.values():
            if t.assigned_vehicle is None or t.state != TaskState.ASSIGNED:
                continue
            tgt = t.pickup if side == "inbound" else t.dropoff
            if tgt in cells:
                targeting += 1
        return occ + targeting

    def _assign_tasks(self, changed_v: set[int], events: list[dict]):
        # WIP 限流（规格假设：基准负载约 80 台在途、20 台空闲待命）：
        # 在途任务达上限时停止派发，防止全队投入引发拥塞死亡螺旋
        active = sum(1 for t in self.dispatcher.tasks.values()
                     if t.state in (TaskState.ASSIGNED, TaskState.IN_PROGRESS))
        if active >= cfg.MAX_ACTIVE_TASKS:
            return
        # 002 性能修复：站点负载每 tick 缓存一次（此前每个候选任务重算一次，
        # 每调用全队+全任务扫描，3000 tick 内被调 18.6 万次、占 1/3 引擎耗时）
        station_load = {side: self._station_load(side)
                        for side in ("inbound", "outbound")}
        attempts = 0
        for v in self.dispatcher.assignable_vehicles(self.vehicles):
            if attempts >= 3:  # 每 tick 限流尝试，避免不可达任务拖垮派单
                break
            task = self.dispatcher.peek_task()
            if task is None:
                break
            # 工作站容量（002 FR-004）：指向饱和站点的任务暂缓派发（留队列）
            if station_load["inbound" if task.type == "inbound" else "outbound"] \
                    >= cfg.STATION_CAPACITY:
                self.dispatcher.rotate_task(task)
                continue
            attempts += 1
            path = self.planner.plan(v.cell, task.pickup, v.facing, False,
                                     self.occupied_by_others(v.id), max_nodes=20_000)
            if path is None:
                if task.state != TaskState.BLOCKED:
                    task.state = TaskState.BLOCKED
                    events.append({"kind": "task_blocked", "task_id": task.id})
                self.dispatcher.rotate_task(task)  # 移到队尾，障碍物变更后自动重试
                continue
            self.dispatcher.take_task(task)
            task.assigned_vehicle = v.id
            task.assigned_tick = self.tick_n
            task.last_progress_tick = self.tick_n
            station_load["inbound" if task.type == "inbound" else "outbound"] += 1
            v.assigned_count += 1
            v.defer_charge = False  # 任务召回优先于错峰充电（FR-009）
            self._parked_ok.discard(v.cell)
            v.task = task
            v.path = path
            v.state = VehicleState.TO_PICKUP
            changed_v.add(v.id)
            active += 1
            if active >= cfg.MAX_ACTIVE_TASKS:
                break

    # ---- 路径规划（限流）----

    def _plan_paths(self, changed_v: set[int]):
        budget = 8  # 每 tick 最多规划/重规划的车数（CPU 限流，R2/R9）
        for v in self.vehicles.values():
            if budget <= 0:
                break
            if v.path or v.state not in (VehicleState.TO_PICKUP, VehicleState.TO_DROPOFF,
                                         VehicleState.TO_CHARGE, VehicleState.MAKING_WAY,
                                         VehicleState.WAITING):
                continue
            # 规划失败冷却：每车 5 tick 内不重复尝试（高拥塞下防规划风暴）
            if self.tick_n - self._plan_fail_tick.get(v.id, -10**9) < 5:
                continue
            goal = self._current_goal(v)
            if goal is None:
                continue
            carrying = v.carrying is not None
            path = self.planner.plan(v.cell, goal, v.facing, carrying,
                                     self.occupied_by_others(v.id), max_nodes=15_000)
            if path is not None:
                v.path = path
                self._plan_fail_tick.pop(v.id, None)
                if v.state == VehicleState.WAITING:  # 重规划成功，恢复原状态
                    v.state = v.prev_state or VehicleState.TO_PICKUP
                    v.prev_state = None
                changed_v.add(v.id)
                budget -= 1
            else:
                self._plan_fail_tick[v.id] = self.tick_n

    def _current_goal(self, v: Vehicle) -> Optional[Cell]:
        st = v.state
        if st == VehicleState.TO_PICKUP and v.task:
            return v.task.pickup
        if st == VehicleState.TO_DROPOFF and v.task:
            return v.task.dropoff
        if st == VehicleState.TO_CHARGE:
            return v.charge_target
        if st == VehicleState.MAKING_WAY:
            return v.way_target
        return None

    # ---- 移动执行（FR-005/006，R1 预约仲裁）----

    def _move_vehicles(self, changed_v: set[int], changed_t: set[int],
                       events: list[dict]):
        self.reservations.clear()
        # 公平仲裁：等待最久者优先预约（防饿死）；WAITING 车每 tick 重试。
        # STALLED 车保留移动能力（002 修正）：受阻=检测标记而非移动冻结——
        # 路径下一格空闲时照常走（仲裁仍生效），否则"前车受阻滞留、后车堵死"
        # 且恢复阶梯无法解围（路径相同被拒/阻挡车在充电/改派churn）的死锁形态
        movers = [v for v in self.vehicles.values()
                  if v.path and v.state not in (VehicleState.IDLE,
                                                VehicleState.CHARGING)]
        movers.sort(key=lambda v: (-v.stall_ticks, v.id))
        eligible: set[int] = set()  # 本 tick 获得位移配额的车（停滞计数仅对其生效）
        for v in movers:
            nxt = v.path[0]
            # ---- 位移配额（002 FR-006：进度累加速度模型，research.md D1）----
            dr0, dc0 = nxt[0] - v.cell[0], nxt[1] - v.cell[1]
            move_dir = {(0, 1): "E", (0, -1): "W", (1, 0): "S", (-1, 0): "N"}.get(
                (dr0, dc0))
            turning = move_dir is not None and move_dir != v.facing
            if v.progress < 1.0:
                v.progress += self._vehicle_speed(v, turning)
                if v.progress < 1.0:
                    continue  # 未获配额：非等待/停滞（载货/转弯的合法慢速）
            eligible.add(v.id)
            # 障碍物/围墙校验（FR-011：绝不驶入障碍格）
            if not self.planner.can_enter(nxt, v.carrying is not None):
                v.path = []  # 触发重规划
                continue
            # 充电桩矢量性（002 FR-008：与规划同源校验，杜绝"规划能过、执行被拒"）
            if not self.grid.can_traverse(v.cell, nxt,
                                          to_is_goal=(nxt == v.charge_target)):
                v.path = []  # 触发重规划
                continue
            other = self.occupant.get(nxt)
            if other is not None and other != v.id:
                if v.state != VehicleState.WAITING and v.prev_state is None:
                    v.prev_state = v.state
                v.state = VehicleState.WAITING
                v.wait_reason = other
                self._wait_graph[v.id] = other
                continue
            if not self.reservations.try_reserve(nxt, v.id):
                owner = self.reservations.owner(nxt)
                if v.state != VehicleState.WAITING and v.prev_state is None:
                    v.prev_state = v.state
                v.state = VehicleState.WAITING
                v.wait_reason = owner
                if owner:
                    self._wait_graph[v.id] = owner
                continue
            # 执行移动（一 tick 至多一格；配额 ≥1 方可位移）
            del self.occupant[v.cell]
            v.render_from = v.cell
            v.cell = nxt
            v.path.pop(0)
            v.progress = max(0.0, v.progress - 1.0)
            self.occupant[nxt] = v.id
            v.last_move_tick = self.tick_n
            dr, dc = nxt[0] - v.render_from[0], nxt[1] - v.render_from[1]
            v.facing = {(0, 1): "E", (0, -1): "W", (1, 0): "S", (-1, 0): "N"}.get(
                (dr, dc), v.facing)
            # 电量消耗（002 FR-007：载重倍率 × 转向附加，非负截断）
            self._apply_move_energy(v, turning)
            v.stall_ticks = 0
            v.moved_this_tick = True
            if v.state == VehicleState.WAITING:
                v.state = v.prev_state or VehicleState.TO_PICKUP
                v.prev_state = None
                v.wait_reason = None
            elif v.state == VehicleState.STALLED:
                # 受阻车恢复移动：回 stall 前状态（002：STALLED 不再冻结移动）
                v.state = v.prev_state or VehicleState.TO_PICKUP
                v.prev_state = None
            changed_v.add(v.id)
            if v.task is not None:
                v.task.last_progress_tick = self.tick_n  # 无进度改派口径（FR-002）
            if v.carrying is not None:
                changed_t.add(v.carrying.id)
        # 未移动的 mover 恢复计时（仅对获得位移配额仍被阻者计停滞）
        for v in movers:
            if v.id in eligible and not v.moved_this_tick:
                v.stall_ticks += 1
            v.moved_this_tick = False

    # ---- 硬件模型（002 FR-006/007，research.md D1/D2）----

    @staticmethod
    def _vehicle_speed(v: Vehicle, turning: bool) -> float:
        """状态唯一决定速度：1.0 空载直行；×载货比率；×转向比率。"""
        speed = 1.0
        if v.carrying is not None:
            speed *= cfg.LOADED_SPEED_RATIO
        if turning:
            speed *= cfg.TURN_SPEED_RATIO
        return speed

    @staticmethod
    def _apply_move_energy(v: Vehicle, turning: bool):
        """每格电量扣减：基础 1.0 × 载重倍率，转向步叠加附加能耗；非负截断。"""
        drain = 1.0 * (cfg.LOAD_FACTOR if v.carrying is not None else 1.0)
        if turning:
            drain += cfg.TURN_ENERGY
        v.battery = max(0.0, v.battery - drain)

    # ---- 到达处理 ----

    def _handle_arrivals(self, changed_v: set[int], changed_t: set[int],
                         events: list[dict]):
        for v in self.vehicles.values():
            if v.path or not v.is_moving_state():
                continue
            if v.state == VehicleState.MAKING_WAY:
                # 让路完成：恢复让路前活动（idle 待命 / 继续原任务 / 继续充电行程）
                tgt = v.way_target
                v.way_target = None
                v.state = v.prev_state or VehicleState.IDLE
                v.prev_state = None
                v.path = []  # 恢复的任务由 _plan_paths 重新规划
                if tgt is not None and self.grid.is_slot(tgt):
                    self._parked_ok.add(v.cell)  # 靠边停好（不占通道）
                changed_v.add(v.id)
                continue
            if v.state == VehicleState.TO_CHARGE:
                self._arrive_charge(v, changed_v)
                continue
            task = v.task
            if task is None:
                v.state = VehicleState.IDLE
                changed_v.add(v.id)
                continue
            if v.state == VehicleState.TO_PICKUP and v.cell == task.pickup:
                self._pickup(v, task, changed_t, changed_v)
            elif v.state == VehicleState.TO_DROPOFF and v.cell == task.dropoff:
                self._dropoff(v, task, changed_t, changed_v, events)

    def _pickup(self, v: Vehicle, task: Task, changed_t: set[int], changed_v: set[int]):
        if task.type == "outbound":
            tote = self.totes.get(task.tote_id)
            if tote is None or tote.location != v.cell:
                # 料箱不在（异常）：任务回收
                self.dispatcher.unassign(task)
                v.task = None
                v.state = VehicleState.IDLE
                changed_v.add(v.id)
                return
            self.docked_cells.discard(v.cell)
            tote.location = ("carried", v.id)
            tote.state = ToteState.CARRIED
            v.carrying = tote
        else:  # inbound：从入库口池领取循环料箱
            if not self.inbound_pool:
                return  # 池暂空：在库口等待循环料箱回归（不退单）
            tid = self.inbound_pool.popleft()
            tote = self.totes[tid]
            tote.location = ("carried", v.id)
            tote.state = ToteState.CARRIED
            task.tote_id = tid
            v.carrying = tote
        changed_t.add(v.carrying.id)
        v.state = VehicleState.TO_DROPOFF
        task.state = TaskState.IN_PROGRESS
        changed_v.add(v.id)

    def _dropoff(self, v: Vehicle, task: Task, changed_t: set[int],
                 changed_v: set[int], events: list[dict]):
        tote = v.carrying
        if tote is not None:
            if task.type == "outbound":
                tote.location = "outbound"
                tote.state = ToteState.CYCLING
                self.cycling.append((tote.id, self.tick_n + cfg.TICK_RATE * 5))
            else:
                tote.location = task.dropoff
                tote.state = ToteState.DOCKED
                self.docked_cells.add(task.dropoff)
            changed_t.add(tote.id)
            v.carrying = None
        task.state = TaskState.DONE
        self.dispatcher.mark_done(task, self.tick_n)
        self._done_cum_log.append((self.tick_n, self.metrics_tasks_done))
        self._cycle_log.append((self.tick_n,
                                self.tick_n - task.created_tick))
        v.task = None
        v.completed_count += 1
        self.metrics_tasks_done += 1
        v.state = VehicleState.IDLE
        changed_v.add(v.id)
        self.dispatcher.release_claims(task)
        # FR-025：完成任务瞬间若需充电 → 派发充电行程（优先于新任务，含错峰判定）
        if v.need_charge and v.battery < cfg.battery_low():
            self._maybe_start_charge(v, changed_v, events)

    # ---- 充电（FR-025/026，R10；002 FR-008/009 增量）----

    def _charge_queue_depth(self) -> int:
        """充电排队深度 = 排队车辆 + 在充车辆（错峰判定口径，FR-009）。"""
        return (len(self.grid.charging.queue)
                + sum(1 for o in self.vehicles.values()
                      if o.state == VehicleState.CHARGING))

    def _maybe_start_charge(self, v: Vehicle, changed_v: set[int],
                            events: list[dict]):
        """错峰充电判定（FR-009 / research.md D7）：排队深度超软上限时，非紧急
        低电车延迟充电（保持待命、可被任务召回）；紧急车（≤临界）始终优先。"""
        depth = self._charge_queue_depth()
        # defer 兜底（002 快验证修正）：电量已逼近临界（≤2×临界值）的车不再错峰——
        # 继续排队等待的代价（排队期间持续耗电）已高于拥塞贡献，拖到归零即违反 SC-006
        defer_floor = cfg.battery_critical() * 2
        if (self.govern_charge_defer and depth >= cfg.CHARGE_QUEUE_SOFT_LIMIT
                and v.battery > defer_floor):
            if not v.defer_charge:
                v.defer_charge = True
                events.append({"kind": "governance_event",
                               "action": "charge_deferred",
                               "vehicles": [v.id],
                               "detail": f"queue depth {depth} >= "
                                         f"{cfg.CHARGE_QUEUE_SOFT_LIMIT}, deferred"})
            return
        v.defer_charge = False
        self._start_charge_trip(v, changed_v, events)

    def _start_charge_trip(self, v: Vehicle, changed_v: set[int],
                           events: list[dict]):
        self._release_pile_claim(v)  # 防御：重试前释放陈旧认领（防一车双桩）
        v.charge_target = None
        # 002 性能修复（快验证 profile 实测）：就近试至多 2 个空闲桩；全部规划
        # 失败 → 回退排队分支。此前失败即返回不排队——约 100 台低电车每冷却窗
        # 重试同一不可达桩，1500 tick 内 4.1 万次调用、烧 8200 万 A* 节点
        r0, c0 = v.cell
        free_piles = sorted(
            (p for p, o in self.grid.charging.pile_owner.items() if o is None),
            key=lambda p: abs(p[0] - r0) + abs(p[1] - c0))[:2]
        planned = False
        for pile in free_piles:
            if pile in self.occupant and self.occupant[pile] != v.id:
                continue  # 充电位被滞留车物理占据
            path = self.planner.plan_any_dir(v.cell, pile, False,
                                             self.occupied_by_others(v.id),
                                             max_nodes=3_000)
            if path is None:
                continue  # 暂不可达：换桩/回退排队，冷却后重试
            self.area_claim_pile(pile, v.id)
            v.state = VehicleState.TO_CHARGE
            v.charge_target = pile
            v.path = path
            changed_v.add(v.id)
            planned = True
            break
        if planned:
            return
        # 桩满/不可达 → 排队格等待（电量更低者优先出队）。必须规划到排队格的
        # 实际路径并成功后才入队——只设 charge_target 不规划路径会让车原地成
        # to_charge 僵尸、自愈回 idle 后队列条目残留（排队深度失真 → 错峰失效）
        target = self.charging.assign_queue_cell(
            v, set(self.occupant) - {v.cell})
        if target is None:
            return  # 排队格也满：留在原地稍后再试
        path = self.planner.plan_any_dir(v.cell, target, False,
                                         self.occupied_by_others(v.id),
                                         max_nodes=3_000)
        if path is None:
            return  # 暂不可达：不入队（防僵尸条目），冷却后重试
        v.state = VehicleState.TO_CHARGE
        v.charge_target = target
        v.path = path
        self.charging.enqueue(v.id, v.battery)
        changed_v.add(v.id)

    def area_claim_pile(self, pile: Cell, vid: int):
        self.grid.charging.pile_owner[pile] = vid

    def _release_pile_claim(self, v: Vehicle):
        """释放该车认领的充电位（如有）。"""
        tgt = v.charge_target
        if tgt is not None and self.grid.charging.pile_owner.get(tgt) == v.id:
            self.grid.charging.pile_owner[tgt] = None

    def _arrive_charge(self, v: Vehicle, changed_v: set[int]):
        pile = self.charging.at_pile(v)
        if pile is not None and v.cell == pile:
            # 供电功率预算（002 FR-008）：在充总功率不得超预算（比例 <1 时生效）
            charging_now = sum(1 for o in self.vehicles.values()
                               if o.state == VehicleState.CHARGING)
            rate = cfg.charge_rate_per_tick()
            budget = (cfg.CHARGE_PILES * rate * cfg.CHARGE_POWER_BUDGET_RATIO)
            if (charging_now + 1) * rate > budget:
                v.state = VehicleState.WAITING  # 桩前等待功率额度
                v.prev_state = VehicleState.TO_CHARGE
                changed_v.add(v.id)
                return
            v.state = VehicleState.CHARGING
            changed_v.add(v.id)
            return
        if v.cell in self.grid.charging.queue_cells:
            # 在排队格等待分配充电位（出队由 _charge_queue_scheduling 驱动）
            v.state = VehicleState.WAITING
            v.prev_state = VehicleState.TO_CHARGE
            changed_v.add(v.id)
            return
        # 未到目标（被挤开等）：重试
        v.path = []

    def _charge_progress(self, changed_v: set[int], changed_t: set[int],
                         events: list[dict]):
        for v in self.vehicles.values():
            if v.state != VehicleState.CHARGING:
                continue
            if self.charging.charge_tick(v):
                pile = self.charging.at_pile(v)
                if pile is not None:
                    self.grid.charging.pile_owner[pile] = None
                v.need_charge = False
                v.charge_target = None
                v.prev_state = None  # 充电周期闭环：不得让残留 prev_state 复活
                v.state = VehicleState.IDLE
                events.append({"kind": "charge_done", "vehicle_id": v.id})
                changed_v.add(v.id)

    def _charge_queue_scheduling(self, changed_v: set[int], events: list[dict]):
        # 002 FR-027 监控发现（tick ~4300 现场解剖：9 个空闲桩 0 台在充、20 台
        # 滞零饿死）：原实现队首出队失败即 break——队首恰是最缺电（滞零、离桩
        # 远、规划必败）的车，10-tick 冷却内整条队列停摆，空闲桩无人充电。
        # 修复：失败者移队尾并继续试下一个候选（电量更低者优先出队不变，
        # 但失败不再阻塞队伍）；每 tick 至多 3 次出队尝试限规划成本
        attempts = 0
        while attempts < 3:
            got = self.charging.dequeue_for_pile(
                {v.id: v.battery for v in self.vehicles.values()})
            if got is None:
                break
            vid, pile = got
            v = self.vehicles.get(vid)
            if v is None or not v.need_charge or v.state == VehicleState.CHARGING:
                continue  # 失效条目：丢弃，不占尝试配额
            # 充电位仍被滞留车物理占据（充满未驶离）→ 暂缓分配，防其堵在桩旁
            if pile in self.occupant and self.occupant[pile] != vid:
                self.charging.area.queue.insert(0, vid)
                break
            attempts += 1
            # 规划冷却（002 性能修复）：失败车 10 tick 内不反复规划（拥塞下
            # 不可达充电行程的 A* 代价极高）——移队尾，不阻塞后续候选
            if self.tick_n - self._charge_retry_tick.get(vid, -10**9) < 10:
                self.charging.area.queue.append(vid)
                continue
            # 先释放该车此前认领的充电位（防重复认领）
            self._release_pile_claim(v)
            path = self.planner.plan_any_dir(v.cell, pile, False,
                                             self.occupied_by_others(v.id),
                                             max_nodes=20_000)
            if path is None:
                self.charging.area.queue.append(vid)  # 暂不可达：移队尾
                self._charge_retry_tick[vid] = self.tick_n
                continue
            self.area_claim_pile(pile, vid)
            v.charge_target = pile
            v.state = VehicleState.TO_CHARGE
            v.prev_state = None
            v.path = path
            changed_v.add(v.id)
        # ---- 空闲车直发（002 FR-027 补充）：队列服务完仍有空闲桩时，按电量
        # 升序为未入队的 need_charge 闲车直接规划去桩行程——此前该路径走
        # _need_charge_flags（tick 末段），预算已被任务规划吃穿，其充电尝试
        # 永远拿不到规划资源（车辆 72 滞零 2400+ tick 的根因）
        while attempts < 3:
            free = [p for p, o in self.grid.charging.pile_owner.items()
                    if o is None and p not in self.occupant]
            if not free:
                break
            cands = [o for o in self.vehicles.values()
                     if o.need_charge and o.state is VehicleState.IDLE
                     and o.charge_target is None
                     and o.id not in self.grid.charging.queue
                     and (not o.defer_charge
                          or o.battery <= cfg.battery_critical())]
            if not cands:
                break
            c = min(cands, key=lambda o: o.battery)
            cr, cc = c.cell
            pile = min(free, key=lambda p: abs(p[0] - cr) + abs(p[1] - cc))
            path = self.planner.plan_any_dir(c.cell, pile, False,
                                             self.occupied_by_others(c.id),
                                             max_nodes=10_000)
            if path is None:
                self._plan_fail_tick[c.id] = self.tick_n
                self._charge_try_tick[c.id] = self.tick_n
                break  # 就近桩不可达：本 tick 不再直发（冷却后重试）
            self.area_claim_pile(pile, c.id)
            c.charge_target = pile
            c.state = VehicleState.TO_CHARGE
            c.prev_state = None
            c.path = path
            c.defer_charge = False
            changed_v.add(c.id)
            attempts += 1

    # ---- 料箱循环（FR-018）----

    def _cycling_advance(self, changed_t: set[int]):
        remaining = []
        for tid, ready in self.cycling:
            if self.tick_n >= ready:
                tote = self.totes[tid]
                tote.location = "inbound"
                tote.state = ToteState.DOCKED
                self.inbound_pool.append(tid)
                changed_t.add(tid)
            else:
                remaining.append((tid, ready))
        self.cycling = remaining

    # ↑ cycling 料箱回到入库口（location="inbound"），不占用 slot，无需更新 docked_cells

    # ---- 电量标记与状态清理（FR-025/026）----

    def _need_charge_flags(self, changed_v: set[int], events: list[dict]):
        for v in self.vehicles.values():
            low = v.battery < cfg.battery_low()
            if low and not v.need_charge:
                v.need_charge = True
                # 出发由下方统一逻辑处理（空闲车每 tick 尝试，含错峰判定）
            # 兜底（FR-026/SC-010）：电量临界仍未充电 → 放弃当前等待，直接去充电
            # 载货车：就地落箱（任务起点更新后重派），保证任何车辆不电量归零。
            # 002 性能修复：同样受 5-tick 规划冷却约束——否则深拥塞下每台临界车
            # 每 tick 烧一次 A*（≤2 万节点/次），tick 成本无界。
            # 002 FR-027 监控发现（tick ~6000 规划风暴实测 3087 次/600 tick）：
            # 电量归零的车恒满足"临界"，兜底每 5 tick 把已在充电排队的车拽出
            # 队列、重规划入队、再拽出——无限循环烧穿节点预算。已在充电流程中
            # （排队中 / 已有充电目标）的车一律交给充电调度，兜底让位
            in_charge_flow = (v.id in self.grid.charging.queue
                              or v.charge_target is not None)
            if (v.battery < cfg.battery_critical()
                    and v.state not in (VehicleState.CHARGING, VehicleState.TO_CHARGE)
                    and not in_charge_flow
                    and self.tick_n - self._plan_fail_tick.get(v.id, -10**9) >= 5
                    and self.tick_n - self._charge_try_tick.get(v.id, -10**9) >= 30):
                if v.carrying is not None:
                    if self._drop_tote_here(v) and v.task is not None:
                        v.task.pickup = v.cell
                        v.assigned_count = max(0, v.assigned_count - 1)
                        self.dispatcher.unassign(v.task)
                        v.task.assigned_vehicle = None
                    v.task = None
                elif v.task is not None:
                    v.assigned_count = max(0, v.assigned_count - 1)
                    self.dispatcher.unassign(v.task)
                    v.task.assigned_vehicle = None
                    v.task = None
                self._release_pile_claim(v)
                v.state = VehicleState.IDLE
                v.prev_state = None
                v.way_target = None
                v.stall_ticks = 0
                v.defer_charge = False  # 紧急车不受错峰约束（FR-009）
                self._start_charge_trip(v, changed_v, events)
                # 无论成败：30 tick 内兜底不再重试（成败均由各自调度驱动）
                self._plan_fail_tick[v.id] = self.tick_n
                self._charge_try_tick[v.id] = self.tick_n
                changed_v.add(v.id)
            # ---- 002 FR-009 错峰充电（research.md D7）----
            # defer 车重估：排队深度回落或电量进入临界 → 解除延迟（紧急车始终优先）
            if v.defer_charge and v.state == VehicleState.IDLE:
                if (v.battery <= cfg.battery_critical()
                        or self._charge_queue_depth() < cfg.CHARGE_QUEUE_SOFT_LIMIT):
                    v.defer_charge = False
                    changed_v.add(v.id)
            # 空闲需充电车每 5 tick 尝试出发（defer 中除外；修复 001 期
            # "排队格满后永不重试"的松散点；冷却防规划风暴）。
            # 002 性能修复：充电尝试独立冷却（30 tick）——拥塞下可达性分钟级才
            # 变化，高频重试只是重复烧节点（FR-027 监控在 tick 15000 二次充电波
            # 捕获 105s/窗的规划风暴；同窗口实测 84 次 plan 调用/tick）
            if (v.state == VehicleState.IDLE and v.need_charge
                    and not v.defer_charge and not v.path
                    and self.tick_n - self._plan_fail_tick.get(v.id, -10**9) >= 5
                    and self.tick_n - self._charge_try_tick.get(v.id, -10**9) >= 30):
                before_state = v.state
                self._maybe_start_charge(v, changed_v, events)
                if v.state == before_state and not v.path:
                    self._plan_fail_tick[v.id] = self.tick_n  # 失败：5 tick 冷却
                    self._charge_try_tick[v.id] = self.tick_n  # 充电尝试：15 tick 冷却
            # 状态清理：无处可等的 WAITING 僵尸（无路径、不在充电排队）→ 回 idle
            if (v.state == VehicleState.WAITING and not v.path
                    and not (v.cell in self.grid.charging.queue_cells and v.need_charge)):
                self._release_pile_claim(v)  # 002 修复：陈旧桩位认领一并释放
                v.charge_target = None
                v.state = VehicleState.IDLE
                v.prev_state = None
                changed_v.add(v.id)
            # TO_CHARGE 僵尸自愈（缩尺烟测车辆 10 定位）：无路径、不在充电位/
            # 排队格 → 回 idle（若仍需充电由临界清扫重新发起）。否则车辆永久
            # 卡在 to_charge：不可派单、不再移动、电量冻结（prev_state 复活所致）
            if (v.state == VehicleState.TO_CHARGE and not v.path
                    and not self.charging.at_pile(v)
                    and v.cell not in self.grid.charging.queue_cells):
                self._release_pile_claim(v)
                if v.id in self.grid.charging.queue:
                    self.grid.charging.queue.remove(v.id)  # 队列条目同步清理
                v.state = VehicleState.IDLE
                v.prev_state = None
                v.charge_target = None
                changed_v.add(v.id)
            # 不变量自愈：idle 车不应携带料箱
            if v.state == VehicleState.IDLE and v.carrying is not None:
                if v.task is not None and v.task.state == TaskState.IN_PROGRESS:
                    v.state = VehicleState.TO_DROPOFF  # 继续送货
                elif self._drop_tote_here(v):
                    v.task = None
                changed_v.add(v.id)
            # FR-028 待命靠边：idle 车停在通道上会堵窄道 → 主动挪到最近空点位格
            # （无载车可停在静置料箱格下方，FR-015；让出 1 格宽通道减少死锁）
            # 澄清 2026-09-30：①入库/出库工作站放置位不得驻留空闲车——否则后续
            # 送货车被堵成片 stalled；②充电区内滞留的 idle 车（充满未驶离）
            # MUST 立即驶离——否则充电位被白占，后续充电车全堵在桩旁
            if v.state == VehicleState.IDLE and not v.path \
                    and v.cell not in self._parked_ok:
                if self.grid.in_charging_area(v.cell):
                    self._exit_charging(v, changed_v)
                elif self.grid.kind.get(v.cell) in (CellKind.FLOOR,
                                                    CellKind.INBOUND,
                                                    CellKind.OUTBOUND):
                    self._try_park_aside(v, changed_v)

    def _drop_tote_here(self, v: Vehicle) -> bool:
        """就地落箱（料箱落于当前格，变为静置）。"""
        cell = v.cell
        k = self.grid.kind.get(cell)
        if (cell in self.grid.obstacles or self._tote_at(cell)
                or k not in (CellKind.FLOOR, CellKind.SLOT)
                or cell in self.grid.charging.pile_owner):
            return False
        tote = v.carrying
        tote.location = cell
        tote.state = ToteState.DOCKED
        self.docked_cells.add(cell)
        v.carrying = None
        return True

    def _exit_charging(self, v: Vehicle, changed_v: set[int]):
        """滞留充电区的 idle 车就近驶离（澄清 2026-09-30：充满后不得滞留充电位）。

        一段直达：目标即空闲存储点位（就近 + 分散选点，避开他车让路目标），
        到达即完成 FR-028 靠边待命并恢复可派；避免「先出区再靠边」两段
        MAKING_WAY 挤占可派时间（缩尺均匀性烟测曾因此超界）。
        """
        if self.tick_n - self._park_try_tick.get(v.id, -10**9) < 30:
            return
        self._park_try_tick[v.id] = self.tick_n
        # 002 性能修复：单次至多尝试 3 个选点（此前 20 个逐一规划 × 3 万节点，
        # 100 台空闲车构成每 tick ~40 次调用的规划风暴，tick 成本失控）
        for best in self._pick_parking_slots(v)[:3]:
            path = self.planner.plan_any_dir(v.cell, best, False,
                                             self.occupied_by_others(v.id),
                                             max_nodes=10_000)
            if path:
                v.state = VehicleState.MAKING_WAY
                v.way_target = best
                v.prev_state = None  # 靠边停靠后应回 idle，不复活旧状态
                self._parked_ok.discard(v.cell)
                v.path = path
                changed_v.add(v.id)
                return

    def _pick_parking_slots(self, v: Vehicle, k: int = 20) -> list[Cell]:
        """就近 + 分散选点（澄清 2026-09-30 选项 A，FR-028）。

        从最近 K 个空闲储位中，优先选与其它停靠车（idle 车当前位置 +
        他车让路目标）间距最大者，平局取更近者——兼顾短挪车与散开，
        避免疏散车流在站点/充电区附近的储位扎堆。
        """
        r, c = v.cell
        taken = {o.way_target for o in self.vehicles.values()
                 if o.id != v.id and o.way_target is not None}
        parked = {o.cell for o in self.vehicles.values()
                  if o.id != v.id and o.state == VehicleState.IDLE} | taken
        cands = sorted(
            (s for s in self.grid.slots
             if s not in self.occupant and s not in taken
             and s not in self.grid.blocked and s not in self.docked_cells),
            key=lambda s: abs(s[0] - r) + abs(s[1] - c))[:k]
        if not parked or len(cands) < 2:
            return cands
        def score(s):
            spread = min(abs(s[0] - pr) + abs(s[1] - pc) for pr, pc in parked)
            return (-spread, abs(s[0] - r) + abs(s[1] - c))
        return sorted(cands, key=score)

    def _try_park_aside(self, v: Vehicle, changed_v: set[int]):
        """空闲车就近挪到存储点位格待命（不占通道/工作站，FR-028 靠边待命）。

        注：need_charge 车同样 MUST 靠边（002 FR-027 监控发现：充电待命车滞留
        通道会把前往充电位的车永久堵死）——其充电行程由调度从任意泊位发起，
        靠边不影响充电优先级。
        """
        if self.tick_n - self._park_try_tick.get(v.id, -10**9) < 30:
            return  # 冷却，防反复尝试
        self._park_try_tick[v.id] = self.tick_n
        r, c = v.cell
        k = self.grid.kind.get(v.cell)
        if k in (CellKind.INBOUND, CellKind.OUTBOUND):
            # 工作站放置位：半径搜索必然失败（附近无点位）→ 全局疏散，
            # 选点用「就近 + 分散」（_pick_parking_slots，澄清 2026-09-30 选项 A）；
            # 单次至多 3 个选点（同 _exit_charging 的规划风暴修复）
            for best in self._pick_parking_slots(v)[:3]:
                path = self.planner.plan(v.cell, best, v.facing, False,
                                         self.occupied_by_others(v.id),
                                         max_nodes=10_000)
                if path:
                    v.state = VehicleState.MAKING_WAY
                    v.way_target = best
                    v.prev_state = None  # 靠边停靠后应回 idle，不复活旧状态
                    v.path = path
                    changed_v.add(v.id)
                    return
            return
        for radius in (1, 2, 3):
            best = None
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    cand = (r + dr, c + dc)
                    if (self.grid.is_slot(cand) and cand not in self.occupant
                            and cand not in self.grid.blocked):
                        best = cand
                        break
                if best:
                    break
            if best is None:
                continue
            path = self.planner.plan(v.cell, best, v.facing, False,
                                     self.occupied_by_others(v.id), max_nodes=20_000)
            if path:
                v.state = VehicleState.MAKING_WAY
                v.way_target = best
                self._parked_ok.discard(v.cell)  # 原车位让出
                v.path = path
                changed_v.add(v.id)
                return
        return

    # ---- 死锁检测与恢复（FR-012，research.md R3）----

    def _deadlock_detection(self, changed_v: set[int], events: list[dict]):
        for v in self.vehicles.values():
            if v.stall_ticks < cfg.STALL_TICKS:
                continue
            if v.state != VehicleState.STALLED:
                if v.prev_state is None:  # 保留既有的恢复点（如让路前状态）
                    v.prev_state = v.state
                v.state = VehicleState.STALLED
                events.append({"kind": "stall_detected", "vehicle_id": v.id})
                changed_v.add(v.id)
            self._try_recover(v, changed_v, events)

    def _try_recover(self, v: Vehicle, changed_v: set[int], events: list[dict]):
        """恢复阶梯（R3-3）：重规划 → 让路 → 改派。每车冷却 20 tick 限流。"""
        last = self._recover_cooldown.get(v.id, -10**9)
        if self.tick_n - last < 20:
            return
        self._recover_cooldown[v.id] = self.tick_n
        # a) 重规划绕行：仅当找到与当前不同的可行路径才算成功
        #    （规划器允许终点被他车占据，若结果与现状相同则继续让路/改派）
        goal = self._current_goal(v)
        if goal is not None and goal != v.cell:
            path = self.planner.plan(v.cell, goal, v.facing, v.carrying is not None,
                                     self.occupied_by_others(v.id), max_nodes=15_000)
            if path is not None and path != v.path:
                v.path = path
                v.state = v.prev_state or VehicleState.TO_PICKUP
                v.prev_state = None
                v.way_target = None
                v.stall_ticks = 0
                events.append({"kind": "stall_recovered", "vehicle_id": v.id,
                               "detail": "rerouted"})
                changed_v.add(v.id)
                return
        # b) 让路：请挡路的空闲/等待/受阻车移向邻近空格
        blocker = self.occupant.get(goal) if goal else None
        if blocker is None and v.path:
            blocker = self.occupant.get(v.path[0])
        blocker = blocker or v.wait_reason
        bv = self.vehicles.get(blocker or 0)
        if bv is not None and bv.id != v.id and \
                bv.state in (VehicleState.IDLE, VehicleState.WAITING,
                             VehicleState.STALLED):
            if self._make_way(bv, changed_v):
                events.append({"kind": "stall_recovered", "vehicle_id": v.id,
                               "detail": f"vehicle {bv.id} making way"})
                return
        # c) 改派：任务回收重派；无任务车（充电/让路途中）取消当前行程回 idle
        if v.task is not None:
            if v.carrying is not None:
                # 载货受阻：就地落箱（料箱落格、任务起点更新后重派），
                # 严禁带着料箱去执行新任务（FR-015 载货车不可进静置箱格）
                if self._drop_tote_here(v):
                    v.task.pickup = v.cell  # 任务起点 = 料箱当前位置
                    v.assigned_count = max(0, v.assigned_count - 1)
                    self.dispatcher.unassign(v.task)
                    v.task.assigned_vehicle = None
                    v.task = None
                    v.state = VehicleState.IDLE
                    v.stall_ticks = 0
                    events.append({"kind": "stall_recovered", "vehicle_id": v.id,
                                   "detail": "tote dropped, task reassigned"})
                    changed_v.add(v.id)
                # 当前格不可落箱（桩位/站点/已有箱）：保持受阻，等待下轮恢复
                return
            v.assigned_count = max(0, v.assigned_count - 1)
            self.dispatcher.unassign(v.task)
            v.task = None
            v.state = VehicleState.IDLE
            v.stall_ticks = 0
            events.append({"kind": "stall_recovered", "vehicle_id": v.id,
                           "detail": "task reassigned"})
            changed_v.add(v.id)
        else:
            # 充电/让路行程不可达：取消行程（need_charge 保留，稍后重试）
            self._release_pile_claim(v)
            if v.id in self.grid.charging.queue:
                self.grid.charging.queue.remove(v.id)
            v.charge_target = None
            v.state = VehicleState.IDLE
            v.stall_ticks = 0
            events.append({"kind": "stall_recovered", "vehicle_id": v.id,
                           "detail": "trip cancelled"})

    # ---- 停滞改派（002 FR-002，research.md D4）----

    def _reassign_stalled_tasks(self, changed_v: set[int], events: list[dict]):
        """任务无进度超过 REASSIGN_TICKS 的「未取货」任务自动改派（阈值兜底）。

        口径：task.last_progress_tick 起算执行车持续无移动时长——恢复阶梯
        （FR-012）负责卡死车辆的常规回收（dispatcher.unassign 亦计改派），
        本方法兜底"重规划持续成功但任务始终无法推进"的形态。
        仅处理 ASSIGNED（尚未取货）任务；料箱/点位认领保持不变（防双重认领）。
        """
        if not self.govern_reassign:
            return
        d = self.dispatcher
        for task in list(d.tasks.values()):
            if task.state != TaskState.ASSIGNED:
                continue
            v = self.vehicles.get(task.assigned_vehicle)
            if v is None:
                continue
            task.stall_ticks = v.stall_ticks
            if self.tick_n - task.last_progress_tick < cfg.REASSIGN_TICKS:
                continue
            task.assigned_vehicle = None
            task.state = TaskState.QUEUED
            task.reassign_count += 1
            d.ledger_reassigned += 1
            d.task_queue.append(task.id)
            if v.task is task:
                v.task = None
            v.path = []
            v.state = VehicleState.IDLE
            changed_v.add(v.id)
            events.append({"kind": "governance_event", "action": "reassigned",
                           "vehicles": [v.id],
                           "detail": f"task #{task.id} no progress "
                                     f"{cfg.REASSIGN_TICKS}+ ticks, reassigned"})

    def _make_way(self, v: Vehicle, changed_v: set[int]) -> bool:
        """空闲/等待车短距离避让（FR-028 例外移动）。"""
        for cand in self._neighbors(v.cell):
            if (self.grid.passable(cand) and cand not in self.occupant
                    and not (self.grid.is_slot(cand) and self._tote_at(cand))):
                path = self.planner.plan_any_dir(v.cell, cand, False,
                                                 self.occupied_by_others(v.id))
                if path:
                    if v.prev_state is None:
                        v.prev_state = v.state
                    v.state = VehicleState.MAKING_WAY
                    v.way_target = cand
                    v.path = path
                    v.stall_ticks = 0
                    changed_v.add(v.id)
                    return True
        return False

    def _neighbors(self, cell: Cell) -> list[Cell]:
        r, c = cell
        return [(r - 1, c), (r, c + 1), (r + 1, c), (r, c - 1)]

    # ---- 冲突事件（FR-022）----

    def _conflict_detection(self):
        """互堵判定：等待关系图中 ≥2 台车成团（含互等环）。

        002 期口径修正（SC-003 可比性）：仅"阻挡车本身未推进"（≥ CONFLICT_BLOCK_TICKS
        无位移）的等待边才计入互堵——002 硬件模型下载货车 0.35~0.7 格/tick，跟车等待
        属正常交通行为而非互堵；001 期 1 格/tick 下该情形不存在，不过滤则同口径频次
        虚高一个量级。
        """
        if not self._wait_graph:
            clusters = []
        else:
            blocked_edges = {
                (a, b) for a, b in self._wait_graph.items()
                if b in self.vehicles
                and self.tick_n - self.vehicles[b].last_move_tick
                >= cfg.CONFLICT_BLOCK_TICKS
            }
            if not blocked_edges:
                clusters = []
            else:
                nodes = {n for edge in blocked_edges for n in edge}
                adj = {n: set() for n in nodes}
                for a, b in blocked_edges:
                    if a in nodes and b in nodes:
                        adj[a].add(b)
                        adj[b].add(a)
                seen, clusters = set(), []
                for n in nodes:
                    if n in seen:
                        continue
                    comp, stack = set(), [n]
                    while stack:
                        cur = stack.pop()
                        if cur in comp:
                            continue
                        comp.add(cur)
                        stack.extend(adj[cur] - comp)
                    seen |= comp
                    if len(comp) >= 2:
                        clusters.append(frozenset(comp))
        for ev in self._active_conflicts:
            for cl in clusters:
                if ev.vehicles and cl & set(ev.vehicles):
                    ev.vehicles = sorted(set(ev.vehicles) | cl)
                    break
        for cl in clusters:
            if not any(cl & set(ev.vehicles) for ev in self._active_conflicts):
                vids = sorted(cl)
                # （002 T045 修复存量 bug：此处曾重复 append 两次，互堵事件被双倍计数）
                self._active_conflicts.append(ConflictEvent(
                    start_tick=self.tick_n, vehicles=vids,
                    positions=[self.vehicles[v].cell for v in vids],
                    carried=[self.vehicles[v].carrying is not None for v in vids]))

    def _finalize_conflicts(self):
        active_ids = {v for ev in self._active_conflicts for v in ev.vehicles}
        waiting = set(self._wait_graph)
        keep = []
        for ev in self._active_conflicts:
            if set(ev.vehicles) & waiting:
                keep.append(ev)
                continue
            ev.end_tick = self.tick_n
            dur = ev.end_tick - ev.start_tick
            if dur >= cfg.CONFLICT_MIN_TICKS:
                self.conflict_events.append(ev)
                self._conflict_end_log.append(ev.end_tick)
                self._update_hotspots(ev)
                if self._conflict_fh:
                    self._conflict_fh.write(json.dumps(ev.to_json(), ensure_ascii=False) + "\n")
                    self._conflict_fh.flush()
        self._active_conflicts = keep

    # ---- 热点优化（FR-024，research.md R9：与 TURN_PENALTY 正交）----

    HOTSPOT_WEIGHT = 2.0       # 每次冲突事件对涉事格的代价增量
    HOTSPOT_DECAY_TICKS = 500  # 每 500 tick 衰减一半（滚动适应）

    def _update_hotspots(self, ev: ConflictEvent):
        if not getattr(cfg, "CONFLICT_OPTIMIZATION", True):
            return
        for pos in ev.positions:
            self.planner.hotspots[pos] = (self.planner.hotspots.get(pos, 0.0)
                                          + self.HOTSPOT_WEIGHT)
        # 涉事车辆被堵的目标格也算热点
        for vid in ev.vehicles:
            v = self.vehicles.get(vid)
            if v is not None and v.path:
                nxt = v.path[0]
                self.planner.hotspots[nxt] = (self.planner.hotspots.get(nxt, 0.0)
                                              + self.HOTSPOT_WEIGHT)

    def _decay_hotspots(self):
        if self.tick_n % self.HOTSPOT_DECAY_TICKS == 0 and self.planner.hotspots:
            for c in list(self.planner.hotspots):
                w = self.planner.hotspots[c] * 0.5
                if w < 0.1:
                    del self.planner.hotspots[c]
                else:
                    self.planner.hotspots[c] = w

    # ---- 快照与增量（契约 rest-api.md / websocket.md）----

    def snapshot(self) -> dict:
        g = self.grid
        return {
            "tick": self.tick_n,
            "paused": self.paused,
            "grid": {
                "size": g.size,
                "slots": [list(c) for c in g.slots],
                "inbound": {"rows": list(g.inbound_rows),
                            "cols": list(g.inbound_cols)},
                "outbound": {"rows": list(g.outbound_rows),
                             "cols": list(g.outbound_cols)},
                "charging_area": {
                    "piles": [list(c) for c in g.charging.piles],
                    "entry": [list(c) for c in g.charging.entry_lane],
                    "exit": [list(c) for c in g.charging.exit_lane]},
                "obstacles": [list(c) for c in g.obstacles],
            },
            "vehicles": [self._vehicle_json(v) for v in self.vehicles.values()],
            "totes": [{"id": t.id, **self._tote_loc(t), "state": t.state.value}
                      for t in self.totes.values()],
            "charging": {
                "pile_owner": {f"{r},{c}": vid for (r, c), vid in
                               g.charging.pile_owner.items() if vid is not None},
                "queue": list(g.charging.queue)},
            "metrics": self._metrics(),
            "ledger": self.dispatcher.ledger(),
            "metrics_history": list(self.metrics_history)[-360:],
        }

    def _vehicle_json(self, v: Vehicle) -> dict:
        return {"id": v.id, "cell": list(v.cell), "state": v.state.value,
                "task_id": v.task.id if v.task else None,
                "completed": v.completed_count, "battery": int(v.battery)}

    def _tote_loc(self, t: Tote) -> dict:
        if isinstance(t.location, tuple) and len(t.location) == 2 \
                and all(isinstance(x, int) for x in t.location):
            return {"at": list(t.location)}  # type: ignore[arg-type]
        if isinstance(t.location, tuple):  # ("carried", vid)
            return {"carried_by": t.location[1]}
        return {"at": t.location}  # "inbound" / "outbound"

    def _metrics(self) -> dict:
        waiting = sum(1 for v in self.vehicles.values()
                      if v.state in (VehicleState.WAITING, VehicleState.STALLED))
        stalled = sum(1 for v in self.vehicles.values()
                      if v.state == VehicleState.STALLED)
        charging = sum(1 for v in self.vehicles.values()
                       if v.state == VehicleState.CHARGING)
        return {
            "tasks_done": self.metrics_tasks_done,
            "waiting": waiting,
            "stalled": stalled,
            "collision": self.collision_count,
            "charging": charging,
            "charge_queue": len(self.grid.charging.queue),
            "avg_battery": round(sum(v.battery for v in self.vehicles.values())
                                 / max(len(self.vehicles), 1) / cfg.BATTERY_FULL * 100),
            "per_vehicle_completed": [self.vehicles[i].completed_count
                                      for i in sorted(self.vehicles)],
        }

    def vehicle_detail(self, vid: int) -> dict | None:
        v = self.vehicles.get(vid)
        if v is None:
            return None
        d = self._vehicle_json(v)
        d["path"] = [list(c) for c in v.path]
        d["task"] = ({"id": v.task.id, "type": v.task.type,
                      "pickup": list(v.task.pickup), "dropoff": list(v.task.dropoff),
                      "state": v.task.state.value} if v.task else None)
        d["need_charge"] = v.need_charge
        d["progress"] = round(v.progress, 3)
        d["defer_charge"] = v.defer_charge
        d["reassign_count"] = v.task.reassign_count if v.task else 0
        d["state_history"] = [{"tick": t, "state": s}
                              for t, s in self._state_hist.get(v.id, [])]
        return d

    # ---- 障碍物操作（FR-009/010/011，契约 rest-api.md）----

    def add_obstacle(self, cell: Cell) -> tuple[bool, str]:
        occupant = self.occupant.get(cell)
        # 预约占用：已有车将下一步进入该格也视为 reserved（契约 409 语义）
        if occupant is None and self.reservations.owner(cell) is not None:
            return False, "cell occupied or reserved"
        ok, reason = self.grid.can_add_obstacle(cell, occupant,
                                                self._tote_at(cell))
        if not ok:
            return False, reason
        if self.grid.add_obstacle(cell, self.tick_n):
            self._pending_events.append({"type": "obstacle_changed",
                                         "tick": self.tick_n,
                                         "added": [list(cell)], "removed": []})
        self._replan_affected(cell)
        return True, ""

    def remove_obstacle(self, cell: Cell) -> bool:
        if not self.grid.remove_obstacle(cell):
            return False
        self._pending_events.append({"type": "obstacle_changed",
                                     "tick": self.tick_n,
                                     "added": [], "removed": [list(cell)]})
        self._replan_affected(cell)
        return True

    def _replan_affected(self, cell: Cell):
        """FR-011：路径经过该格的车清空路径触发重规划。"""
        for v in self.vehicles.values():
            if cell in v.path or v.cell == cell:
                v.path = []

    def reset(self):
        """POST /api/reset：重置到初始布局。"""
        self.tick_n = 0
        self.paused = False
        self.collision_count = 0
        self.metrics_tasks_done = 0
        self.vehicles.clear()
        self.totes.clear()
        self.occupant.clear()
        self.docked_cells.clear()
        self.inbound_pool.clear()
        self.cycling.clear()
        self.grid.obstacles.clear()
        self.grid.blocked = {c for c, k in self.grid.kind.items()
                             if k == CellKind.BOUNDARY}  # 障碍物清空后重建 blocked
        self.dispatcher = Dispatcher(self.grid, self.rng)
        self.dispatcher.docked_cells = self.docked_cells
        self.planner.hotspots.clear()
        self._recover_cooldown.clear()
        self._plan_fail_tick.clear()
        self._charge_try_tick.clear()
        self._charge_retry_tick.clear()
        self._park_try_tick.clear()
        self._parked_ok.clear()
        self.conflict_events.clear()
        self._active_conflicts.clear()
        self.metrics_history.clear()
        self._done_cum_log.clear()
        self._cycle_log.clear()
        self._conflict_end_log.clear()
        self._done_base_cum = 0
        self._state_hist.clear()
        self._init_world()
