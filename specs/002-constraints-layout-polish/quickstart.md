# Quickstart 验证指南: 002-constraints-layout-polish

**前置**: 全部命令在 docker 容器 rcs_core_dev 内执行（宿主机仅浏览器访问）。

```bash
docker exec -it rcs_core_dev bash -c "cd /home/dev/workspace/binglee/rcs_demo && \
  pip install -r requirements.txt && python3 -m rcs_demo --check"
```

启动自检（扩展后）必须输出 `OK: grid layout / planner / engine self-test passed`，
并新增：布局预设加载、全图可达性、充电桩矢量性（单向进出/桩格不可穿越）、
待命区容量、配置矛盾组合校验。

## ① 自动化回归（SC-007）

```bash
docker exec -it rcs_core_dev bash -c \
  "cd /home/dev/workspace/binglee/rcs_demo && python3 -m pytest tests/ -q"
```

既有 25 用例 + 新增 `tests/test_constraints.py`：

- 台账恒等式：运行 3000 tick，任意采样点 `spawned == done + active + reassigned`（SC-002）
- 停滞改派：注入围堵使执行车停滞 > REASSIGN_TICKS，断言任务改派、tote 认领唯一
- 背压：在途达上限或等待率超阈值时 spawn 间隔放大，缓解后恢复
- 速度/能耗模型：载货车 progress 增速 = 空载 × 0.7；转向步额外能耗；电量非负
- 充电桩矢量性：A* 对桩格（非目标）不可达、进口/出口方向约束成立、全部布局预设通过
- 布局预设：三套预设 `--check` 全过、缩尺引擎可用任意预设建图且零碰撞

## ② 布局方案对比（US3，SC-004）

```bash
python3 -m rcs_demo --layout baseline --seed 42 &      # 跑 10 分钟记录指标
python3 -m rcs_demo --layout wide-oneway --seed 42 &   # 同种子对比
python3 -m rcs_demo --layout charge-expanded --seed 42 &
```

预期：三套均可启动且 `--check` 通过；wide-oneway 热点频次或吞吐可量化优于 baseline
（指标面板/冲突分析工具对比）；charge-expanded 充电排队深度峰值低于 baseline。

## ③ 效率双口径验收（US4，SC-001/SC-003；快慢分层，FR-026/FR-027）

**快（迭代验证，分钟级）**：headless 批量推进——`Engine.tick()` 纯同步驱动，不受
墙钟限制（实测全程 ~120× 实时）：

```bash
python3 -m rcs_demo.acceptance --ticks 8000 --report-dir logs/acc_smoke
```

**慢（收尾验收，一次）**：36000 tick（仿真 60 分钟）全场景，同一入口（FR-027
过程监控内建：逐窗口 marks.jsonl 落盘；命中碰撞/tick 成本失控/吞吐停摆/电量
滞零/台账恒等式任一异常判据立即中止 exit 2 并写 anomaly.json，修复后从头重跑）：

```bash
python3 -m rcs_demo.acceptance --ticks 36000 --seed 42 --report-dir logs/acc_final
# 布局对比口径（SC-004）：--layout wide-oneway / --layout charge-expanded
```

核对双口径（summary.json / 控制台摘要）：

- 主口径：后 30 分钟平均吞吐（sc001_ratio）≥ 前 10 分钟峰值的 80%
- 辅口径：marks.jsonl waiting 峰值 < 30%
- 全程零碰撞（监控自动判据）、零电量归零停摆（滞零 >900 tick 自动中止，SC-006）
- 互堵频次（`python3 -m rcs_demo.analyze_conflicts logs/acc_final/conflicts/*.jsonl`）
  较 001 期同种子基线下降 ≥30%（SC-003）

## ④ 显示效果人工核验清单（US5，SC-005）

1. 五态着色：任务=蓝 / 等待=橙 / 充电=绿 / 空闲=灰 / 受阻=红，一眼可分
2. 电量条：车体下缘随电量变化；<20% 橙、<10% 红；充电车有 ⚡ 指示
3. 热力图开关：打开数分钟后通道交汇/站前现热点，关闭即隐藏
4. 目标连线开关（默认隐藏）：打开后执行/充电/待命车出现到目标的直连线，
   目标变化跟随，关闭即消失
5. 轨迹时间蒸发：车辆停住 ~3 秒后轨迹完全淡出；再起步轨迹重新生长
6. 点击车辆 → 详情卡片（编号/状态/电量/当前任务/最近状态变化）
7. 指标面板趋势图滚动更新，吞吐/平均周期可切换查看
8. 001 期交互不回退：缩放平移、动态障碍增删、事件面板、暂停/复位、轨迹开关

## ⑤ 容器内冒烟

```bash
docker exec -it rcs_core_dev bash -c \
  "cd /home/dev/workspace/binglee/rcs_demo && python3 -m rcs_demo --layout charge-expanded --check"
```

预期通过；将 `--layout` 换成不存在的名字预期报可读错误退出非 0。
