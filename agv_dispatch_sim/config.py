"""可调参数配置（全量参数支持 AGV_* 环境变量覆盖）。

布局常量依据 data-model.md「布局与电量常量」与 research.md R6/R10。
002 期增量（specs/002-constraints-layout-polish）：硬件速度/能耗模型、治理开关、
轨迹/指标参数（T002），矛盾组合校验 validate()（T003）——布局常量仅作为
baseline 预设缺省值（FR-011：坐标真源为 layouts/*.json）。
"""
from __future__ import annotations

import os
from pathlib import Path

LAYOUTS_DIR = Path(__file__).resolve().parent / "layouts"


def _env(name: str, default: float, cast=float):
    v = os.environ.get(f"AGV_{name}")
    return default if v is None else cast(v)


def _env_str(name: str, default: str):
    return os.environ.get(f"AGV_{name}", default)


def _env_bool(name: str, default: bool):
    v = os.environ.get(f"AGV_{name}")
    return default if v is None else v == "1"


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
# 002 期快验证发现：原默认 30s 使单桩真实速率仅 26.7 点/s（20%→100% 跨度），
# FR-029 核算公式却按 33.3 点/s 计——10 桩在 1 任务/秒下零余量，车队电量塌方式
# 归零（SC-006 失败）。取 20s（规格明确"可配置"）后单桩 40 点/s，重算余量 ~37%。
CHARGE_SECONDS = _env("CHARGE_SECONDS", 20)                     # 从 20% 充到满电秒数
CHARGE_RETURN_RATIO = _env("CHARGE_RETURN_RATIO", 1.0)          # 充至该比例离开充电区
CHARGE_PILES = int(_env("CHARGE_PILES", 10, int))               # 充电位数量（FR-029 核算默认 10）
# 充电区供电总功率预算 = 桩数 × 单桩速率 × 该比例（002 FR-008；<1 时功率受限）
CHARGE_POWER_BUDGET_RATIO = _env("CHARGE_POWER_BUDGET_RATIO", 1.0)

# ---- 路径规划（FR-027 / R9）----
TURN_PENALTY = _env("TURN_PENALTY", 3)          # 每次转向的额外代价（软约束）
# 加权 A* 启发式权重（002 性能修复）：高拥塞下 WH=1.6 的 A* 前沿近全图爆炸
# （实测第 2500+ tick 段 500 tick 耗 239s）；WH=3.0 全程平坦且吞吐仅差 ~3.4%
PLANNER_WH = _env("PLANNER_WH", 3.0)
# 每 tick 全局 A* 节点预算（002 性能修复）：深度拥塞下规划调用激增，
# 预算保证 tick 成本有界——超限的规划按暂时失败处理（冷却后重试）
PLANNER_NODE_BUDGET = int(_env("PLANNER_NODE_BUDGET", 100_000, int))

# ---- 硬件速度/能耗模型（002 FR-006/007，research.md D1/D2）----
# 进度累加器：每 tick progress += speed，≥1 才移动一格；
# speed = 1.0（空载直行）× LOADED_SPEED_RATIO（载货）× TURN_SPEED_RATIO（转向步）
LOADED_SPEED_RATIO = _env("LOADED_SPEED_RATIO", 0.7)
TURN_SPEED_RATIO = _env("TURN_SPEED_RATIO", 0.5)
TURN_ENERGY = _env("TURN_ENERGY", 0.5)          # 转向步额外耗电（点）

# ---- 业务治理（002 FR-002/004/005/009，research.md D4–D7）----
REASSIGN_TICKS = int(_env("REASSIGN_TICKS", 300, int))   # 任务停滞改派阈值
STATION_CAPACITY = int(_env("STATION_CAPACITY", 6, int))  # 工作站同时在场车辆上限
# 充电排队软上限：深度（排队+在充）超过即错峰延迟非紧急低电车（FR-009）
CHARGE_QUEUE_SOFT_LIMIT = int(_env(
    "CHARGE_QUEUE_SOFT_LIMIT", max(1, int(CHARGE_PILES * 0.6)), int))
# 拥塞治理组合开关（FR-015）：默认全启用，可独立关闭以便对比
GOVERN_BACKPRESSURE = _env_bool("GOVERN_BACKPRESSURE", True)
GOVERN_REASSIGN = _env_bool("GOVERN_REASSIGN", True)
GOVERN_CHARGE_DEFER = _env_bool("GOVERN_CHARGE_DEFER", True)
BACKPRESSURE_WAIT_RATIO = _env("BACKPRESSURE_WAIT_RATIO", 0.4)   # 等待率拥塞阈值
BACKPRESSURE_PRESSURE_TICKS = int(_env("BACKPRESSURE_PRESSURE_TICKS", 100, int))  # 去抖

# ---- 指标与轨迹（002 FR-016/025）----
METRICS_WINDOW_TICKS = int(_env("METRICS_WINDOW_TICKS", 600, int))  # 吞吐滑动窗口
TRAIL_TTL_TICKS = int(_env("TRAIL_TTL_TICKS", 30, int))  # 轨迹消失时长（≈3 秒）

# ---- 布局预设（002 FR-011/013，坐标真源 layouts/*.json）----
LAYOUT = _env_str("LAYOUT", "baseline")
# （001 期实验性 RCS_ONE_WAY_AISLES 全图单向开关已移除（002 T045）：
#  实测 ~1800 tick 全图锁死；单向化改由布局预设的局部 oneway_segments 承担）

# ---- 死锁检测（FR-012 / SC-002）----
STALL_TICKS = int(_env("STALL_TICKS", 150, int))          # 连续未移动 tick 阈值（~15s）
DEADLOCK_RECOVER_TICKS = int(_env("DEADLOCK_RECOVER_TICKS", 600, int))  # SC-002: 60s 内恢复

# ---- 冲突事件（FR-022/023）----
CONFLICT_MIN_TICKS = int(_env("CONFLICT_MIN_TICKS", 3, int))  # 互堵持续 tick 数判定阈值
# 互堵判定中"阻挡车未推进"的判 stall 阈值（002 口径修正）：载货+转弯 legit 速度
# 0.35 格/tick（合法慢速），阻挡车 ≥4 tick 无位移才视为真阻挡，否则视为正常跟车
CONFLICT_BLOCK_TICKS = int(_env("CONFLICT_BLOCK_TICKS", 4, int))

# ---- 充电行程治理（002 D14，tick-9600 解剖）----
# 在途充电行程上限（waiting-room 限流）：TO_CHARGE 旅行者达上限时不再新发桩
# 行程，改排队等待——防止充电波峰时全队涌向充电区、西入口走廊物理堵死
CHARGE_TRIP_CAP = int(_env("CHARGE_TRIP_CAP", 8, int))
# 桩认领滞留释放阈值：TO_CHARGE 认领桩后超过该 tick 数无位移 → 释放认领回
# 队列（防"旅行者堵在半路、桩被认领泄漏、队列无可服务桩"的占用泄漏）
CHARGE_CLAIM_STALL_TICKS = int(_env("CHARGE_CLAIM_STALL_TICKS", 300, int))
# 非在途/在充状态挂名桩的宽限：让路 WAITING 等状态挂名超过该 tick 数 → 释放认领
# （倒车入位后行程变长让路增多，防"10 桩被非在充车挂名、队列饿死"，T051）
WRONG_STATE_CLAIM_GRACE_TICKS = int(_env("WRONG_STATE_CLAIM_GRACE_TICKS", 60, int))
CONFLICT_LOG_DIR = os.environ.get("AGV_CONFLICT_LOG_DIR", "logs")
CONFLICT_OPTIMIZATION = os.environ.get("AGV_CONFLICT_OPTIMIZATION", "1") == "1"  # FR-024 热点加权

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

    单桩速率 MUST 用真实跨度速率（charge_rate_per_tick：只充 20%→满的 800 点跨度），
    002 期修正——原实现按 电池容量/充满时长（33.3 点/s）核算，比硬件实际高 25%，
    导致 10 桩零余量、车队电量塌方。
    基准参数（1 任务/秒、单任务往返约 200 格、满电 1000、20 秒充满）核算为 9。
    """
    avg_task_energy = 100 * 1.0 + 100 * LOAD_FACTOR      # 往返各约 100 格
    single_pile_rate = charge_rate_per_tick() * TICK_RATE  # 点/秒（真实跨度速率）
    busy = TASKS_PER_SECOND * avg_task_energy / single_pile_rate
    import math
    return max(1, math.ceil(busy * 1.3))


def available_layouts() -> list[str]:
    """layouts/ 目录下的全部预设名（*.json）。"""
    if not LAYOUTS_DIR.is_dir():
        return []
    return sorted(p.stem for p in LAYOUTS_DIR.glob("*.json"))


def validate() -> list[str]:
    """矛盾配置组合校验（002 T003 / FR-010）。返回可读错误列表（空 = 通过）。"""
    errs = []
    if not (0 < LOADED_SPEED_RATIO <= 1):
        errs.append(f"LOADED_SPEED_RATIO 应 ∈ (0,1]，当前 {LOADED_SPEED_RATIO}")
    if not (0 < TURN_SPEED_RATIO <= 1):
        errs.append(f"TURN_SPEED_RATIO 应 ∈ (0,1]，当前 {TURN_SPEED_RATIO}")
    if TURN_ENERGY < 0:
        errs.append(f"TURN_ENERGY 应 ≥ 0，当前 {TURN_ENERGY}")
    if CHARGE_SECONDS <= 0:
        errs.append(f"CHARGE_SECONDS 应 > 0，当前 {CHARGE_SECONDS}")
    if CHARGE_PILES < 1:
        errs.append(f"CHARGE_PILES 应 ≥ 1，当前 {CHARGE_PILES}")
    if REASSIGN_TICKS <= STALL_TICKS:
        errs.append(f"REASSIGN_TICKS({REASSIGN_TICKS}) 必须 > STALL_TICKS({STALL_TICKS})："
                    "任务需先经历卡死检测才谈得上停滞改派")
    if CHARGE_QUEUE_SOFT_LIMIT < 1:
        errs.append(f"CHARGE_QUEUE_SOFT_LIMIT 应 ≥ 1，当前 {CHARGE_QUEUE_SOFT_LIMIT}")
    if TRAIL_TTL_TICKS < 5:
        errs.append(f"TRAIL_TTL_TICKS 过小（{TRAIL_TTL_TICKS}），轨迹将不可见")
    if METRICS_WINDOW_TICKS < 100:
        errs.append(f"METRICS_WINDOW_TICKS 应 ≥ 100，当前 {METRICS_WINDOW_TICKS}")
    if LAYOUT not in available_layouts():
        errs.append(f"LAYOUT='{LAYOUT}' 不存在，可选：{available_layouts() or '(layouts/ 目录缺失)'}")
    return errs


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
        "charge_power_budget_ratio": CHARGE_POWER_BUDGET_RATIO,
        "recommended_charge_piles": recommended_charge_piles(),
        "turn_penalty": TURN_PENALTY,
        "stall_ticks": STALL_TICKS,
        # ---- 002 期增量（contracts/api-delta.md）----
        "layout": LAYOUT,
        "available_layouts": available_layouts(),
        "loaded_speed_ratio": LOADED_SPEED_RATIO,
        "turn_speed_ratio": TURN_SPEED_RATIO,
        "turn_energy": TURN_ENERGY,
        "reassign_ticks": REASSIGN_TICKS,
        "station_capacity": STATION_CAPACITY,
        "charge_queue_soft_limit": CHARGE_QUEUE_SOFT_LIMIT,
        "govern_backpressure": GOVERN_BACKPRESSURE,
        "govern_reassign": GOVERN_REASSIGN,
        "govern_charge_defer": GOVERN_CHARGE_DEFER,
        "trail_ttl_ticks": TRAIL_TTL_TICKS,
        "metrics_window_ticks": METRICS_WINDOW_TICKS,
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
