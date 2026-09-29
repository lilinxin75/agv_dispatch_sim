"""可调参数配置：布局常量、仿真节奏、阈值。

环境变量以 ``RCS_`` 前缀覆盖默认值（见 from_env）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    # --- 地图与固定布局 (research.md R6) ---
    grid_size: int = 200
    shelf_row_range: tuple = (60, 139)      # 货架区行范围（含端点）
    shelf_col_range: tuple = (40, 159)      # 货架区列范围
    slot_row_step: int = 2                  # 存储点位行间隔
    slot_col_step: int = 2                  # 存储点位列间隔
    inbound_row_range: tuple = (95, 104)    # 入库口（西侧，紧邻环场主干道）
    inbound_col_range: tuple = (2, 6)
    outbound_row_range: tuple = (95, 104)   # 出库口（东侧，紧邻环场主干道）
    outbound_col_range: tuple = (193, 197)

    # --- 车队与料箱 ---
    vehicle_count: int = 100
    fill_ratio: float = 0.5                 # 初始半仓 (FR-017)
    inbound_initial_totes: int = 20         # 初始停在入库口的料箱数

    # --- 仿真节奏 ---
    tick_rate: int = 10                     # 逻辑 tick / 秒
    fast_multiplier: int = 5                # --fast 时 tick 倍率
    task_interval: int = 5                  # 每隔多少 tick 尝试成对生成任务
    max_queued_tasks: int = 60
    cycling_ticks: int = 50                 # 出库口循环回入库口所需 tick

    # --- 调度/防碰撞/死锁 ---
    reserve_horizon: int = 8                # A* 预约前瞻 tick 数
    stall_threshold: int = 150              # 卡死判定阈值 tick (~15s @10Hz)

    # --- 冲突记录与优化 (Phase 9 / FR-022~024) ---
    record_conflicts: bool = True           # 互堵事件 JSONL 记录
    conflict_optimization: bool = True      # 拥堵感知优化（A/B 对比开关）
    data_dir: str = "data"                  # 冲突批次文件目录

    # --- 服务 ---
    port: int = 8000
    host: str = "0.0.0.0"
    max_events: int = 200
    fast: bool = False                       # --fast 加速仿真

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls()
        for fname in (
            "port",
            "vehicle_count",
            "tick_rate",
            "task_interval",
            "stall_threshold",
            "fill_ratio",
            "inbound_initial_totes",
            "max_queued_tasks",
            "record_conflicts",
            "conflict_optimization",
        ):
            env = os.environ.get(f"RCS_{fname.upper()}")
            if env:
                cur = getattr(cfg, fname)
                if isinstance(cur, bool):
                    setattr(cfg, fname, env.lower() not in ("0", "false", "no"))
                else:
                    setattr(cfg, fname, type(cur)(env))
        return cfg

    @classmethod
    def scaled(cls) -> "Config":
        """缩尺配置：供 pytest 快速回归使用（与全量共享引擎代码）。"""
        return cls(
            grid_size=40,
            shelf_row_range=(12, 27),
            shelf_col_range=(8, 31),
            inbound_row_range=(18, 21),
            inbound_col_range=(2, 4),
            outbound_row_range=(18, 21),
            outbound_col_range=(35, 37),
            vehicle_count=10,
            inbound_initial_totes=4,
            stall_threshold=25,
            cycling_ticks=20,
            task_interval=2,
            max_queued_tasks=20,
        )
