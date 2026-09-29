# Implementation Plan: 多车网格调度演示 (Multi-Vehicle Grid Dispatch Demo)

**Branch**: `001-multi-vehicle-dispatch` | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-multi-vehicle-dispatch/spec.md`

## Summary

构建一个仓储料箱搬运调度演示：200×200 网格地图（中部货架区 + 边缘入库/出库口），
100 台同质小车在离散时间步进仿真中执行入库/出库料箱搬运任务（1:1 成对生成、循环复用），
采用 A* 路径规划 + 时空预约表实现零碰撞与路径重合等待，配合死锁检测与自动恢复；
前端为 Python 服务直接托管的 Canvas 双层显示页面（半透明料箱层 + 编号小车层），
支持动态增删障碍物、暂停/恢复与运行指标展示。开发与验证在 docker 容器 rcs_core_dev 内进行。

## Technical Context

**Language/Version**: Python 3.10+（容器内已有解释器）

**Primary Dependencies**: FastAPI + uvicorn（Web 服务与 WebSocket）、原生 HTML/CSS/Canvas JS（前端，无构建链）

**Storage**: 内存（仿真状态全内存；可选 JSON 快照导出，不引入数据库）

**Testing**: pytest（单元 + 冒烟）+ `python -m rcs_demo --check` 启动自检

**Target Platform**: docker 容器 rcs_core_dev（Linux，host 网络），桌面浏览器访问

**Project Type**: web-service（Python 单栈：API + 托管静态前端）

**Performance Goals**: 仿真逻辑 10 tick/s（每 tick 100 车决策 + 路径规划增量计算）；
前端渲染 ≥30 fps；状态下发延迟 ≤100ms；障碍物变更 1s 内生效

**Constraints**: 零碰撞（任一时刻任一格最多一车）；任务分配偏差 ≤±20%；
卡死检测阈值 60s 内自动恢复；≤3 条命令启动

**Scale/Scope**: 200×200 格、100 车、~数百料箱、单操作者、单页面演示

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 状态 | 依据 |
|------|------|------|
| I. 可运行优先 | ✅ | `pip install -r requirements.txt && python -m rcs_demo`（+可选 `--check`）即启动，README 记录 |
| II. Python 单栈 | ✅ | 后端 FastAPI；前端为服务托管的静态 HTML/Canvas，无 Node 构建链 |
| III. 前端可视化演示 | ✅ | Canvas 双层地图页即核心演示面，含车辆点击详情与指标面板 |
| IV. 简单性 (YAGNI) | ✅ | 无数据库、无消息队列、无插件机制；预约表用内存 dict；仿真循环用单线程 asyncio |
| V. 可验证性 | ✅ | `--check` 自检 + pytest 冒烟（碰撞为零、分配均匀性断言）+ quickstart 场景 |

**Phase 1 复查**: 通过。设计未引入超出原则 IV 的组件（无新增抽象层/服务拆分）。

## Project Structure

### Documentation (this feature)

```text
specs/001-multi-vehicle-dispatch/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── rest-api.md      # REST 接口契约
│   └── websocket.md     # WebSocket 状态流契约
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
rcs_demo/                  # Python 包（唯一工程根）
├── __main__.py            # 入口：python -m rcs_demo [--check] [--port N]
├── config.py              # 端口/尺寸/车速/任务节奏等可调参数（env 覆盖）
├── sim/
│   ├── grid.py            # 网格、固定布局（货架区/出入库口）、障碍物
│   ├── entities.py        # Vehicle / Tote / Task 数据结构
│   ├── planner.py         # A* + 时空预约表（防碰撞/等待）
│   ├── dispatcher.py      # 任务生成(1:1)、均匀分配、死锁检测与恢复
│   └── engine.py          # tick 循环、状态快照与增量
├── api/
│   └── routes.py          # REST 控制接口 + WebSocket 状态流
└── static/
    ├── index.html         # 演示页面（Canvas + 控制面板 + 指标面板）
    ├── app.js             # 渲染与交互（双层绘制、点选、障碍物编辑）
    └── style.css

tests/
├── test_smoke.py          # 启动自检
├── test_grid.py           # 布局/障碍物约束
├── test_planner.py        # A* 正确性、预约/等待
└── test_simulation.py     # 零碰撞、分配均匀性、死锁恢复（缩尺快速回归）

requirements.txt
README.md
```

**Structure Decision**: 单一 Python 包 + `static/` 前端目录（Option 1 单项目结构），
由 FastAPI 直接托管静态页面，符合章程"前端 MUST 由 Python 服务直接托管"。

## Complexity Tracking

> 无宪法违规，不需要 justified violations。

（空）
