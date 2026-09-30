# Data Model: 多车网格调度演示

Date: 2026-09-29 | Feature: 001-multi-vehicle-dispatch

全部实体为内存对象（无持久化）。坐标约定：`(row, col)`，0 起始，`(0,0)` 为左上角。

## Cell（格子）

| 字段 | 类型 | 说明 |
|------|------|------|
| row, col | int | 坐标 |
| kind | enum | `boundary`（边界，不可通行）/ `floor`（通道）/ `slot`（存储点位）/ `inbound`（入库口）/ `outbound`（出库口）/ `charge_pile`（充电位）/ `charge_in`（进站通道）/ `charge_out`（出站通道）/ `charge_queue`（排队格） |
| blocked | bool | 是否被障碍物阻挡（操作者动态设置；boundary 恒为 true 语义上不可通行） |
| occupant | Vehicle? | 当前占据该车（携带料箱或普通停留） |

**规则**：
- 任一 tick 内 `occupant` 至多一车（零碰撞不变量，违规即为引擎 bug）。
- `slot` 上静置的 Tote 不影响可通行性；`slot` 不可被添加为障碍物？——可以，但若点位有料箱则拒绝（FR/US4 场景 4 的延伸：有车或有箱的格子拒绝放置障碍物）。
- `boundary` 不可改为可通行；站点格、充电区格（pile/in/out/queue）不可添加障碍物、不可删除。

## Vehicle（小车）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 1..100 唯一编号（前端显示） |
| cell | (row, col) | 当前逻辑格 |
| render_pos | (float, float) | 渲染插值位置（格间线性插值） |
| facing | enum | N/E/S/W（渲染朝向） |
| state | enum | `idle` 空闲待命（不漫游）/ `to_pickup` 前往取货点 / `to_dropoff` 携带中前往目的点 / `to_charge` 前往充电区 / `charging` 充电中 / `waiting` 等待放行 / `stalled` 疑似卡死 / `making_way` 让路中 |
| task | Task? | 当前搬运任务（充电行程不占用 Task，见 ChargingArea） |
| completed_count | int | 已完成搬运任务数（均匀性指标，不含充电行程） |
| battery | int | 剩余电量点数，0..BATTERY_FULL（默认满电 1000；空载 1 点/格、载货 1.5 点/格） |
| need_charge | bool | battery 低于低电阈值（默认 20%）后置位，完成当前任务后触发充电行程 |
| charge_target | (row,col)? | 分配到的充电位（排队期间为空） |
| path | [(row,col)] | 剩余路径（方向感知 A* 产出，R9） |
| stall_ticks | int | 连续未移动 tick 计数（死锁检测） |

**状态转移**：`idle → to_pickup`（派单）→ `to_pickup`（到达点位顶起料箱）→ `to_dropoff`（送达）→ `idle`；`idle/to_dropoff` 完成且 `need_charge` → `to_charge`（经进站通道至空闲充电位，无车位则先至 `charge_queue` 排队格）→ 到位 → `charging`（按速率回电）→ 达返回阈值（默认充满）→ 经出站通道离开 → `idle`（`need_charge` 清零）；任意移动状态受阻 → `waiting`；`waiting` 超阈值 → `stalled`；恢复后回原状态；空闲且被要求让路 → `making_way` → `idle`。`to_charge`/`charging` 中的车不参与任务分配。

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

## ChargingArea（充电区，FR-016/026/029）

| 字段 | 类型 | 说明 |
|------|------|------|
| piles | [(row,col)] × N | 充电位格列表（N=10，按 FR-029 公式核算），每格同一时刻至多一车 |
| pile_owner | dict[cell → vehicle_id?] | 当前在充车辆 |
| entry_lane / exit_lane | [(row,col)] | 进站/出站单向通道格序列（进站口西侧入、出站口东侧出） |
| queue | [vehicle_id] | 充电位全满时的排队序列（电量更低者优先出队） |
| queue_cells | [(row,col)] | 进站通道外指定排队格（不占主干道） |

**规则**：
- 充电行程为车级内部状态（`to_charge`/`charging`），**不生成 Task**，不计入 completed_count 与 1:1 任务对。
- 充电速率 = BATTERY_FULL / CHARGE_SECONDS（默认 30 秒从 20% 充到满，可配置）；达到返回阈值（默认充满）即离开。
- 电量降至临界阈值（默认 5%）仍未获得车位 → 完成当前移动段后直接前往充电区入口待命（兜底，不电量归零趴窝，SC-010）。

## Reservation（预约，引擎内部）

`dict[(row, col, tick) -> vehicle_id]`：某车声明将在某 tick 占用某格。规划写入、移动前校验、tick 推进时清理过期项。互斥写入失败 = 该格该 tick 被他车占用/预约 → 等待。

## Simulation Metrics（指标快照，FR-013）

`tick`、`tasks_done_total`、`per_vehicle_completed[]`、`waiting_count`、`stalled_count`、`collision_count`（恒为 0 的断言计数器）、`totes_in_shelf`、`charging_count`（充电中车辆数）、`charge_queue_count`（排队待充数）、`avg_battery`（车队平均电量百分比）。

## 布局与电量常量（config.py，R6/R10）

`GRID = 200×200`；货架区 rows 60..139 × cols 40..159（点位行列间隔均为 1 格通道）；入库口 rows 95..104 × cols 0..4；出库口 rows 95..104 × cols 195..199；充电区 rows 185..193 × cols 85..114（10 充电位一排 + 进/出站单向通道 + 排队格）；边界不可通行。
电量参数：`BATTERY_FULL=1000`、`LOAD_FACTOR=1.5`、`BATTERY_LOW_RATIO=0.2`、`BATTERY_CRITICAL_RATIO=0.05`、`CHARGE_SECONDS=30`、`CHARGE_RETURN_RATIO=1.0`、`TASKS_PER_SECOND=1`、`TURN_PENALTY=3`（均支持 env 覆盖）。
