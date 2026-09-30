"""路径规划（A*）与时空预约表（research.md R1/R2/R9）。

- A*：4 邻接 + 曼哈顿启发式；搜索状态 (row, col, dir)，转向附加 TURN_PENALTY
  软代价（FR-027），偏向直线但不改变可达性。
- 预约表：每 tick 清空后由引擎按序为移动车辆预约下一格 (cell -> vehicle_id)，
  互斥写入失败即等待（零碰撞仲裁，R1 单窗口简化版）。
"""
from __future__ import annotations

import heapq
from typing import Callable, Optional

from .entities import Cell
from .grid import Grid
from .. import config as cfg

DIRS = {"N": (-1, 0), "E": (0, 1), "S": (1, 0), "W": (0, -1)}
DIR_LIST = ["N", "E", "S", "W"]


class ReservationTable:
    """单窗口预约：dict[cell -> vehicle_id]，每 tick 重建。"""

    def __init__(self):
        self.reserved: dict[Cell, int] = {}

    def clear(self):
        self.reserved.clear()

    def try_reserve(self, cell: Cell, vid: int) -> bool:
        """互斥写入：已被他车预约返回 False（等待，R1）。"""
        owner = self.reserved.get(cell)
        if owner is not None and owner != vid:
            return False
        self.reserved[cell] = vid
        return True

    def owner(self, cell: Cell) -> Optional[int]:
        return self.reserved.get(cell)


class Planner:
    def __init__(self, grid: Grid, tote_at: Callable[[Cell], bool]):
        self.grid = grid
        self.tote_at = tote_at  # cell 上是否有静置料箱
        self.docked: set[Cell] = set()  # 引擎注入（静置料箱格集合，热路径内联用）
        # FR-024 热点格通行代价加权（与 TURN_PENALTY 正交叠加进 g 函数）
        self.hotspots: dict[Cell, float] = {}
        # 货架横向通道单向化（隔行交替 E/W）：row ∈ aisle_e 只许东行，aisle_w 只许西行
        self.aisle_e: set[int] = set()
        self.aisle_w: set[int] = set()
        c0, c1 = cfg.SHELF_COL_RANGE
        if cfg.ONE_WAY_AISLES:
            r0, r1 = cfg.SHELF_ROW_RANGE
            for r in range(r0 + 1, r1, 2):  # 通道行（奇数行，点位行为偶数）
                if (r // 2) % 2 == 0:
                    self.aisle_e.add(r)
                else:
                    self.aisle_w.add(r)
        self._shelf_col = (c0, c1)

    def cell_cost(self, cell: Cell) -> float:
        return 1.0 + self.hotspots.get(cell, 0.0)

    def can_enter(self, cell: Cell, carrying: bool) -> bool:
        """FR-015：无载车可穿行静置料箱格；载货车不得进入静置料箱格。"""
        if cell in self.grid.blocked:
            return False
        if carrying and cell in self.docked:
            return False
        return True

    def plan(self, start: Cell, goal: Cell, facing: str, carrying: bool,
             occupied: set[Cell], max_nodes: int = 80_000) -> Optional[list[Cell]]:
        """方向感知加权 A*（f = g + WH·h）。返回不含 start 的格序列；不可达返回 None。

        occupied：其他车辆当前占据的格（规划时视为不可通行，R2）。
        热路径内联集合查询（can_enter/hotspot），避免每邻居多层函数调用。
        加权启发式（WH=1.6）牺牲少量路径最优性换取高拥塞下的搜索速度（演示可接受）。
        """
        if start == goal:
            return []
        blocked = self.grid.blocked
        docked = self.docked
        hotspots = self.hotspots
        turn_penalty = cfg.TURN_PENALTY
        aisle_e, aisle_w = self.aisle_e, self.aisle_w
        sc0, sc1 = self._shelf_col
        if goal in blocked or (carrying and goal in docked):
            return None

        start_dir = facing if facing in DIRS else "E"
        gr, gc = goal
        # 加权曼哈顿启发式（WH 可 > 0，加权 A* 标准做法；热点/转弯代价均 ≥0）
        wh = 1.6
        h = lambda r, c: wh * (abs(r - gr) + abs(c - gc))
        tie = 0
        open_heap = [(h(*start), 0, 0, (start[0], start[1], start_dir))]
        best: dict[tuple, int] = {(start[0], start[1], start_dir): 0}
        parent: dict[tuple, Optional[tuple]] = {(start[0], start[1], start_dir): None}
        nodes = 0
        heappop, heappush = heapq.heappop, heapq.heappush

        while open_heap:
            f, neg_g, g, (r, c, d) = heappop(open_heap)
            state = (r, c, d)
            if g > best.get(state, 1 << 60):
                continue
            if r == gr and c == gc:
                # 回溯路径（仅格坐标）
                path = []
                cur = state
                while parent[cur] is not None:
                    path.append((cur[0], cur[1]))
                    cur = parent[cur]
                path.reverse()
                return path
            nodes += 1
            if nodes > max_nodes:
                return None
            di = DIR_LIST.index(d)
            for turn, nd_i in ((0, di), (1, (di + 1) % 4), (-1, (di + 3) % 4),
                               (2, (di + 2) % 4)):
                nd = DIR_LIST[nd_i]
                dr, dc = DIRS[nd]
                nr, nc = r + dr, c + dc
                ncell = (nr, nc)
                if ncell in blocked or (carrying and ncell in docked):
                    continue
                # 货架横向通道单向流：逆行即跳过（消除对向互堵，SC-002）
                if nr in aisle_e and nd == "W" and sc0 <= nc <= sc1:
                    continue
                if nr in aisle_w and nd == "E" and sc0 <= nc <= sc1:
                    continue
                if ncell in occupied and ncell != goal:
                    continue
                ng = g + (1.0 + hotspots.get(ncell, 0.0)
                          if turn == 0 else
                          1.0 + hotspots.get(ncell, 0.0) + turn_penalty)
                nstate = (nr, nc, nd)
                if ng < best.get(nstate, 1 << 60):
                    best[nstate] = ng
                    parent[nstate] = state
                    tie += 1
                    # f 平局时优先扩展 g 更深（-g 更小）的节点：迷宫网格大幅减少扩展
                    heappush(open_heap, (ng + h(nr, nc), -ng, ng, nstate))
        return None

    def plan_any_dir(self, start: Cell, goal: Cell, carrying: bool,
                     occupied: set[Cell], max_nodes: int = 30_000) -> Optional[list[Cell]]:
        """无朝向要求的规划（让路/充电等场景）；任一方向可达即返回（性能优先）。"""
        for d in DIR_LIST:
            p = self.plan(start, goal, d, carrying, occupied, max_nodes)
            if p is not None:
                return p
        return None
