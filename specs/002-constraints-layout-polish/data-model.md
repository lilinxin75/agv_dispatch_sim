# Data Model: 调度约束完善与布局/效率/显示优化

**Feature**: 002-constraints-layout-polish | **Date**: 2026-09-30
**基线**: 001 期 data-model（Vehicle/Tote/Task/Obstacle/ChargingArea）——本文只列扩展与新增。

## 实体扩展

### Task（扩展：显式生命周期，FR-001/002）

| 字段 | 类型 | 约束 |
|------|------|------|
| id | int | 全局唯一，单调递增 |
| tote_id | int | → Tote；任意时刻至多一辆车认领（tote.claimed_by 唯一） |
| kind | enum | INBOUND / OUTBOUND（1:1 成对生成不变） |
| origin / dest | Cell | 站点取放格 |
| state | enum | PENDING / ACTIVE / DONE / REASSIGNED |
| assigned_vehicle | int? | ACTIVE 时非空；改派时置空并 reassign_count+=1 |
| created_tick / done_tick | int | done_tick 用于平均任务周期 |
| stall_ticks | int | 同步执行车 stall_ticks；≥ REASSIGN_TICKS 触发改派（FR-002） |
| reassign_count | int | ≥0 |

**状态迁移**：PENDING →（派发）→ ACTIVE →（完成）→ DONE；ACTIVE →（超时改派）→
PENDING（记 REASSIGNED 事件后重新入队，台账 reassigned 计数 +1，任务对象继续存活）。
**不变量**：`spawned == done + active_count + reassigned`（SC-002）。

### Tote（扩展：身份连续，FR-003）

| 字段 | 约束 |
|------|------|
| id | 循环全程不变（出库→入库口→再出库，不销毁不重建） |
| position | enum: SLOT(cell) / ON_VEHICLE(vid) / STATION(cell)；位置迁移逐 tick 连续可追溯 |
| cycle_count | 完成轮次 |
| claimed_by | int?；与 Task.assigned_vehicle 一致性为引擎不变量 |

### Vehicle（扩展：硬件模型，FR-006/007）

| 字段 | 类型 | 约束 |
|------|------|------|
| progress | float [0,1) | 每 tick `+= speed`；≥1 才移动一格并清零（D1） |
| speed | float | 状态唯一决定：1.0 空载直行；×0.7 载货；×0.5 转向步（可配） |
| battery | float | 非负；扣减 = 载重倍率 × 转弯附加（D2） |
| defer_charge | bool | 错峰延迟标记；被派任务即清除（D7） |
| state | enum | 001 五态 + 充电子态不变；受阻=STALLED |

### ChargingPile / ChargingArea（扩展：矢量性，FR-008，2026-10-01 修订为倒车入位）

| 字段 | 约束 |
|------|------|
| cell | 桩格；对非目标路径视为墙（不可穿越） |
| connector_north | 桩位**上方**连接格（`pile_row-1`），N/S 双向：进入=自北向南驶入，离开=原路向北倒出（D15）；进出仅能经由该格 |
| area.queue_cells | 西侧进站通道降级为排队等待格（D13/D14 排队治理不变）；东出通道降级为普通通道 |
| queue | 深度 ≤ CHARGE_QUEUE_SOFT_LIMIT 时非紧急车 defer（D7） |
| power_budget | 同时充电桩数 ≤ CHARGE_PILES；总功率 = 在充数 × rate ≤ 预算（D2/FR-008） |

### Grid（扩展：布局参数化 + 有向边，FR-011/014）

| 字段 | 约束 |
|------|------|
| profile: LayoutProfile | 建图唯一来源；config.py 布局常量降级为 baseline profile 缺省值 |
| directed: dict[Cell, frozenset[dir]] | 仅充电区内部格注册；A* can_enter 与仲裁同源校验 |
| standby_zones: list[rect] | FR-028 待命选点范围；容量 ≥ 空闲峰值（自检校验） |

## 新增实体

### LayoutProfile（FR-011~013，`rcs_demo/layouts/*.json`）

```json
{
  "name": "wide-oneway",
  "grid": 200,
  "shelf":     {"row_range": [60, 139], "col_range": [40, 159]},
  "inbound":   {"row_range": [95, 104], "col_range": [0, 4]},
  "outbound":  {"row_range": [95, 104], "col_range": [195, 199]},
  "charging":  {"row_range": [185, 193], "col_range": [85, 114],
                "piles": 10, "entry_cols": [86, 88], "exit_cols": [112, 114]},
  "standby_zones": [],
  "oneway_segments": [],
  "ring_road_width": 3
}
```

> **2026-10-01 修订**：`entry_cols`/`exit_cols` 语义降级——倒车入位后进/出桩走
> 桩位上方通道，`entry_cols` 仅划定排队等待格范围，`exit_cols` 段视为普通通道；
> 预设 JSON 字段名保持兼容，不迁移。

预设：`baseline`（=001 现状） / `wide-oneway`（主干道加宽 + 局部单向段） /
`charge-expanded`（桩 16 + 南移蓄车区 + 显式待命区）。`--layout` 选择，重启生效。

### MetricsSnapshot（FR-016/023，每 10 tick 采样，600 点环形缓冲）

| 字段 | 说明 |
|------|------|
| tick | 采样时刻 |
| throughput_window | 窗口内完成任务数（600 tick 滑动窗口） |
| avg_cycle_ticks | 窗口内完成任务的平均周期（created→done） |
| waiting_ratio | 等待+受阻车辆占比（SC-001 辅口径） |
| charge_queue_depth | 排队 + 在充数 |
| conflict_per_ktick | 互堵事件频次（次/千 tick，001 口径） |

### CongestionEvent（扩展：治理动作，FR-015）

在 001 期 conflict event 基础上增 `action` 字段：`reassigned / backpressure /
charge_deferred / rerouted`——事件面板据此展示"治理动作记录"（US4 场景 2）。

## 关键校验规则汇总

1. 台账恒等式（SC-002）：每 tick 引擎自检 + 测试断言
2. tote.claimed_by 与 Task.assigned_vehicle 一一对应；改派原子交换
3. 电量非负；充电干预先于归零（SC-006）
4. 有向边约束：规划与执行同源（D3/D15）；桩格非目标不可穿越；进出仅经北侧
   连接格（倒车入位，2026-10-01）
5. 布局自检（--check）：全图可达 + 矢量性成立 + 待命容量充足 + 配置矛盾组合拒绝
   （如 CHARGE_SECONDS=0、SPEED_RATIO≤0）
