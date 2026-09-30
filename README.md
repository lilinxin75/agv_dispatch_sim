# RCS 多车料箱搬运调度演示 (Multi-Vehicle Grid Dispatch Demo)

仓储料箱搬运调度演示：200×200 网格地图、100 台同质小车在离散时间步进仿真中
执行入库/出库料箱搬运任务（1:1 成对生成、循环复用），A* 路径规划（转弯软代价，
偏向直线）+ 时空预约保证零碰撞，死锁自动恢复，电量模型与集中充电区，
前端 Canvas 双层可视化，支持动态增删障碍物与视口缩放平移。

## 启动（3 条命令内）

开发、测试、运行统一在 docker 容器 **rcs_core_dev** 内进行（宿主机仅浏览器访问，host 网络直通）。

```bash
pip install -r requirements.txt
python -m rcs_demo            # 默认 0.0.0.0:8000，--port 改端口
# 浏览器打开 http://localhost:8000
```

可选参数：`--port N`（端口）、`--seed N`（固定随机种子复现场景）、`--fast`（仿真 10 倍加速）、
`--layout NAME`（布局预设：`baseline` / `wide-oneway` / `charge-expanded`，重启生效；
`--check` 不带 `--layout` 时校验全部预设）。

## 停止

终端 `Ctrl+C`。

## 启动自检

```bash
python -m rcs_demo --check
```

校验固定布局生成（货架区 2400 点位/站点/充电区）、A* 可达性抽检、预约表互斥、
API 端口可绑定，输出 `OK: grid layout / planner / engine self-test passed`。

## 功能清单

- **可视化运行画面**：200×200 网格、100 台编号小车实时移动（10 tick/s 逻辑 +
  前端插值渲染），料箱半透明方块叠于点位、小车带编号圆点，车在箱下穿行 / 料箱跟随携带车显示
- **任务调度**：入库/出库任务 1:1 成对生成（1 任务/秒），least-loaded 均匀分配
  （任意两车差 ≤ 平均值 ±20%），料箱出库后循环回入库口无限演示
- **零碰撞**：时空预约 + 逐 tick 移动仲裁，路径重合自动等待排队，任意时刻任一格至多一车
- **动态障碍物**：页面点击空地添加 / 点击障碍物删除，1 秒内影响规划并触发绕行重规划；
  被车/料箱占据或站点/充电区格拒绝（toast 提示）
- **死锁避免与恢复**：15s 未移动检测 → 重规划 → 让路 → 改派三级恢复；事件面板实时显示
- **电量与充电**：满电 1000 点，能耗 = 载重倍率 × 距离 + 转向附加；低于 20% 完成当前任务后
  自动前往南侧集中充电区（充电位一排、西进东出**单向进出且桩格不可穿越**（矢量性），
  桩满低电量优先排队，排队深度超限自动**错峰延迟**非紧急低电车），30 秒充满返回；
  无电量耗尽停摆
- **任务治理**（002）：任务台账恒等式（生成 = 完成 + 在途 + 待派 + 改派）、停滞超时
  自动改派、工作站容量上限、拥塞背压（等待率超阈值自动放慢任务生成节奏，缓解后恢复）
- **视口缩放平移**：滚轮以光标为锚缩放 0.25×–4×，拖拽平移（与点击智能区分，无需切换模式），
  一键复位整图
- **移动轨迹尾迹**：轨迹随**时间蒸发**（默认约 3 秒，`RCS_TRAIL_TTL_TICKS` 可调）——
  行走刷新、停顿渐消，而非固定长度拖尾（工具栏「轨迹」开关）；位置跳变自动断开不连线
- **显示效果**（002）：五态着色（执行任务=蓝/等待=橙/充电=绿/空闲=灰/受阻=红）、
  车载电量条（<20% 橙、<10% 红、充电 ⚡）、拥塞热力图图层、车辆目标点直连线图层
  （按钮显隐，默认关）、点击车辆详情卡片（含最近状态变化）、吞吐/平均周期趋势图
- **指标面板**：完成任务数、等待/受阻车辆数、碰撞计数（恒 0）、充电中/排队数、
  平均电量、各车完成数分布、吞吐趋势图
- **冲突事件记录分析（面向开发迭代）**：≥2 台车成团互堵判定为一次事件，
  JSONL 落盘 `logs/conflicts_<启动时间戳>.jsonl`；分析工具输出热点位置、
  频次（次/千 tick）、时长分布：
  ```bash
  python -m rcs_demo.analyze_conflicts logs/conflicts_XXXXXX.jsonl
  ```

## 可调参数（RCS_* 环境变量覆盖，全量见 `GET /api/config`）

| 参数 | 默认 | 说明 |
|------|------|------|
| `RCS_TASKS_PER_SECOND` | 1 | 任务生成节奏（核算基准） |
| `RCS_BATTERY_FULL` | 1000 | 满电点数（可续航格数） |
| `RCS_LOAD_FACTOR` | 1.5 | 载货每格耗电倍率（空载 1 点/格） |
| `RCS_BATTERY_LOW_RATIO` | 0.2 | 低电阈值（触发充电标记） |
| `RCS_BATTERY_CRITICAL_RATIO` | 0.05 | 临界阈值（兜底直奔充电区） |
| `RCS_CHARGE_SECONDS` | 20 | 从 20% 充到满电秒数（002 期自 30 调整：恢复充电容量余量，见 research.md D11） |
| `RCS_CHARGE_PILES` | 10 | 充电位数量（FR-029 公式核算默认值） |
| `RCS_CHARGE_POWER_BUDGET_RATIO` | 1.0 | 充电区供电总功率预算比例（<1 时功率受限） |
| `RCS_LOADED_SPEED_RATIO` | 0.7 | 载货速度倍率（空载 1.0） |
| `RCS_TURN_SPEED_RATIO` | 0.5 | 转向步速度倍率 |
| `RCS_TURN_ENERGY` | 0.5 | 转向步额外耗电（点） |
| `RCS_REASSIGN_TICKS` | 300 | 任务无进度改派阈值（tick） |
| `RCS_STATION_CAPACITY` | 6 | 工作站同时在场车辆上限 |
| `RCS_CHARGE_QUEUE_SOFT_LIMIT` | 6 | 充电排队深度软上限（超过触发错峰延迟） |
| `RCS_CHARGE_TRIP_CAP` | 8 | 在途充电行程上限（waiting-room 限流，防充电区入口走廊堵死，research.md D14） |
| `RCS_CHARGE_CLAIM_STALL_TICKS` | 300 | 充电位认领滞留释放阈值（旅行者滞留超时释放认领回队列，D14） |
| `RCS_GOVERN_BACKPRESSURE` / `RCS_GOVERN_REASSIGN` / `RCS_GOVERN_CHARGE_DEFER` | 1 | 拥塞治理组合开关（背压/改派/错峰，可独立关闭对比） |
| `RCS_TRAIL_TTL_TICKS` | 30 | 轨迹消失时长（tick，≈3 秒） |
| `RCS_TURN_PENALTY` | 3 | 每次转向的路径代价惩罚（软约束） |
| `RCS_ZOOM_MIN` / `RCS_ZOOM_MAX` | 0.25 / 4.0 | 前端视口缩放范围 |
| `RCS_TICK_RATE` | 10 | 仿真 tick 频率（/秒） |
| `RCS_STALL_TICKS` | 150 | 卡死检测阈值（tick，~15s） |

充电位数量按 FR-029 公式联动：忙碌桩数 ≈ 任务节奏 × 单任务平均耗电 ÷ 单桩充电速率，
上取整 + 约 30% 余量；基准参数核算结果为 10（`GET /api/config` 的
`recommended_charge_piles` 显示当前核算值）。

## REST / WebSocket 接口

见 [specs/001-multi-vehicle-dispatch/contracts/](specs/001-multi-vehicle-dispatch/contracts/)：
`GET /api/state`、`GET /api/vehicle/{id}`、`GET /api/config`、
`POST/DELETE /api/obstacle`、`POST /api/control/pause|resume`、`POST /api/reset`、
`WS /ws`（snapshot + delta + 事件流）。

## 自动化测试

```bash
pytest tests/ -q
```

缩尺快速回归（40×40 / 10 车 / 与全量共享引擎代码）：布局约束、A* 可达/绕障/转弯代价、
预约互斥、零碰撞、分配均匀性、空闲不漫游、电量扣减与充电流程、充电位互斥、
死锁恢复、冲突事件 JSONL，002 期新增：充电桩矢量性、布局三预设、任务台账恒等式、
停滞改派、拥塞背压、硬件速度/能耗模型、指标快照、目标连线/状态历史契约
（tests/test_constraints.py）。

## 容器内验证（docker rcs_core_dev）

```bash
docker exec -it rcs_core_dev \
  bash -c "cd /home/dev/workspace/binglee/rcs_demo && pip install -r requirements.txt && python -m rcs_demo --check"
# 容器内启动服务后，宿主机浏览器访问 http://localhost:8000（host 网络直通）
```

## 布局预设与效率验收（002）

- **布局预设**（`rcs_demo/layouts/*.json`，`--layout` 切换、重启生效）：
  - `baseline`：001 现状（2 格宽环场、双向通道、10 桩）
  - `wide-oneway`：环场主干道加宽至 3 格 + 货架南北缘局部单向段（有平行替代通路，无锁死陷阱）
  - `charge-expanded`：充电桩 16 + 充电区东扩（缓解充电排队拥塞）
- **效率双口径**（quickstart ③ / SC-001）：连续运行 60 分钟，后 30 分钟平均吞吐
  ≥ 前 10 分钟峰值的 80%（主），等待/受阻占比峰值 <30%（辅）；互堵频次
  较 001 基线下降 ≥30%（SC-003，`analyze_conflicts` 口径）
- **缩尺回归口径**：FR-004 ±20% 均匀性在规格全尺寸场景（100 车/10 桩）验收；
  40×40 缩尺因充电占空比与窄道拥塞被放大、且背压会主动降速生成，
  测试仅断言烟测上界 ±80%（tests/test_simulation.py 注释）

## 目录结构

```text
rcs_demo/
├── __main__.py            # 入口（--check/--layout/--port/--seed/--fast）
├── config.py              # 全量可调参数（RCS_* env 覆盖 + 矛盾组合校验）
├── layouts/               # 布局预设 JSON（baseline / wide-oneway / charge-expanded）
├── analyze_conflicts.py   # 冲突事件批次分析 CLI
├── sim/
│   ├── grid.py            # 网格与布局（LayoutProfile 驱动 + 充电桩矢量性有向边）
│   ├── entities.py        # Vehicle/Tote/Task(生命周期)/Obstacle/ChargingArea
│   ├── planner.py         # 方向感知 A* + 预约表（矢量性同源校验）
│   ├── dispatcher.py      # 1:1 任务生成 + least-loaded + 台账/改派/背压
│   ├── charging.py        # 充电位/排队管理
│   └── engine.py          # tick 循环/进度累加移动/死锁恢复/冲突事件/指标快照
├── api/routes.py          # REST + WebSocket + 静态托管
└── static/                # 前端（原生 HTML/CSS/JS，三层 Canvas）
```
