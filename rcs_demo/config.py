"""可调参数配置（全量参数支持 RCS_* 环境变量覆盖）。

布局常量依据 data-model.md「布局与电量常量」与 research.md R6/R10。
"""
from __future__ import annotations

import os


def _env(name: str, default: float, cast=float):
    v = os.environ.get(f"RCS_{name}")
    return default if v is None else cast(v)


# ---- 网格与车队 ----
GRID = int(_env("GRID", 200, int))              # 地图边长（GRID×GRID 格）
VEHICLES = int(_env("VEHICLES", 100, int))      # 车辆数
TICK_RATE = int(_env("TICK_RATE", 10, int))     # 仿真 tick 频率（tick/秒）

# ---- 任务 ----
TASKS_PER_SECOND = _env("TASKS_PER_SECOND", 1)  # 任务生成节奏（核算基准，FR-003/FR-029）
# 在途任务上限（assigned+in_progress）：规格假设基准负载下约 80 台在途、20 台空闲待命；
# 限制并发可防全队投入导致的车流拥塞死亡螺旋（超出部分留在队列排队）
MAX_ACTIVE_TASKS = int(_env("MAX_ACTIVE_TASKS", 80, int))

# ---- 电量模型（FR-025 / R10）----
BATTERY_FULL = int(_env("BATTERY_FULL", 1000, int))            # 满电点数（可续航 1000 格）
LOAD_FACTOR = _env("LOAD_FACTOR", 1.5)                          # 载货每格耗电倍率（空载 1 点/格）
BATTERY_LOW_RATIO = _env("BATTERY_LOW_RATIO", 0.2)              # 低电阈值比例
BATTERY_CRITICAL_RATIO = _env("BATTERY_CRITICAL_RATIO", 0.05)   # 临界阈值比例（兜底待命）
CHARGE_SECONDS = _env("CHARGE_SECONDS", 30)                     # 从 20% 充到满电秒数
CHARGE_RETURN_RATIO = _env("CHARGE_RETURN_RATIO", 1.0)          # 充至该比例离开充电区
CHARGE_PILES = int(_env("CHARGE_PILES", 10, int))               # 充电位数量（FR-029 核算默认 10）

# ---- 路径规划（FR-027 / R9）----
TURN_PENALTY = _env("TURN_PENALTY", 3)          # 每次转向的额外代价（软约束）
# 货架横向通道单向化（隔行交替 E/W）：实验性选项，默认关闭。
# 实测（3000 tick 全尺寸对比）：开启后约 1800 tick 起完全锁死（任务冻结），
# 原因是长单向通道内目标在身后的车辆无法回转、纵向逃逸被拥塞封死。
# 关闭时依赖让路 + 死锁恢复（规格澄清项「相向会车依赖让路」的原始语义）。
ONE_WAY_AISLES = os.environ.get("RCS_ONE_WAY_AISLES", "0") == "1"

# ---- 死锁检测（FR-012 / SC-002）----
STALL_TICKS = int(_env("STALL_TICKS", 150, int))          # 连续未移动 tick 阈值（~15s）
DEADLOCK_RECOVER_TICKS = int(_env("DEADLOCK_RECOVER_TICKS", 600, int))  # SC-002: 60s 内恢复

# ---- 冲突事件（FR-022/023）----
CONFLICT_MIN_TICKS = int(_env("CONFLICT_MIN_TICKS", 3, int))  # 互堵持续 tick 数判定阈值
CONFLICT_LOG_DIR = os.environ.get("RCS_CONFLICT_LOG_DIR", "logs")
CONFLICT_OPTIMIZATION = os.environ.get("RCS_CONFLICT_OPTIMIZATION", "1") == "1"  # FR-024 热点加权

# ---- 前端视口（FR-030）----
ZOOM_MIN = _env("ZOOM_MIN", 0.25)
ZOOM_MAX = _env("ZOOM_MAX", 4.0)

# ---- 固定布局（research.md R6）----
SHELF_ROW_RANGE = (60, 139)     # 货架区行范围（含端点）
SHELF_COL_RANGE = (40, 159)     # 货架区列范围（含端点）
# 点位行列均为偶数：60,62,..,138（40 行）× 40,42,..,158（60 列）= 2400 个 slot
INBOUND_ROW_RANGE = (95, 104)   # 入库口（西侧边缘）
INBOUND_COL_RANGE = (0, 4)
OUTBOUND_ROW_RANGE = (95, 104)  # 出库口（东侧边缘）
OUTBOUND_COL_RANGE = (195, 199)
CHARGE_ROW_RANGE = (185, 193)   # 充电区（南侧环场主干道内侧）
CHARGE_COL_RANGE = (85, 114)

# ---- 派生量 ----
RING_ROAD_WIDTH = 2             # 环场主干道宽度（FR-020）


def battery_low() -> int:
    """低电阈值点数。"""
    return int(BATTERY_FULL * BATTERY_LOW_RATIO)


def battery_critical() -> int:
    """临界电量阈值点数。"""
    return int(BATTERY_FULL * BATTERY_CRITICAL_RATIO)


def charge_rate_per_tick() -> float:
    """单桩每 tick 回电量（CHARGE_SECONDS 秒从 20% 充到满）。"""
    span = BATTERY_FULL * (1.0 - BATTERY_LOW_RATIO)
    return span / (CHARGE_SECONDS * TICK_RATE)


def recommended_charge_piles() -> int:
    """FR-029 桩数核算：忙碌桩数 ≈ 节奏×单任务耗电 ÷ 单桩速率，上取整 +30% 余量。

    基准参数（1 任务/秒、单任务往返约 200 格、满电 1000、30 秒充满）核算为 10。
    """
    avg_task_energy = 100 * 1.0 + 100 * LOAD_FACTOR      # 往返各约 100 格
    single_pile_rate = BATTERY_FULL / CHARGE_SECONDS     # 点/秒
    busy = TASKS_PER_SECOND * avg_task_energy / single_pile_rate
    import math
    return max(1, math.ceil(busy * 1.3))


def all_params() -> dict:
    """GET /api/config 全量参数（契约 rest-api.md）。"""
    return {
        "grid": GRID,
        "vehicles": VEHICLES,
        "tick_rate": TICK_RATE,
        "tasks_per_second": TASKS_PER_SECOND,
        "battery_full": BATTERY_FULL,
        "load_factor": LOAD_FACTOR,
        "battery_low_ratio": BATTERY_LOW_RATIO,
        "battery_critical_ratio": BATTERY_CRITICAL_RATIO,
        "charge_seconds": CHARGE_SECONDS,
        "charge_return_ratio": CHARGE_RETURN_RATIO,
        "charge_piles": CHARGE_PILES,
        "recommended_charge_piles": recommended_charge_piles(),
        "turn_penalty": TURN_PENALTY,
        "stall_ticks": STALL_TICKS,
        "zoom_min": ZOOM_MIN,
        "zoom_max": ZOOM_MAX,
        "shelf_rows": list(SHELF_ROW_RANGE),
        "shelf_cols": list(SHELF_COL_RANGE),
        "inbound_rows": list(INBOUND_ROW_RANGE),
        "inbound_cols": list(INBOUND_COL_RANGE),
        "outbound_rows": list(OUTBOUND_ROW_RANGE),
        "outbound_cols": list(OUTBOUND_COL_RANGE),
        "charge_rows": list(CHARGE_ROW_RANGE),
        "charge_cols": list(CHARGE_COL_RANGE),
    }
