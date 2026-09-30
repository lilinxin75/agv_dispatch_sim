# API Contract Delta: 002-constraints-layout-polish

**基线**: specs/001-multi-vehicle-dispatch/contracts/（REST + WS 契约不变部分不赘述）。
本文只列**增量字段**；全部向后兼容（新字段为附加，不改动既有字段语义）。

## REST

### GET /api/config（扩展，FR-010/SC-004）

新增字段（含默认值与取值范围，供配置面板与自检使用）：

```json
{
  "layout": "baseline",
  "available_layouts": ["baseline", "wide-oneway", "charge-expanded"],
  "loaded_speed_ratio": 0.7,
  "turn_speed_ratio": 0.5,
  "turn_energy": 0.5,
  "reassign_ticks": 300,
  "station_capacity": 6,
  "charge_queue_soft_limit": 6,
  "govern_backpressure": true,
  "govern_reassign": true,
  "govern_charge_defer": true,
  "trail_ttl_ticks": 30,
  "metrics_window_ticks": 600
}
```

新增启动自检校验：`speed_ratio ∈ (0,1]`、`CHARGE_SECONDS > 0`、
`REASSIGN_TICKS > STALL_TICKS`、布局预设存在——矛盾组合返回可读错误并退出非 0。

### GET /api/state（扩展，FR-001/016）

```json
{
  "ledger": {"spawned": 1200, "active": 63, "done": 1120, "reassigned": 17},
  "metrics_history": [{"tick": 6000, "throughput_window": 58,
                        "avg_cycle_ticks": 410, "waiting_ratio": 0.22,
                        "charge_queue_depth": 8, "conflict_per_ktick": 1.7}]
}
```

`metrics_history` 下发最近 ≤360 采样点（每 10 tick 一点）。

### GET /api/vehicle/{id}（扩展，FR-022）

增：`goal`（当前最终目标格）、`progress`、`reassign_count`（当前任务）、
`defer_charge`、`state_history`（最近 8 条状态变化：state + tick）。

## WebSocket

### delta 车辆对象（扩展，FR-018/019/021）

```json
{"id": 7, "cell": [101, 42], "facing": "E", "state": "TO_DROPOFF",
 "battery": 743.5, "carrying": 512, "tx": 42, "ty": 101,
 "goal": [99, 197], "defer_charge": false}
```

- `goal`：目标连线绘制端点（任务终点/桩位/待命位）；无目标时省略字段
- 前端由 `state` 映射五态着色，由 `battery` 渲染电量条

### 事件流（扩展）

新增 `governance_event`：`{"kind": "governance_event", "action": "reassigned|"
"backpressure|charge_deferred|rerouted", "tick": ..., "vehicles": [...],
"detail": "..."}`——事件面板与指标共用（FR-015/US4）。

## 不变契约

`POST/DELETE /api/obstacle`、`POST /api/control/pause|resume`、`POST /api/reset`、
`WS snapshot` 结构不变；冲突事件 JSONL 口径不变（仅增 action 字段于治理事件，
conflict 文件本身格式不变）。
