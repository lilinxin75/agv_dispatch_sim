# RCS 多车料箱搬运调度演示 (Multi-Vehicle Grid Dispatch Demo)

仓储料箱搬运调度演示：200×200 网格地图、100 台同质小车在离散时间步进仿真中
执行入库/出库料箱搬运任务（1:1 成对生成、循环复用），A* 路径规划（转弯软代价，
偏向直线）+ 时空预约保证零碰撞，死锁自动恢复，电量模型与集中充电区，
前端 Canvas 双层可视化，支持动态增删障碍物与视口缩放平移。

## 启动（3 条命令内）

```bash
pip install -r requirements.txt
python -m rcs_demo            # 默认 0.0.0.0:8000，--port 改端口
# 浏览器打开 http://localhost:8000
```

可选参数：`--port N`（端口）、`--seed N`（固定随机种子复现场景）、`--fast`（仿真 10 倍加速）。

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
- **电量与充电**：满电 1000 点，空载 1 点/格、载货 1.5 点/格；低于 20% 完成当前任务后
  自动前往南侧集中充电区（10 充电位、西进东出单向通道、桩满低电量优先排队），
  30 秒充满返回；无电量耗尽停摆
- **视口缩放平移**：滚轮以光标为锚缩放 0.25×–4×，拖拽平移（与点击智能区分，无需切换模式），
  一键复位整图
- **指标面板**：完成任务数、等待/受阻车辆数、碰撞计数（恒 0）、充电中/排队数、
  平均电量、各车完成数分布
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
| `RCS_CHARGE_SECONDS` | 30 | 从 20% 充到满电秒数 |
| `RCS_CHARGE_PILES` | 10 | 充电位数量（FR-029 公式核算默认值） |
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
死锁恢复、冲突事件 JSONL。

## 容器内验证（docker rcs_core_dev）

```bash
docker exec -it cuda12_8_torch_2_9_1_core_dev \
  bash -c "cd /home/dev/workspace/binglee/rcs_demo && pip install -r requirements.txt && python -m rcs_demo --check"
# 容器内启动服务后，宿主机浏览器访问 http://localhost:8000（host 网络直通）
```

## 已知限制与调优选择点（长期运行稳态）

- **基准负载长时稳态**：前 10–20 分钟运行流畅（任务持续推进、零碰撞、自恢复可见）；
  持续满负载下拥塞逐步累积，部分车辆进入等待/受阻状态并占用在途额度，
  任务生成快于消化的时段吞吐会回落。可选调优（均可用环境变量/开关验证）：
  - `RCS_MAX_ACTIVE_TASKS`（默认 80）：在途任务上限，调低可减轻拥塞
  - `RCS_ONE_WAY_AISLES=1`：货架横向通道单向化（实验性；实测长单向通道易锁死，默认关）
  - `RCS_STALL_TICKS`：卡死检测阈值（默认 150 tick≈15s）
  - 深层方案（WHCA\* 窗口式时空规划 / 全图车道纪律）为后续迭代选择点
- **缩尺回归口径**：FR-004 ±20% 均匀性在规格全尺寸场景（100 车/10 桩）验收；
  40×40 缩尺因充电占空比与窄道拥塞被放大，测试仅断言烟测上界（tests/test_simulation.py 注释）

## 目录结构

```text
rcs_demo/
├── __main__.py            # 入口（--check/--port/--seed/--fast）
├── config.py              # 全量可调参数（RCS_* env 覆盖）
├── analyze_conflicts.py   # 冲突事件批次分析 CLI
├── sim/
│   ├── grid.py            # 网格与固定布局（货架/站点/充电区/环场道）
│   ├── entities.py        # Vehicle/Tote/Task/Obstacle/ChargingArea
│   ├── planner.py         # 方向感知 A* + 预约表
│   ├── dispatcher.py      # 1:1 任务生成 + least-loaded 分配
│   ├── charging.py        # 充电位/排队管理
│   └── engine.py          # tick 循环/移动仲裁/死锁恢复/冲突事件/指标
├── api/routes.py          # REST + WebSocket + 静态托管
└── static/                # 前端（原生 HTML/CSS/JS，三层 Canvas）
```
