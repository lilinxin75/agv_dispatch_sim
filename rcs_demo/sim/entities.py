"""实体数据结构 (data-model.md: Vehicle / Tote / Task)。"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Vehicle 状态 ---
V_IDLE = "idle"
V_TO_PICKUP = "to_pickup"
V_TO_DROPOFF = "to_dropoff"
V_WAITING = "waiting"
V_STALLED = "stalled"
V_MAKING_WAY = "making_way"

# --- Tote 状态 ---
T_DOCKED = "docked"      # 静置（存储点位 / 入库口 / 出库口）
T_CARRIED = "carried"    # 被某车携带
T_CYCLING = "cycling"    # 出库送达后循环回归中

# --- Task 状态 ---
K_QUEUED = "queued"
K_ASSIGNED = "assigned"
K_IN_PROGRESS = "in_progress"
K_DONE = "done"
K_BLOCKED = "blocked"

# --- Task 类型 ---
INBOUND = "inbound"      # 入库口 → 存储点位
OUTBOUND = "outbound"    # 存储点位 → 出库口


@dataclass
class Vehicle:
    id: int
    cell: tuple[int, int]
    state: str = V_IDLE
    task_id: int | None = None
    completed: int = 0
    assigned_total: int = 0          # 累计被分配任务数（均匀性指标）
    path: list = field(default_factory=list)   # 剩余路径 [(r,c), ...]
    facing: str = "E"
    stall_ticks: int = 0             # 连续未移动 tick 计数
    target: tuple | None = None      # 当前移动目标（wander 或任务起/终点）
    making_way: bool = False         # 让路 sidestep 标记


@dataclass
class Tote:
    id: int
    location: tuple[int, int]        # 始终为具体格（入库口/出库口/点位）
    state: str = T_DOCKED
    carried_by: int | None = None
    claimed_by_task: int | None = None
    cycle_timer: int = 0             # T_CYCLING 状态下的剩余 tick


@dataclass
class Task:
    id: int
    type: str                        # INBOUND / OUTBOUND
    pickup: tuple[int, int]
    dropoff: tuple[int, int]
    tote_id: int
    state: str = K_QUEUED
    assigned_vehicle: int | None = None
