# Tasks: 多车网格调度演示 (Multi-Vehicle Grid Dispatch Demo)

**Input**: Design documents from `/specs/001-multi-vehicle-dispatch/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅ | quickstart.md ✅

**Tests**: 包含测试任务——宪法原则 V（可验证性）与 quickstart.md 明确要求 pytest 缩尺回归 + `--check` 自检。

**Organization**: 按用户故事分组（US1 可视化 P1 / US2 均匀分配 P1 / US3 防碰撞 P2 / US4 障碍物 P2 / US5 死锁恢复 P3）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事
- 所有路径为仓库根相对路径；布局常量见 `specs/001-multi-vehicle-dispatch/research.md` R6

## Path Conventions

单一 Python 工程（plan.md Structure Decision）：`rcs_demo/` 包 + `rcs_demo/static/` 前端 + `tests/`。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 工程骨架与依赖

- [x] T001 Create package skeleton per plan.md: `rcs_demo/`（含 `__init__.py`、`sim/`、`api/`、`static/`）与 `tests/`，创建 `requirements.txt`（fastapi、uvicorn、pytest、httpx）及 `pyproject`-free 约定说明
- [x] T002 [P] Implement configuration constants in `rcs_demo/config.py`：GRID=200×200、货架区 rows 60..139 × cols 40..159（点位行间隔 2、列间隔 1）、入库口 rows 95..104 × cols 0..4、出库口 rows 95..104 × cols 195..199、TICK_RATE=10、VEHICLE_COUNT=100、HALF_FILL=0.5、STALL_TICKS_THRESHOLD、env 覆盖（`RCS_*`）与 CLI 参数解析约定
- [x] T003 [P] Create failing smoke-test harness in `tests/test_smoke.py`：调用 `rcs_demo --check` 逻辑入口，断言退出码 0 与输出含 `OK`（先失败，T010 使其通过）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有用户故事依赖的核心骨架——网格、实体、寻路、引擎、API、页面骨架

**⚠️ CRITICAL**: 本阶段完成前不得开始任何用户故事

- [x] T004 Implement Grid with fixed layout in `rcs_demo/sim/grid.py`：Cell(kind∈{boundary,floor,slot,inbound,outbound}, blocked, occupant)、按 T002 常量生成布局（slot 坐标列表、inbound/outbound 格集）、`is_passable(cell)`（boundary/blocked 不可通行）、`occupant` 互斥赋值（二次赋值抛 `CollisionError`）
- [x] T005 [P] Implement entities in `rcs_demo/sim/entities.py`：Vehicle(id, cell, facing, state∈{idle,to_pickup,to_dropoff,waiting,stalled,making_way}, task, completed_count, path, stall_ticks)、Tote(id, location, state∈{docked,carried,cycling})、Task(id, type∈{inbound,outbound}, pickup, dropoff, tote_id, state∈{queued,assigned,in_progress,done,blocked})——字段与状态枚举严格按 data-model.md
- [x] T006 Implement plain A* pathfinding in `rcs_demo/sim/planner.py`：4 邻接、曼哈顿启发式、禁对角；`find_path(grid, start, goal)` 视 boundary 与 blocked 为不可通行；不可达返回 `None`；附带单元自测函数供 `--check` 调用
- [x] T007 Implement simulation engine core in `rcs_demo/sim/engine.py`：固定 TICK_RATE 的 `step()`（推进 tick、沿 path 逐格移动、render_pos 插值数据）、`snapshot()` 与 `delta(prev_tick)` 构造器（结构严格按 contracts/rest-api.md 与 contracts/websocket.md）、`collision_count` 断言计数器
- [x] T008 Implement FastAPI app & entry in `rcs_demo/api/routes.py` + `rcs_demo/__main__.py`：StaticFiles 挂载 `rcs_demo/static/`、`GET /api/state`、`WS /ws`（连接即发 snapshot，此后按 tick 推 delta）、`--check`（布局生成+A* 自测+端口绑定检查，打印 `OK: ...`）、`--port/--seed/--fast`
- [x] T009 Create frontend skeleton in `rcs_demo/static/index.html` + `app.js` + `style.css`：三层 Canvas（static 静态层：网格/货架/站点，一次性绘制；dynamic 动态层）、WS 连接与 snapshot 初始化、delta 处理骨架、小车圆点+编号基础渲染
- [x] T010 Wire `--check` to pass T003：`python -m rcs_demo --check` 与 `pytest tests/test_smoke.py` 绿

**Checkpoint**: `python -m rcs_demo` 可启动，浏览器出现静态地图页面，`--check` 通过

---

## Phase 3: User Story 1 - 网格地图与车队实时运行可视化 (Priority: P1) 🎯 MVP

**Goal**: 100 台车在 200×200 地图上持续移动，页面实时可视、可点选查看详情

**Independent Test**: 启动服务打开页面，3s 内看到地图+100 编号小车持续移动；点选小车显示编号/状态/目标——不依赖 US2~US5

### Tests for User Story 1

- [x] T011 [P] [US1] Write layout tests in `tests/test_grid.py`：slot 总数=2400、inbound/outbound 格数=50/50、边界全不可通行、`occupant` 二次赋值抛 CollisionError
- [x] T012 [P] [US1] Write wander/visibility test in `tests/test_simulation.py`（缩尺 40×40/10 车/`--fast` 时钟）：空转模式跑 N tick 后所有车辆位置发生过变化且 render_pos 在界内

### Implementation for User Story 1

- [x] T013 [US1] Implement vehicle wandering in `rcs_demo/sim/engine.py`：初始化 100 车随机分布在可通行格；idle 车随机选可达目标格并经 planner 求路径，走完后重新选目标（US2 将替换为真实任务）
- [x] T014 [US1] Implement `GET /api/vehicle/{id}` in `rcs_demo/api/routes.py`：返回 id/cell/state/task/path/completed（契约见 contracts/rest-api.md），未知 id 404
- [x] T015 [US1] Implement interactive visualization in `rcs_demo/static/app.js`：格间线性插值动画（≥30fps）、点选小车→面板显示编号/状态/任务目标/路径（`watch` WS 消息或轮询 GET）、≥100 动态实体下渲染流畅
- [x] T016 [US1] Add paused overlay hint & 3s-first-render check in `rcs_demo/static/app.js`：页面加载到首帧 ≤3s（本地），无 snapshot 时显示 loading 态

**Checkpoint**: US1 独立可演示（MVP!）——地图+车流+点选详情

---

## Phase 4: User Story 2 - 任务随机均匀分配 (Priority: P1)

**Goal**: 1:1 入库/出库任务成对生成，least-loaded 均匀分配，料箱循环复用

**Independent Test**: 运行一段时间，指标面板各车完成数趋同（差 ≤ 平均值 ±20%），料箱在货架/出入库口间循环

### Tests for User Story 2

- [x] T017 [P] [US2] Write fairness & circulation tests in `tests/test_simulation.py`：缩尺跑 N tick 断言 ① 任意两车 completed_count 差 ≤ 平均值 ×20% ② 任务成对生成（inbound 数==outbound 数 ±1）③ 出库送达的料箱最终回到入库口可再入库 ④ slot 占用守恒（一格至多一箱）

### Implementation for User Story 2

- [x] T018 [US2] Implement task generation in `rcs_demo/sim/dispatcher.py`：按固定节奏成对生成（1:1，FR-018）；出库随机选有料箱 slot、入库随机选空 slot（含入库口待入料箱）；终点不可达置 `blocked` 保留队列
- [x] T019 [US2] Implement least-loaded assignment in `rcs_demo/sim/dispatcher.py`：每次选 completed+assigned 总数最小的 idle 车（平局随机），数学上保证任意两车差 ≤1
- [x] T020 [US2] Implement tote lifecycle in `rcs_demo/sim/engine.py`：到达 pickup 格顶起（slot 变空、tote→carried）、送达 dropoff 释放（tote→docked）、出库送达→cycling→回到入库口可复用（FR-018）
- [x] T021 [US2] Wire task state machine into engine in `rcs_demo/sim/engine.py`：idle→to_pickup→（顶起）→to_dropoff→（送达）→idle；completed_count 与 metrics.tasks_done 更新
- [x] T022 [US2] Implement metrics & tote layer in frontend：指标面板（tasks_done/waiting/stalled/collision/per-vehicle 完成数柱状或列表）、半透明料箱方块层（含 carried 跟随小车显示）——FR-019 双层显示
- [x] T023 [US2] Verify T017 tests pass（全绿）并更新 `tests/test_simulation.py` 注释记录实测偏差值

**Checkpoint**: US1+US2 可独立演示——车流搬运料箱、任务计数均匀

---

## Phase 5: User Story 3 - 多车防碰撞与路径重合等待 (Priority: P2)

**Goal**: 时空预约表实现零碰撞；被挡车辆原地等待

**Independent Test**: 缩尺/全量运行 N tick，断言任一 tick 任一格至多一车；画面可见密集区排队等待

### Tests for User Story 3

- [x] T024 [P] [US3] Write reservation tests in `tests/test_planner.py`：同格同 tick 二次预约抛错/失败；前车离开后下一 tick 可预约（跟随语义）；blocked 格不可预约
- [x] T025 [P] [US3] Write zero-collision test in `tests/test_simulation.py`：缩尺高密度（40×40/30 车）跑 N tick，逐步检查 grid.occupant 互斥与 collision_count==0

### Implementation for User Story 3

- [x] T026 [US3] Implement space-time reservation table in `rcs_demo/sim/planner.py`：`reserved[(row,col,tick)]→vehicle_id`，规划时写入未来占用、`try_move` 前校验、tick 过期清理（research.md R1 语义，含跟随）
- [x] T027 [US3] Integrate move-confirmation & waiting into engine in `rcs_demo/sim/engine.py`：下一格被他占/被预约 → state=waiting 原地不动并重预约；格子释放后继续；waiting 计入 metrics.waiting
- [x] T028 [US3] Add waiting visualization in frontend：waiting/stalled 车辆变色（黄/红描边）、指标面板 waiting 实时显示
- [x] T029 [US3] Verify T024/T025 pass；在 `tests/test_simulation.py` 补一条"交叉路口两车必有一车等待"的确定性场景断言

**Checkpoint**: US1~US3 可独立演示——高密度车流零碰撞、可见排队

---

## Phase 6: User Story 4 - 前端动态增删障碍物 (Priority: P2)

**Goal**: 点击增删障碍物，1s 内生效，车流绕行/恢复

**Independent Test**: 页面点击空格添加障碍物→变黑、车流绕行；再点→删除恢复；点有车/有箱格→拒绝提示

### Tests for User Story 4

- [x] T030 [P] [US4] Write obstacle validation tests in `tests/test_grid.py`：有 occupant 格拒绝、有静置料箱 slot 拒绝、boundary/站点格拒绝、重复添加幂等、删除不存在障碍物报错（对照 contracts/rest-api.md 409/404 语义）
- [x] T031 [P] [US4] Write replan-on-obstacle test in `tests/test_simulation.py`：缩尺运行中在车流前方加障碍，断言受影响车辆 M tick 内 path 不再经过该格且无碰撞

### Implementation for User Story 4

- [x] T032 [US4] Implement obstacle add/remove in `rcs_demo/sim/grid.py` + engine：校验规则（T030）、生效置 blocked、触发受影响车辆（剩余路径经过该格）重规划或 waiting
- [x] T033 [US4] Implement REST & WS in `rcs_demo/api/routes.py`：`POST /api/obstacle`（409 语义）、`DELETE /api/obstacle`（404 语义）、`obstacle_changed` WS 消息（契约见 contracts/）
- [x] T034 [US4] Implement obstacle UI in `rcs_demo/static/app.js`：障碍层 Canvas（变更时重绘）、点击空格添加/点击障碍物删除、被拒时 toast 提示、1s 生效观感验证
- [x] T035 [US4] Implement blocked-task retry in `rcs_demo/sim/dispatcher.py`：终点不可达任务置 blocked 并在障碍删除/格子释放后自动重试（含 `task_blocked`/`task_unblocked` 事件）

**Checkpoint**: US1~US4 可独立演示——动态障碍物与绕行

---

## Phase 7: User Story 5 - 死锁避免与自动恢复 (Priority: P3)

**Goal**: 卡死检测 + 分层恢复（重规划/让路/改派），无永久卡死

**Independent Test**: 障碍物围堵制造拥堵，车辆短暂 stalled 后数秒内恢复移动，出现 stall_recovered 事件

### Tests for User Story 5

- [x] T036 [P] [US5] Write deadlock tests in `tests/test_simulation.py`：缩尺构造围堵→解除场景，断言 ① stall 超阈值车辆被标记 stalled ② 移除阻挡后 M tick 内全部恢复移动 ③ 互等对峙场景经让路后解开（无永久等待）

### Implementation for User Story 5

- [x] T037 [US5] Implement stall detection in `rcs_demo/sim/engine.py`：stall_ticks 计数、超 STALL_TICKS_THRESHOLD 置 stalled、发 `stall_detected` 事件（contracts/websocket.md event 消息）
- [x] T038 [US5] Implement layered recovery in `rcs_demo/sim/dispatcher.py`：a) 受阻车重规划绕行 → b) 挡路 idle 车让路（state=making_way 移向邻近空格）→ c) 任务回收改派（含兜底相位放行）；恢复后发 `stall_recovered`
- [x] T039 [US5] Add stall/recovery visualization in frontend：stalled 车辆红色高亮、事件面板（滚动日志：stall_detected/stall_recovered/task_blocked）

**Checkpoint**: 全部用户故事独立可演示

---

## Phase 8: Polish & Cross-Cutting Concerns

- [x] T040 Implement pause/resume in `rcs_demo/api/routes.py` + frontend 控制面板按钮：`POST /api/control/pause|resume`，暂停时冻结 tick 但障碍物编辑仍可用（FR-014、契约约束）
- [x] T041 [P] Add `POST /api/reset` in `rcs_demo/api/routes.py`：重置到初始半仓布局
- [x] T042 Performance pass：增量 delta 仅含变化实体（抽样验证空 tick 零载荷）、静态层/障碍层不逐帧重绘、100 车 @10tick/s 下前端 ≥30fps（DevTools 实测并记录）
- [x] T043 [P] Write README.md：启动命令（≤3 条）、访问地址与默认端口、功能清单、`--check`/pytest 验证方式、停止方式、可调参数表（宪法 I/III/V）
- [x] T044 Run quickstart.md full validation：手动过一遍 6 个演示场景 + `python -m rcs_demo --check` + `pytest tests/ -q` 全绿；在容器 rcs_core_dev 内复验（quickstart 容器小节）

---

## Phase 9: 冲突事件记录、分析与调度优化 (2026-09-29 澄清增量)

**Purpose**: 落地 FR-015 修订（带箱车禁入料箱格）、FR-022~024（互堵事件 JSONL 记录→加载分析→优化任务/路径安排）与 SC-008/009（频次/时长双降 ≥30%）

**Independent Test**: 运行生成 JSONL 批次文件 → `analyze_conflicts.py` 输出统计报告 → 开启优化后同 seed 对比频次与平均时长下降 ≥30% 且碰撞=0、均匀性 ±20% 不回退

### Tests for Phase 9

- [x] T045 [P] [US3] Write conflict-event tests in `tests/test_conflicts.py`：① 构造两车相向互堵场景（隔离任务生成），断言记录到一次互堵事件（起止 tick、涉事车辆 id 及有无料箱、位置格）② 单车普通排队不产生事件 ③ 事件在解堵后闭合（end_tick > start_tick）
- [x] T046 [P] [US3] Write FR-015 carrying-avoidance tests in `tests/test_simulation.py`：① 携带料箱车辆的 path 不含任何静置料箱所在格 ② 无载车辆的 path 可穿过静置料箱格 ③ 带箱车被料箱格阻挡时进入 waiting 而非驶入

### Implementation for Phase 9

- [x] T047 [US3] Implement mutual-blocking detector & JSONL recorder in `rcs_demo/sim/conflicts.py` + `rcs_demo/sim/engine.py`：每 tick 检测相互等待成团（≥2 台车彼此阻挡，含 making_way 参与者），事件闭合时追加一行 JSONL（字段：batch/start_tick/end_tick/vehicles[{id,carrying}]/cells）到 `data/conflict_events_<启动时间戳>.jsonl`；`data/` 目录按需创建并加入 `.gitignore`
- [x] T048 [P] [US3] Implement FR-015 carrying-avoidance in `rcs_demo/sim/planner.py` + `rcs_demo/sim/engine.py`：带箱车辆规划与移动校验时将静置料箱格视为不可进入（无载车不受限）；带箱车路径受阻时按既有 waiting/重规划语义处理
- [x] T049 [P] [US5] Implement analysis CLI in `rcs_demo/analyze_conflicts.py`：加载指定批次 JSONL，输出互堵事件总数、热点格子 Top-N、带箱/无箱组合占比、单次事件时长与规模分布；`python -m rcs_demo.analyze_conflicts <file>` 与直接执行均可运行
- [x] T050 [US5] Implement congestion-aware optimization in `rcs_demo/sim/dispatcher.py` + `rcs_demo/sim/planner.py`：① 依据冲突记录/实时等待密度对热点格子通行代价动态加权（A* 绕开拥堵）② 任务目标点位选择避开近期互堵高发区 ③ 提供配置开关（Config 字段，默认开启）以便 A/B 对比；不回退零碰撞与 ±20% 均匀分配
- [x] T051 [US5] Verify SC-008/SC-009 & update docs：同 seed 基线（优化关闭）与优化开启各跑等长仿真，断言互堵频次（次/千 tick）与单次平均时长均下降 ≥30%、collision=0、分配差 ≤±20%；在 `README.md` 参数表补充冲突记录/优化开关与分析工具用法

**Checkpoint**: 冲突可记录、可分析、可量化优化（SC-008/009 达标）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (1)** → **Foundational (2)**：T004~T010 依次就绪（T004/T005、T011/T012 可先行并行）
- **Foundational 完成前禁止任何 US 任务**
- **US1 (3)** 依赖 Phase 2；**US2 (4)** 依赖 US1 的 wander 骨架被任务替换；**US3 (5)** 依赖引擎移动语义（US1）；**US4 (6)** 依赖 US3 的重规划路径；**US5 (7)** 依赖 US3/US4
- **Polish (8)** 依赖全部 US 完成（T042/T043/T044）
- **Phase 9（冲突记录与优化增量）** 依赖 Polish 后的稳定基线：T045/T046 测试先行 → T047/T048 并行实现 → T049 分析工具（可并行）→ T050 优化 → T051 A/B 验收

### User Story 依赖图

```text
Phase1 → Phase2 ──→ US1 ──→ US2 ──→ US3 ──→ US4 ──→ US5 ──→ Polish
                    (MVP)    └───────────────┘(US2/US3 内部测试可并行写)
```

### Parallel Opportunities

- Phase 1：T002、T003 与 T001 并行
- Phase 2：T005 与 T004 并行；T011/T012 测试先行
- 各 US 的测试任务（标 [P]）均可在该 US 实现开始前并行编写
- US2 与 US3 的测试文件互不相同，可并行推进

---

## Implementation Strategy

- **MVP First**：Phase 1→2→3 完成即可演示（车流可视化）；STOP 验证 US1
- **Incremental**：每 US 完成即独立验证（Checkpoint），先测试后实现
- **缩尺原则**：所有集成断言用 40×40/加速时钟跑，全量 200×200 留给 T044 手动验证
