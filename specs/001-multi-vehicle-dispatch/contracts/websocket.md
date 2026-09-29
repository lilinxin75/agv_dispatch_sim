# Contract: WebSocket 状态流

URL: `ws://<host>:<port>/ws`

服务端按仿真节拍推送增量消息（正常 ≤100ms 一条；`paused` 时仅推送事件型消息）。
所有消息为 JSON 文本帧，带公共字段 `{"type": "<msg_type>", "tick": <int>, ...}`。

## 消息类型

### `snapshot`
连接建立后服务端立刻发送一条完整快照（结构同 `GET /api/state`），type 为 `"snapshot"`。
客户端以此初始化，之后只处理增量。

### `delta` （高频，约每 tick 一条）
```json
{ "type": "delta", "tick": 12346,
  "vehicles": [ { "id": 7, "from": [12,34], "to": [12,35], "state": "to_dropoff" } ],
  "totes":   [ { "id": 3, "at": [12,35], "state": "carried", "carried_by": 7 } ],
  "metrics": { "tasks_done": 341, "waiting": 5, "stalled": 0, "collision": 0 } }
```
- `vehicles[].from/to` 为逻辑格；客户端做插值渲染。
- 数组为增量：仅包含本 tick 发生变化的实体；空数组表示无变化。
- `metrics` 每条 delta 都全量携带（字段少，免去客户端合并逻辑）。

### `obstacle_changed`
```json
{ "type": "obstacle_changed", "tick": 12350,
  "added": [[10,10]], "removed": [[11,10]] }
```

### `vehicle_detail` （点选请求的应答/更新）
客户端发送 `{"type": "watch", "vehicle_id": 7}` 订阅单车详情；服务端以
`{"type": "vehicle_detail", "vehicle": {...同 GET /api/vehicle/{id}...}}` 回复，
并在该车状态/路径变化时持续推送，直到客户端发送 `{"type": "watch", "vehicle_id": null}`。

### `event` （低频事件通知）
```json
{ "type": "event", "tick": 12400, "kind": "stall_recovered",
  "vehicle_id": 42, "detail": "rerouted after obstacle" }
```
`kind` ∈ `stall_detected` | `stall_recovered` | `task_blocked` | `task_unblocked`。

## 客户端→服务端消息

仅 `watch`（上见）与 `ping`（服务端回 `{"type":"pong","tick":N}`）。

## 断线重连约定

客户端重连后收到新 `snapshot`，丢弃旧增量状态全量重建。服务端不保存历史增量。
