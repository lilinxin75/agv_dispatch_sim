# Implementation Plan: 调度约束完善与布局/效率/显示优化

**Branch**: `002-constraints-layout-polish` | **Date**: 2026-09-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-constraints-layout-polish/spec.md`

## Summary

对 001 期已交付的多车料箱搬运调度演示做增量完善（不推翻架构）：
① 业务约束——任务台账恒等式、停滞超时改派、料箱身份连续、工作站容量上限、拥塞背压限流；
② 硬件约束——进度累加式速度模型（空载/载货/转弯差异）、能耗×载重模型、充电功率与速率上限、
错峰充电、参数集中配置 + 启动自检校验；③ 布局——LayoutProfile 配置化（通道/货架/站点/
充电区/待命区），基线 + ≥2 套优化预设，充电桩矢量性（单向进出、桩格不可穿越）纳入规划
与自检；④ 效率——组合治理开关（在途上限/改派/错峰/背压）+ 滑动窗口指标 + 双口径验收
（稳态吞吐 ≥ 峰值 80%、等待率 <30%）；⑤ 显示——五态着色、电量条、拥塞热力图、目标点
直连线（可显隐）、车辆详情卡片、吞吐趋势图、轨迹时间蒸发（可配置，默认约 3 秒）。

## Technical Context

**Language/Version**: Python 3.10+（容器 rcs_core_dev 内 `/usr/bin/python3` 3.10.12）

**Primary Dependencies**: FastAPI + uvicorn（既有）、原生 HTML/CSS/JS（无构建链，宪法 II/III）
——不新增任何依赖

**Storage**: 无数据库；运行态在内存，冲突事件 JSONL 落盘（沿用 001 期口径），布局方案为
`rcs_demo/layouts/*.json` 配置文件

**Testing**: pytest（缩尺回归 + 新增约束断言）；`python -m rcs_demo --check` 启动自检扩展
（可达性 + 矢量性 + 配置矛盾组合）

**Target Platform**: Linux docker 容器 rcs_core_dev（host 网络），宿主机浏览器访问 :8000

**Project Type**: web-service + 静态托管前端演示（既有单项目结构）

**Performance Goals**: 引擎 10 tick/s 稳定推进（100 车）；前端无操作 ≥30fps；delta 序列化
不因新增字段显著膨胀（<1.5× 当前单帧体积）

**Constraints**: 开发/测试/运行全在容器 rcs_core_dev 内；零碰撞不变量不可破坏；
001 期既有交互不回退；布局切换仅重启生效（无热切换）

**Scale/Scope**: 200×200 全尺寸 100 车 + 40×40 缩尺回归共用代码；本特性涉及 sim 全部
6 个模块、config、api、static 前端的增量修改

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 状态 | 依据 |
|------|------|------|
| I. 可运行优先 | ✅ | 增量修改后 `pip install -r requirements.txt && python -m rcs_demo` 三条命令内启动不变；`--check` 扩展为校验新约束（布局可达/矢量性/配置矛盾） |
| II. Python 单栈 | ✅ | 布局方案用 JSON 配置 + Python 加载，无第二后端语言；无新 pip 依赖 |
| III. 前端可视化演示 | ✅ | 新能力全部落在既有 Canvas 页面（五态着色/电量条/热力图/连线/卡片/趋势图） |
| IV. 简单性 (YAGNI) | ✅ | 硬件模型取"适度仿真"（进度累加，不做动力学）；WHCA* 明确拒绝（research.md 记录理由）；无新抽象层 |
| V. 可验证性 | ✅ | pytest 新增台账/改派/背压/速度模型/矢量性断言（SC-007）；README 保留访问地址 |

**Post-Phase-1 复核**：design 产物未引入新依赖、未新增语言、未添加超出规格的抽象——
五项原则全部维持 ✅（见 research.md「Rejected Alternatives」的 YAGNI 论证）。

## Project Structure

### Documentation (this feature)

```text
specs/002-constraints-layout-polish/
├── plan.md              # This file
├── research.md          # Phase 0 output（技术决策与拒绝备选）
├── data-model.md        # Phase 1 output（实体扩展与状态机）
├── quickstart.md        # Phase 1 output（验证指南）
├── contracts/           # Phase 1 output（REST/WS 增量契约）
│   └── api-delta.md
└── tasks.md             # Phase 2 output (/speckit-tasks) — 尚未生成
```

### Source Code (repository root)

```text
rcs_demo/
├── __main__.py            # 修改：--layout 参数选择布局预设
├── config.py              # 修改：新增硬件/治理/轨迹参数（RCS_* env 覆盖 + 取值范围 + 自检校验）
├── layouts/               # 新增：布局预设 JSON（baseline.json / wide-oneway.json / charge-expanded.json）
├── sim/
│   ├── grid.py            # 修改：LayoutProfile 驱动建图；有向边覆盖层（充电区矢量性）
│   ├── entities.py        # 修改：Task 状态机/台账字段；Vehicle 速度进度累加器；充电桩矢量属性
│   ├── planner.py         # 修改：A* 读取有向边约束；转弯检测供速度/能耗模型
│   ├── dispatcher.py      # 修改：任务台账、停滞改派、工作站容量暂缓、背压限流
│   ├── charging.py        # 修改：错峰策略、队列容量、供电总功率约束
│   └── engine.py          # 修改：进度累加移动仲裁、能耗模型、指标快照/滑动窗口、治理开关
├── api/routes.py          # 修改：/api/config 扩展、state 增加台账与指标历史、delta 增加目标点
└── static/
    ├── index.html         # 修改：目标连线按钮、详情卡片容器、趋势图容器
    ├── app.js             # 修改：五态着色/电量条/热力图/直连线/详情卡片/趋势图/轨迹时间蒸发
    └── style.css          # 修改：详情卡片、警示色样式
tests/
└── test_constraints.py    # 新增：台账恒等式/改派/背压/速度能耗/矢量性/布局预设用例
```

**Structure Decision**: 沿用 001 期单项目结构（`rcs_demo/` 包 + `tests/`），不拆分前后端
目录——前端由 FastAPI 静态托管，符合宪法 II/IV。

## Complexity Tracking

> 无宪法违规，本节留空。
