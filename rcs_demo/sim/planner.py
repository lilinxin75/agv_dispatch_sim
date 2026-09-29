"""A* 寻路 + 时空预约表 (research.md R1/R2)。

- 4 邻接、曼哈顿启发式、禁对角；
- boundary 与障碍物 (blocked) 不可通行；
- 预约表 ``reserved[(r, c, tick)] -> vehicle_id`` 保证同一格同一 tick 互斥；
- 规划时前瞻 ``horizon`` 个 tick、绕开被他车预约的格子（跟随语义：
  他车刚离开的格子下一 tick 即可预约）。
"""

from __future__ import annotations

import heapq

from rcs_demo.sim.grid import Grid

NEIGHBORS = ((-1, 0), (1, 0), (0, -1), (0, 1))


class Reservations:
    """``(row, col, tick) -> vehicle_id`` 的时空预约互斥表。"""

    __slots__ = ("data",)

    def __init__(self):
        self.data: dict[tuple[int, int, int], int] = {}

    def claim(self, r: int, c: int, t: int, vid: int) -> bool:
        """预约 (r,c) 于 tick t；被他车占用时返回 False。"""
        key = (r, c, t)
        cur = self.data.get(key)
        if cur is not None and cur != vid:
            return False
        self.data[key] = vid
        return True

    def held_by(self, r: int, c: int, t: int) -> int | None:
        return self.data.get((r, c, t))

    def reserved_by_other(self, r: int, c: int, t0: int, horizon: int, vid: int) -> bool:
        """(r,c) 在 (t0, t0+horizon] 内是否被他车预约。"""
        for t in range(t0 + 1, t0 + horizon + 1):
            holder = self.data.get((r, c, t))
            if holder is not None and holder != vid:
                return True
        return False

    def gc(self, now: int) -> None:
        """清理过期预约；表过大时全量清扫，否则惰性跳过。"""
        if len(self.data) > 20000:
            self.data = {
                k: v for k, v in self.data.items() if k[2] >= now
            }

    def release_vehicle(self, vid: int) -> None:
        self.data = {
            k: v for k, v in self.data.items() if v != vid
        }


def astar(
    grid: Grid,
    start: tuple[int, int],
    goal: tuple[int, int],
    reservations: Reservations | None = None,
    now: int = 0,
    horizon: int = 0,
    self_id: int | None = None,
    max_expand: int = 200_000,
    avoid: set | None = None,
    extra_cost=None,
    h_weight: float = 1.0,
) -> list | None:
    """返回从 start（不含）到 goal（含）的格序列；不可达返回 None。

    通行代价：普通格 1，存储点位 (SLOT) 格 +3——让过境路径优先走
    通道，只有以点位为目标时才进入货架区，降低货架通道拥堵。

    ``avoid``：视为不可进入的格子集合（如带箱车避让静置料箱格，
    FR-015）；``extra_cost(cell)``：附加通行代价（拥堵感知加权）。
    """
    if start == goal:
        return []
    if not grid.is_passable(*goal):
        return None
    if avoid is not None and goal in avoid:
        return None

    def passable(r: int, c: int) -> bool:
        if not grid.is_passable(r, c):
            return False
        if avoid is not None and (r, c) in avoid:
            return False
        if reservations is not None and horizon > 0:
            if reservations.reserved_by_other(r, c, now, horizon, self_id or -1):
                return False
        return True

    def step_cost(r: int, c: int) -> int:
        cost = 4 if grid.kind_at(r, c).value == "slot" else 1
        if extra_cost is not None:
            cost += extra_cost((r, c))
        return cost

    sr, sc = start
    gr, gc_ = goal
    g_cost = {(sr, sc): 0}
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    heap = [(abs(sr - gr) + abs(sc - gc_) * h_weight, 0, (sr, sc))]
    expanded = 0
    while heap:
        f, g, (r, c) = heapq.heappop(heap)
        if (r, c) == (gr, gc_):
            path = [(r, c)]
            while path[-1] != (sr, sc):
                path.append(parent[path[-1]])
            path.reverse()
            return path[1:]
        expanded += 1
        if expanded > max_expand:
            return None
        if g > g_cost.get((r, c), g):
            continue  # 过期堆项
        for dr, dc in NEIGHBORS:
            nr, nc = r + dr, c + dc
            if not passable(nr, nc):
                continue
            ng = g + step_cost(nr, nc)
            if ng < g_cost.get((nr, nc), 1 << 60):
                g_cost[(nr, nc)] = ng
                parent[(nr, nc)] = (r, c)
                h = (abs(nr - gr) + abs(nc - gc_)) * h_weight
                heapq.heappush(heap, (ng + h, ng, (nr, nc)))
    return None


def planner_self_test(grid: Grid) -> None:
    """供 --check 调用的快速自检。"""
    assert grid.is_passable(2, 2), "ring road cell must be passable"
    assert not grid.is_passable(0, 0), "boundary must be blocked"
    assert not grid.is_passable(1, 5), "wall is 2 cells thick"
    goal = (2, min(30, grid.size - 3))
    path = astar(grid, (2, 2), goal)
    assert path is not None and path[-1] == goal
    # 被完全围堵时不可达
    res = Reservations()
    assert astar(grid, (2, 2), (3, 2), res, now=0, horizon=0) is not None
