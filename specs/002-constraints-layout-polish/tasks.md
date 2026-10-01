---
description: "Task list for 002-constraints-layout-polish implementation"
---

# Tasks: 调度约束完善与布局/效率/显示优化

**Input**: Design documents from `/specs/002-constraints-layout-polish/`

**Prerequisites**: plan.md (required), spec.md (required for user stories),
research.md (D1–D15 决策，D15/D10 为 2026-10-01 修订), data-model.md (实体扩展),
contracts/api-delta.md (增量契约), quickstart.md (验证场景)

**Tests**: SC-007 明确要求新增约束（台账恒等式/改派/背压/硬件速度模型/矢量性/布局预设）
均有测试断言 → 每个 US 先写失败测试再实现（TDD）。

**Organization**: 按 US 分组；US1/US2 同为 P1（US1 先行，dispatcher 与 engine 有少量
交汇点按序执行）；US3/US4 依赖 US1/US2 的台账与硬件模型可信后验收。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 所属用户故事（US1–US5）
- 路径以仓库根为基准；实现口径引用 research.md D 编号

## Path Conventions

```text
rcs_demo/           # Python 包（config / sim / api / static / layouts）
tests/              # pytest（现有 test_planner.py / test_grid_layout.py / test_simulation.py + 新增）
```

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 配置扩展与布局预设文件——所有 US 的参数/配置基础

- [X] T001 创建 `rcs_demo/layouts/baseline.json`：数值逐一等价 config.py 现布局常量
      （SHELF_ROW_RANGE (60,139)/SHELF_COL_RANGE (40,159)/INBOUND (95,104)×(0,4)/
      OUTBOUND (95,104)×(195,199)/CHARGE (185,193)×(85,114) piles 10 entry_cols
      [86,88] exit_cols [112,114] ring_road_width 2），含 name/grid/standby_zones/
      oneway_segments 字段（结构见 data-model.md LayoutProfile）
- [X] T002 [P] 扩展 `rcs_demo/config.py`：新增参数（research.md D1/D2/D4/D5/D7）——
      LOADED_SPEED_RATIO 0.7 / TURN_SPEED_RATIO 0.5 / TURN_ENERGY 0.5 /
      REASSIGN_TICKS 300 / STATION_CAPACITY 6 / CHARGE_QUEUE_SOFT_LIMIT
      (0.6×CHARGE_PILES) / GOVERN_BACKPRESSURE / GOVERN_REASSIGN /
      GOVERN_CHARGE_DEFER（默认全 1）/ TRAIL_TTL_TICKS 30 / METRICS_WINDOW_TICKS 600 /
      LAYOUT（RCS_LAYOUT，默认 "baseline"），全部 `_env` 支持 RCS_* 覆盖并加入
      `all_params()`（键名遵循 contracts/api-delta.md）
- [X] T003 [P] `rcs_demo/config.py` 新增 `validate()`：矛盾组合校验（speed 比率 ∈(0,1]、
      CHARGE_SECONDS>0、REASSIGN_TICKS>STALL_TICKS、CHARGE_PILES≥1、布局名存在于
      layouts/），违规返回可读中文错误列表（FR-010/quickstart ⑤）

**Checkpoint**: `python3 -c "from rcs_demo import config; config.validate()"` 通过

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: LayoutProfile 建图 + 充电区有向边矢量性——US1–US4 全部依赖

- [X] T004 `rcs_demo/sim/grid.py` 改造为 LayoutProfile 驱动建图（research.md D8）：
      新增 `load_profile(name)` 读取 `rcs_demo/layouts/<name>.json`；Grid 构造函数接受
      profile 参数，站区/充电区/待命区几何全部来自 profile；config.py 布局常量降级为
      baseline 缺省值引用（不留两份真源）；默认 LAYOUT="baseline" 行为与 001 完全一致
- [X] T005 `rcs_demo/sim/grid.py` 实现有向边覆盖层（research.md D3 / FR-008）：新增
      `directed: dict[Cell, frozenset[方向]]`——仅充电区内部格（entry_lane/桩前通道/
      exit_lane/桩格）注册方向白名单（基线西进东出）；桩格对"非最终目标"路径一律不可
      进入（不可穿越）；暴露 `can_traverse(cell, direction, is_target) -> bool`
- [X] T006 `rcs_demo/sim/planner.py` + `rcs_demo/sim/engine.py` 同源接入有向边：A* 扩展
      与 `_move_vehicles` 仲裁均经 `can_traverse` 校验（单一真源，防止"规划能过、执行
      被拒"）；路径代价对桩前通道保持原 TURN_PENALTY 语义
- [X] T007 `rcs_demo/__main__.py` 新增 `--layout NAME` 参数（默认 baseline）；`--check`
      扩展：调用 config.validate()、加载当前布局、全图可达性校验（站点+充电区 +
      A* 抽检）、矢量性约束校验（任一桩格存在非目标可达路径即失败）、输出沿用
      `OK: grid layout / planner / engine self-test passed` 格式
- [X] T008 在 `tests/test_constraints.py` 新建并在实现前置为失败：矢量性测试——
      ① A* 无法把桩格作为中转格规划路径；② 从出口侧逆行进入充电区的路径不存在；
      ③ 以桩格为目标时可经 entry_lane 到达；④ baseline 建图下 001 既有
      tests/test_grid_layout.py 全绿（回归护栏）

**Checkpoint**: `--check` 通过；矢量性测试转绿；既有 25 用例全绿

---

## Phase 3: User Story 1 - 业务逻辑约束完善 (Priority: P1) 🎯 MVP

**Goal**: 任务台账恒等式、停滞超时改派、工作站容量上限、拥塞背压——调度正确性地基

**Independent Test**: 固定种子跑 3000 tick，台账恒等式任意采样成立；注入围堵观察
REASSIGN_TICKS 内改派；监控 spawn 间隔随等待率自适应

### Tests for User Story 1 ⚠️（先写，确认 FAIL）

- [X] T009 [P] [US1] `tests/test_constraints.py` 新增：① 台账恒等式——运行 3000 tick
      每 300 tick 采样断言 `spawned == done + active + reassigned`（SC-002 缩尺口径）；
      ② 改派测试——执行车围堵超过 REASSIGN_TICKS 后任务改派给最少负载车、
      tote.claimed_by 原子转移、reassign_count≥1；③ 同一料箱任意时刻至多一个认领者

### Implementation for User Story 1

- [X] T010 [US1] `rcs_demo/sim/entities.py` + `rcs_demo/sim/dispatcher.py` 实现任务
      生命周期（data-model.md Task）：state ∈ PENDING/ACTIVE/DONE/REASSIGNED、
      created_tick/done_tick/stall_ticks/reassign_count；dispatcher 持 ledger
      （spawned/active/done/reassigned 计数 + tasks 字典）
- [X] T011 [US1] `rcs_demo/sim/dispatcher.py` 实现停滞改派（FR-002 / D4）：任务
      stall_ticks ≥ REASSIGN_TICKS → 释放 tote.claimed_by、assigned_vehicle 置空、
      任务回 PENDING 重新按 least-loaded 派发、原车触发 FR-028 疏散流程；
      GOVERN_REASSIGN=0 时跳过
- [X] T012 [US1] `rcs_demo/sim/dispatcher.py` 实现工作站容量（FR-004 / D6）：站点
      占用度 = 在站车辆 + 指向该站的 way_targets；≥ STATION_CAPACITY 时新任务暂缓
      该站（留 PENDING，不改指站点——1:1 配对约束下目的地由料箱来源决定）
- [X] T013 [US1] `rcs_demo/sim/dispatcher.py` + `rcs_demo/sim/engine.py` 实现背压
      （FR-005 / D5）：spawn_interval = base × multiplier；在途 ≥ MAX_ACTIVE_TASKS 或
      等待率 ≥ 0.4 时 multiplier ×2（上限 ×8），连续 600 tick 缓解后每次 ×0.5 回落；
      GOVERN_BACKPRESSURE=0 时恒为 1；改派/限流动作发 `governance_event`
      （action: reassigned/backpressure，contracts/api-delta.md 事件格式）
- [X] T014 [US1] `rcs_demo/api/routes.py`：`GET /api/state` 增 `ledger` 字段
      （contracts/api-delta.md）；`GET /api/vehicle/{id}` 增 reassign_count
- [X] T015 [US1] 运行 T009 测试转绿 + 既有 25 用例无回归；`--check` 仍通过

**Checkpoint**: US1 独立可验收（spec 验收场景 1–4 对应测试/接口就绪）

---

## Phase 4: User Story 2 - 硬件逻辑约束完善 (Priority: P1)

**Goal**: 进度累加速度模型（空载/载货/转向差异）、能耗×载重×转弯、充电功率/错峰、
参数集中可查

**Independent Test**: 修改 LOADED_SPEED_RATIO / CHARGE_SECONDS 重启后指标按新参数
生效；断言载货 progress 增速 = 空载 × 比率、电量恒非负

### Tests for User Story 2 ⚠️（先写，确认 FAIL）

- [X] T016 [P] [US2] `tests/test_constraints.py` 新增：① 速度模型——空载车每 tick
      progress += 1.0、载货车 ×LOADED_SPEED_RATIO、转向步 ×TURN_SPEED_RATIO，
      progress≥1 才移动一格；② 能耗——移动扣减 = 1.0×(LOAD_FACTOR if carrying)，
      转向步额外 +TURN_ENERGY，电量 max(0,·) 恒非负；③ 100 车全尺寸分位下零碰撞
      不变量保持（速度差异不得破坏预约仲裁）

### Implementation for User Story 2

- [X] T017 [US2] `rcs_demo/sim/engine.py` `_move_vehicles` 改造为进度累加移动
      （research.md D1/D2）：Vehicle 增 `progress: float`（entities.py）；每 tick
      `progress += speed`（speed 按载重/转向步确定），≥1 才走一格并清零；转向判定用
      path 下一格方向 ≠ facing；电量扣减并入同一处（转向步加 TURN_ENERGY）；
      等待/预约逻辑结构不变（仍一 tick 至多一格，公平仲裁排序保留）
- [X] T018 [US2] `rcs_demo/sim/charging.py` 实现供电容量与错峰（FR-008/009 / D7）：
      ① 在充桩数 ≤ CHARGE_PILES 且总功率 = 在充数 × charge_rate_per_tick() ≤ 预算；
      ② 排队深度（queue+在充）≥ CHARGE_QUEUE_SOFT_LIMIT 时 battery>critical 的车
      不入队、置 defer_charge=True 保持待命；≤critical 紧急车始终入队；defer 车
      每 150 tick 重估，被派任务即清标记
- [X] T019 [US2] `rcs_demo/sim/engine.py`：defer 触发/解除发 `governance_event`
      （action: charge_deferred）；defer 车可正常参与派发（召回优先于充电，FR-009）
- [X] T020 [US2] `rcs_demo/api/routes.py`：`GET /api/config` 下发全部新参数
      （contracts/api-delta.md 字段），`GET /api/vehicle/{id}` 增 progress/defer_charge
- [X] T021 [US2] 运行 T016 测试转绿 + 既有 25 用例无回归（特别是
      test_battery_drain_and_charge_flow / test_no_battery_death /
      test_charge_pile_exclusivity）

**Checkpoint**: US1+US2 均独立可验收（MVP 达成）

---

## Phase 5: User Story 3 - 布局优化 (Priority: P2)

**Goal**: 三套预设布局可切换、可对比，矢量性/可达性/待命容量自检全覆盖

**Independent Test**: 三套预设分别 `--check` 通过并以缩尺引擎跑 600 tick 零碰撞；
baseline 与 wide-oneway 同种子 10 分钟指标差异可量化

### Tests for User Story 3 ⚠️（先写，确认 FAIL）

- [X] T022 [P] [US3] `tests/test_constraints.py` 新增：① 三套预设均可被 Grid 加载且
      `--check` 校验通过（可达性+矢量性+待命容量）；② 缩尺引擎分别用三套预设跑
      600 tick 零碰撞且任务推进；③ oneway_segments 单向段内逆行路径不存在

### Implementation for User Story 3

- [X] T023 [P] [US3] 创建 `rcs_demo/layouts/wide-oneway.json`：环场主干道
      ring_road_width 3、充电区前横向通道 + 站前通道设 oneway_segments（局部单向段，
      research.md D8——吸取 RCS_ONE_WAY_AISLES 全图锁死教训，仅短段）
- [X] T024 [P] [US3] 创建 `rcs_demo/layouts/charge-expanded.json`：piles 16、充电区
      南移留蓄车区（queue 扩容）、显式 standby_zones 划定
- [X] T025 [US3] `rcs_demo/sim/grid.py` + `rcs_demo/sim/planner.py` 支持
      oneway_segments（段内方向白名单并入 directed 覆盖层）与 ring_road_width/
      standby_zones 参数化（FR-011/012）
- [X] T026 [US3] `rcs_demo/__main__.py` `--check` 支持逐预设全量校验模式（遍历
      layouts/*.json 全部校验，FR-014），布局名错误时输出可读错误退出非 0
- [X] T027 [US3] 运行 T022 测试转绿；README 记录三预设对比运行命令（quickstart ②）

**Checkpoint**: US1–US3 独立可验收；布局切换仅重启生效（无热切换）

---

## Phase 6: User Story 4 - 运行效率优化 (Priority: P2)

**Goal**: 指标快照/滑动窗口可观测、治理开关组合闭环、双口径验收数据源就绪

**Independent Test**: 运行 6000 tick 后 `GET /api/state` metrics_history 有连续采样、
数值口径正确；三个治理开关独立关闭时行为符合预期

### Tests for User Story 4 ⚠️（先写，确认 FAIL）

- [X] T028 [P] [US4] `tests/test_constraints.py` 新增：① 每 10 tick 产生
      MetricsSnapshot、环形缓冲 ≤600 点；② throughput_window/avg_cycle_ticks/
      waiting_ratio/charge_queue_depth/conflict_per_ktick 数值口径（手工小场景核对）；
      ③ 治理开关关闭路径：GOVERN_* = 0 时对应动作不发生且 ledger/指标仍自洽

### Implementation for User Story 4

- [X] T029 [US4] `rcs_demo/sim/engine.py` 实现 MetricsSnapshot 采样（research.md D9 /
      FR-016）：每 10 tick 采样 5 指标入 600 点环形缓冲；吞吐窗口
      = METRICS_WINDOW_TICKS 内完成数；avg_cycle_ticks 用 Task.done_tick-created_tick
- [X] T030 [US4] `rcs_demo/sim/engine.py` 治理开关贯通（FR-015）：三个 GOVERN_* 开关
      独立生效（接线 T011/T013/T019 的实现）；CongestionEvent/治理事件统一带 action
      字段（reassigned/backpressure/charge_deferred/rerouted），事件面板可见
- [X] T031 [US4] `rcs_demo/api/routes.py`：`GET /api/state` 增 `metrics_history`
      （最近 ≤360 点，contracts/api-delta.md）
- [X] T032 [US4] 运行 T028 测试转绿；quickstart ③ 双口径验收流程在容器内试跑
      （缩短口径冒烟：10 分钟窗口趋势可用即视为就绪，60 分钟正式验收放 Polish）

**Checkpoint**: US1–US4 独立可验收；SC-001/SC-003 验收数据源就绪

---

## Phase 7: User Story 5 - 显示效果完善 (Priority: P3)

**Goal**: 五态着色/电量条/热力图/目标连线/详情卡片/趋势图/轨迹时间蒸发全部落地前端

**Independent Test**: quickstart ④ 的 8 项人工核验清单逐项通过；100 车无操作 ≥30fps

### Tests for User Story 5 ⚠️（先写，确认 FAIL）

- [X] T033 [P] [US5] `tests/test_constraints.py` 新增契约测试：delta 车辆对象含
      `goal` 字段（有目标时）且值与车辆当前任务终点/桩位/待命位一致；无目标时字段
      省略；`/api/vehicle/{id}` 含 state_history

### Implementation for User Story 5

- [X] T034 [P] [US5] `rcs_demo/api/routes.py`：delta 车辆序列化增 `goal`
      （当前最终目标：任务终点/桩位/待命位，数据模型见 data-model.md Vehicle）；
      `GET /api/vehicle/{id}` 增 state_history（最近 8 条 state+tick）
- [X] T035 [US5] `rcs_demo/static/app.js` + `index.html`：五态着色（执行=蓝
      #3b82f6/等待=橙 #f59e0b/充电=绿 #10b981/空闲=灰 #9ca3af/受阻=红 #ef4444，
      research.md D10）+ 车体下缘电量条（<20% 橙、<10% 红、充电 ⚡ 角标）（FR-018/019）
- [X] T036 [P] [US5] `rcs_demo/static/app.js` + `index.html`：拥塞热力图图层——
      客户端 `heat: Map<cell, value>`、车辆所在格 +1/全图 ×0.995 衰减、红黄半透明
      网格、按钮开关默认关（FR-020）
- [X] T037 [US5] `rcs_demo/static/app.js` + `index.html`：目标点直连线图层——按
      `goal` 字段画车→目标虚线（拍板 Q1-A 直线形态），按钮控制显隐默认隐藏，
      目标变化跟随（FR-021）
- [X] T038 [P] [US5] `rcs_demo/static/app.js` + `style.css`：点击车辆详情卡片——
      视口坐标命中最近车（< 半格），显示编号/状态/电量/当前任务/最近状态变化时间
      （FR-022）
- [X] T039 [US5] `rcs_demo/static/app.js` + `index.html`：指标面板吞吐趋势图
      （260×60 mini-canvas 折线，metrics_history 驱动，可切平均周期）（FR-023）
- [X] T040 [US5] `rcs_demo/static/app.js`：轨迹时间蒸发（FR-025 / D10）——移除
      TRAIL_LEN=30 计数上限，TRAIL_TTL_TICKS（默认 30，读 /api/config）为唯一下界；
      保留跳变断线与 4 桶 Path2D 分批描边；透明度按 1 - age/TTL 连续渐隐（行进刷新
      与停顿蒸发同源）
- [X] T041 [US5] T033 契约测试转绿；quickstart ④ 八项人工核验自查通过；
      确认 001 期交互（缩放平移/障碍/事件面板/暂停复位/轨迹开关）不回退（FR-024）

**Checkpoint**: 全部 US 完成，功能齐备

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T042 更新 `README.md`：新参数表（速度/能耗/改派/背压/错峰/TRAIL_TTL/--
      layout）、三布局预设说明、显示功能清单、quickstart ②③④ 入口引用
- [X] T043 容器 rcs_core_dev 内全量验证：`python3 -m pytest tests/ -q` 全绿 +
      `--layout <三预设逐个> --check` 全过 + 启动服务浏览器冒烟
      （quickstart ①②⑤）
- [X] T044 [P] 60 分钟效率正式验收（SC-001/SC-003/SC-006）：`--seed 42` 连续运行，
      记录主口径（后 30 分钟吞吐 ≥ 前 10 分钟峰值 80%）、辅口径（等待率峰值 <30%）、
      互堵频次较 001 基线下降 ≥30%，结果记入本任务勾选备注
      **结果（2026-09-30，D14 两轮修复后全程跑通，FR-027 监控零异常中止）**：
      36000 tick / wall 838s（~43× 实时）；零碰撞 ✓；辅口径等待率峰值 26% <30% ✓；
      SC-006 零电量停摆 ✓（全程无 >1800 tick 滞零）；台账恒等式全程成立 ✓；
      done 2128。**SC-001 主口径 0.778（34.23/44）差 2.2pp 未达 0.8**——后半程
      吞吐 33-38/窗 vs 峰值 44，残余为货架区 1 格宽巷道结构性拥塞（与 SC-003
      同源，需布局级工作或口径级决策）；CHARGE_TRIP_CAP 10（更差）/6（中止）
      已验证 8 为甜点；wide-oneway 同口径 0.735 无改善。SC-003 互堵 33.2 次/千
      tick，较 001 基线的对比口径仍待定（见 research.md D12）
- [X] T045 代码清理：删除 ONE_WAY_AISLES 全图开关（被 oneway_segments 取代）及
      config.py 中已无引用的死常量；确认 config.py 布局常量仅作为 baseline 缺省值
      单一来源（SC-004 无隐藏硬编码）

---

## Phase 9: 2026-10-01 修订增量 — FR-008 倒车入位 / FR-018 子态颜色 / FR-020 热力图双模式

**Purpose**: 落地 spec Session 2026-10-01 三条拍板（research.md D15 + D10 修订）。
T001–T045 已完成的历史任务不动；本 Phase 增量执行。

**Independent Test**: quickstart ④ 人工核验第 1/3/9 项逐项通过；pytest 矢量性
用例（倒车入位口径）全绿；8000 tick 快验证 FR-027 零异常

### Implementation

- [X] T046 [P] [US5] `rcs_demo/static/app.js` `stateColor()`：`to_charge` 改用
      冷色青 `#06b6d4`，`charging` 独占绿色 `#10b981`（FR-018 拍板：两子态颜色
      可区分）；⚡ 角标逻辑不变（仅 charging 显示）；详情卡片/事件面板文案不变
- [X] T047 [P] [US5] `rcs_demo/static/app.js` + `index.html`：热力图双模式
      （FR-020 拍板）——新增 `heatMode`（"realtime" | "cumulative"，默认
      realtime）：实时模式保持既有 ×0.995 帧衰减、<0.05 删除（约 10 秒淡出）；
      累计模式跳过衰减、只增不减，着色按当前热度最大值归一（供布局对比分析）；
      工栏热力图按钮旁新增模式切换按钮；复位/重启清零两模式共用（`heat.clear()`
      既有路径）；图层关闭仍立即隐藏且零渲染成本
- [X] T048 [US2] `tests/test_constraints.py` 矢量性用例改为倒车入位口径（先改，
      确认对 T049 实现为 FAIL）：① 桩格对非目标路径不可穿越（不变）；② 以桩格为
      目标仅可从桩位上方经北侧连接格向南进入；③ 离开桩格仅能向北经连接格倒出；
      ④ 连接格 E/W 方向不可通行；⑤ baseline + wide-oneway + charge-expanded
      三预设全部通过
- [X] T049 [US2] `rcs_demo/sim/grid.py` `_build_charging` / `_build_directed`
      实现倒车入位（research.md D15 / FR-008 拍板）：① 连接格由 `pile_row+1`
      移至 `pile_row-1`（桩位上方），注册方向白名单 {N,S}；② 桩格进入仅允许
      自北向南（经连接格），离开仅允许自南向北（原路倒出）；③ 桩格对非目标
      路径仍视为墙（不可穿越不变）；④ 西侧进站段（entry_cols 范围）降级为
      CHARGE_QUEUE 排队格，不再注册进桩方向；⑤ 东出段（exit_cols 范围）降级为
      普通通道，不再注册出桩方向；⑥ `can_traverse` 语义同步更新
- [X] T050 [US2] `rcs_demo/__main__.py` `--check` 矢量性校验改为倒车入位口径：
      北口进出成立 + 桩格不可穿越 + 连接格 N/S 双向 + 全图可达性含"上方进桩
      通道可达每个桩位"；`rcs_demo/layouts/*.json` 不改字段名（entry_cols/
      exit_cols 语义按 data-model.md 修订注记降级，保持兼容）
- [X] T051 [US2] 回归核查：确认 `engine.py` `_arrive_charge`（cell==pile 判定）、
      `_charge_queue_scheduling` 与 D13/D14 治理逻辑（认领释放/TRIP_CAP/排队
      预算）在倒车入位下无需改动且行为正确；T048 测试转绿 + 既有全部用例
      无回归；容器内 8000 tick 快验证冒烟
      （`python3 -m rcs_demo.acceptance --ticks 8000`）FR-027 零异常中止
- [X] T052 [US3] quickstart ④ 人工核验第 1 项（前往充电=青/充电中=绿）、第 3 项
      （热力图实时/累计双模式）、第 9 项（低电车从桩位上方直入、充满原路向北
      倒出）逐项通过；在本 Phase 末尾记录验证结果备注

**Checkpoint**: 2026-10-01 三拍板全部落地；全量测试绿；快验证零异常

**执行记录（2026-10-01）**：
- T048 先行确认 6/6 FAIL → T049/T050 实现后 56/56 全绿（含修正 planner.py
  内联进桩规则同源拷贝、旧口径用例 test_dock_path_exists_for_every_pile /
  test_preset_reachable_and_vector_valid / 障碍校验用例、no_bypass 豁免双向通道行）
- T051 快验证两轮解剖：①首轮中止——包围盒北侧 fence（charge_rows[0]-2 起）
  挡死上方进桩通道 → fence 下移至 charge_rows[0]，主货流直连进桩通道；
  ②二轮中止（seed 42）——10 桩被让路/滞留的 WAITING 状态车挂名（D14 释放
  治理仅覆盖 TO_CHARGE）→ 新增泄漏治理②：非在途/在充挂名超 60 tick 释放
  （WRONG_STATE_CLAIM_GRACE_TICKS，在桩等功率额度豁免；reset 同步清计时器）。
  治理后 8400 tick 全程跑通：零碰撞、FR-027 零异常、done 548、滞零 3–8 台
  波动、sc001_ratio 0.732
- 同 seed 42 对照实验（旧西进东出几何）：done 519 / sc001 0.857 / wall 194s
  vs 新几何 done 548 / 0.732 / 48s——整体吞吐与尾部吞吐（41 vs 37.7/窗）均优，
  比率偏低仅因直入使前期峰值抬高（56 vs 44）摊薄分母，非回退
- T052：第 9 项（倒车入位行为）由测试+快验证自动化证明 ✓；第 1/3 项代码与
  静态资源服务确认（/app.js 含 #06b6d4、/index.html 含 btn-heat-mode）✓，
  浏览器视觉核验待人工确认

---

## Dependencies & Execution Order

### Phase 9 追加依赖（2026-10-01）

- 依赖 Phase 1–8 全部完成（T001–T045 已勾选）
- T046 ∥ T047（同为 app.js/index.html 前端小改，无相互依赖；建议串行合并避免
  同文件冲突）
- T048（测试先行）→ T049（grid.py 实现）→ T050（--check 口径）→ T051（回归
  核查 + 快验证）→ T052（人工核验），严格顺序——T049/T050/T051 同源改动链

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即可做（T001 → T002/T003 可并行）
- **Foundational (Phase 2)**: 依赖 Setup；T004→T005→T006→T007→T008 严格顺序（同文件
  链），T008 测试先于 T005/T006 实现失败确认
- **US1 (Phase 3)**: 依赖 Phase 2；T009 测试先行 → T010→T011→T012→T013→T014→T015
  （dispatcher.py 链式修改，顺序执行）
- **US2 (Phase 4)**: 依赖 Phase 2（矢量性/布局就绪）；与 US1 无文件级强依赖，可在
  US1 完成后开始（engine.py _move_vehicles 与 T013 事件有交汇，顺序执行更稳）；
  T016 先行 → T017→T018→T019→T020→T021
- **US3 (Phase 5)**: 依赖 Phase 2 + US2（速度模型改变预设调参基线）；T023/T024 与
  T022 并行
- **US4 (Phase 6)**: 依赖 US1（ledger/cycle 数据）+ US2（能耗口径）；T029→T030→T031
- **US5 (Phase 7)**: 依赖 US4（metrics_history/goal 数据源）；T034 先行（API 契约），
  前端任务 T035–T040 可两两并行（同文件不同图层，注意 app.js 顺序合并）
- **Polish (Phase 8)**: 依赖全部 US；T044 需 60 分钟墙钟时间，最后执行

### Parallel Opportunities

- Setup：T002 ∥ T003（同文件 config.py，建议一次编辑完成）
- US3：T023 ∥ T024 ∥ T022（不同 JSON/测试文件）
- US5：T034（routes.py）∥ T033（测试）；app.js 各图层任务建议逐个串行合并避免冲突
- T044 验收运行可与 T042/T045 文档清理并行（不阻塞）

## Implementation Strategy

### MVP First（US1+US2）

1. Phase 1+2 完成 → 布局参数化 + 矢量性地基
2. US1（台账/改派/背压）→ 测试绿 → **STOP 验证**
3. US2（速度/能耗/错峰）→ 测试绿 → 缩尺 + 全尺寸冒烟，MVP 可演示

### Incremental Delivery

MVP → US3 布局对比 → US4 指标闭环 → US5 显示增强 → Polish（60 分钟验收）

## Notes

- 所有运行/测试在容器 rcs_core_dev 内（python3，非 python）
- 速度模型改造是本特性最大风险点：保持"一 tick 至多一格 + 预约仲裁"结构不变，
  只在移动闸门前加 progress 累加（research.md D1）
- 每完成一个 Phase 在容器内跑一次既有 25 用例防回归
