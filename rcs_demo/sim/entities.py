"""实体数据结构（字段与枚举严格按 data-model.md）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

Cell = tuple[int, int]  # (row, col)，0 起始，(0,0) 左上角


class CellKind(str, Enum):
    BOUNDARY = "boundary"      # 边界围墙，不可通行
    FLOOR = "floor"            # 通道
    SLOT = "slot"              # 存储点位
    INBOUND = "inbound"        # 入库口
    OUTBOUND = "outbound"      # 出库口
    CHARGE_PILE = "charge_pile"  # 充电位
    CHARGE_IN = "charge_in"    # 进站通道（单向）
    CHARGE_OUT = "charge_out"  # 出站通道（单向）
    CHARGE_QUEUE = "charge_queue"  # 排队格


class VehicleState(str, Enum):
    IDLE = "idle"              # 空闲待命（不漫游，FR-028）
    TO_PICKUP = "to_pickup"    # 前往取货点
    TO_DROPOFF = "to_dropoff"  # 携带中前往目的点
    TO_CHARGE = "to_charge"    # 前往充电区
    CHARGING = "charging"      # 充电中
    WAITING = "waiting"        # 等待放行
    STALLED = "stalled"        # 疑似卡死
    MAKING_WAY = "making_way"  # 让路中


class ToteState(str, Enum):
    DOCKED = "docked"      # 静置
    CARRIED = "carried"    # 被携带
    CYCLING = "cycling"    # 循环中（出库口→入库口回归）


class TaskState(str, Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"    # 终点不可达暂时搁置


@dataclass
class Vehicle:
    id: int
    cell: Cell
    facing: str = "E"                       # N/E/S/W（渲染朝向）
    state: VehicleState = VehicleState.IDLE
    task: Optional["Task"] = None
    completed_count: int = 0                # 仅计搬运任务（均匀性指标）
    assigned_count: int = 0                 # 累计被分配任务数（FR-004 分配均匀性度量）
    battery: float = 1000.0                 # 剩余电量点数（BATTERY_FULL）
    need_charge: bool = False               # 低于低电阈值后置位
    charge_target: Optional[Cell] = None    # 分配到的充电位（排队期间为空）
    way_target: Optional[Cell] = None       # 让路目标格（making_way 专用）
    carrying: Optional["Tote"] = None       # 携带中的料箱
    path: list[Cell] = field(default_factory=list)  # 剩余路径（不含当前格）
    stall_ticks: int = 0                    # 连续未移动 tick 计数（死锁检测）
    prev_state: Optional[VehicleState] = None  # waiting/stalled 恢复后回原状态

    # 渲染插值用
    render_from: Cell = (0, 0)
    moved_this_tick: bool = False
    wait_reason: Optional[int] = None       # 等待因谁（对方 vehicle_id，冲突检测用）

    # ---- 002 期扩展（FR-006/009，data-model.md Vehicle）----
    progress: float = 0.0                   # 位移进度累加器（≥1 才移动一格）
    defer_charge: bool = False              # 错峰充电延迟标记（队列满时置位）
    last_move_tick: int = -10**9            # 最近一次实际位移时刻（互堵判定口径）

    def is_moving_state(self) -> bool:
        return self.state in (
            VehicleState.TO_PICKUP,
            VehicleState.TO_DROPOFF,
            VehicleState.TO_CHARGE,
            VehicleState.MAKING_WAY,
        )

    def dispatchable(self) -> bool:
        """是否可参与任务分配（充电中的车不参与，data-model.md Vehicle 规则）。"""
        return self.state not in (
            VehicleState.TO_CHARGE,
            VehicleState.CHARGING,
            VehicleState.STALLED,
        )


@dataclass
class Tote:
    id: int
    # location: (row,col) slot / "inbound" / "outbound" / ("carried", vehicle_id)
    location: Cell | str | tuple
    state: ToteState = ToteState.DOCKED


@dataclass
class Task:
    id: int
    type: str                # "inbound" | "outbound"
    pickup: Cell
    dropoff: Cell
    tote_id: int
    state: TaskState = TaskState.QUEUED
    assigned_vehicle: Optional[int] = None
    # ---- 002 期扩展（FR-001/002，data-model.md Task）----
    created_tick: int = 0            # 生成时刻（平均任务周期口径）
    done_tick: Optional[int] = None  # 完成时刻
    assigned_tick: int = 0           # 最近一次派发时刻
    last_progress_tick: int = 0      # 执行车最近一次移动时刻（无进度改派口径）
    stall_ticks: int = 0             # 执行车停滞计数（同步自 vehicle.stall_ticks）
    reassign_count: int = 0          # 改派次数


@dataclass
class Obstacle:
    cell: Cell
    created_at_tick: int = 0


@dataclass
class ChargingArea:
    piles: list[Cell] = field(default_factory=list)          # 充电位格（N=10）
    pile_owner: dict[Cell, Optional[int]] = field(default_factory=dict)  # cell -> vehicle_id?
    entry_lane: list[Cell] = field(default_factory=list)     # 充电区西侧内部通道（倒车入位后为排队区标注）
    exit_lane: list[Cell] = field(default_factory=list)      # 充电区东侧内部通道（倒车入位后为普通通道）
    queue: list[int] = field(default_factory=list)           # 排队 vehicle_id（电量更低者优先出队）
    queue_cells: list[Cell] = field(default_factory=list)    # 排队格（不占主干道）
    approach_row: int = 0                                    # 上方进桩通道行（倒车入位，D15）


class ConflictEvent:
    """互堵冲突事件（FR-022）：≥2 台车相互等待成团。"""

    def __init__(self, start_tick: int, vehicles: list[int], positions: list[Cell],
                 carried: list[bool]):
        self.start_tick = start_tick
        self.end_tick: Optional[int] = None
        self.vehicles = sorted(set(vehicles))
        self.positions = positions
        self.carried = carried  # 涉事车辆有无料箱

    def to_json(self) -> dict:
        return {
            "kind": "conflict_event",
            "start_tick": self.start_tick,
            "end_tick": self.end_tick,
            "duration_ticks": (self.end_tick or self.start_tick) - self.start_tick,
            "vehicles": self.vehicles,
            "positions": [list(p) for p in self.positions],
            "carried": self.carried,
        }
