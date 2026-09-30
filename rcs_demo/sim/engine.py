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
    def __init__(self, seed: int | None = None, conflict_log_dir: str | None = None):
        self.rng = random.Random(seed)
        self.grid = Grid()
        self.planner = Planner(self.grid, self._tote_at)
        self.reservations = ReservationTable()
        self.dispatcher = Dispatcher(self.grid, self.rng)
        self.charging = ChargingManager(self.grid.charging)

        self.tick_n = 0
        self.paused = False
        self.collision_count = 0

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
        self._park_try_tick: dict[int, int] = {}
        self._parked_ok: set[Cell] = set()  # 已靠边停好的车所在格
        self._delta: dict = {"tick": 0, "vehicles": [], "totes": [], "metrics": {}}
        self._pending_events: list[dict] = []

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
        for i in range(cfg.VEHICLES):
            cell = valid[i % len(valid)]
            v = Vehicle(id=i + 1, cell=cell, render_from=cell,
                        battery=cfg.BATTERY_FULL)
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
        self._wait_graph.clear()
        changed_v: set[int] = set()
        changed_t: set[int] = set()
        events: list[dict] = []

        self.dispatcher.maybe_generate(self.tick_n, self.totes, self.inbound_pool)
        self._assign_tasks(changed_v, events)
        self._plan_paths(changed_v)
        self._move_vehicles(changed_v, changed_t, events)
        self._handle_arrivals(changed_v, changed_t, events)
        self._charge_progress(changed_v, changed_t, events)
        self._charge_queue_scheduling(changed_v, events)
        self._cycling_advance(changed_t)
        self._need_charge_flags(changed_v, events)
        self._deadlock_detection(changed_v, events)
        self._conflict_detection()
        self._finalize_conflicts()
        self._decay_hotspots()
        self._build_delta(changed_v, changed_t, events)

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

    def last_delta(self) -> dict:
        return self._delta

    def drain_ws_events(self) -> list[dict]:
        ev = self._pending_events
        self._pending_events = []
        return ev

    # ---- 派单（FR-004 / R4）----

    def _assign_tasks(self, changed_v: set[int], events: list[dict]):
        # WIP 限流（规格假设：基准负载约 80 台在途、20 台空闲待命）：
        # 在途任务达上限时停止派发，防止全队投入引发拥塞死亡螺旋
        active = sum(1 for t in self.dispatcher.tasks.values()
                     if t.state in (TaskState.ASSIGNED, TaskState.IN_PROGRESS))
        if active >= cfg.MAX_ACTIVE_TASKS:
            return
        attempts = 0
        for v in self.dispatcher.assignable_vehicles(self.vehicles):
            if attempts >= 3:  # 每 tick 限流尝试，避免不可达任务拖垮派单
                break
            task = self.dispatcher.peek_task()
            if task is None:
                break
            attempts += 1
            path = self.planner.plan(v.cell, task.pickup, v.facing, False,
                                     self.occupied_by_others(v.id), max_nodes=60_000)
            if path is None:
                if task.state != TaskState.BLOCKED:
                    task.state = TaskState.BLOCKED
                    events.append({"kind": "task_blocked", "task_id": task.id})
                self.dispatcher.rotate_task(task)  # 移到队尾，障碍物变更后自动重试
                continue
            self.dispatcher.take_task(task)
            task.assigned_vehicle = v.id
            v.assigned_count += 1
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
                                     self.occupied_by_others(v.id), max_nodes=50_000)
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
        # 公平仲裁：等待最久者优先预约（防饿死）；WAITING 车每 tick 重试
        movers = [v for v in self.vehicles.values()
                  if v.path and v.state not in (VehicleState.IDLE,
                                                VehicleState.CHARGING,
                                                VehicleState.STALLED)]
        movers.sort(key=lambda v: (-v.stall_ticks, v.id))
        for v in movers:
            nxt = v.path[0]
            # 障碍物/围墙校验（FR-011：绝不驶入障碍格）
            if not self.planner.can_enter(nxt, v.carrying is not None):
                v.path = []  # 触发重规划
                continue
            other = self.occupant.get(nxt)
            if other is not None and other != v.id:
                if v.state != VehicleState.WAITING:
                    v.prev_state = v.state
                v.state = VehicleState.WAITING
                v.wait_reason = other
                self._wait_graph[v.id] = other
                continue
            if not self.reservations.try_reserve(nxt, v.id):
                owner = self.reservations.owner(nxt)
                if v.state != VehicleState.WAITING:
                    v.prev_state = v.state
                v.state = VehicleState.WAITING
                v.wait_reason = owner
                if owner:
                    self._wait_graph[v.id] = owner
                continue
            # 执行移动（一 tick 一格）
            del self.occupant[v.cell]
            v.render_from = v.cell
            v.cell = nxt
            v.path.pop(0)
            self.occupant[nxt] = v.id
            dr, dc = nxt[0] - v.render_from[0], nxt[1] - v.render_from[1]
            v.facing = {(0, 1): "E", (0, -1): "W", (1, 0): "S", (-1, 0): "N"}.get(
                (dr, dc), v.facing)
            # 电量消耗（FR-025：空载 1 点/格，载货 1.5 点/格）
            v.battery = max(0.0, v.battery - (cfg.LOAD_FACTOR if v.carrying else 1.0))
            v.stall_ticks = 0
            v.moved_this_tick = True
            if v.state == VehicleState.WAITING:
                v.state = v.prev_state or VehicleState.TO_PICKUP
                v.prev_state = None
                v.wait_reason = None
            changed_v.add(v.id)
            if v.carrying is not None:
                changed_t.add(v.carrying.id)
        # 未移动的 mover 恢复计时
        for v in movers:
            if not v.moved_this_tick:
                v.stall_ticks += 1
            v.moved_this_tick = False

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
        v.task = None
        v.completed_count += 1
        self.metrics_tasks_done += 1
        v.state = VehicleState.IDLE
        changed_v.add(v.id)
        self.dispatcher.release_claims(task)
        # FR-025：完成任务瞬间若需充电 → 派发充电行程（优先于新任务）
        if v.need_charge and v.battery < cfg.battery_low():
            self._start_charge_trip(v, changed_v, events)

    # ---- 充电（FR-025/026，R10）----

    def _start_charge_trip(self, v: Vehicle, changed_v: set[int],
                           events: list[dict]):
        pile = self.charging.free_pile()
        if (pile is not None and pile in self.occupant
                and self.occupant[pile] != v.id):
            pile = None  # 充电位被滞留车物理占据：按无空桩处理（进排队）
        if pile is None:
            # 桩满 → 排队格等待（电量更低者优先出队）
            target = self.charging.assign_queue_cell(
                v, set(self.occupant) - {v.cell})
            if target is None:
                return  # 排队格也满：留在原地稍后再试
            v.state = VehicleState.TO_CHARGE
            v.charge_target = target
            self.charging.enqueue(v.id, v.battery)
            changed_v.add(v.id)
            return
        path = self.planner.plan_any_dir(v.cell, pile, False,
                                         self.occupied_by_others(v.id))
        if path is None:
            return  # 暂不可达：下 tick 重试（pile 未被认领）
        self.area_claim_pile(pile, v.id)
        v.state = VehicleState.TO_CHARGE
        v.charge_target = pile
        v.path = path
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
        while True:
            got = self.charging.dequeue_for_pile(
                {v.id: v.battery for v in self.vehicles.values()})
            if got is None:
                break
            vid, pile = got
            v = self.vehicles.get(vid)
            if v is None or not v.need_charge or v.state == VehicleState.CHARGING:
                continue
            # 充电位仍被滞留车物理占据（充满未驶离）→ 暂缓分配，防其堵在桩旁
            if pile in self.occupant and self.occupant[pile] != vid:
                self.charging.area.queue.insert(0, vid)
                break
            # 先释放该车此前认领的充电位（防重复认领）
            self._release_pile_claim(v)
            path = self.planner.plan_any_dir(v.cell, pile, False,
                                             self.occupied_by_others(v.id))
            if path is None:
                self.charging.area.queue.insert(0, vid)  # 暂不可达：回队
                break
            self.area_claim_pile(pile, vid)
            v.charge_target = pile
            v.state = VehicleState.TO_CHARGE
            v.prev_state = None
            v.path = path
            changed_v.add(v.id)

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
                # 空闲车立即出发；执行任务的车等任务完成（_dropoff 触发）
                if v.state == VehicleState.IDLE:
                    self._start_charge_trip(v, changed_v, events)
            # 兜底（FR-026/SC-010）：电量临界仍未充电 → 放弃当前等待，直接去充电
            # 载货车：就地落箱（任务起点更新后重派），保证任何车辆不电量归零
            if (v.battery < cfg.battery_critical()
                    and v.state not in (VehicleState.CHARGING, VehicleState.TO_CHARGE)):
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
                if v.id in self.grid.charging.queue:
                    self.grid.charging.queue.remove(v.id)
                v.state = VehicleState.IDLE
                v.prev_state = None
                v.way_target = None
                v.stall_ticks = 0
                self._start_charge_trip(v, changed_v, events)
                changed_v.add(v.id)
            # 状态清理：无处可等的 WAITING 僵尸（无路径、不在充电排队）→ 回 idle
            if (v.state == VehicleState.WAITING and not v.path
                    and not (v.cell in self.grid.charging.queue_cells and v.need_charge)):
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
        for best in self._pick_parking_slots(v):
            path = self.planner.plan_any_dir(v.cell, best, False,
                                             self.occupied_by_others(v.id),
                                             max_nodes=30_000)
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
        """空闲车就近挪到存储点位格待命（不占通道/工作站，FR-028 靠边待命）。"""
        if self.tick_n - self._park_try_tick.get(v.id, -10**9) < 30:
            return  # 冷却，防反复尝试
        self._park_try_tick[v.id] = self.tick_n
        r, c = v.cell
        k = self.grid.kind.get(v.cell)
        if k in (CellKind.INBOUND, CellKind.OUTBOUND):
            # 工作站放置位：半径搜索必然失败（附近无点位）→ 全局疏散，
            # 选点用「就近 + 分散」（_pick_parking_slots，澄清 2026-09-30 选项 A）
            for best in self._pick_parking_slots(v):
                path = self.planner.plan(v.cell, best, v.facing, False,
                                         self.occupied_by_others(v.id),
                                         max_nodes=30_000)
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
                                     self.occupied_by_others(v.id), max_nodes=40_000)
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
        """互堵判定：等待关系图中 ≥2 台车成团（含互等环）。"""
        if not self._wait_graph:
            clusters = []
        else:
            nodes = set(self._wait_graph) | set(self._wait_graph.values())
            nodes = {n for n in nodes if n in self.vehicles}
            adj = {n: set() for n in nodes}
            for a, b in self._wait_graph.items():
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
                self._active_conflicts.append(ConflictEvent(
                    start_tick=self.tick_n, vehicles=vids,
                    positions=[self.vehicles[v].cell for v in vids],
                    carried=[self.vehicles[v].carrying is not None for v in vids]))
                vids = sorted(cl)
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
                "inbound": {"rows": list(cfg.INBOUND_ROW_RANGE),
                            "cols": list(cfg.INBOUND_COL_RANGE)},
                "outbound": {"rows": list(cfg.OUTBOUND_ROW_RANGE),
                             "cols": list(cfg.OUTBOUND_COL_RANGE)},
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
        self._park_try_tick.clear()
        self._parked_ok.clear()
        self.conflict_events.clear()
        self._active_conflicts.clear()
        self._init_world()
