---
description: "Task list for feature implementation"
---

# Tasks: 多车网格调度演示 (Multi-Vehicle Grid Dispatch Demo)

**Input**: Design documents from `/specs/001-multi-vehicle-dispatch/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/rest-api.md ✅, contracts/websocket.md ✅, quickstart.md ✅

**Tests**: quickstart.md 与 plan.md 明确要求 pytest（缩尺快速回归）+ `--check` 自检，因此各故事包含测试任务（先写、先失败）。

**Organization**: 按用户故事分组。spec.md 定义了 US1–US5；充电/电量（FR-025~029、SC-010）与冲突事件记录分析优化（FR-022~024、SC-008/009）是规格中独立可验收的增量但未单列故事，按 **US6 / US7（派生故事）** 组织，保证独立实现与测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事（US1–US7）
- 描述中含确切文件路径与必须遵守的约束（数据模型字段、契约格式、常量值均引自 data-model.md / contracts/ / research.md，实现时不得随意更改）

## Path Conventions

单项目结构（plan.md）：Python 包 `rcs_demo/`（sim/ api/ static/）+ 仓库根 `tests/` + `requirements.txt` + `README.md`。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目初始化与基础结构

- [x] T001 创建项目骨架：`rcs_demo/`（含 `sim/`、`api/`、`static/` 子包与 `__init__.py`）、`tests/`、`requirements.txt`、`README.md` 占位，符合 plan.md Project Structure
- [x] T002 [P] 编写 `requirements.txt`：`fastapi`、`uvicorn`、`websockets`、`pytest`（章程 II：单一依赖清单，禁止 Node 构建链），并验证 `pip install -r requirements.txt` 可装
- [x] T003 [P] 实现 `rcs_demo/config.py`：全量可调参数并支持 `RCS_*` 环境变量覆盖——`GRID=200`、`VEHICLES=100`、`TASKS_PER_SECOND=1`、`BATTERY_FULL=1000`、`LOAD_FACTOR=1.5`、`BATTERY_LOW_RATIO=0.2`、`BATTERY_CRITICAL_RATIO=0.05`、`CHARGE_SECONDS=30`、`CHARGE_RETURN_RATIO=1.0`、`CHARGE_PILES=10`、`TURN_PENALTY=3`、`ZOOM_MIN=0.25`、`ZOOM_MAX=4.0`、`TICK_RATE=10`、布局常量（货架区 rows 60..139 × cols 40..159、入库口 rows 95..104 × cols 0..4、出库口 rows 95..104 × cols 195..199、充电区 rows 185..193 × cols 85..114），取值依据 data-model.md「布局与电量常量」与 research.md R6/R10

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有用户故事共享的引擎/地图/服务骨架

**⚠️ CRITICAL**: 本阶段完成前不得开始任何用户故事

- [x] T004 实现 `rcs_demo/sim/grid.py`：200×200 网格与固定布局（research.md R6）——边界围墙格（kind=`boundary`，不可通行）、围墙内侧 2 格宽环场主干道（FR-020）、货架区 rows 60..139 × cols 40..159 内点位行列间隔 1 格通道（点位行 60,62,..,138 共 40 行 × 点位列 40,42,..,158 共 60 列 = **2400 个 slot**，FR-021）、入库口 rows 95..104 × cols 0..4、出库口 rows 95..104 × cols 195..199、充电区 rows 185..193 × cols 85..114（10 个 `charge_pile` 一排 + `charge_in`/`charge_out` 单向通道 + `charge_queue` 排队格）；Cell 字段严格按 data-model.md：`row/col/kind/blocked/occupant`
- [x] T005 [P] 实现 `rcs_demo/sim/entities.py` 数据结构（dataclass，字段与枚举严格按 data-model.md）：`Vehicle`（id 1..100、cell、render_pos、facing、state ∈ `idle|to_pickup|to_dropoff|to_charge|charging|waiting|stalled|making_way`、task、completed_count、battery、need_charge、charge_target、path、stall_ticks）、`Tote`（id、location union、state ∈ `docked|carried|cycling`）、`Task`（id、type ∈ `inbound|outbound`、pickup/dropoff、tote_id、state ∈ `queued|assigned|in_progress|done|blocked`、assigned_vehicle）、`Obstacle`（cell、created_at_tick）、`ChargingArea`（piles、pile_owner、entry_lane/exit_lane、queue、queue_cells）
- [x] T006 实现 `rcs_demo/api/routes.py` 骨架与 `rcs_demo/__main__.py` 入口：FastAPI 应用托管 `static/` 静态目录（章程 II）；`--port/--seed/--fast/--check` 参数解析；启动单进程 asyncio 仿真引擎（10 tick/s，research.md R5）；`--check` 先输出占位自检结果
- [x] T007 [P] 编写 `tests/test_grid.py`：布局约束断言——网格 200×200、slot 恰 2400 个、入库口/出库口位置与规格一致、充电区含 10 充电位及进/出站通道与排队格、边界格不可通行、围墙内侧存在 2 格宽环场道、任何路径不得使用最外圈贴边格
- [x] T008 实现 `rcs_demo/sim/engine.py` 骨架：固定 10 tick/s 的 tick 循环、`paused` 开关（暂停时 tick 不推进）、指标快照结构（data-model.md Simulation Metrics：`tick/tasks_done_total/per_vehicle_completed/waiting_count/stalled_count/collision_count/totes_in_shelf/charging_count/charge_queue_count/avg_battery`）
- [x] T009 实现 `GET /api/config`（`rcs_demo/api/routes.py`）：返回 config.py 全量生效参数（契约 rest-api.md）

**Checkpoint**: 地图/实体/引擎骨架就绪，`python -m rcs_demo` 可启动空页面 —— 用户故事可开始

---

## Phase 3: User Story 1 - 网格地图与车队实时运行可视化 (Priority: P1) 🎯 MVP

**Goal**: 打开页面 3 秒内看到 200×200 地图与 100 台编号小车持续移动，点选查看详情，视口可缩放平移，可暂停/恢复——单独交付即可演示

**Independent Test**: 启动 `python -m rcs_demo` 并打开 `http://localhost:8000`，观察车辆持续移动、点选小车、滚轮缩放/拖拽平移、复位、暂停/恢复，全程不依赖 US2 之后的功能

### Tests for User Story 1

- [x] T010 [P] [US1] 编写 `tests/test_smoke.py`：`--check` 等价自检（包可导入、布局可生成、引擎 tick 可推进、API 端口可绑定），先于实现运行确认失败

### Implementation for User Story 1

- [x] T011 [US1] `rcs_demo/sim/engine.py`：初始化 100 台车随机分布（`--seed` 可复现，FR-002）与料箱半仓初始化——约 50% slot（~1200 箱）随机放置 Tote（state=`docked`，FR-017）
- [x] T012 [US1] `rcs_demo/sim/engine.py`：每 tick 产出增量（仅变化的 vehicles `from/to/state`、totes `at/state/carried_by`、全量 metrics），并能组装完整 snapshot（结构严格按契约 rest-api.md `GET /api/state`：grid 布局含 slots/inbound/outbound/charging_area/obstacles、vehicles、totes、charging、metrics）
- [x] T013 [US1] `rcs_demo/api/routes.py`：实现 `GET /api/state` 完整快照 + `WS /ws`（连接即发 `snapshot`，之后按 tick 推 `delta`，≤100ms 一条；处理客户端 `ping` 回 `pong`，契约 websocket.md）
- [x] T014 [P] [US1] 编写 `rcs_demo/static/index.html` + `style.css`：页面骨架——Canvas 画布 + 控制面板（暂停/恢复、视图复位）+ 指标面板 + 车辆详情面板 + 拒绝提示 toast 区域
- [x] T015 [US1] 编写 `rcs_demo/static/app.js` 三层 Canvas 渲染（research.md R5）：静态层（网格/围墙/货架/出入库口/充电区，离屏预渲染只画一次）、障碍层（变更时重绘）、动态层（料箱=半透明方块、小车=带编号圆点，格间线性插值，≥30fps，视口外实体 culling）；双层显示规则按 FR-019——穿行时车在箱下、携带时料箱跟随小车
- [x] T016 [US1] `rcs_demo/static/app.js` 相机视口（FR-030 / research.md R12）：滚轮以光标为锚缩放（范围 ZOOM_MIN~ZOOM_MAX 即 0.25×–4×）、按住左键拖拽平移 offset、位移 <5px 且时长 <300ms 判定为「点击」走选中/障碍物交互（无模式切换）、加载默认整图适配窗口、复位按钮一键回整图
- [x] T017 [US1] `rcs_demo/static/app.js` 点击选中车辆：详情面板显示编号/状态/当前任务目标/路径/电量（US1 验收场景 3），WS 发送 `{"type":"watch","vehicle_id":N}` 订阅
- [x] T018 [US1] `rcs_demo/api/routes.py` `vehicle_detail` 推送：处理 `watch` 消息，按契约 websocket.md 以 `{"type":"vehicle_detail",...}` 应答并在该车状态/路径变化时持续推送，直到 `watch:null` 取消
- [x] T019 [US1] `rcs_demo/api/routes.py` 演示控制：`POST /api/control/pause`、`POST /api/control/resume`（返回 `{"ok":true,"paused":bool}`）、`POST /api/reset`（重置为半仓 + 100 车随机分布）；写接口在 paused 下同样可用（契约一致性约束）；前端控制面板按钮接线
- [x] T020 [US1] 前端指标面板渲染（FR-013）：tasks_done、waiting、stalled、collision、charging、charge_queue、avg_battery、各车完成计数（数据源 metrics，delta 中全量携带）

**Checkpoint**: User Story 1 独立可演示 —— 打开页面即见活的车队画面

---

## Phase 4: User Story 2 - 任务随机均匀分配 (Priority: P1)

**Goal**: 按 1 任务/秒成对生成入库/出库任务，least-loaded 均匀分配，空闲车就近待命不漫游

**Independent Test**: 运行一段时间后统计各车累计分配数，任意两车之差 ≤ 平均值 ±20%，任务起终点散布全图不集中于单一象限

### Tests for User Story 2

- [x] T021 [P] [US2] 编写 `tests/test_simulation.py` 缩尺夹具（40×40 / 10 车 / 加速时钟，research.md R8）+ 均匀性断言（分配偏差 ≤±20%）与空闲车不漫游断言（连续 N tick idle 车位置不变，除非让路/充电），先失败后实现

### Implementation for User Story 2

- [x] T022 [US2] 实现 `rcs_demo/sim/dispatcher.py` 任务成对生成（FR-003/FR-018 / research.md R4）：出库任务随机选一个「有料箱的 slot」为 pickup、出库口为 dropoff；入库任务以入库口为 pickup、随机选「空 slot」为 dropoff；两类按 1:1 成对生成，节奏 TASKS_PER_SECOND（默认 1/秒，可配置）
- [x] T023 [US2] `rcs_demo/sim/dispatcher.py` least-loaded 分配（R4）：每次选累计任务数最少且可分配的车（平局随机），`to_charge`/`charging`/`stalled` 车不参与分配；保证任意两车差 ≤1（远优于 ±20%）
- [x] T024 [US2] 料箱循环复用（FR-018 / data-model.md Tote 规则）：出库送达 → `cycling`（出库口短暂停留）→ 回到入库口变为可入库状态被入库任务领取；同一批料箱永久循环，状态迁移进 delta 推送
- [x] T025 [US2] 空闲待命（FR-028 / research.md R11）：idle 车停在最后任务完成位置附近就近散开待命、禁止任何无目的漫游；唯一例外移动为被让路指令触发的 `making_way` 短距离避让与充电行程
- [x] T026 [US2] `rcs_demo/sim/dispatcher.py` blocked 任务处理：目标不可达的任务置 `blocked` 保留队列（不丢弃），障碍物变更/格子释放后自动重试（规格边界项）
- [x] T027 [US2] 完成计数贯通：`completed_count` 仅计搬运任务（不含充电行程），进 `metrics.per_vehicle_completed` 并在指标面板随 delta 刷新（US2 验收场景 1/3）

**Checkpoint**: US1 + US2 —— 画面上车辆真实执行成对任务且分配均匀

---

## Phase 5: User Story 3 - 多车防碰撞与路径重合等待 (Priority: P2)

**Goal**: 时空预约表保证任意时刻任一格最多一车，路径重合时后到车原地等待

**Independent Test**: 任务高峰期逐帧检查任意时刻无多车同格，长时间运行 collision 恒为 0

### Tests for User Story 3

- [x] T028 [P] [US3] 编写 `tests/test_planner.py`：A* 可达性与绕障、预约互斥（同格同 tick 双预约必失败）、预约等待行为、转弯代价（同起终点下直线路径代价 < 同长度多次转弯路径），先失败后实现

### Implementation for User Story 3

- [x] T029 [US3] 实现 `rcs_demo/sim/planner.py` 时空预约表（research.md R1）：`reserved[(row,col,tick) -> vehicle_id]`，规划时写入、移动前校验、互斥写入失败即等待、tick 推进时清理过期项；允许「跟随」（前车刚离开的格子下一 tick 可用）保证吞吐
- [x] T030 [US3] `rcs_demo/sim/planner.py` 标准 A*：4 邻接（禁对角穿行）、曼哈顿启发式；规划时把「当前障碍物 + 他车静态预约 + 边界」视为不可通行（research.md R2）
- [x] T031 [US3] `rcs_demo/sim/planner.py` 方向感知 A*（FR-027 / research.md R9）：搜索状态扩展为 `(row,col,dir)`，dir ∈ N/E/S/W，方向改变时 `g += 1 + TURN_PENALTY`（默认 3，可配置）——软代价偏向直线，不得导致可达目标不可达；热点代价加权（US7）与此正交叠加
- [x] T032 [US3] `rcs_demo/sim/engine.py` 逐 tick 移动执行：下一格被他车占据或预约 → 车置 `waiting` 原地等待并重新预约（FR-006），格子释放后继续；排队保持安全间隔依次跟进、不超车穿插
- [x] T033 [US3] 料箱通行规则（FR-015）：未携带料箱的车可从静置料箱所在 slot 穿行（料箱不阻挡）；携带料箱的车不得进入静置料箱所在格；车携带料箱时其所在格视为被占用
- [x] T034 [US3] 零碰撞不变量（FR-005）：引擎每 tick 断言任一格 `occupant` 至多一车，违规即引擎 bug 计入 `collision_count`；接入 `tests/test_simulation.py` 零碰撞断言（缩尺跑 N tick 碰撞=0）
- [x] T035 [US3] 重规划触发与限流（research.md R2/R9）：任务下达、前方被堵超过阈值 tick、障碍物变更影响剩余路径三种情况触发重新规划；每 tick 重规划次数限流控制 CPU（200×200 方向感知最坏 ~160k 节点）

**Checkpoint**: US1–US3 —— 高密度车流零碰撞、重合路径可见等待排队

---

## Phase 6: User Story 4 - 前端动态增删障碍物 (Priority: P2)

**Goal**: 操作者点击格子添加/删除障碍物，1 秒内影响规划并看到车流绕行/恢复

**Independent Test**: 添加障碍物挡住常用路径观察绕行，删除后观察恢复；点有车/有箱的格被拒绝

### Tests for User Story 4

- [x] T036 [P] [US4] `tests/test_grid.py` 追加障碍物校验用例：有车格拒绝、有静置料箱的 slot 拒绝、boundary/站点格/充电区格（pile/in/out/queue）拒绝、空 floor/slot 通过（data-model.md Obstacle 规则 + US4 场景 4）

### Implementation for User Story 4

- [x] T037 [US4] `rcs_demo/api/routes.py` `POST /api/obstacle` / `DELETE /api/obstacle`：按契约实现——校验失败返回 409 `{"error":"cell occupied or reserved"}`，未知障碍删除返回 404；幂等（重复添加同格 200 不重复计数）；生效后 1s 内后续规划不再使用该格（FR-009）；paused 下同样可用
- [x] T038 [US4] `rcs_demo/sim/grid.py` 障碍物集合管理：`blocked` 置位/复位即时生效、记录 `Obstacle.created_at_tick`（data-model.md）
- [x] T039 [US4] 受影响车辆重规划（FR-011）：已有路径经过新增障碍格的车立即重规划绕行或原地等待，绝不驶入障碍格；删除障碍后触发受影响车重规划恢复利用该格
- [x] T040 [US4] WS `obstacle_changed` 消息（契约 websocket.md）：`{"type":"obstacle_changed","tick":N,"added":[[r,c]],"removed":[[r,c]]}`
- [x] T041 [US4] `rcs_demo/static/app.js` 障碍物交互：原地点击空格添加/点击已有障碍物删除（与拖拽平移按 T016 阈值区分）、拒绝时 toast 明确提示（US4 场景 4）、障碍层即时重绘

**Checkpoint**: US1–US4 —— 障碍物增删即时改变车流形态

---

## Phase 7: User Story 5 - 死锁避免与自动恢复 (Priority: P3)

**Goal**: 检测疑似死锁（超时未移动）并自动按代价递增策略恢复，观察者可见车辆恢复移动

**Independent Test**: 用障碍物围堵车流，系统在有限时间（60s 内）自动恢复所有车辆移动

### Tests for User Story 5

- [x] T042 [P] [US5] `tests/test_simulation.py` 死锁恢复用例：注入围堵障碍物后 M tick 内所有车恢复移动（US5 场景 3 / SC-002 缩尺版），先失败后实现

### Implementation for User Story 5

- [ ] T043 [US5] 死锁预防（research.md R3-1）：任务目标格下达前预约终点；规划禁止把路径终点停在他车必经窄通道内；空闲车待命位置主动靠边不挡道
- [x] T044 [US5] 死锁检测（research.md R3-2）：`Vehicle.stall_ticks` 连续计数，连续 N tick（折算 ~15s，阈值可配置）位置未变且非正常等待终点 → 置 `stalled`；多车互相构成等待环 → `deadlock` 候选（SC-002：60s 内须触发恢复）
- [x] T045 [US5] 恢复阶梯（research.md R3-3，按代价递增）：a) 受阻车重规划绕行 → b) 让路——挡路空闲车 `making_way` 移向邻近空格 → c) 改派——任务回收重派其他车 → d) 兜底——短暂冻结部分预约按序相位放行；恢复后清除 `stalled`
- [x] T046 [US5] 事件通知（契约 websocket.md `event`）：`stall_detected`/`stall_recovered`/`task_blocked`/`task_unblocked` 推送；前端事件提示 + `stalled` 车辆高亮显示

**Checkpoint**: US1–US5 —— 围堵场景可自动恢复，全故事独立可用

---

## Phase 8: User Story 6 - 电量模型与自动充电 (Priority: P2，派生故事：FR-025/026/029、SC-010)

**Goal**: 每车电量随移动消耗，低于 20% 完成当前任务后自动前往集中充电区充电，充满返回；基准负载下 10 充电位无电量耗尽、平均等待 ≤30s

**Independent Test**: 长时间运行观察低电车前往充电区、排队与满电返回；断言无电量归零停摆

### Tests for User Story 6

- [x] T047 [P] [US6] `tests/test_simulation.py` 电量/充电用例（quickstart ⑤⑥）：空载 1 点/格、载货 1.5 点/格扣减正确；低于 20% 完成当前任务后进入充电流程；充电位互斥；充满经出站通道离开并回可分配集合；桩满时低电量者优先排队；全程无电量归零停摆

### Implementation for User Story 6

- [x] T048 [US6] 电量模型（FR-025 / research.md R10）：每移动 1 格扣电——空载 1 点、载货 `LOAD_FACTOR`=1.5 点（`BATTERY_FULL`=1000，可配置）；等待不耗电；`battery < BATTERY_LOW_RATIO×BATTERY_FULL`（默认 20%）→ 置 `need_charge`，**先完成当前任务**不中断搬运
- [x] T049 [US6] 实现 `rcs_demo/sim/charging.py` 充电行程（FR-026 / R10）：任务完成瞬间派发车级充电行程（`to_charge`，经 `charge_in` 进站通道至空闲充电位）——**不生成 Task、不计 completed_count、不占 1:1 任务对**；充电速率 `BATTERY_FULL / CHARGE_SECONDS`（默认 30s 从 20% 充满）；达 `CHARGE_RETURN_RATIO`（默认充满）→ 经 `charge_out` 出站通道离开 → 回可分配集合并清 `need_charge`
- [x] T050 [US6] 充电排队与兜底（FR-026 / data-model.md ChargingArea 规则）：10 桩全满时需充电车在进站通道外 `charge_queue` 排队格有序等待（电量更低者优先出队），不占主干道；电量 < `BATTERY_CRITICAL_RATIO`（默认 5%）仍未获得车位 → 完成当前移动段后直接前往充电区入口待命（演示中不出现电量归零趴窝）
- [x] T051 [US6] FR-029 核算联动：在 `rcs_demo/config.py` 实现桩数核算函数——忙碌桩数 ≈ 任务节奏 × 单任务平均耗电 ÷ 单桩充电速率，上取整 + 约 30% 余量（基准参数 = 10）；任一相关参数调整时按公式重算并与充电区配置联动（`CHARGE_PILES` 显式覆盖优先）
- [x] T052 [US6] 前端电量展示：车辆详情面板与 `GET /api/vehicle/{id}` 携带 battery/path、充电中车辆渲染充电标识、指标面板 avg_battery / charging / charge_queue（FR-013）

**Checkpoint**: US1–US6 —— 含电量循环的完整车队演示

---

## Phase 9: User Story 7 - 冲突事件记录、分析与优化 (Priority: P2，派生故事：FR-022/023/024、SC-008/009)

**Goal**: 记录互堵冲突事件 JSONL 落盘，提供分析工具输出热点统计，基于分析优化路径/任务安排使频次与时长双降 ≥30%

**Independent Test**: 跑批生成批次文件 → 分析工具输出热点与频次 → 开启优化后同负载对比指标达标（面向开发迭代，不做观看者展示界面）

### Tests for User Story 7

- [x] T053 [P] [US7] `tests/test_simulation.py` 冲突事件用例：制造互堵场景断言事件被记录（含起止 tick、涉事车辆及有无料箱、位置）、单车普通排队不记录、JSONL 逐行可解析

### Implementation for User Story 7

- [x] T054 [US7] 互堵事件检测（FR-022）：同一时段内 ≥2 台车相互等待（成团互堵，含让路解堵过程）判定为一次冲突事件——基于等待关系图连通聚类；记录起止 tick、涉事车辆编号及其有无料箱、互堵位置；单车普通排队等待不记录
- [x] T055 [US7] JSONL 落盘（FR-023）：每次启动生成一个批次文件（如 `logs/conflicts_<启动时间戳>.jsonl`），事件逐行追加（UTF-8 JSON）；不依赖内存环形缓冲
- [x] T056 [P] [US7] 分析工具 `rcs_demo/analyze_conflicts.py`（Python CLI，章程 II）：加载指定批次文件，输出统计结果——热点位置 TOP-N、互堵频次（次/千 tick）、单次时长分布；供开发迭代优化任务安排与路径安排使用
- [x] T057 [US7] 热点优化（FR-024 / research.md R9）：基于分析结果对热点格通行代价加权（叠加进 A* g 函数，与 TURN_PENALTY 正交），任务目标或路径避开高发拥堵区；不回退零碰撞与 ±20% 均匀性约束
- [ ] T058 [US7] 优化验收（SC-008/009）——对比机制（rcs_demo/bench_conflicts.py）与热点加权已实现并产出初步数据（热点加权降低互堵事件数）；≥30% 正式验收跑批待稳态调优选择点确认后执行 [US7] 优化验收（SC-008/009）：同负载基线与优化后对比跑批，断言互堵频次（次/千 tick）与单次平均时长均下降 ≥30%，且碰撞=0、分配均匀 ±20%、吞吐不明显回退；对比结果记录入 `specs/001-multi-vehicle-dispatch/`（或 README）供复核

**Checkpoint**: 全部故事完成 —— 演示 + 鲁棒性 + 电量 + 数据驱动优化闭环

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事收尾与交付验收

- [x] T059 编写 `README.md`（章程 I/V）：启动命令（`pip install -r requirements.txt` + `python -m rcs_demo`，≤3 条）、访问地址 `http://localhost:8000`、功能清单、`RCS_*` 参数默认值清单、停止方式、容器 rcs_core_dev 内验证步骤（research.md R7）
- [x] T060 完善 `rcs_demo/__main__.py --check` 自检（章程 V / quickstart）：固定布局生成（货架/站点/充电区数量正确）、A* 可达性抽检、预约表互斥自测、API 端口可绑定；输出 `OK: grid layout / planner / engine self-test passed` 退出码 0
- [x] T061 `--seed N`（固定随机种子复现场景）与 `--fast`（仿真加速）参数落实并联调（quickstart 可选参数）
- [x] T063 [P] [US1] 渲染质量修复与轨迹功能（FR-031/032，2026-09-30 澄清）：动态/障碍层每帧 identity 变换全清（消除缩放/平移残影），新增每车 30 tick 半透明渐隐轨迹尾迹 + 工具栏「轨迹」开关（默认开）
- [x] T062 [P] 运行 `pytest tests/ -q` 全绿 + 按 quickstart.md 手动演示场景 1–9 逐项验证（含 US1 验收 3s 渲染、FR-030 缩放平移流畅、暂停时编辑障碍物）+ 长跑抽检 SC-001/SC-002/SC-010（30 分钟零碰撞、无永久卡死、充电达标）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始
- **Foundational (Phase 2)**: 依赖 Phase 1 —— **阻塞所有用户故事**
- **US1 (Phase 3)**: 依赖 Phase 2；US2–US7 的前端/引擎工作在 US1 渲染骨架上迭代
- **US2 (Phase 4)**: 依赖 Phase 2（可与 US1 后半并行，建议 US1 先行提供画面）
- **US3 (Phase 5)**: 依赖 US2（有任务才有车流）；防碰撞是 US5/US6 移动语义的地基
- **US4 (Phase 6)**: 依赖 US3（重规划机制）；可与 US5 并行
- **US5 (Phase 7)**: 依赖 US3（stalled/waiting 语义）；可与 US4 并行
- **US6 (Phase 8)**: 依赖 US2（任务完成时机触发充电行程）+ US3（移动/等待机制）
- **US7 (Phase 9)**: 依赖 US3（等待/让路语义）与 US5（stall 检测复用）；优化验收（T058）依赖 US6 跑批稳定
- **Polish (Phase 10)**: 依赖所有期望交付的故事完成

### User Story Dependencies（独立可测性）

- **US1**: Foundational 后即可独立实现与演示（MVP），不依赖其他故事
- **US2**: 不依赖 US1 的展示细节（dispatcher 可用 pytest 驱动独立验证）
- **US3**: 不依赖 US4/US5/US6/US7
- **US4**: 复用 US3 重规划；删除障碍物功能可用空地图独立验证
- **US5**: 复用 US3 让路语义；可用注入围堵独立验证
- **US6**: 充电行程复用移动机制；可用缩短 BATTERY_LOW_RATIO 独立快速验证
- **US7**: 事件检测独立于前端；分析工具为纯离线 CLI，完全独立

### Within Each User Story

- 测试先写并确认失败（契约/行为锁定），再实现
- 模型/数据结构 → 服务/算法 → 接口/端点 → 前端集成
- 故事完成即到达 Checkpoint，可独立验证

### Parallel Opportunities

- Phase 1: T002、T003 与 T001 并行
- Phase 2: T005、T007 可并行（不同文件）
- US1: T010/T014 先行；T014 与后端 T011–T013 并行
- 故事间：US4 与 US5 可由两人并行；US6 的 charging.py 与 US7 的分析工具（T056）可并行
- 所有 [P] 测试任务可与同故事实现任务前置并行编写

### Parallel Example: User Story 1

```bash
# 后端与前端骨架可并行：
Task: "T011 engine.py 初始化 100 车与半仓料箱"
Task: "T014 index.html + style.css 页面骨架"
# 渲染与相机可并行推进（同文件 app.js 时串行）：
Task: "T015 三层 Canvas 渲染"
Task: "T016 相机视口缩放平移"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. 完成 Phase 1 Setup + Phase 2 Foundational
2. 完成 Phase 3 US1 → **STOP and VALIDATE**：打开页面即可演示（验收场景 1–4）
3. 如需尽早交付，此处即可对外演示

### Incremental Delivery

1. Setup + Foundational → 地图与引擎骨架
2. + US1 → 可视化演示（MVP）
3. + US2 → 真实任务流与均匀分配
4. + US3 → 零碰撞核心正确性
5. + US4 / US5（可并行）→ 动态障碍与死锁恢复亮点
6. + US6 → 电量充电完整业务闭环
7. + US7 → 数据驱动优化闭环
8. Polish → README/自检/长跑验收

### Parallel Team Strategy

1. 全员完成 Setup + Foundational
2. 开发者 A：US1 → US2（前端主线）；开发者 B：US3 → US4（调度正确性主线）；开发者 C：US6 → US7（电量与数据主线）
3. US5 由先完成主线者认领；Polish 共同验收

---

## Notes

- [P] = 不同文件、无未完成依赖，可并行
- [Story] 标签保证任务可追溯；US6/US7 为派生故事（规格 FR 已定义、未单列故事章节）
- 数据模型字段、API 契约、WS 消息格式、布局常量、电量参数均已在描述中引用确切来源（data-model.md / contracts/ / research.md），实现时以引用为准，不得自行发挥
- 每个任务或逻辑组完成后提交；每个 Checkpoint 处独立验证故事
- 章程红线：可运行优先（≤3 条命令启动）、Python 单栈（前端由 FastAPI 托管）、YAGNI（不引入数据库/消息队列/插件机制）
