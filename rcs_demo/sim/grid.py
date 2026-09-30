"""网格与固定布局（research.md R6 / FR-016/017/020/021）。

布局：200×200；外圈围墙；围墙内侧 2 格宽环场主干道；中部货架区
（2400 个存储点位，1 格宽通道）；西入库口、东出库口；南侧集中充电区
（10 充电位一排 + 西进东出单向通道 + 排队格）。
"""
from __future__ import annotations

from typing import Optional

from .entities import Cell, CellKind, ChargingArea, Obstacle
from .. import config as cfg


class Grid:
    def __init__(self):
        n = cfg.GRID
        self.size = n
        self.kind: dict[Cell, CellKind] = {}
        self.obstacles: dict[Cell, Obstacle] = {}
        # 不可通行格集合（boundary + 障碍物，O(1) 查询，规划热路径用）
        self.blocked: set[Cell] = set()

        self._build_boundary_and_floor()
        self._build_shelf()
        self._build_stations()
        self.charging = self._build_charging()

        self.slots: list[Cell] = [
            c for c, k in self.kind.items() if k == CellKind.SLOT
        ]
        self.inbound_cells: list[Cell] = [
            c for c, k in self.kind.items() if k == CellKind.INBOUND
        ]
        self.outbound_cells: list[Cell] = [
            c for c, k in self.kind.items() if k == CellKind.OUTBOUND
        ]
        # 不可添加障碍物的格子（boundary/站点/充电区全部格子，data-model.md 规则）
        self.protected: set[Cell] = {
            c for c, k in self.kind.items() if k != CellKind.FLOOR and k != CellKind.SLOT
        }
        self.blocked = {c for c, k in self.kind.items() if k == CellKind.BOUNDARY}

    # ---- 布局构建 ----

    def _build_boundary_and_floor(self):
        n = self.size
        for r in range(n):
            for c in range(n):
                if r == 0 or r == n - 1 or c == 0 or c == n - 1:
                    self.kind[(r, c)] = CellKind.BOUNDARY
                else:
                    self.kind[(r, c)] = CellKind.FLOOR
        # 环场主干道（围墙内侧 2 格宽，FR-020）：本身就是 floor，此处仅显式标注范围
        self.ring_road: set[Cell] = set()
        for i in range(1, n - 1):
            for d in (1, 2, n - 2, n - 3):
                self.ring_road.add((i, d))
                self.ring_road.add((d, i))

    def _build_shelf(self):
        r0, r1 = cfg.SHELF_ROW_RANGE
        c0, c1 = cfg.SHELF_COL_RANGE
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                if r % 2 == 0 and c % 2 == 0:  # 点位间隔 1 格通道（FR-021）
                    self.kind[(r, c)] = CellKind.SLOT

    def _build_stations(self):
        # 入库口（西侧）、出库口（东侧）：col 0/199 为围墙，实际通行格从 col 1/198 起
        for r in range(cfg.INBOUND_ROW_RANGE[0], cfg.INBOUND_ROW_RANGE[1] + 1):
            for c in range(max(1, cfg.INBOUND_COL_RANGE[0]), cfg.INBOUND_COL_RANGE[1] + 1):
                self.kind[(r, c)] = CellKind.INBOUND
        for r in range(cfg.OUTBOUND_ROW_RANGE[0], cfg.OUTBOUND_ROW_RANGE[1] + 1):
            for c in range(cfg.OUTBOUND_COL_RANGE[0], min(self.size - 2, cfg.OUTBOUND_COL_RANGE[1]) + 1):
                self.kind[(r, c)] = CellKind.OUTBOUND

    def _build_charging(self) -> ChargingArea:
        r0, r1 = cfg.CHARGE_ROW_RANGE
        c0, c1 = cfg.CHARGE_COL_RANGE
        area = ChargingArea()
        lane_row = (r0 + r1) // 2          # 189 通道行：西进东出，整体单向（西→东）
        pile_row = lane_row - 2            # 187 充电位行
        # 通道行：进站段（charge_in）+ 中段 aisle（floor）+ 出站段（charge_out）
        entry_cols = list(range(c0 + 1, c0 + 4))       # 86..88 进站段
        exit_cols = list(range(c1 - 2, c1 + 1))        # 112..114 出站段
        for c in range(c0 + 1, c1):
            k = CellKind.FLOOR
            if c in entry_cols:
                k = CellKind.CHARGE_IN
            elif c in exit_cols:
                k = CellKind.CHARGE_OUT
            self.kind[(lane_row, c)] = k
        # 10 个充电位（一排，间隔 1 格），pile_row 上
        pile_cols = list(range(c0 + 5, c0 + 5 + 2 * cfg.CHARGE_PILES, 2))  # 90,92,..,108
        for c in pile_cols:
            self.kind[(pile_row, c)] = CellKind.CHARGE_PILE
            self.kind[(pile_row + 1, c)] = CellKind.CHARGE_IN  # 取车/还车连接格（保护，不可设障）
            area.piles.append((pile_row, c))
            area.pile_owner[(pile_row, c)] = None
        # 排队格：进站段以西同排（不占主干道，FR-026）
        for c in range(c0 - 3, c0 + 1):
            self.kind[(lane_row, c)] = CellKind.CHARGE_QUEUE
            area.queue_cells.append((lane_row, c))
        area.queue_cells.reverse()  # 距进站口近的在前
        # 进站/出站通道格序列（车级路径用）
        area.entry_lane = [(lane_row, c) for c in entry_cols]
        area.exit_lane = [(lane_row, c) for c in exit_cols]
        return area

    # ---- 查询 ----

    def passable(self, cell: Cell) -> bool:
        """静态可通行性（boundary/障碍物）。料箱规则由引擎按携带状态叠加。"""
        return cell not in self.blocked and cell not in self.obstacles

    def is_slot(self, cell: Cell) -> bool:
        return self.kind.get(cell) == CellKind.SLOT

    def is_station(self, cell: Cell) -> bool:
        return self.kind.get(cell) in (CellKind.INBOUND, CellKind.OUTBOUND)

    def in_charging_area(self, cell: Cell) -> bool:
        r, c = cell
        return (cfg.CHARGE_ROW_RANGE[0] - 2 <= r <= cfg.CHARGE_ROW_RANGE[1] and
                cfg.CHARGE_COL_RANGE[0] - 3 <= c <= cfg.CHARGE_COL_RANGE[1] + 1)

    # ---- 障碍物（FR-009/010/011，data-model.md Obstacle 规则）----

    def can_add_obstacle(self, cell: Cell, occupant: Optional[int],
                         docked_tote: bool) -> tuple[bool, str]:
        """添加校验：返回 (ok, 错误原因)。占格/有箱由引擎传入。"""
        if cell in self.obstacles:
            return True, ""  # 幂等：重复添加视为成功
        k = self.kind.get(cell)
        if k == CellKind.BOUNDARY or k is None:
            return False, "boundary cell"
        if k != CellKind.FLOOR and k != CellKind.SLOT:
            return False, "station or charging cell"  # 站点/充电区格不可设障
        if occupant is not None:
            return False, "cell occupied or reserved"
        if k == CellKind.SLOT and docked_tote:
            return False, "cell occupied or reserved"  # 有静置料箱的点位拒绝
        return True, ""

    def add_obstacle(self, cell: Cell, tick: int) -> bool:
        if cell in self.obstacles:
            return False
        self.obstacles[cell] = Obstacle(cell=cell, created_at_tick=tick)
        self.blocked.add(cell)
        return True

    def remove_obstacle(self, cell: Cell) -> bool:
        if self.obstacles.pop(cell, None) is not None:
            self.blocked.discard(cell)
            return True
        return False
