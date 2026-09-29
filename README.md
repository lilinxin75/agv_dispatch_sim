# RCS 多车料箱搬运调度演示

仓储场景的多车调度 2D 仿真演示：200×200 网格地图、100 台小车、入库/出库料箱搬运、
时空预约零碰撞调度、前端动态增删障碍物。

## 启动（≤3 条命令）

```bash
pip install -r requirements.txt
python -m rcs_demo            # 默认 0.0.0.0:8000
# 浏览器打开 http://localhost:8000
```

可选参数：

| 参数 | 说明 |
|------|------|
| `--port N` | 服务端口（默认 8000，可用 `RCS_PORT` 环境变量） |
| `--seed N` | 固定随机种子，复现同一场景 |
| `--fast` | 仿真时钟 ×5 加速 |
| `--check` | 运行自检（布局/规划器/预约/引擎）后退出 |

## 停止方式

前台运行直接 `Ctrl+C`。

## 场景说明

- **地图**：200×200 格；中部货架区含 2400 个存储点位（半仓初始约 1200 箱）；
  西缘入库口、东缘出库口各 50 格。
- **任务**：入库（入库口→点位）与出库（点位→出库口）按 1:1 成对生成；
  出库料箱送达后循环回入库口，同一批料箱永久复用，演示可无限运行。
- **调度**：least-loaded 均匀分配（任意两车累计分配差 ≤1~2）；A* 4 邻接寻路；
  时空预约表保证任一时刻任一格至多一台车（零碰撞）；路径重合时后车原地等待。
- **死锁恢复**：级联让路（等待中的挡路车向邻近空格侧移）→ 重规划 → 任务改派三层兜底。
- **穿行**：小车可从静置料箱底部穿行；携带料箱时该格视为占用。
- **冲突记录与优化**：互堵冲突（≥2 台车相互等待成团）自动记录为事件，按批次落盘
  `data/conflict_events_<启动时间戳>.jsonl`；拥堵优化默认开启——热点格子热力加权寻路、
  任务点位避开高热区、拥堵背压限流、更早的级联让路、拥堵感知就近指派。

## 前端操作

| 操作 | 效果 |
|------|------|
| 点击小车 | 查看编号/状态/任务/剩余路径 |
| 点击空格 | 添加障碍物（1s 内生效，车流绕行） |
| 点击障碍物 | 删除障碍物（车流恢复） |
| 暂停 / 恢复 / 重置 | 仿真控制（暂停时仍可编辑障碍物） |
| 显示编号 | 切换小车编号显示 |

右侧面板实时显示任务完成数、各车负载分布、等待/受阻车辆数、事件日志。

## 验证

```bash
python -m rcs_demo --check     # 启动自检
pytest tests/ -q               # 缩尺快速回归（38 项）
```

测试覆盖：布局约束、A* 与预约互斥、零碰撞、任务 1:1 成对、分配均匀性（±20%）、
料箱循环复用、障碍物校验与绕行、对向会车恢复、围堵解除恢复。

## docker 容器内验证（rcs_core_dev）

```bash
docker exec -it cuda12_8_torch_2_9_1_core_dev \
  bash -c "cd /home/dev/workspace/binglee/rcs_demo && pip install -r requirements.txt && python -m rcs_demo --check"
# 容器内启动服务后宿主机浏览器直接访问 http://localhost:8000（host 网络直通）
```

## 可调参数（环境变量 `RCS_*`）

`RCS_PORT` `RCS_VEHICLE_COUNT` `RCS_TICK_RATE` `RCS_TASK_INTERVAL`
`RCS_STALL_THRESHOLD` `RCS_FILL_RATIO` `RCS_INBOUND_INITIAL_TOTES`
`RCS_MAX_QUEUED_TASKS` `RCS_RECORD_CONFLICTS` `RCS_CONFLICT_OPTIMIZATION`
`RCS_DATA_DIR`

| 开关 | 默认 | 说明 |
|------|------|------|
| `RCS_RECORD_CONFLICTS` | `1` | 互堵冲突事件记录（0 关闭，停止写 JSONL） |
| `RCS_CONFLICT_OPTIMIZATION` | `1` | 拥堵优化（0 关闭可作 A/B 基线） |
| `RCS_DATA_DIR` | `data` | 冲突事件批次文件目录 |

## 冲突事件分析（开发迭代用）

```bash
python -m rcs_demo.analyze_conflicts data/conflict_events_<批次>.jsonl
python -m rcs_demo.analyze_conflicts 'data/*.jsonl'   # 多批次合并
```

输出事件总数与总时长、热点格子 Top-N、带箱/无箱组合占比、单次事件时长与规模分布。
JSONL 每行一个事件：`batch / start_tick / end_tick / duration / vehicles[{id,carrying}] / cells`。

## 技术栈

Python 3.10+ / FastAPI + uvicorn（WebSocket 状态流）/ 原生 Canvas 2D 三层渲染，
无数据库、无前端构建链。设计文档见 `specs/001-multi-vehicle-dispatch/`。
