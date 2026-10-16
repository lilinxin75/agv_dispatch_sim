"""网格与布局（research.md R6 / 002 FR-011~014：LayoutProfile 驱动 + 矢量性）。

布局：200×200；外圈围墙；围墙内侧环场主干道；中部货架区（2400 个存储点位，
1 格宽通道）；西入库口、东出库口；南侧集中充电区（充电位一排 + 西进东出单向
通道 + 排队格）。

002 期：坐标真源为 layouts/*.json（load_profile）；Grid(profile) 按 profile 建图，
Grid() 缺省回退 config 常量（= baseline 缺省值，兼容缩尺测试的 cfg monkeypatch）。
充电区矢量性（FR-008）：桩格不可穿越（仅可作充电目标自南向北进入）、桩前行
单向（西→东）、充电区包围盒内其余格禁止穿行——can_traverse() 单一真源，
planner 与引擎仲裁同源校验。
"""
from __future__ import annotations

import json
from typing import Optional

from .entities import Cell, CellKind, ChargingArea, Obstacle
from .. import config as cfg


def load_profile(name: str) -> dict:
    """读取布局预设 JSON（002 T004）。文件缺失/格式错误抛 ValueError。"""
    path = cfg.LAYOUTS_DIR / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"布局预设 '{name}' 不存在（{path}），"
                         f"可选：{cfg.available_layouts()}")
    with open(path, encoding="utf-8") as fh:
        p = json.load(fh)
    if not isinstance(p, dict) or "grid" not in p or "charging" not in p:
        raise ValueError(f"布局预设 '{name}' 格式错误：缺少 grid/charging 字段")
    return p


class Grid:
    def __init__(self, profile: dict | None = None):
        p = profile or {}
        self.profile_name = p.get("name")
        n = int(p.get("grid", cfg.GRID))
        self.size = n
        self.kind: dict[Cell, CellKind] = {}
        self.obstacles: dict[Cell, Obstacle] = {}
        # 不可通行格集合（boundary + 障碍物，O(1) 查询，规划热路径用）
        self.blocked: set[Cell] = set()
        # 布局几何（profile 优先；缺省 = config 常量 baseline 缺省值）
        self.shelf_rows: tuple[int, int] = tuple(
            p.get("shelf", {}).get("row_range", cfg.SHELF_ROW_RANGE))
        self.shelf_cols: tuple[int, int] = tuple(
            p.get("shelf", {}).get("col_range", cfg.SHELF_COL_RANGE))
        self.inbound_rows: tuple[int, int] = tuple(
            p.get("inbound", {}).get("row_range", cfg.INBOUND_ROW_RANGE))
        self.inbound_cols: tuple[int, int] = tuple(
            p.get("inbound", {}).get("col_range", cfg.INBOUND_COL_RANGE))
        self.outbound_rows: tuple[int, int] = tuple(
            p.get("outbound", {}).get("row_range", cfg.OUTBOUND_ROW_RANGE))
        self.outbound_cols: tuple[int, int] = tuple(
            p.get("outbound", {}).get("col_range", cfg.OUTBOUND_COL_RANGE))
        self.charge_rows: tuple[int, int] = tuple(
            p.get("charging", {}).get("row_range", cfg.CHARGE_ROW_RANGE))
        self.charge_cols: tuple[int, int] = tuple(
            p.get("charging", {}).get("col_range", cfg.CHARGE_COL_RANGE))
        self.pile_count: int = int(p.get("charging", {}).get("piles", cfg.CHARGE_PILES))
        self.entry_cols_profile: list[int] | None = (
            p.get("charging", {}).get("entry_cols"))
        self.exit_cols_profile: list[int] | None = (
            p.get("charging", {}).get("exit_cols"))
        self.ring_width: int = int(p.get("ring_road_width", cfg.RING_ROAD_WIDTH))
        self.oneway_segments: list[dict] = list(p.get("oneway_segments", []))
        self.standby_zones: list[dict] = list(p.get("standby_zones", []))

        self._build_boundary_and_floor()
        self._build_shelf()
        self._build_stations()
        self.charging = self._build_charging()
        self._apply_oneway_segments()

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
        w = self.ring_width
        for r in range(n):
            for c in range(n):
                if r == 0 or r == n - 1 or c == 0 or c == n - 1:
                    self.kind[(r, c)] = CellKind.BOUNDARY
                else:
                    self.kind[(r, c)] = CellKind.FLOOR
        # 环场主干道（围墙内侧 w 格宽，FR-020）：本身就是 floor，此处仅显式标注范围
        self.ring_road: set[Cell] = set()
        for i in range(1, n - 1):
            for d in list(range(1, w + 1)) + list(range(n - 1 - w, n - 1)):
                self.ring_road.add((i, d))
                self.ring_road.add((d, i))

    def _build_shelf(self):
        r0, r1 = self.shelf_rows
        c0, c1 = self.shelf_cols
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                if r % 2 == 0 and c % 2 == 0:  # 点位间隔 1 格通道（FR-021）
                    self.kind[(r, c)] = CellKind.SLOT

    def _build_stations(self):
        # 入库口（西侧）、出库口（东侧）：col 0/199 为围墙，实际通行格从 col 1/198 起
        for r in range(self.inbound_rows[0], self.inbound_rows[1] + 1):
            for c in range(max(1, self.inbound_cols[0]), self.inbound_cols[1] + 1):
                self.kind[(r, c)] = CellKind.INBOUND
        for r in range(self.outbound_rows[0], self.outbound_rows[1] + 1):
            for c in range(self.outbound_cols[0], min(self.size - 2, self.outbound_cols[1]) + 1):
                self.kind[(r, c)] = CellKind.OUTBOUND

    def _build_charging(self) -> ChargingArea:
        r0, r1 = self.charge_rows
        c0, c1 = self.charge_cols
        area = ChargingArea()
        lane_row = (r0 + r1) // 2          # 通道行：西进东出，整体单向（西→东）
        pile_row = lane_row - 2            # 充电位行
        area.lane_row = lane_row
        area.pile_row = pile_row
        if self.entry_cols_profile is not None:
            e0, e1 = self.entry_cols_profile
            entry_cols = list(range(e0, e1 + 1))
        else:
            entry_cols = list(range(c0 + 1, c0 + 4))
        if self.exit_cols_profile is not None:
            x0, x1 = self.exit_cols_profile
            exit_cols = list(range(x0, x1 + 1))
        else:
            exit_cols = list(range(c1 - 2, c1 + 1))
        # 通道行（倒车入位修订后不再承担进/出桩通行）：进站段降级为排队格、
        # 中段/出站段为普通双向通道（research.md D15）
        for c in range(c0 + 1, c1):
            k = CellKind.CHARGE_QUEUE if c in entry_cols else CellKind.FLOOR
            self.kind[(lane_row, c)] = k
        # 充电位（一排，间隔 1 格），pile_row 上
        pile_cols = list(range(c0 + 5, c0 + 5 + 2 * self.pile_count, 2))
        for c in pile_cols:
            self.kind[(pile_row, c)] = CellKind.CHARGE_PILE
            self.kind[(pile_row - 1, c)] = CellKind.CHARGE_IN  # 桩位上方连接格（倒车入位，保护不可设障）
            area.piles.append((pile_row, c))
            area.pile_owner[(pile_row, c)] = None
        # 上方进桩通道（倒车入位，D15）：桩位上二排，正对各桩普通双向通道
        area.approach_row = pile_row - 2
        for c in range(c0 + 1, c1):
            if self.kind.get((area.approach_row, c)) is None:
                self.kind[(area.approach_row, c)] = CellKind.FLOOR
        # 排队格：进站段以西同排（不占主干道，FR-026）
        for c in range(c0 - 3, c0 + 1):
            self.kind[(lane_row, c)] = CellKind.CHARGE_QUEUE
            area.queue_cells.append((lane_row, c))
        area.queue_cells.reverse()  # 距通道口近的在前
        # 进站段降级排队格（FR-008 倒车入位修订）：并入排队序列（西端优先）
        area.queue_cells.extend((lane_row, c) for c in entry_cols)
        # 进站/出站段序列（仅作充电区内部标注/渲染用，不再承担进桩通行）
        area.entry_lane = [(lane_row, c) for c in entry_cols]
        area.exit_lane = [(lane_row, c) for c in exit_cols]
        self._build_directed(area)
        return area

    def _build_directed(self, area: ChargingArea):
        """充电区矢量性覆盖层（002 T049 / FR-008 倒车入位，research.md D15）。

        - 桩格：不可穿越；仅可作充电目标，且只能自北向南（经上方连接格）进入
        - 连接格（桩正上方）：仅允许 N/S（进桩/倒出）
        - 上方进桩通道（桩上二排）与通道行（排队/中段/出站段）：普通双向通道，
          不注册方向（倒车入位后通道行不承担进桩通行）
        - 包围盒内其余格：禁止穿行（no_transit），杜绝绕行旁路
        """
        self.pile_cells: set[Cell] = set(area.piles)
        self.directed: dict[Cell, frozenset[str]] = {}
        self.no_transit: set[Cell] = set()
        lane_row, pile_row = area.lane_row, area.pile_row
        c0, c1 = self.charge_cols
        conn_row = pile_row - 1
        approach_row = pile_row - 2
        # 北侧 fence 自充电区首行起（不再向上扩 2 行）：上方进桩通道与主货流
        # 通道直接连通，车辆可自任意北部位置直入（倒车入位的关键，D15）
        for r in range(self.charge_rows[0], self.charge_rows[1] + 1):
            for c in range(c0 - 3, c1 + 1):
                cell = (r, c)
                k = self.kind.get(cell)
                if cell in self.pile_cells:
                    self.directed[cell] = frozenset({"N"})  # 离桩只能向北（原路倒出）
                elif r == conn_row and k == CellKind.CHARGE_IN:
                    self.directed[cell] = frozenset({"N", "S"})  # 进桩（S）/倒出（N）
                elif r in (approach_row, lane_row):
                    pass  # 双向通道：不注册方向也不禁行
                elif k is not None:  # 包围盒内其余已标注格 → 禁止穿行
                    self.no_transit.add(cell)
                # kind 为 None（包围盒内未标注 floor）同样禁行
                else:
                    self.no_transit.add(cell)

    def _apply_oneway_segments(self):
        """局部单向段（002 T025 / FR-012，research.md D8）。

        仅对普通通道格（FLOOR）注册方向白名单，不触碰充电区/站点格；
        段外保持双向。吸取 001 期 RCS_ONE_WAY_AISLES 全图锁死教训——
        单向段必须短且有平行替代通路（预设数据责任，见 layouts/*.json）。
        """
        for seg in self.oneway_segments:
            d = seg.get("direction")
            if d not in ("N", "E", "S", "W"):
                raise ValueError(f"oneway_segments 方向非法: {d!r}（{self.profile_name}）")
            r0, r1 = seg["row_range"]
            c0, c1 = seg["col_range"]
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    cell = (r, c)
                    if self.kind.get(cell) == CellKind.FLOOR and cell not in self.no_transit:
                        self.directed[cell] = frozenset({d})
        # 矢量规则格总集（规划热路径快速判断用：只有这些格需要走 can_traverse）
        self.vector_cells: set[Cell] = set(self.directed) | self.no_transit

    # ---- 矢量性（FR-008 单一真源）----

    def can_traverse(self, frm: Cell, to: Cell, to_is_goal: bool = False) -> bool:
        """车辆从 frm 移动到 to 是否符合矢量性约束（planner/仲裁同源校验）。"""
        if to in self.pile_cells:
            # 桩格：仅作充电目标，且只能自北向南经上方连接格进入（倒车入位）
            return to_is_goal and frm == (to[0] - 1, to[1])
        if to in self.no_transit:
            return False  # 永不驶入禁行格（杜绝绕行旁路）
        allowed = self.directed.get(frm)
        if allowed is not None:
            if frm in self.pile_cells:  # 离桩：仅向北到连接格（原路倒出）
                return to == (frm[0] - 1, frm[1])
            dr, dc = to[0] - frm[0], to[1] - frm[1]
            d = {(0, 1): "E", (0, -1): "W", (1, 0): "S", (-1, 0): "N"}.get((dr, dc))
            return d is not None and d in allowed
        return frm not in self.no_transit

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
        return (self.charge_rows[0] - 2 <= r <= self.charge_rows[1] and
                self.charge_cols[0] - 3 <= c <= self.charge_cols[1] + 1)

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
