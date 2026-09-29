# Quickstart: 运行与验证指南

Date: 2026-09-29 | Feature: 001-multi-vehicle-dispatch

## 前置条件

- Python 3.10+（宿主机或 docker 容器 rcs_core_dev 内均可；容器为 host 网络，端口直通）
- 现代桌面浏览器（Chrome/Edge/Firefox）

## 启动（3 条命令内）

```bash
pip install -r requirements.txt
python -m rcs_demo            # 默认 0.0.0.0:8000，可用 --port 改端口
# 浏览器打开 http://localhost:8000
```

可选参数：`--port N`、`--seed N`（固定随机种子复现场景）、`--fast`（仿真加速）。

## 启动自检（ constitution 原则 V ）

```bash
python -m rcs_demo --check
```

预期输出 `OK: grid layout / planner / engine self-test passed`，退出码 0。
校验内容：固定布局生成（货架区/站点数量正确）、A* 可达性抽检、预约表互斥自测、
API 端口可绑定。

## 自动化测试

```bash
pytest tests/ -q
```

覆盖（缩尺快速回归，与全量共享引擎代码）：
- `test_smoke.py`：`--check` 等价自检
- `test_grid.py`：布局约束、障碍物添加校验（有车/有箱/边界格拒绝）
- `test_planner.py`：A* 可达与绕障、预约互斥（同格同 tick 双预约必失败）
- `test_simulation.py`：缩尺 40×40/10 车/加速时钟跑 N tick，断言
  ① 零同格碰撞 ② 任务分配偏差 ≤±20% ③ 注入围堵障碍物后 M tick 内恢复移动

## 手动演示场景（对应 spec 验收）

1. **US1 可视化**：打开页面 → 3s 内出现 200×200 地图 + 100 辆编号小车持续移动；
   点选任意小车 → 面板显示编号/状态/任务目标/路径。
2. **US2 均匀分配**：观察指标面板"各车完成数"随时间趋同（差值远小于 ±20%）。
3. **US3 防碰撞**：观察车流密集区（货架通道、出入库口）车辆排队等待、无重叠穿格；
   长时间运行指标面板 `collision` 恒为 0。
4. **US4 障碍物**：点击地图空格 → 该格变障碍物，附近车流 1s 内绕行；
   再点击障碍物 → 删除、车流恢复；尝试点击有小车/料箱的格 → 提示拒绝。
5. **US5 死锁恢复**：用障碍物半围堵车流 → 车辆短暂 `stalled`（高亮）后数秒内
   重规划/让路恢复移动；事件面板出现 `stall_recovered`。
6. **暂停/恢复**：控制面板按钮 → 画面与 tick 冻结/继续；暂停时仍可编辑障碍物。

## 容器内验证（rcs_core_dev）

```bash
docker exec -it cuda12_8_torch_2_9_1_core_dev \
  bash -c "cd /home/dev/workspace/binglee/rcs_demo && pip install -r requirements.txt && python -m rcs_demo --check"
# 容器内启动服务后，宿主机浏览器访问 http://localhost:8000（host 网络直通）
```

## 参考文档

- 接口契约：[contracts/rest-api.md](contracts/rest-api.md)、[contracts/websocket.md](contracts/websocket.md)
- 数据模型：[data-model.md](data-model.md)
- 技术决策：[research.md](research.md)
