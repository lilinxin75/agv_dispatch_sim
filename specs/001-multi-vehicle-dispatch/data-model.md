# Data Model: 多车网格调度演示

Date: 2026-09-29 | Feature: 001-multi-vehicle-dispatch

全部实体为内存对象（无持久化）。坐标约定：`(row, col)`，0 起始，`(0,0)` 为左上角。

## Cell（格子）

| 字段 | 类型 | 说明 |
|------|------|------|
| row, col | int | 坐标 |
| kind | enum | `boundary`（边界，不可通行）/ `floor`（通道）/ `slot`（存储点位）/ `inbound`（入库口）/ `outbound`（出库口） |
| blocked | bool | 是否被障碍物阻挡（操作者动态设置；boundary 恒为 true 语义上不可通行） |
| occupant | Vehicle? | 当前占据该车（携带料箱或普通停留） |

**规则**：
- 任一 tick 内 `occupant` 至多一车（零碰撞不变量，违规即为引擎 bug）。
- `slot` 上静置的 Tote 不影响可通行性；`slot` 不可被添加为障碍物？——可以，但若点位有料箱则拒绝（FR/US4 场景 4 的延伸：有车或有箱的格子拒绝放置障碍物）。
- `boundary` 不可改为可通行；站点格不可删除。

## Vehicle（小车）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 1..100 唯一编号（前端显示） |
| cell | (row, col) | 当前逻辑格 |
| render_pos | (float, float) | 渲染插值位置（格间线性插值） |
| facing | enum | N/E/S/W（渲染朝向） |
| state | enum | `idle` 空闲 / `to_pickup` 前往取货点 / `to_dropoff` 携带中前往目的点 / `waiting` 等待放行 / `stalled` 疑似卡死 / `making_way` 让路中 |
| task | Task? | 当前任务 |
| completed_count | int | 已完成任务数（均匀性指标） |
| path | [(row,col)] | 剩余路径（A* 产出） |
| stall_ticks | int | 连续未移动 tick 计数（死锁检测） |

**状态转移**：`idle → to_pickup`（派单）→ `to_pickup`（到达点位顶起料箱）→ `to_dropoff`（送达）→ `idle`；任意移动状态受阻 → `waiting`；`waiting` 超阈值 → `stalled`；恢复后回原状态；空闲且被要求让路 → `making_way` → `idle`。

## Tote（料箱）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 唯一编号（循环复用，不销毁） |
| location | union | `slot(row,col)` / `at_inbound` / `at_outbound` / `carried_by(vehicle_id)` |
| state | enum | `docked` 静置 / `carried` 被携带 / `cycling` 循环中（出库口→入库口回归） |

**规则**：
- 初始 ~50% slot 有箱（半仓，FR-017）。
- 出库送达 → `cycling`（出库口停留短暂时间）→ 出现在入库口 → 可被入库任务领取。
- 一格一箱；小车顶起后 `slot` 变空、可入库新箱。

## Task（任务）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 递增唯一编号 |
| type | enum | `inbound`（入库口→存储点位）/ `outbound`（存储点位→出库口） |
| pickup / dropoff | (row,col) | 起终点格 |
| tote_id | int | 目标/搬运料箱 |
| state | enum | `queued` / `assigned` / `in_progress` / `done` / `blocked`（终点不可达暂时搁置） |
| assigned_vehicle | int? | 承运车 |

**规则**：
- 入:出按 1:1 成对生成（FR-018）；出库选随机"有箱点位"，入库选随机"空点位"。
- `blocked` 任务保留队列，障碍物变更/格子释放后重试（规格边界项）。

## Obstacle（障碍物）

| 字段 | 类型 | 说明 |
|------|------|------|
| cell | (row,col) | 唯一键 |
| created_at_tick | int | 用于 UI 展示 |

**规则**：添加前校验：非 boundary/站点格、无 occupant、slot 无静置料箱（否则拒绝，US4 场景 4）。删除立即恢复可通行并触发受影响车辆重规划。

## Reservation（预约，引擎内部）

`dict[(row, col, tick) -> vehicle_id]`：某车声明将在某 tick 占用某格。规划写入、移动前校验、tick 推进时清理过期项。互斥写入失败 = 该格该 tick 被他车占用/预约 → 等待。

## Simulation Metrics（指标快照，FR-013）

`tick`、`tasks_done_total`、`per_vehicle_completed[]`、`waiting_count`、`stalled_count`、`collision_count`（恒为 0 的断言计数器）、`totes_in_shelf`。

## 布局常量（config.py，R6）

`GRID = 200×200`；货架区 rows 60..139 × cols 40..159（点位行间隔 2、列间隔 1）；入库口 rows 95..104 × cols 0..4；出库口 rows 95..104 × cols 195..199；边界不可通行。
