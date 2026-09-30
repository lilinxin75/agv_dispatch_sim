# Research: 调度约束完善与布局/效率/显示优化

**Feature**: 002-constraints-layout-polish | **Date**: 2026-09-30
**基线代码**: rcs_demo 001 期交付（engine.py 1026 行 / grid.py 157 行 / dispatcher.py 153 行 / app.js 491 行）

## D1 速度模型：进度累加器（progress accumulator）

**Decision**: 车辆持有 `progress: float`，每 tick `progress += speed`；`progress ≥ 1` 才允许
移动一格并扣减。`speed` 由状态唯一决定（FR-006）：
`speed = 1.0`（空载直行）/ `× RCS_LOADED_SPEED_RATIO`（默认 0.7，载货）/
`× RCS_TURN_SPEED_RATIO`（默认 0.5，当格转向时，即 `v.facing` 将变化的那一步）。

**Rationale**: 001 期 `_move_vehicles` 是"一 tick 一格"硬编码（engine.py:289）；累加器把
速度差异做成逐车逐 tick 的通过闸门，不改预约/仲裁结构，零碰撞不变量天然保持（仍一 tick
至多一格）。转弯判定复用 planner 已知的 path 转折点。

**Alternatives rejected**:
- 全局 tick 分频（载货车隔 tick 走）：粒度太粗（50% 档），且与公平仲裁排序交互差；
- 真实动力学（加速度/减速度曲线）：规格 Assumptions 明确排除，YAGNI。

## D2 能耗模型：基础 × 载重 × 转弯附加

**Decision**: 每移动一格 `Δbattery = 1.0 × (LOAD_FACTOR if carrying else 1.0) × cell_factor`，
转向格额外 `+ TURN_ENERGY`（默认 0.5 点）。`cell_factor` 预留 1.0（布局可给拥堵区加成，
本期不启用）。`max(0, ...)` 保持非负（FR-007）。

**Rationale**: 在 001 期"空载 1/载货 1.5"上叠加转弯项即可支撑"能耗随路况变化"的演示
口径；充电干预兜底沿用 BATTERY_LOW/CRITICAL 双阈值（已实现且测试覆盖）。

## D3 充电桩矢量性：有向边覆盖层（directed-edge overlay）

**Decision**: Grid 增加 `directed: dict[Cell, frozenset[方向]]` 覆盖层——仅充电区内部格
（进口道/桩前通道/出口道）注册方向白名单；桩格本身：非"最终目标"时对所有路径视为墙
（不可穿越，FR-008），作为目标时只允许从进口侧方向进入。A* 的 `can_enter` 与引擎
仲裁同源校验（单一真源，防止"规划能过、走的时候被拒"）。

**Rationale**: 001 期 grid.py 已有 entry_lane（86-88 列西进）/exit_lane（112-114 列东出）
几何，矢量性只是把"事实上单向"升级为"结构上强制"；有向边覆盖层只在充电区局部生效，
不影响主货流通道的 A* 性能（200×200 全图 planner 无回归）。

**Alternatives rejected**:
- 全图车道纪律（所有通道单向化）：001 期实测 RCS_ONE_WAY_AISLES=1 于 ~1800 tick 全图
  锁死（config.py 注释在案），拒绝；
- 桩格当普通墙 + 修出口绕行：会让"到达充电位"不可规划，语义错误。

## D4 任务台账与停滞改派（FR-001/002）

**Decision**: Dispatcher 持任务台账 `ledger`：`spawned/active/done/reassigned` 计数 +
`tasks: dict[id, Task]`（状态 PENDING/ACTIVE/DONE/REASSIGNED）。恒等式
`spawned == done + active + reassigned` 作为引擎不变量（tick 内自检 + 测试断言）。
改派：`Task.stall_ticks`（其执行车 `stall_ticks` 同步）≥ `REASSIGN_TICKS`（默认 300，
可配）→ 释放 tote 认领、任务回 PENDING 并记 `reassign_count += 1`、原车走 FR-028
疏散流程；料箱认领唯一性由 `tote.claimed_by` 单字段保证。

## D5 拥塞背压（FR-005）

**Decision**: 生成节奏调节器：`spawn_interval = base_interval × multiplier`，
`multiplier` 随两级信号自适应——在途 ≥ MAX_ACTIVE_TASKS（既有）或等待率 ≥ 0.4 时
×2（上限 ×8），连续 600 tick 拥塞缓解后每次 ×0.5 回落至 1.0。

**Rationale**: 现有 MAX_ACTIVE_TASKS 是硬截断（队列无限堆积语义）；乘法调节把"队列
延迟"转为"生成放缓"，避免任务台账出现大量 PENDING 积压污染指标。独立开关
`RCS_GOVERN_BACKPRESSURE=0` 可关（FR-015 对比口径）。

## D6 工作站容量与改指（FR-004）

**Decision**: 工作站占用度 = 停在站内格车辆数 + 指向该站的活跃 way_targets 数；
`STATION_CAPACITY`（默认 6）满时新任务暂缓该站（dispatch 跳过，任务留 PENDING），
既有 FR-028 疏散逻辑负责清场。

## D7 错峰充电（FR-009）

**Decision**: 充电排队深度（queue 长度 + 占用桩数）≥ `CHARGE_QUEUE_SOFT_LIMIT`
（默认 0.6 × CHARGE_PILES）时：`battery > critical` 的非紧急车不入队，标记
`defer_charge=True` 保持待命（可被任务召回，召回即清标记）；`≤ critical` 紧急车
始终入队优先。defer 车每 150 tick 重估一次。

## D8 布局参数化与预设（FR-011~014）

**Decision**: `LayoutProfile` JSON（`rcs_demo/layouts/*.json`）：`grid`、
`shelf{row_range,col_range}`、`inbound/outbound rect`、
`charging{row_range,col_range,piles,entry_cols,exit_cols}`、`standby_zones[]`、
`oneway_aisles[]`（可选单向通道段列表，逐段白名单而非全图开关）。grid.py 改为
profile 驱动建图；`--layout {baseline,wide-oneway,charge-expanded}` 选择；
缺省 baseline（数值等价现 config 常量，保证 001 行为回归）。三套预设：
1. **baseline**：001 现状原样；
2. **wide-oneway**：环场主干道加宽至 3、充电区前横向通道 + 站前通道单向化（局部）；
3. **charge-expanded**：充电桩 10→16、充电区南移留出排队蓄车区、standby 区显式划定。

**Rationale**: 布局常量目前散在 config.py（SHELF_ROW_RANGE 等），profile 化即满足
"无硬编码坐标"；局部单向段列表吸取 RCS_ONE_WAY_AISLES 全图化的锁死教训。
`--check` 扩展：全图可达性 + 矢量性约束校验 + 待命区容量 ≥ 空闲峰值估算。

## D9 指标快照与滑动窗口（FR-016/023）

**Decision**: 引擎每 10 tick 采样 `MetricsSnapshot`（吞吐/10tick、平均任务周期、
等待率、充电排队深度、互堵频次），存 600 点环形缓冲（= 1 小时 @10tick/s）。
`GET /api/state` 增 `ledger` 与 `metrics_history`（步长压缩下发：前端取最近 360 点）。
趋势图与 SC-001 双口径验收共用同一数据源。

## D10 前端显示（FR-018~025）

**Decision**（全部原生 Canvas/CSS，无新依赖）：
- **五态着色**：执行=蓝 `#3b82f6`、等待=橙 `#f59e0b`、充电=绿 `#10b981`、
  空闲=灰 `#9ca3af`、受阻=红 `#ef4444`（含 hover/低缩放下的形状徽标备选）；
- **电量条**：车体下缘 2px 条，宽度=battery%，<20% 橙、<10% 红；充电中显示 ⚡ 角标；
- **热力图**：客户端维护 `heat: Map<cell, value>`，车辆每帧对所在格 +1、全图 ×0.995
  衰减；渲染为半透明红-黄网格覆盖层，按钮开关（默认关）；
- **目标连线**：delta 车辆对象增 `goal: [r,c]`（当前最终目标：任务终点/桩位/待命位），
  虚线直连绘制，按钮开关（默认关，FR-021 拍板 Q1-A 直线形态）；
- **详情卡片**：点击命中最近车辆（视口坐标距离 < 半格），弹出绝对定位卡片；
- **趋势图**：指标面板内 260×60 mini-canvas 折线（吞吐），点击可切换为平均周期曲线；
- **轨迹时间蒸发（FR-025）**：移除 001 期 TRAIL_LEN=30 计数上限，保留 tick 时间戳与
  跳变断线；`TRAIL_TTL`（默认 30 tick≈3s，可配）为唯一下界——行进刷新与停顿蒸发
  同源：新点入列、过期点移除，透明度按 `1 - age/TTL` 连续渐隐（4 桶分批描边结构保留）。

**Rationale**: 全部增量字段随既有 delta 通道下发，无新连接；热力/连线均为独立图层，
关闭时零渲染成本，保障 SC-005 的 30fps 底线。

## Rejected Alternatives（宪法 IV 论证存档）

| 备选 | 拒绝理由 |
|------|----------|
| WHCA* 窗口式时空规划 | 实现复杂度与回归风险远超演示需要；D4–D7 组合治理先行，若 60 分钟稳态仍不达标再立项 |
| 全图单向通道 | 001 期实测 ~1800 tick 全图锁死（config.py 注释），仅允许布局预设内局部单向段 |
| 前端框架/构建链 | 宪法 II/III 明令禁止；原生 Canvas 增量即可覆盖全部 FR-018~025 |
| 电池寿命衰减模型（循环数→容量折损） | 长时演示中不可观察（数百循环才可见），YAGNI |
| 布局热切换 | 规格已拍板重启生效，避免运行态迁移复杂度 |
| 收紧恢复时序（STALL_TICKS 150→60 / REASSIGN_TICKS 300→150）以压互堵频次 | 快验证实测 8000 tick 跑不完 400s 预算（≥6× 变慢）：恢复阶梯提前触发引发重规划/让路风暴，CPU 成本失控；维持默认时序 |
| WIP 上限收紧（MAX_ACTIVE_TASKS 80→60） | 快验证实测在途任务从未触及 60，限流不生效、指标零变化——瓶颈在空间密度而非任务排队 |

## D11 充电容量核算修正 + 初始电量错相（快验证发现，2026-09-30）

**Decision**:
1. `recommended_charge_piles()` 改用真实跨度速率（`charge_rate_per_tick`：只充
   20%→满的 800 点跨度 = 26.7 点/秒@30s），修正原实现按 `电池容量/充满时长`
   （33.3 点/秒）高估 25% 的核算 bug；
2. `CHARGE_SECONDS` 默认 30→20（001 期拍板明确"可配置"）：单桩 40 点/秒，
   重算 9 桩 ≤ 现有 10 桩，余量 ~37%；
3. 初始电量 50%–100% 错相分布（`Engine` 初始化）：全队满电出发时 drain 速率
   相近，在低电阈值处形成**同步充电波**（tick ~2200 起数十台同时排队，队尾车
   在等待中耗尽电量）；错相后充电需求在时间上摊开；
4. 错峰 defer 兜底：电量 ≤ 2×临界值的车不再 defer（继续排队等待的代价高于
   拥塞贡献，拖到归零即违反 SC-006）。

**Rationale**: 快验证（8000 tick / 65s）实测：修正前充电队列深度虚增至千级、
充电位利用率峰值仅 7/10、车队电量塌方式归零且不可恢复；修正后任一时刻滞零
仅 3–7 台、全部在 900 tick 内恢复（最长连续滞零 328 tick），SC-006 达成。

## D12 互堵判定口径修正（SC-003 可比性，2026-09-30）

**Decision**: 互堵团的等待边仅计入"阻挡车本身未推进"（≥ `CONFLICT_BLOCK_TICKS`=4
tick 无位移）的边。002 硬件模型下载货车 0.35–0.7 格/tick，跟车等待属正常交通
行为；001 期 1 格/tick 下该情形不存在（跟车等待 <3 tick 被 MIN_TICKS 过滤），
不过滤则同口径频次虚高一个量级（563→211 事件/8000 tick）。

**Rationale**: SC-003 要求与 001 基线同口径对比；语义修正后残余互堵集中于货架区
1 格宽巷道对头相遇（63%）与充电行程混流（42%），属结构性密度问题——见验收报告
对 SC-003 的诚实结论与后续选项。

## D13 充电行程规划风暴治理（快验证 profile 实测，2026-09-30）

**Decision**: `_start_charge_trip` ①就近试至多 2 个空闲桩、全部规划失败→**回退
排队分支**（此前失败即返回不排队）；②充电尝试独立 15-tick 冷却
（`_charge_try_tick`，与 5-tick 规划冷却解耦）；③排队/桩规划预算降至 3k 节点。
另修复排队入队无路径导致的车队条目泄漏（`enqueue` 去重 + 入队前必须规划成功）。

**Rationale**: 快验证 profile：1500 tick 内充电规划 4.6 万次调用、烧 1 亿 A* 节点
（占全引擎 80%+）；治理后同窗口 42s→5.5×提速，8000 tick 全程 65s（~120× 实时）。

## 环境约束（沿用）

开发、测试、运行全部在 docker 容器 **rcs_core_dev** 内（Python 3.10.12，`python3`），
宿主机仅浏览器访问 http://localhost:8000。
