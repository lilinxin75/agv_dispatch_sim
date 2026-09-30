# Contract: REST API

Base URL: `http://<host>:<port>/api` （默认端口 8000，`--port` 可调）
所有请求/响应 body 为 JSON（UTF-8）。错误统一格式 `{"error": "<message>"}`，HTTP 4xx/5xx。

## GET /api/state

返回当前完整状态快照（页面首次加载用，此后走 WebSocket 增量）。

**Response 200**:
```json
{
  "tick": 12345,
  "paused": false,
  "grid": { "size": 200,
            "slots": [[60,40],[60,42], "..."],
            "inbound": {"rows": [95,104], "cols": [0,4]},
            "outbound": {"rows": [95,104], "cols": [195,199]},
            "charging_area": { "piles": [[190,90],"...共10个"],
                               "entry": [[185,85],"..."], "exit": [[185,114],"..."] },
            "obstacles": [[10,10], "..."] },
  "vehicles": [ { "id": 1, "cell": [12,34], "state": "to_pickup",
                  "task_id": 88, "completed": 12, "battery": 842 } ],
  "totes":   [ { "id": 1, "at": [60,40], "state": "docked" },
               { "id": 2, "at": "inbound", "state": "docked" },
               { "id": 3, "carried_by": 5, "state": "carried" } ],
  "charging": { "pile_owner": {"190,90": 7}, "queue": [23, 41] },
  "metrics": { "tasks_done": 340, "waiting": 6, "stalled": 0, "collision": 0,
               "charging": 8, "charge_queue": 2, "avg_battery": 71,
               "per_vehicle_completed": [12, 11, "..."] }
}
```

- `vehicles[].state` 枚举：`idle | to_pickup | to_dropoff | to_charge | charging | waiting | stalled | making_way`。
- `battery` 为剩余电量点数（0..1000，`GET /api/state` 全量携带；delta 中变化时携带）。

## POST /api/obstacle

添加障碍物。

**Request**: `{"cell": [10, 10]}`
**Response 200**: `{"ok": true, "cell": [10, 10]}`
**Response 409**（格上有车/有箱/为边界或站点）: `{"error": "cell occupied or reserved"}`

**效果约定**：生效后 1s 内后续规划不再使用该格（FR-009）；受影响路径车辆自动重规划。

## DELETE /api/obstacle

删除障碍物。

**Request**: `{"cell": [10, 10]}` （或 `DELETE` with same body）
**Response 200**: `{"ok": true}`；**Response 404**: `{"error": "no obstacle at cell"}`

## POST /api/control/pause 、POST /api/control/resume

暂停/恢复仿真推进。**Response 200**: `{"ok": true, "paused": true|false}`

## GET /api/vehicle/{id}

单车详情（前端点选用）。**Response 200**: `{"id": 1, "cell": [...], "state": "...", "task": {...}|null, "path": [[r,c],...], "completed": 12, "battery": 842}`
**Response 404**: 未知 id。

## GET /api/config

返回当前生效的可调参数（前端关于面板/调试用）。**Response 200**: `{"tasks_per_second": 1, "battery_full": 1000, "battery_low_ratio": 0.2, "charge_seconds": 30, "charge_piles": 10, "turn_penalty": 3, "zoom_min": 0.25, "zoom_max": 4.0, "...": "见 config.py 全量参数"}`

## POST /api/reset

重置仿真到初始布局（半仓、100 车随机分布）。**Response 200**: `{"ok": true}`

## 一致性约束

- 所有写接口在 `paused` 状态下同样可用（障碍物编辑常在暂停时进行）。
- 障碍物操作幂等性：重复添加同格返回 200 且不重复计数；重复删除 404。
- 充电区格（充电位/进站/出站/排队格）不可添加障碍物：`POST /api/obstacle` 返回 409。
- 视口缩放/平移（FR-030）为纯前端行为，无对应 REST 接口。
