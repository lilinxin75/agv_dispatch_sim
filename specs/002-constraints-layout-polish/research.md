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

> **2026-10-01 修订**：进出方式由"西进东出穿入式"改为**倒车入位**（拍板），几何与
> 有向边注册以 **D15** 为准；本节的有向边覆盖层机制（A* 与仲裁同源校验、仅在充电区
> 局部注册）仍然成立。

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
  **2026-10-01 修订（FR-018 拍板）**：充电大类内两个子态颜色区分——`charging`
  （充电中）独占绿色 `#10b981`，`to_charge`（前往充电）改用冷色青 `#06b6d4`。
  起因：用户观察到"车没进桩也在充电/充满后进去一下就出来"——诊断为既有实现
  两子态同色 + 车体插值追赶逻辑位置造成的视觉错觉（逻辑状态机无缺陷，充电全程
  在桩上），拍板仅做颜色区分、插值渲染保持现状；
- **电量条**：车体下缘 2px 条，宽度=battery%，<20% 橙、<10% 红；充电中显示 ⚡ 角标；
- **热力图**：客户端维护 `heat: Map<cell, value>`，车辆每帧对所在格 +1；
  **2026-10-01 修订（FR-020 拍板）**：双模式——①实时模式（默认）：全图 ×0.995
  帧衰减、低于 0.05 删除（约 10 秒冷却，保持演示观感）；②累计模式：跳过衰减、
  只增不减，饱和后按当前最大值归一着色，供布局对比与热点分析；工具栏按钮切换
  模式，累计热度仅复位/重启清零；衰减时长保持前端常量（`HEAT_DECAY` 折算约
  10s，YAGNI 不进 /api/config）；渲染为半透明红-黄网格覆盖层，按钮开关（默认关）；
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

## D14 充电队列三重僵死修复（tick-20400 解剖定位，2026-09-30）

**Decision**: `_charge_queue_scheduling` 重写，修复 FR-027 第二轮监控解剖
（36000-tick 验收 tick 20400 中止：10 桩全空、24 台滞零、吞吐塌到 5/窗）定位的
三个叠加缺陷：
① **min-battery 首选出队 + 前车物理阻挡 → 整线僵死**：滞零车排在队首高电量
   车之后（单排排队格仅东向可出），首选规划必败进 10-tick 冷却、每 tick 尝试
   预算全烧在必败规划上。修复为按电量升序**逐候选试规划**，失败者冷却跳过、
   不阻塞后续候选；
② **`free_pile()` 单点阻塞**：返回第一座未认领桩，被充满滞留车物理占据时
   全队列停摆（其余空桩无人服务）。修复为在**物理空闲**的未认领桩中就近选位；
③ **排队格分配无互斥**：`assign_queue_cell` 只查物理占用，不查他车已认准的
   目标格——实测 8 台车目标同指 (189,85) 在入口串成一线互堵。修复为 `taken`
   集合并入全部他车 `charge_target` 所指排队格。
另：队列服务与空闲直发改**独立预算**（各 2 次/tick，此前队列失败烧光 3 次
共享预算令直发饿死）；排队→桩规划上限 20k→6k 节点（行程仅 ~10-30 步，
失败止损 3 倍速，profile 实测队列规划曾占 tick 成本过半）。

**Rationale**: 低电量压力场景（初始电量压至 30–60%）复现：修复前滞零 14–18 台、
队列深度 18、充电区西入口走廊成片互堵；修复后滞零 3–9 台、队列深度 ≤4（= 排
队格数）。8000-tick 快验 done 442→493，sc001_ratio 0.828 首次达标（≥0.8）。

**后续（tick-9600 二次中止解剖）**: 首轮 36000 验收仍于 tick 9600 中止（车辆 54
滞零 >1800 tick）——10 桩中 5 桩被滞留在途旅行者**认领泄漏**挂名、充电区西入口
走廊被物理堵死致桩不可达。追加两项治理：①**滞留认领释放**（TO_CHARGE 认领桩
超 300 tick 无位移 → 释放回队列，`RCS_CHARGE_CLAIM_STALL_TICKS`）；②**在途行程
上限**（TO_CHARGE 旅行者 ≥8 时不再新发桩行程改排队等待，`RCS_CHARGE_TRIP_CAP`；
waiting-room 限流）。修复后 36000 tick 全程跑通（FR-027 零异常中止，done 2128，
零碰撞，waiting 峰值 26%）。CAP 参数验证：10 → 更差且更慢（拥塞回潮）、6 →
触发电量滞零中止，8 为甜点。

## D15 充电位倒车入位（FR-008 修订，拍板 2026-10-01）

**Decision**: 充电位改为**朝上开放、倒车入位**——
- 几何：连接格由桩位**下方**（`pile_row+1`）移到桩位**上方**（`pile_row-1`），
  注册为 N/S 双向（有向边覆盖层：`{(186,c): {N,S}}`）；
- 进入：车辆从上方通道（主货流侧，行 `pile_row-2`）直接向南经连接格驶入桩位，
  **不再绕行西侧进站通道**——消除"东边来的车必须绕到最西端入口再横穿整个通道"
  的绕圈；
- 离开：充满后**原路向北倒出**（桩→连接格→上方通道），东出通道不再承担出桩专用，
  降级为普通通道；西侧进站通道降级为排队等待格（queue_cells 保留，D13/D14 的
  排队治理逻辑不变）；
- 矢量性不变量：桩格仍不可穿越（非最终目标视为墙）；进出仅能经由该北侧连接格；
  连接格只允许 N/S（杜绝横向借道）。

**Rationale**: 用户拍板（spec Session 2026-10-01）；`_build_directed`（grid.py:171）
只改覆盖层注册表与连接格位置，A* `can_enter` 与引擎仲裁同源校验结构不动；
`_arrive_charge` 判定（cell==pile）与排队/功率预算/认领治理（D11/D13/D14）全部
不受影响——改动集中在 `_build_charging`/`_build_directed` 与布局 JSON 的
`entry_cols`/`exit_cols` 语义（降级为排队格范围）。

**Alternatives rejected**:
- 进站通道改东西双向：与 D3 记录的"全图/长段单向化锁死"教训反向操作，对头互堵
  风险回潮，且改动波及整条通道仲裁——blast radius 大于收益；
- 东端增设第二入口：保留穿入式复杂度（排队/方向注册翻倍），绕行只是缩短未消除。

**对验收的影响**: `--check` 矢量性校验改为"倒车入位成立"（北口进出、桩格不可
穿越、连接格 N/S 双向）；`tests/test_constraints.py` 矢量性用例同步改口径；
quickstart ④ 人工核验增加"充电车颜色区分"与"热力图双模式"两项。

## 环境约束（沿用）

开发、测试、运行全部在 docker 容器 **rcs_core_dev** 内（Python 3.10.12，`python3`），
宿主机仅浏览器访问 http://localhost:8000。
