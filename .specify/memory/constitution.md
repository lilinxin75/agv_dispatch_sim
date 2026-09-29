<!--
Sync Impact Report
- Version change: (none, unfilled template) → 1.0.0 (initial ratification)
- Modified principles: none (all placeholders filled for the first time)
- Added sections:
  - Core Principles I–V (可运行优先 / Python 单栈 / 前端可视化演示 / 简单性 YAGNI / 可验证性)
  - 技术与交付约束 (SECTION_2)
  - 开发工作流 (SECTION_3)
  - Governance
- Removed sections: none
- Follow-up TODOs: none
- Deferred non-governance intent: "快速实现 demo（Python 实现，具备前端演示功能）" →
  建议后续使用 /speckit-specify 创建功能规格
-->

# RCS Demo Constitution

## Core Principles

### I. 可运行优先 (Runnable First)
任何交付物 MUST 以"可直接运行"为验收标准：克隆仓库后按 README 中的步骤（最多 3 条命令）
即可启动并在浏览器中看到结果。禁止交付无法启动或依赖未文档化手工步骤的代码。
演示优先于完备性——可以裁剪功能，但不可牺牲可运行性。

### II. Python 单栈 (Python Single Stack)
后端、业务逻辑、脚本工具 MUST 使用 Python 实现（标准库或 pip 可安装的依赖），
统一使用项目内 `requirements.txt`（或 `pyproject.toml`）声明依赖。
禁止为同一职责引入第二门后端语言。前端演示页面允许使用 HTML/CSS/JS，
但 MUST 由 Python 服务直接托管（如 FastAPI/Flask 静态目录），不引入独立前端构建链。

### III. 前端可视化演示 (Front-End Demo Surface)
每个核心功能 MUST 暴露一个可通过浏览器访问的演示界面（页面或最小交互表单），
并至少提供一个可观察的结果视图（渲染结果、状态列表或日志输出）。
纯 API 无界面、或仅能用 curl 验证的功能视为未完成演示要求。

### IV. 简单性 (YAGNI)
仅实现演示所需的最小功能集。禁止引入未被当前演示使用的抽象层、插件机制、
数据库迁移框架或微服务拆分。选择技术方案时，在"能用"与"更优雅"之间 MUST 选"能用"。
任何超出演示目标的复杂度必须在 PR 描述中显式论证。

### V. 可验证性 (Verifiability)
项目 MUST 提供至少一条自动化验证路径：最小化的启动自检（如 `python -m app --check`）
或一组可执行的冒烟测试。演示启动方式、访问地址（URL/端口）必须写入 README。
无法验证的功能不得标记为完成。

## 技术与交付约束

- 语言与运行时：Python 3（建议 3.10+），Web 框架优先 FastAPI 或 Flask。
- 前端：由 Python 服务托管的原生 HTML/CSS/JS 或轻量模板（Jinja2）；
  不引入 Node.js 构建、npm 依赖或前端框架工程化链路。
- 数据：演示阶段优先内存数据或 SQLite/JSON 文件；禁止引入需独立部署的数据库服务。
- 配置：所有可变参数（端口、路径、开关）通过环境变量或单一配置文件暴露，
  并在 README 中列出默认值。
- 文档：README 必须包含：启动命令、访问地址、功能清单、停止方式。

## 开发工作流

1. 每个功能先通过 `/speckit-specify` 产出规格，再进入计划与实现。
2. 实现完成后必须本地运行验证（自检或冒烟测试通过 + 浏览器可访问），
   方可提交。
3. 提交信息使用祈使句并概括功能；演示类改动需在描述中附访问路径。
4. 依赖变更必须同步更新依赖清单文件并说明用途。

## Governance

- 本章程是项目的最高约束：任何功能决策、代码评审与任务验收 MUST 与之一致，
  冲突时以本章程为准。
- 修订流程：提出修订 → 在修订说明中记录动机与影响 → 更新版本号与 Last Amended
  日期 → 提交评审。版本遵循语义化：MAJOR（删除/重定义原则）、MINOR（新增原则
  或实质性扩展）、PATCH（措辞澄清）。
- 合规检查：每个功能评审必须核对五项原则，特别是"可运行优先"与
  "前端可视化演示"两条一票否决项。
- 日常开发的运行时指导见各功能 `specs/` 目录下的 plan.md，其不得与本文冲突。

**Version**: 1.0.0 | **Ratified**: 2026-09-29 | **Last Amended**: 2026-09-29
