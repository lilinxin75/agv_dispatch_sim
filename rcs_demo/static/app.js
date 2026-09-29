/* RCS 多车调度演示前端：Canvas 三层渲染 + WebSocket 增量驱动。
 * 静态层：网格/货架/站点（一次性）；障碍层：障碍物变更时重绘；
 * 动态层：料箱 + 小车（每帧重绘，格间线性插值）。
 */
"use strict";

const MAP_PX = 800;
const TICK_MS = 100; // 与服务端 10 tick/s 对齐的插值时长

const cvStatic = document.getElementById("cv-static");
const cvObstacle = document.getElementById("cv-obstacle");
const cvDynamic = document.getElementById("cv-dynamic");
const cvBars = document.getElementById("cv-bars");
for (const cv of [cvStatic, cvObstacle, cvDynamic]) {
  cv.width = MAP_PX;
  cv.height = MAP_PX;
}
const ctxStatic = cvStatic.getContext("2d");
const ctxObstacle = cvObstacle.getContext("2d");
const ctxDyn = cvDynamic.getContext("2d");

const el = (id) => document.getElementById(id);
const loading = el("loading");
const toast = el("toast");

const S = {
  size: 200,
  cell: MAP_PX / 200,
  slots: [],
  obstacles: new Set(),
  vehicles: new Map(),   // id -> {id, fx, fy, tx, ty, t0, state, completed}
  totes: new Map(),      // id -> {id, at:[r,c]|null, carried_by, state}
  watch: null,
  watchDetail: null,
  connected: false,
  tick: 0,
  lastDeltaAt: 0,
};

// ---------------------------------------------------------------- WS
let ws = null;
function connect() {
  ws = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`
  );
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "snapshot":
        handleSnapshot(msg);
        break;
      case "delta":
        handleDelta(msg);
        break;
      case "obstacle_changed":
        for (const c of msg.added || []) S.obstacles.add(c.join(","));
        for (const c of msg.removed || []) S.obstacles.delete(c.join(","));
        drawObstacles();
        break;
      case "vehicle_detail":
        S.watchDetail = msg.vehicle;
        renderVehiclePanel();
        break;
      case "event":
        addEvent(msg);
        break;
      case "pong":
        break;
    }
  };
  ws.onclose = () => {
    S.connected = false;
    loading.classList.remove("hidden");
    loading.textContent = "连接断开，3 秒后重连…";
    setTimeout(connect, 3000);
  };
}

function handleSnapshot(msg) {
  S.connected = true;
  S.size = msg.grid.size;
  S.cell = MAP_PX / S.size;
  S.slots = msg.grid.slots;
  S.gridBounds = { inbound: msg.grid.inbound, outbound: msg.grid.outbound };
  S.obstacles = new Set(msg.grid.obstacles.map((c) => c.join(",")));
  S.vehicles.clear();
  for (const v of msg.vehicles) {
    S.vehicles.set(v.id, {
      id: v.id, fx: v.cell[1], fy: v.cell[0],
      tx: v.cell[1], ty: v.cell[0], t0: 0,
      state: v.state, completed: v.completed, task_id: v.task_id,
    });
  }
  S.totes.clear();
  for (const t of msg.totes) S.totes.set(t.id, { ...t, at: t.at || null });
  S.tick = msg.tick;
  updateMetrics(msg.metrics, msg.paused);
  drawStatic();
  drawObstacles();
  loading.classList.add("hidden");
}

function handleDelta(msg) {
  S.tick = msg.tick;
  S.lastDeltaAt = performance.now();
  for (const v of msg.vehicles) {
    let veh = S.vehicles.get(v.id);
    if (!veh) {
      veh = { id: v.id, fx: v.to[1], fy: v.to[0], tx: v.to[1], ty: v.to[0] };
      S.vehicles.set(v.id, veh);
    }
    if (v.from && v.to && (v.from[0] !== v.to[0] || v.from[1] !== v.to[1])) {
      // 若上一段插值未完成，从当前渲染位置续走，避免跳变
      const p = lerpPos(veh);
      veh.fx = p[0]; veh.fy = p[1];
      veh.tx = v.to[1]; veh.ty = v.to[0];
      veh.t0 = performance.now();
    }
    veh.state = v.state;
  }
  for (const t of msg.totes) {
    const cur = S.totes.get(t.id) || { id: t.id };
    cur.state = t.state;
    cur.carried_by = t.carried_by ?? null;
    cur.at = t.at ?? null;
    S.totes.set(t.id, cur);
  }
  updateMetrics(msg.metrics, null);
  el("tick-label").textContent = `tick ${S.tick}${msg.paused_label || ""}`;
}

// ---------------------------------------------------------------- 渲染
function cellRect(r, c) {
  const s = S.cell;
  return [c * s, r * s, s, s];
}

function drawStatic() {
  const ctx = ctxStatic;
  const s = S.cell;
  ctx.fillStyle = "#0b0e12";
  ctx.fillRect(0, 0, MAP_PX, MAP_PX);
  // 环场主干道（围墙内侧 2 格）微亮提示
  ctx.fillStyle = "#131a23";
  const w2 = 2 * s;
  ctx.fillRect(w2, w2, MAP_PX - 2 * w2, 2 * s);              // 上
  ctx.fillRect(w2, MAP_PX - 4 * s, MAP_PX - 2 * w2, 2 * s);  // 下
  ctx.fillRect(w2, w2, 2 * s, MAP_PX - 2 * w2);              // 左
  ctx.fillRect(MAP_PX - 4 * s, w2, 2 * s, MAP_PX - 2 * w2);  // 右
  // 货架存储点位
  ctx.fillStyle = "#1c2531";
  ctx.strokeStyle = "#2a3644";
  ctx.lineWidth = 0.5;
  for (const [r, c] of S.slots) {
    const [x, y, w, h] = cellRect(r, c);
    ctx.fillRect(x + 0.5, y + 0.5, w - 1, h - 1);
    ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
  }
  // 入库口 / 出库口
  drawStationZones(ctx);
  // 围墙（外圈 2 格厚，明显可见）
  ctx.fillStyle = "#39434f";
  ctx.fillRect(0, 0, MAP_PX, w2);
  ctx.fillRect(0, MAP_PX - w2, MAP_PX, w2);
  ctx.fillRect(0, 0, w2, MAP_PX);
  ctx.fillRect(MAP_PX - w2, 0, w2, MAP_PX);
  // 围墙描边 + 墙垛纹理
  ctx.strokeStyle = "#4d5a6b";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(w2, w2, MAP_PX - 2 * w2, MAP_PX - 2 * w2);
  ctx.strokeStyle = "rgba(77,90,107,0.35)";
  ctx.lineWidth = 0.5;
  for (let i = 0; i < S.size; i += 4) {
    const p = i * s;
    ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, w2);
    ctx.moveTo(p, MAP_PX - w2); ctx.lineTo(p, MAP_PX); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(w2, p);
    ctx.moveTo(MAP_PX - w2, p); ctx.lineTo(MAP_PX, p); ctx.stroke();
  }
}

function drawStationZones(ctx) {
  const s = S.cell;
  // 通过料箱与格型判断站点区域：由快照 bounds 计算
  const g = S.gridBounds;
  if (!g) return;
  ctx.fillStyle = "rgba(53,194,110,0.18)";
  ctx.strokeStyle = "rgba(53,194,110,0.6)";
  zoneRect(ctx, g.inbound, s);
  ctx.fillStyle = "rgba(224,161,60,0.18)";
  ctx.strokeStyle = "rgba(224,161,60,0.6)";
  zoneRect(ctx, g.outbound, s);
}

function zoneRect(ctx, b, s) {
  if (!b) return;
  const [r0, r1] = b.rows, [c0, c1] = b.cols;
  ctx.fillRect(c0 * s, r0 * s, (c1 - c0 + 1) * s, (r1 - r0 + 1) * s);
  ctx.strokeRect(c0 * s, r0 * s, (c1 - c0 + 1) * s, (r1 - r0 + 1) * s);
}

function drawObstacles() {
  const ctx = ctxObstacle;
  ctx.clearRect(0, 0, MAP_PX, MAP_PX);
  ctx.fillStyle = "#04060a";
  ctx.strokeStyle = "#5a6674";
  for (const key of S.obstacles) {
    const [r, c] = key.split(",").map(Number);
    const [x, y, w, h] = cellRect(r, c);
    ctx.fillRect(x, y, w, h);
    ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
  }
}

const STATE_COLOR = {
  idle: "#4da3ff",
  to_pickup: "#39c2ff",
  to_dropoff: "#7a6cff",
  waiting: "#ffb04d",
  stalled: "#ff5d5d",
  making_way: "#9ad14b",
};

function lerpPos(v) {
  const k = Math.min(1, (performance.now() - v.t0) / TICK_MS);
  return [v.fx + (v.tx - v.fx) * k, v.fy + (v.ty - v.fy) * k];
}

function render() {
  const ctx = ctxDyn;
  ctx.clearRect(0, 0, MAP_PX, MAP_PX);
  const s = S.cell;

  // 料箱（半透明方块；静置在点位上 / 被携带时跟随小车）
  ctx.fillStyle = "rgba(240,200,90,0.42)";
  ctx.strokeStyle = "rgba(240,200,90,0.85)";
  ctx.lineWidth = 0.6;
  for (const t of S.totes.values()) {
    if (t.state === "cycling") continue;
    let r, c;
    if (t.state === "carried") {
      const veh = S.vehicles.get(t.carried_by);
      if (!veh) continue;
      const [fx, fy] = lerpPos(veh);
      c = fx; r = fy;
    } else if (t.at) {
      c = t.at[1]; r = t.at[0];
    } else continue;
    const x = c * s, y = r * s;
    ctx.fillRect(x + 0.5, y + 0.5, s - 1, s - 1);
    ctx.strokeRect(x + 0.5, y + 0.5, s - 1, s - 1);
  }

  // 选中车辆的路径
  if (S.watchDetail && S.watchDetail.path) {
    ctx.fillStyle = "rgba(122,108,255,0.5)";
    for (const [r, c] of S.watchDetail.path) {
      ctx.fillRect(c * s + s * 0.3, r * s + s * 0.3, s * 0.4, s * 0.4);
    }
  }

  // 小车（圆点 + 可选编号）
  const showIds = el("show-ids").checked || s >= 6;
  ctx.font = `${Math.max(6, s * 1.6)}px monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const v of S.vehicles.values()) {
    const [fx, fy] = lerpPos(v);
    const x = fx * s + s / 2, y = fy * s + s / 2;
    const rad = Math.max(1.6, s * 0.38);
    ctx.fillStyle = STATE_COLOR[v.state] || "#4da3ff";
    ctx.beginPath();
    ctx.arc(x, y, rad, 0, Math.PI * 2);
    ctx.fill();
    if (v.state === "waiting" || v.state === "stalled") {
      ctx.strokeStyle = "rgba(255,255,255,0.7)";
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }
    if (S.watch === v.id) {
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(x, y, rad + 2.2, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (showIds && s >= 3.5) {
      ctx.fillStyle = "rgba(255,255,255,0.55)";
      ctx.fillText(v.id, x, y - rad - s * 0.7);
    }
  }

  requestAnimationFrame(render);
}
requestAnimationFrame(render);

// ---------------------------------------------------------------- 指标
function updateMetrics(m, paused) {
  if (!m) return;
  el("m-done").textContent = m.tasks_done;
  el("m-in").textContent = m.tasks_done_inbound;
  el("m-out").textContent = m.tasks_done_outbound;
  el("m-queue").textContent = m.tasks_queued;
  el("m-wait").textContent = m.waiting;
  el("m-stall").textContent = m.stalled;
  el("m-collision").textContent = m.collision;
  el("m-totes").textContent = m.totes_in_shelf;
  el("m-wait").classList.toggle("hot", m.waiting > 0);
  el("m-stall").classList.toggle("hot", m.stalled > 0);
  el("m-collision").classList.toggle(
    "ok", m.collision === 0 && m.tasks_done > 0
  );
  const per = m.per_vehicle_completed || [];
  if (per.length) {
    const min = Math.min(...per), max = Math.max(...per);
    const avg = per.reduce((a, b) => a + b, 0) / per.length;
    el("load-stats").textContent =
      `min ${min} / avg ${avg.toFixed(1)} / max ${max}` +
      `（偏差 ${(max ? (((max - min) / avg) * 100).toFixed(1) : 0)}%）`;
    drawBars(per, min, max);
  }
  if (paused !== null && paused !== undefined) {
    el("btn-pause").disabled = paused;
    el("btn-resume").disabled = !paused;
  }
}

function drawBars(per, min, max) {
  const ctx = cvBars.getContext("2d");
  const W = cvBars.width, H = cvBars.height;
  ctx.clearRect(0, 0, W, H);
  const n = per.length;
  const bw = W / n;
  for (let i = 0; i < n; i++) {
    const h = max > 0 ? (per[i] / max) * (H - 8) : 0;
    ctx.fillStyle = per[i] === min ? "#5b6a7d" : "#4da3ff";
    ctx.fillRect(i * bw, H - h, Math.max(1, bw - 0.5), h);
  }
}

// ---------------------------------------------------------------- 事件日志
function addEvent(msg) {
  const log = el("event-log");
  if (log.firstChild && log.firstChild.classList.contains("muted")) {
    log.textContent = "";
  }
  const div = document.createElement("div");
  div.className = "evt";
  const label = {
    stall_detected: "受阻",
    stall_recovered: "恢复",
    task_blocked: "任务受阻",
    task_unblocked: "任务恢复",
    task_retry: "任务重试",
    make_way: "让路",
  }[msg.kind] || msg.kind;
  const who = msg.vehicle_id ? ` 车${msg.vehicle_id}` : "";
  div.innerHTML = `<b>${label}</b>${who} · ${msg.detail || ""} · t${msg.tick}`;
  log.prepend(div);
  while (log.children.length > 60) log.removeChild(log.lastChild);
}

// ---------------------------------------------------------------- 车辆面板
function renderVehiclePanel() {
  const panel = el("vehicle-panel");
  if (!S.watch) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");
  const v = S.watchDetail;
  if (!v) return;
  const stateLabel = {
    idle: "空闲", to_pickup: "前往取货", to_dropoff: "送货中",
    waiting: "等待放行", stalled: "受阻", making_way: "让路中",
  }[v.state] || v.state;
  const task = v.task
    ? `任务 #${v.task.id}（${v.task.type === "inbound" ? "入库" : "出库"}）\n` +
      `  取货 (${v.task.pickup}) → 送货 (${v.task.dropoff}) · ${v.task.state}`
    : "无任务（漫游中）";
  el("vehicle-info").textContent =
    `车辆 #${v.id} · ${stateLabel}\n` +
    `位置 (${v.cell}) · 朝向 ${v.facing}\n` +
    `已完成 ${v.completed} 单\n` +
    `${task}\n` +
    `剩余路径 ${v.path.length} 格`;
}

// ---------------------------------------------------------------- 交互
async function api(method, url, body) {
  const resp = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { ok: resp.ok, data: await resp.json() };
}

let toastTimer = null;
function showToast(text) {
  toast.textContent = text;
  toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), 2200);
}

cvDynamic.addEventListener("click", async (ev) => {
  if (!S.connected) return;
  const rect = cvDynamic.getBoundingClientRect();
  const px = ((ev.clientX - rect.left) / rect.width) * MAP_PX;
  const py = ((ev.clientY - rect.top) / rect.height) * MAP_PX;
  const c = Math.floor(px / S.cell), r = Math.floor(py / S.cell);
  const key = `${r},${c}`;

  // 1) 命中小车 → 订阅详情
  let hit = null, best = 2;
  for (const v of S.vehicles.values()) {
    const [fx, fy] = lerpPos(v);
    const d = Math.hypot(fx + 0.5 - (px / S.cell), fy + 0.5 - (py / S.cell));
    if (d < best) { best = d; hit = v.id; }
  }
  if (hit !== null) {
    S.watch = hit;
    ws.send(JSON.stringify({ type: "watch", vehicle_id: hit }));
    renderVehiclePanel();
    return;
  }

  // 2) 障碍物 → 删除
  if (S.obstacles.has(key)) {
    const res = await api("DELETE", "/api/obstacle", { cell: [r, c] });
    if (!res.ok) showToast(res.data.error || "删除失败");
    return;
  }

  // 3) 空格 → 添加障碍物
  const res = await api("POST", "/api/obstacle", { cell: [r, c] });
  if (!res.ok) showToast(`无法放置障碍物：${res.data.error || "未知错误"}`);
});

el("btn-unwatch").addEventListener("click", () => {
  S.watch = null;
  S.watchDetail = null;
  ws.send(JSON.stringify({ type: "watch", vehicle_id: null }));
  renderVehiclePanel();
});

el("btn-pause").addEventListener("click", async () => {
  await api("POST", "/api/control/pause");
  el("btn-pause").disabled = true;
  el("btn-resume").disabled = false;
});
el("btn-resume").addEventListener("click", async () => {
  await api("POST", "/api/control/resume");
  el("btn-resume").disabled = true;
  el("btn-pause").disabled = false;
});
el("btn-reset").addEventListener("click", async () => {
  S.watch = null;
  S.watchDetail = null;
  renderVehiclePanel();
  await api("POST", "/api/reset");
});

connect();
