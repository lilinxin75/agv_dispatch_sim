"""网格与固定布局 (data-model.md: Cell)。

坐标约定 ``(row, col)``，0 起始，(0,0) 左上角。
零碰撞不变量由 :meth:`Grid.set_occupant` 的互斥赋值保证。
"""

from __future__ import annotations

from enum import Enum

from rcs_demo.config import Config


class CollisionError(RuntimeError):
    """任一 tick 内同一格被赋给第二台车——引擎 bug 或违规操作。"""


class ObstacleError(ValueError):
    """障碍物操作非法（有车/有箱/边界或站点格等）。"""


class CellKind(str, Enum):
    FLOOR = "floor"
    SLOT = "slot"          # 货架存储点位（可静置料箱，小车可穿行其下）
    INBOUND = "inbound"    # 入库口
    OUTBOUND = "outbound"  # 出库口


class Grid:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.size = cfg.grid_size
        n = self.size
        self.kind: list[list[CellKind]] = [
            [CellKind.FLOOR] * n for _ in range(n)
        ]
        self.blocked: list[list[bool]] = [[False] * n for _ in range(n)]
        self.occupant: list[list[int | None]] = [[None] * n for _ in range(n)]

        self.slots: list[tuple[int, int]] = []
        self.inbound_cells: list[tuple[int, int]] = []
        self.outbound_cells: list[tuple[int, int]] = []
        self.obstacles: set[tuple[int, int]] = set()
        # 单元格任务声明：某格被某任务预定为入库落位点/出库目的格（一格一箱）
        self.claims: dict[tuple[int, int], int] = {}

        self._build_boundary()
        self._build_shelf()
        self._build_stations()

    # ------------------------------------------------------------------ 布局
    def _build_boundary(self) -> None:
        """围墙：外圈 2 格厚（FR-020），车辆活动区距画布边缘 ≥2 格。"""
        n = self.size
        for i in range(n):
            for b in (0, 1, n - 2, n - 1):
                self.blocked[b][i] = True
                self.blocked[i][b] = True

    def _build_shelf(self) -> None:
        cfg = self.cfg
        r0, r1 = cfg.shelf_row_range
        c0, c1 = cfg.shelf_col_range
        for r in range(r0, r1 + 1, cfg.slot_row_step):
            for c in range(c0, c1 + 1, cfg.slot_col_step):
                self.kind[r][c] = CellKind.SLOT
                self.slots.append((r, c))

    def _build_stations(self) -> None:
        cfg = self.cfg
        ir0, ir1 = cfg.inbound_row_range
        ic0, ic1 = cfg.inbound_col_range
        or0, or1 = cfg.outbound_row_range
        oc0, oc1 = cfg.outbound_col_range
        for r in range(ir0, ir1 + 1):
            for c in range(ic0, ic1 + 1):
                self.kind[r][c] = CellKind.INBOUND
                self.inbound_cells.append((r, c))
        for r in range(or0, or1 + 1):
            for c in range(oc0, oc1 + 1):
                self.kind[r][c] = CellKind.OUTBOUND
                self.outbound_cells.append((r, c))

    # ------------------------------------------------------------------ 查询
    def in_bounds(self, r: int, c: int) -> bool:
        return 0 <= r < self.size and 0 <= c < self.size

    def is_boundary(self, r: int, c: int) -> bool:
        """围墙（外圈 2 格厚）或界外。"""
        return (
            r in (0, 1, self.size - 2, self.size - 1)
            or c in (0, 1, self.size - 2, self.size - 1)
        )

    def kind_at(self, r: int, c: int) -> CellKind:
        return self.kind[r][c]

    def is_passable(self, r: int, c: int) -> bool:
        return self.in_bounds(r, c) and not self.blocked[r][c]

    def occupant_at(self, r: int, c: int) -> int | None:
        return self.occupant[r][c]

    def set_occupant(self, r: int, c: int, vid: int | None) -> None:
        cur = self.occupant[r][c]
        if vid is not None and cur is not None and cur != vid:
            raise CollisionError(
                f"cell ({r},{c}) already occupied by vehicle {cur}"
            )
        self.occupant[r][c] = vid

    # ------------------------------------------------------------------ 障碍物
    def can_obstruct(self, r: int, c: int) -> None:
        """校验该格是否允许放置障碍物，非法时抛 :class:`ObstacleError`。

        仅做格级校验（边界/站点/已有障碍）；车辆占用与料箱占用
        需要引擎数据，由 Engine.add_obstacle 追加校验。
        """
        if not self.in_bounds(r, c):
            raise ObstacleError("out of bounds")
        if (r, c) in self.obstacles:
            return  # 幂等：重复添加合法（幂等语义在 Engine 层处理）
        if self.is_boundary(r, c):
            raise ObstacleError("boundary cell")
        if self.kind[r][c] in (CellKind.INBOUND, CellKind.OUTBOUND):
            raise ObstacleError("station cell")
        if not self.is_passable(r, c):
            raise ObstacleError("cell not passable")

    def add_obstacle(self, r: int, c: int) -> bool:
        """放置障碍物；返回 False 表示已存在（幂等）。"""
        if (r, c) in self.obstacles:
            return False
        self.can_obstruct(r, c)
        self.blocked[r][c] = True
        self.obstacles.add((r, c))
        return True

    def remove_obstacle(self, r: int, c: int) -> bool:
        """删除障碍物；不存在时抛 :class:`ObstacleError`。"""
        if (r, c) not in self.obstacles:
            raise ObstacleError("no obstacle at cell")
        self.obstacles.discard((r, c))
        self.blocked[r][c] = False
        return True

    # ------------------------------------------------------------------ 声明
    def claim(self, r: int, c: int, task_id: int) -> bool:
        key = (r, c)
        if key in self.claims:
            return False
        self.claims[key] = task_id
        return True

    def release_claim(self, r: int, c: int, task_id: int) -> None:
        if self.claims.get((r, c)) == task_id:
            del self.claims[(r, c)]
