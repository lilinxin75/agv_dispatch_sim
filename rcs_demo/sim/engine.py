"""仿真引擎：tick 推进、零碰撞移动、任务执行、料箱循环、指标与增量。"""

from __future__ import annotations

import random
from collections import deque

from rcs_demo.config import Config
from rcs_demo.sim import entities as E
from rcs_demo.sim.conflicts import ConflictRecorder
from rcs_demo.sim.dispatcher import Dispatcher
from rcs_demo.sim.grid import CellKind, CollisionError, Grid, ObstacleError
from rcs_demo.sim.planner import Reservations, astar, planner_self_test

FACING = {
    (-1, 0): "N", (1, 0): "S", (0, -1): "W", (0, 1): "E",
}


class Engine:
    def __init__(self, cfg: Config, seed: int | None = None):
        self.cfg = cfg
        self.rnd = random.Random(seed)
        self.grid = Grid(cfg)
        self.reservations = Reservations()
        self.dispatcher = Dispatcher()

        self.tick = 0
        self.paused = False
        self.vehicles: dict[int, E.Vehicle] = {}
        self.totes: dict[int, E.Tote] = {}
        self.tasks: dict[int, E.Task] = {}
        self.task_queue: list[int] = []          # FIFO（含 blocked，按状态过滤）
        self.blocked: set[int] = set()
        self.collision_count = 0
        self._next_task_id = 1
        self._next_tote_id = 1

        self.events: deque = deque(maxlen=cfg.max_events)
        self._pending_events: list[dict] = []
        self._moves: dict[int, tuple] = {}       # vid -> (from, to)
        self._touched_vehicles: set[int] = set()
        self._touched_totes: set[int] = set()
        self.metrics: dict = {}

        # --- Phase 9：冲突记录 + 拥堵热力 + 带箱避让集合 ---
        self.recorder = ConflictRecorder(
            data_dir=cfg.data_dir, enabled=cfg.record_conflicts,
        )
        self.wait_heat: dict[tuple, float] = {}  # 等待密度热力（拥堵加权）
        self.tote_cells: set[tuple] = set()      # 静置料箱格（带箱车禁入）

        self.stats = {
            "tasks_generated": {"inbound": 0, "outbound": 0},
            "tasks_done": {"inbound": 0, "outbound": 0},
            "totes_recycled": 0,
        }

        self._init_world()

    # ================================================================ 初始化
    def _init_world(self) -> None:
        cfg = self.cfg
        interior = [
            (r, c)
            for r in range(2, self.grid.size - 2)
            for c in range(2, self.grid.size - 2)
            if self.grid.is_passable(r, c)
        ]
        self.rnd.shuffle(interior)
        for i in range(cfg.vehicle_count):
            r, c = interior[i]
            v = E.Vehicle(id=i + 1, cell=(r, c))
            self.vehicles[v.id] = v
            self.grid.set_occupant(r, c, v.id)

        slots = list(self.grid.slots)
        self.rnd.shuffle(slots)
        n_fill = int(len(slots) * cfg.fill_ratio)
        for r, c in slots[:n_fill]:
            self._new_tote((r, c))
        inbound = [c for c in self.grid.inbound_cells
                   if self.grid.is_passable(*c)]
        for r, c in inbound[: cfg.inbound_initial_totes]:
            self._new_tote((r, c))
        self.tote_cells = {
            t.location for t in self.totes.values() if t.state == E.T_DOCKED
        }

    def _new_tote(self, location: tuple[int, int]) -> E.Tote:
        tote = E.Tote(id=self._next_tote_id, location=location)
        self._next_tote_id += 1
        self.totes[tote.id] = tote
        return tote

    # ================================================================ 主循环
    def step(self) -> None:
        self.tick += 1
        t = self.tick
        self.reservations.gc(t)
        self.dispatcher.generate(self)
        self.dispatcher.assign(self)
        self._wander()
        self.dispatcher.recovery(self)
        self._movement(t)
        self._arrivals()
        self._cycling()
        self._update_heat()
        self._update_metrics()
        self.recorder.observe(self)

    def _carrying(self, v: E.Vehicle) -> bool:
        """该车是否正携带料箱（决定 FR-015 避让静置料箱格）。"""
        return (
            v.task_id is not None
            and self.tasks[v.task_id].state == E.K_IN_PROGRESS
        )

    def _in_shelf(self, r: int, c: int) -> bool:
        cfg = self.cfg
        return (
            cfg.shelf_row_range[0] <= r <= cfg.shelf_row_range[1]
            and cfg.shelf_col_range[0] <= c <= cfg.shelf_col_range[1]
        )

    def _plan_extras(self, v: E.Vehicle):
        """astar 的 avoid / extra_cost 参数：带箱避让 + 拥堵加权 (FR-024)。"""
        avoid = self.tote_cells if self._carrying(v) else None
        if not self.cfg.conflict_optimization:
            return avoid, None
        target_outside = (
            v.target is not None and not self._in_shelf(*v.target)
        )
        salt = self.rnd.randrange(1 << 30)  # 本次规划的路径多样化抖动

        def cost(cell):
            c = self.wait_heat.get(cell, 0.0)
            # 过境车流绕行货架窄道：目标在货架区外时，货架内通道加重
            if target_outside and self._in_shelf(*cell):
                c += 3.0
            # 等价路径随机分流：不同车选不同等价路线，降低对头相遇
            c += (hash((cell[0], cell[1], salt)) & 0x7F) / 1024.0
            return c

        return avoid, cost

    def _astar(self, v: E.Vehicle, goal: tuple) -> list | None:
        """带拥堵/避箱代价的规划；扩展超限时退化为普通代价重试。

        附加代价闭包会放大 A* 单次开销，故首次尝试限制扩展数，
        失败（含不可达判定受限）后去掉拥堵代价重试一次兜底。
        """
        avoid, extra = self._plan_extras(v)
        kw = dict(
            reservations=self.reservations, now=self.tick,
            horizon=self.cfg.reserve_horizon, self_id=v.id,
            avoid=avoid, extra_cost=extra,
        )
        if extra is not None:
            # 附加代价抬高了代价面，用加权启发保持搜索贪心性
            # （有界次优，换数量级的扩展速度）
            path = astar(self.grid, v.cell, goal, max_expand=15_000,
                         h_weight=1.3, **kw)
        else:
            path = astar(self.grid, v.cell, goal, max_expand=40_000, **kw)
        if path is None and extra is not None:
            kw["extra_cost"] = None
            path = astar(self.grid, v.cell, goal, max_expand=40_000, **kw)
        return path

    def _update_heat(self) -> None:
        """记录等待密度热力（衰减），供拥堵感知路径/选点加权 (FR-024)。"""
        if not self.cfg.conflict_optimization:
            return
        for v in self.vehicles.values():
            if v.state in (E.V_WAITING, E.V_STALLED):
                self.wait_heat[v.cell] = (
                    self.wait_heat.get(v.cell, 0.0) + 1.0
                )
        if self.tick % 20 == 0 and self.wait_heat:
            self.wait_heat = {
                k: val * 0.9
                for k, val in self.wait_heat.items() if val > 0.05
            }

    # ------------------------------------------------------------- 漫游目标
    def _wander(self) -> None:
        for v in self.vehicles.values():
            if v.task_id is not None:
                # 任务车路径为空但未到目标（被重规划耗尽）→ 重新求路，
                # 否则会变成永久静止的"僵尸"
                if not v.path:
                    task = self.tasks[v.task_id]
                    goal = (
                        task.pickup if task.state == E.K_ASSIGNED
                        else task.dropoff
                    )
                    if v.cell == goal:
                        continue  # 已到位，交由 _arrivals 处理
                    path = self._astar(v, goal)
                    if path:
                        v.path = path
                        self.touch_vehicle(v.id)
                    else:
                        self._abandon_task(v, task, "objective unreachable")
                continue
            if not v.path:
                # 到达漫游点 / 让路点 → 清空目标换新目标
                if v.target is not None and v.cell == v.target:
                    v.target = None
                if v.target is not None:
                    # 路径被重规划耗尽但未到达 → 重试一次，失败则换目标
                    path = self._astar(v, v.target)
                    if path:
                        v.path = path
                        self.touch_vehicle(v.id)
                    else:
                        v.target = None
                    continue
                target = self.random_target()
                if target is None:
                    continue
                path = self._astar(v, target)
                if path is None:
                    continue
                v.target = target
                v.path = path
                v.state = E.V_IDLE
                self.touch_vehicle(v.id)

    def random_target(self) -> tuple | None:
        """漫游目标只落在通道 (FLOOR) 上；拥堵感知时避开货架窄道，
        减少漫游车流与任务车流的对头相遇 (FR-024)。"""
        size = self.grid.size
        for _ in range(8):
            r = self.rnd.randrange(2, size - 2)
            c = self.rnd.randrange(2, size - 2)
            if not self.grid.is_passable(r, c):
                continue
            if self.grid.kind_at(r, c) != CellKind.FLOOR:
                continue
            if (
                self.cfg.conflict_optimization and self._in_shelf(r, c)
            ):
                continue  # 漫游不进货架区
            return (r, c)
        return None

    # ------------------------------------------------------------- 移动
    def _movement(self, t: int) -> None:
        vids = list(self.vehicles)
        self.rnd.shuffle(vids)  # 消除处理顺序偏差
        for vid in vids:
            v = self.vehicles[vid]
            if not v.path:
                continue
            nxt = v.path[0]
            if not self.grid.is_passable(*nxt):
                # 新增障碍物挡路 → 重规划
                self._replan(v)
                continue
            if self._carrying(v) and nxt in self.tote_cells:
                # FR-015：带箱车禁入静置料箱格 → 重规划绕行
                self._replan(v)
                continue
            if self.grid.occupant_at(*nxt) is not None:
                v.state = E.V_WAITING
                v.stall_ticks += 1
                if v.stall_ticks == self.cfg.stall_threshold:
                    v.state = E.V_STALLED
                    self.push_event(
                        "stall_detected", vehicle_id=vid,
                        detail=f"blocked at {v.cell}",
                    )
                self.touch_vehicle(vid)
                continue
            if not self.reservations.claim(nxt[0], nxt[1], t, vid):
                v.state = E.V_WAITING
                v.stall_ticks += 1
                if v.stall_ticks == self.cfg.stall_threshold:
                    v.state = E.V_STALLED
                    self.push_event(
                        "stall_detected", vehicle_id=vid,
                        detail=f"reserved conflict at {v.cell}",
                    )
                self.touch_vehicle(vid)
                continue
            self._do_move(v, nxt, t)

        for v in self.vehicles.values():
            if not v.path and v.making_way:
                v.making_way = False  # sidestep 完成（任务车随后由 _wander 重归正途）
                v.state = E.V_IDLE
                self.touch_vehicle(v.id)

    def _do_move(self, v: E.Vehicle, nxt: tuple, t: int) -> None:
        old = v.cell
        dr, dc = nxt[0] - old[0], nxt[1] - old[1]
        v.facing = FACING.get((dr, dc), v.facing)
        self.grid.set_occupant(old[0], old[1], None)
        self.grid.set_occupant(nxt[0], nxt[1], v.id)
        v.cell = nxt
        v.path.pop(0)
        if v.stall_ticks >= self.cfg.stall_threshold:
            self.push_event(
                "stall_recovered", vehicle_id=v.id, detail="resumed moving",
            )
        v.stall_ticks = 0
        v.state = self._drive_state(v)
        self._moves[v.id] = (old, nxt)
        self._touched_vehicles.add(v.id)

    def _drive_state(self, v: E.Vehicle) -> str:
        if v.making_way:
            return E.V_MAKING_WAY
        if v.task_id is not None:
            task = self.tasks[v.task_id]
            return (
                E.V_TO_PICKUP if task.state == E.K_ASSIGNED else E.V_TO_DROPOFF
            )
        return E.V_IDLE

    def _replan(self, v: E.Vehicle) -> None:
        target = v.target
        if target is None or not self.grid.is_passable(*target):
            target = self.random_target()
        if target is None:
            return
        path = self._astar(v, target)
        v.path = path or []
        self._touched_vehicles.add(v.id)

    # ------------------------------------------------------------- 到站装卸
    def _arrivals(self) -> None:
        for v in list(self.vehicles.values()):
            if v.path or v.task_id is None:
                continue
            task = self.tasks[v.task_id]
            if v.cell == task.pickup:
                self._pickup(v, task)
            elif v.cell == task.dropoff and task.state == E.K_IN_PROGRESS:
                self._dropoff(v, task)

    def _pickup(self, v: E.Vehicle, task: E.Task) -> None:
        tote = self.totes[task.tote_id]
        claimable = tote.claimed_by_task in (None, task.id)
        if (
            tote.state == E.T_DOCKED
            and tote.location == task.pickup
            and claimable
        ):
            tote.claimed_by_task = task.id
            tote.state = E.T_CARRIED
            tote.carried_by = v.id
            task.state = E.K_IN_PROGRESS
            self.tote_cells.discard(tote.location)
            path = self._astar(v, task.dropoff)
            if path is None:
                self._abandon_task(v, task, "dropoff unreachable")
                return
            v.state = E.V_TO_DROPOFF
            v.target = task.dropoff
            v.path = path
            self._touched_totes.add(tote.id)
            self.touch_vehicle(v.id)
        else:
            self._abandon_task(v, task, "tote missing at pickup")

    def _dropoff(self, v: E.Vehicle, task: E.Task) -> None:
        tote = self.totes[task.tote_id]
        tote.state = (
            E.T_CYCLING if task.type == E.OUTBOUND else E.T_DOCKED
        )
        tote.location = v.cell
        tote.carried_by = None
        tote.claimed_by_task = None
        if task.type == E.INBOUND:
            self.tote_cells.add(v.cell)  # 入库料箱落位 → 带箱车禁入
        if task.type == E.OUTBOUND:
            tote.cycle_timer = self.cfg.cycling_ticks
            self.grid.release_claim(*task.dropoff, task.id)
        else:
            self.grid.release_claim(*task.dropoff, task.id)
        task.state = E.K_DONE
        self.stats["tasks_done"][task.type] += 1
        v.task_id = None
        v.completed += 1
        v.target = None
        v.path = []
        v.state = E.V_IDLE
        self._touched_totes.add(tote.id)
        self.touch_vehicle(v.id)

    def _abandon_task(self, v: E.Vehicle, task: E.Task, reason: str) -> None:
        """取货失败：释放声明，入库任务改挂其他料箱或取消重生成。"""
        self.grid.release_claim(*task.dropoff, task.id)
        tote = self.totes[task.tote_id]
        if tote.claimed_by_task == task.id:
            tote.claimed_by_task = None

        if task.type == E.INBOUND and "tote missing" in reason:
            alt = self.pick_inbound_tote()
            if alt is not None:
                task.tote_id = alt.id
                task.pickup = alt.location
                self.task_queue.append(task.id)
                self.push_event(
                    "task_retry",
                    detail=f"task {task.id} retargeted to tote {alt.id}",
                )
                v.task_id = None
                v.state = E.V_IDLE
                v.target = None
                v.path = []
                self.touch_vehicle(v.id)
                return
            self.stats["tasks_cancelled"] = (
                self.stats.get("tasks_cancelled", 0) + 1
            )
            self.tasks[task.id].state = E.K_DONE  # 取消闭环，由生成侧补对
            self.push_event("task_retry",
                            detail=f"task {task.id} cancelled: {reason}")
        else:
            task.state = E.K_QUEUED
            self.task_queue.append(task.id)
            self.push_event("task_retry", detail=f"task {task.id}: {reason}")
        v.task_id = None
        v.state = E.V_IDLE
        v.target = None
        v.path = []
        self.touch_vehicle(v.id)

    # ------------------------------------------------------------- 料箱循环
    def _cycling(self) -> None:
        for tote in self.totes.values():
            if tote.state != E.T_CYCLING:
                continue
            tote.cycle_timer -= 1
            if tote.cycle_timer > 0:
                continue
            cell = self.free_inbound_cell()
            if cell is None:
                tote.cycle_timer = 0  # 入库口满，下 tick 再试
                continue
            tote.state = E.T_DOCKED
            tote.location = cell
            tote.cycle_timer = 0
            self.tote_cells.add(cell)
            self.stats["totes_recycled"] += 1
            self._touched_totes.add(tote.id)

    # ================================================================ 资源选择
    def _tote_at(self, cell: tuple) -> E.Tote | None:
        for tote in self.totes.values():
            if tote.location == cell and tote.state in (E.T_DOCKED, E.T_CYCLING):
                return tote
        return None

    def _heat_around(self, cell: tuple) -> float:
        """点位周边通道的等待热力（拥堵感知选点用）。"""
        r, c = cell
        return sum(
            self.wait_heat.get((r + dr, c + dc), 0.0)
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
        )

    def _shelf_edge_dist(self, cell: tuple) -> int:
        """格子到货架区最近边缘的圈距（外圈为 0/1）。"""
        r0, r1 = self.cfg.shelf_row_range
        c0, c1 = self.cfg.shelf_col_range
        r, c = cell
        return min(r - r0, r1 - r, c - c0, c1 - c)

    def _prefer_low_heat(self, cells: list) -> list:
        """拥堵感知：优先在热力较低的一半候选中随机 (FR-024)。"""
        if not self.cfg.conflict_optimization or len(cells) <= 4:
            return cells
        ranked = sorted(cells, key=lambda s: self._heat_around(s))
        return ranked[: max(1, len(ranked) // 2)]

    def pick_outbound_tote(self) -> E.Tote | None:
        cands = [
            t for t in self.totes.values()
            if t.state == E.T_DOCKED and t.claimed_by_task is None
            and self.grid.kind_at(*t.location) == CellKind.SLOT
            and self.grid.is_passable(*t.location)
        ]
        if not cands:
            return None
        loc = self.rnd.choice(self._prefer_low_heat([t.location for t in cands]))
        return next(t for t in cands if t.location == loc)

    def pick_inbound_tote(self) -> E.Tote | None:
        cands = [
            t for t in self.totes.values()
            if t.state == E.T_DOCKED and t.claimed_by_task is None
            and self.grid.kind_at(*t.location) == CellKind.INBOUND
            and self.grid.is_passable(*t.location)
        ]
        return self.rnd.choice(cands) if cands else None

    def pick_free_slot(self) -> tuple | None:
        occupied = {t.location for t in self.totes.values()}
        cands = [
            s for s in self.grid.slots
            if s not in occupied and s not in self.grid.claims
            and self.grid.is_passable(*s)
        ]
        return self.rnd.choice(self._prefer_low_heat(cands)) if cands else None

    def pick_free_outbound_cell(self) -> tuple | None:
        cands = [
            c for c in self.grid.outbound_cells
            if c not in self.grid.claims
            and self._tote_at(c) is None
            and self.grid.is_passable(*c)
        ]
        return self.rnd.choice(cands) if cands else None

    def free_inbound_cell(self) -> tuple | None:
        occupied = {t.location for t in self.totes.values()}
        for cell in self.grid.inbound_cells:
            if (
                cell not in occupied
                and self.grid.is_passable(*cell)
                and self.grid.occupant_at(*cell) is None
            ):  # 不得把循环料箱落在车辆头顶 (FR-015)
                return cell
        return None

    def free_adjacent(self, cell: tuple, avoid_totes: bool = False) -> tuple | None:
        r, c = cell
        opts = []
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if (
                self.grid.is_passable(nr, nc)
                and self.grid.occupant_at(nr, nc) is None
                and not (avoid_totes and (nr, nc) in self.tote_cells)
            ):
                opts.append((nr, nc))
        return self.rnd.choice(opts) if opts else None

    # ================================================================ 任务
    def create_task(self, type_: str, pickup: tuple, dropoff: tuple,
                    tote: E.Tote) -> E.Task | None:
        if not self.grid.claim(dropoff[0], dropoff[1], self._next_task_id):
            return None
        task = E.Task(
            id=self._next_task_id, type=type_,
            pickup=tuple(pickup), dropoff=tuple(dropoff), tote_id=tote.id,
        )
        self._next_task_id += 1
        if type_ == E.OUTBOUND:
            # 出库：生成时即锁定料箱，防止被其他任务抢走
            tote.claimed_by_task = task.id
        # 入库：料箱在取货时才认领（保持入库口料箱池可流动，
        # 否则生成即锁死会卡住任务生成节奏）
        self.tasks[task.id] = task
        self.task_queue.append(task.id)
        self.stats["tasks_generated"][type_] += 1
        return task

    # ================================================================ 障碍物
    def add_obstacle(self, r: int, c: int) -> bool:
        self.grid.can_obstruct(r, c)
        if (r, c) in self.grid.obstacles:
            return False  # 幂等
        if self.grid.occupant_at(r, c) is not None:
            raise ObstacleError("cell occupied by vehicle")
        if (
            self.grid.kind_at(r, c) == CellKind.SLOT
            and self._tote_at((r, c)) is not None
        ):
            raise ObstacleError("slot holds a tote")
        self.grid.add_obstacle(r, c)
        # 受影响车辆（剩余路径即将经过）立即重规划
        for v in self.vehicles.values():
            if (r, c) in v.path[:6]:
                self._replan(v)
                self.touch_vehicle(v.id)
        return True

    def remove_obstacle(self, r: int, c: int) -> bool:
        changed = self.grid.remove_obstacle(r, c)  # 不存在时抛 ObstacleError
        if changed:
            self.dispatcher.unblock_all(self)
        return changed

    # ================================================================ 输出
    def push_event(self, kind: str, vehicle_id: int | None = None,
                   detail: str = "") -> None:
        evt = {
            "type": "event", "tick": self.tick, "kind": kind,
            "vehicle_id": vehicle_id, "detail": detail,
        }
        self.events.append(evt)
        self._pending_events.append(evt)

    def touch_vehicle(self, vid: int) -> None:
        self._touched_vehicles.add(vid)

    def pop_delta(self) -> dict:
        vehicles = []
        for vid in sorted(self._moves.keys() | self._touched_vehicles):
            v = self.vehicles[vid]
            entry: dict = {"id": vid, "state": v.state}
            if vid in self._moves:
                old, new = self._moves[vid]
                entry["from"] = list(old)
                entry["to"] = list(new)
            else:
                entry["from"] = list(v.cell)
                entry["to"] = list(v.cell)
            vehicles.append(entry)
        totes = []
        for tid in sorted(self._touched_totes):
            t = self.totes[tid]
            entry = {"id": tid, "state": t.state}
            if t.state == E.T_CARRIED:
                entry["carried_by"] = t.carried_by
            else:
                entry["at"] = list(t.location)
            totes.append(entry)
        delta = {
            "type": "delta", "tick": self.tick,
            "vehicles": vehicles, "totes": totes,
            "metrics": dict(self.metrics),
        }
        self._moves.clear()
        self._touched_vehicles.clear()
        self._touched_totes.clear()
        return delta

    def pop_events(self) -> list[dict]:
        out, self._pending_events = self._pending_events, []
        return out

    def _update_metrics(self) -> None:
        waiting = sum(
            1 for v in self.vehicles.values() if v.state == E.V_WAITING
        )
        stalled = sum(
            1 for v in self.vehicles.values() if v.state == E.V_STALLED
        )
        done = self.stats["tasks_done"]
        self.metrics = {
            "tasks_done": done["inbound"] + done["outbound"],
            "tasks_done_inbound": done["inbound"],
            "tasks_done_outbound": done["outbound"],
            "tasks_queued": sum(
                1 for t in self.tasks.values() if t.state == E.K_QUEUED
            ),
            "waiting": waiting,
            "stalled": stalled,
            "collision": self.collision_count,
            "conflicts": self.recorder.total_closed,
            "totes_in_shelf": sum(
                1 for t in self.totes.values()
                if t.state == E.T_DOCKED
                and self.grid.kind_at(*t.location) == CellKind.SLOT
            ),
            "per_vehicle_completed": [
                self.vehicles[i].completed for i in sorted(self.vehicles)
            ],
        }

    def snapshot(self) -> dict:
        cfg = self.cfg
        return {
            "tick": self.tick,
            "paused": self.paused,
            "grid": {
                "size": self.grid.size,
                "slots": [list(s) for s in self.grid.slots],
                "inbound": {
                    "rows": list(cfg.inbound_row_range),
                    "cols": list(cfg.inbound_col_range),
                },
                "outbound": {
                    "rows": list(cfg.outbound_row_range),
                    "cols": list(cfg.outbound_col_range),
                },
                "obstacles": [list(o) for o in sorted(self.grid.obstacles)],
            },
            "vehicles": [
                {
                    "id": v.id, "cell": list(v.cell),
                    "state": v.state, "task_id": v.task_id,
                    "completed": v.completed,
                }
                for v in sorted(self.vehicles.values(), key=lambda x: x.id)
            ],
            "totes": [
                self.tote_public(t) for t in sorted(
                    self.totes.values(), key=lambda x: x.id
                )
            ],
            "metrics": dict(self.metrics),
        }

    @staticmethod
    def tote_public(t: E.Tote) -> dict:
        entry = {"id": t.id, "state": t.state}
        if t.state == E.T_CARRIED:
            entry["carried_by"] = t.carried_by
        else:
            entry["at"] = list(t.location)
        return entry

    def vehicle_detail(self, vid: int) -> dict | None:
        v = self.vehicles.get(vid)
        if v is None:
            return None
        task = self.tasks.get(v.task_id) if v.task_id else None
        return {
            "type": "vehicle_detail",
            "vehicle": {
                "id": v.id, "cell": list(v.cell), "state": v.state,
                "completed": v.completed, "facing": v.facing,
                "path": [list(p) for p in v.path[:60]],
                "task": (
                    {
                        "id": task.id, "type": task.type,
                        "pickup": list(task.pickup),
                        "dropoff": list(task.dropoff),
                        "state": task.state,
                    } if task else None
                ),
            },
        }

    # ================================================================ 自检
    def self_check(self) -> list[str]:
        """--check 自检：布局、规划器、预约互斥、短程运行。"""
        checks: list[str] = []
        cfg = self.cfg
        n_slots = len(self.grid.slots)
        n_in = len(self.grid.inbound_cells)
        n_out = len(self.grid.outbound_cells)
        exp_slots = (
            (cfg.shelf_row_range[1] - cfg.shelf_row_range[0] + 1
             + cfg.slot_row_step - 1) // cfg.slot_row_step
        ) * (
            (cfg.shelf_col_range[1] - cfg.shelf_col_range[0] + 1
             + cfg.slot_col_step - 1) // cfg.slot_col_step
        )
        exp_station = (cfg.inbound_row_range[1] - cfg.inbound_row_range[0] + 1) * (
            cfg.inbound_col_range[1] - cfg.inbound_col_range[0] + 1
        )
        assert n_slots == exp_slots, f"slots {n_slots} != {exp_slots}"
        assert n_in == exp_station and n_out == exp_station
        assert not self.grid.is_passable(0, 0), "boundary must block"
        assert not self.grid.is_passable(1, 100), "wall must be 2 cells thick"
        assert self.grid.is_passable(2, 100), "ring road must be passable"
        checks.append(
            f"layout ok (slots={n_slots}, stations={n_in}/{n_out}, "
            f"wall=2, ring road ok)"
        )

        planner_self_test(self.grid)
        checks.append("planner ok")

        res = Reservations()
        assert res.claim(3, 3, 5, 1)
        assert not res.claim(3, 3, 5, 2), "double reservation must fail"
        assert res.claim(3, 3, 5, 1), "same vehicle re-claim ok"
        checks.append("reservations ok")

        for _ in range(5):
            self.step()
        assert self.collision_count == 0
        seen = set()
        for r in range(self.grid.size):
            for c in range(self.grid.size):
                occ = self.grid.occupant_at(r, c)
                if occ is not None:
                    assert occ not in seen, "occupancy invariant broken"
                    seen.add(occ)
        checks.append(f"engine ok ({len(self.vehicles)} vehicles, 5 ticks)")
        return checks
