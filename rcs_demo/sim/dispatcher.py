"""任务生成（1:1 成对）与均匀分配（least-loaded）。

research.md R4：出库任务随机选「有料箱的 slot」，入库任务随机选「空 slot」；
两类任务按 1:1 成对生成（FR-018）；分配选累计任务数最少的车（平局随机），
保证任意两车差 ≤1（远优于 ±20%，FR-004）。
"""
from __future__ import annotations

import random
from collections import deque

from .entities import Cell, Task, TaskState, Tote, ToteState, Vehicle, VehicleState
from .grid import Grid
from .. import config as cfg


class Dispatcher:
    def __init__(self, grid: Grid, rng: random.Random):
        self.grid = grid
        self.rng = rng
        self.docked_cells: set[Cell] = set()  # 引擎注入（静置料箱格，O(1) 查询）
        self.tasks: dict[int, Task] = {}
        self.task_queue: deque[int] = deque()
        self.next_task_id = 1
        self.generated_in = 0
        self.generated_out = 0
        self.gen_parity = 0  # 0→生成入库，1→生成出库（1:1 交替，节奏=每秒 1 任务）
        self.active_in = 0   # 在途入库任务数（限制入库任务不超过入库口料箱供给）
        self.ticks_since_gen = 0
        self._claimed_totes: set[int] = set()   # 已被任务认领的料箱
        self._claimed_slots: set[Cell] = set()  # 已被入库任务认领的空点位

    # ---- 任务生成（FR-003/FR-018）----

    def maybe_generate(self, tick: int, totes: dict[int, Tote],
                       inbound_pool: list[int]):
        """每秒生成 1 个任务，入库/出库交替（成对 1:1）。"""
        self.ticks_since_gen += 1
        if self.ticks_since_gen < cfg.TICK_RATE / max(cfg.TASKS_PER_SECOND, 1e-9):
            return
        self.ticks_since_gen = 0
        # 入库优先级翻转，保证两种都可行时交替生成
        order = [0, 1] if self.gen_parity == 0 else [1, 0]
        self.gen_parity ^= 1
        for want in order:
            t = self._generate_one(want == 0, tick, totes, inbound_pool)
            if t is not None:
                self.task_queue.append(t.id)
                return

    def _generate_one(self, inbound: bool, tick: int, totes: dict[int, Tote],
                      inbound_pool: list[int]) -> Task | None:
        if inbound:
            # 供给约束：入库任务数不得超过入库口待入库料箱（否则车辆空跑饿死）
            if len(inbound_pool) <= self.active_in:
                return None
            # 目标点位：随机「空 slot」且未被其他入库任务认领
            empty = [s for s in self.grid.slots
                     if s not in self._claimed_slots and s not in self.docked_cells]
            if not empty:
                return None
            dropoff = self.rng.choice(empty)
            # 取货点：入库口随机一格
            pickup = self.rng.choice(self.grid.inbound_cells)
            tote_id = -1  # 到达入库口时领取循环回归的料箱
            self._claimed_slots.add(dropoff)
            self.generated_in += 1
            self.active_in += 1
        else:
            # 随机「有料箱的 slot」（未被认领）：直接从静置格集合取
            cands = [t for t in totes.values()
                     if t.state == ToteState.DOCKED and isinstance(t.location, tuple)
                     and t.id not in self._claimed_totes]
            # （totes 规模恒定 ~1200，此扫描每秒一次可接受）
            if not cands:
                return None
            tote = self.rng.choice(cands)
            pickup = tote.location  # type: ignore[assignment]
            dropoff = self.rng.choice(self.grid.outbound_cells)
            tote_id = tote.id
            self._claimed_totes.add(tote.id)
            self.generated_out += 1
        t = Task(id=self.next_task_id, type="inbound" if inbound else "outbound",
                 pickup=pickup, dropoff=dropoff, tote_id=tote_id,
                 state=TaskState.QUEUED)
        self.next_task_id += 1
        self.tasks[t.id] = t
        return t

    @staticmethod
    def _slot_has_tote(slot: Cell, totes: dict[int, Tote]) -> bool:
        # 兼容保留：常规路径用 docked_cells 集合（O(1)）
        return any(t.state == ToteState.DOCKED and t.location == slot for t in totes.values())

    def release_claims(self, task: Task):
        self._claimed_totes.discard(task.tote_id)
        self._claimed_slots.discard(task.dropoff if task.type == "inbound" else Cell((0, 0)))
        if task.type == "inbound":
            self._claimed_slots.discard(task.dropoff)
            if task.state == TaskState.DONE:
                self.active_in -= 1

    # ---- 均匀分配（FR-004 / R4）----

    def assignable_vehicles(self, vehicles: dict[int, Vehicle]) -> list[Vehicle]:
        cands = [v for v in vehicles.values()
                 if v.state == VehicleState.IDLE and v.task is None and v.dispatchable()]
        # least-loaded：累计「分配数」最少优先（FR-004 度量口径；平局按 id 稳定）
        cands.sort(key=lambda v: (v.assigned_count, v.id))
        return cands

    def peek_task(self) -> Task | None:
        """按 FIFO 查看第一个可派任务（不出队）；清理已完成的脏引用。"""
        seen = set()
        for tid in list(self.task_queue):
            if tid in seen:
                self.task_queue.remove(tid)  # 防御：去重
                continue
            seen.add(tid)
            t = self.tasks[tid]
            if t.state in (TaskState.QUEUED, TaskState.BLOCKED):
                return t
            if t.state in (TaskState.DONE, TaskState.ASSIGNED, TaskState.IN_PROGRESS):
                self.task_queue.remove(tid)
        return None

    def rotate_task(self, task: Task):
        """不可达任务移到队尾（避免头阻塞），稍后重试。"""
        try:
            self.task_queue.remove(task.id)
        except ValueError:
            pass
        self.task_queue.append(task.id)

    def take_task(self, task: Task) -> Task:
        """从队列移除并标记已派。"""
        try:
            self.task_queue.remove(task.id)
        except ValueError:
            pass
        task.state = TaskState.ASSIGNED
        return task

    def unassign(self, task: Task):
        """任务回收重派（死锁恢复 c 步）。"""
        if task.assigned_vehicle is not None:
            task.assigned_vehicle = None
        task.state = TaskState.QUEUED
        self.task_queue.appendleft(task.id)

    def stats(self) -> dict:
        return {"generated_in": self.generated_in, "generated_out": self.generated_out,
                "queued": sum(1 for t in self.tasks.values() if t.state == TaskState.QUEUED)}
