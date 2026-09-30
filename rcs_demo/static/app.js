/* RCS 多车调度演示前端：三层 Canvas 渲染 + 相机视口 + WS 增量。
 * 静态层（网格/围墙/货架/站点/充电区，离屏预渲染）、障碍层、动态层（料箱+小车）。
 * 相机 {scale, offset} 与仿真逻辑完全解耦（FR-030 / research R12）。
 */
"use strict";

const CELL = 8; // 世界坐标像素/格（静态层分辨率）
const GRID = 200;
const ZOOM_MIN = 0.25, ZOOM_MAX = 4.0;
const DRAG_PX = 5, DRAG_MS = 300; // 点击/拖拽区分阈值（R12）

const cvStatic = document.getElementById("cv-static");
const cvObst = document.getElementById("cv-obstacle");
const cvDyn = document.getElementById("cv-dynamic");
const wrap = document.getElementById("canvas-wrap");
const ctxS = cvStatic.getContext("2d");
const ctxO = cvObst.getContext("2d");
const ctxD = cvDyn.getContext("2d");

// ---- 世界状态 ----
let world = null;            // snapshot
let obstacles = new Set();   // "r,c"
let vehicleVis = new Map();  // id -> {x, y, target:{x,y}, state, battery, completed, carrying}
let toteCells = new Map();   // "r,c" -> tote（静置料箱）
let carriedTotes = new Map(); // vehicle_id -> tote_id
let events = [];
let watchedId = null;
let paused = false;
let showTrails = true;          // 轨迹开关（FR-032，默认开启）
const TRAIL_LEN = 30;           // 尾迹长度（tick，≈3 秒 @10tick/s）

// ---- 相机 ----
const cam = { scale: 1, x: 0, y: 0 };

function fitView() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  const s = Math.min(w / (GRID * CELL), h / (GRID * CELL));
  cam.scale = s;
  cam.x = (w - GRID * CELL * s) / 2;
  cam.y = (h - GRID * CELL * s) / 2;
}

function worldToScreen(wx, wy) { return [wx * cam.scale + cam.x, wy * cam.scale + cam.y]; }
function screenToWorld(sx, sy) { return [(sx - cam.x) / cam.scale, (sy - cam.y) / cam.scale]; }

// ---- 静态层预渲染 ----
const offscreen = document.createElement("canvas");
offscreen.width = offscreen.height = GRID * CELL;

function prerenderStatic(g) {
  const c = offscreen.getContext("2d");
  c.fillStyle = "#191c22"; // 地面
  c.fillRect(0, 0, offscreen.width, offscreen.height);
  const S = new Set(g.grid.slots.map(p => p[0] + "," + p[1]));
  const inB = g.grid.inbound, outB = g.grid.outbound;
  const piles = new Set(g.grid.charging_area.piles.map(p => p[0] + "," + p[1]));
  const chEntry = new Set(g.grid.charging_area.entry.map(p => p[0] + "," + p[1]));
  const chExit = new Set(g.grid.charging_area.exit.map(p => p[0] + "," + p[1]));

  // 货架区底色
  c.fillStyle = "#20242d";
  c.fillRect(inB.cols[1] * CELL, 0, GRID * CELL, GRID * CELL);
  c.fillStyle = "#191c22";
  c.fillRect(0, 0, GRID * CELL, GRID * CELL);

  // 存储点位
  for (const key of S) {
    const [r, col] = key.split(",").map(Number);
    c.fillStyle = "#2c3340";
    c.fillRect(col * CELL + 1, r * CELL + 1, CELL - 2, CELL - 2);
  }
  // 入库口（西）/出库口（东）
  c.fillStyle = "#1f4d3a";
  for (let r = inB.rows[0]; r <= inB.rows[1]; r++)
    for (let col = Math.max(1, inB.cols[0]); col <= Math.min(GRID - 2, inB.cols[1]); col++)
      c.fillRect(col * CELL, r * CELL, CELL, CELL);
  c.fillStyle = "#4d3a1f";
  for (let r = outB.rows[0]; r <= outB.rows[1]; r++)
    for (let col = Math.max(1, outB.cols[0]); col <= Math.min(GRID - 2, outB.cols[1]); col++)
      c.fillRect(col * CELL, r * CELL, CELL, CELL);
  // 充电区
  c.fillStyle = "#3a2f52";
  const cs = g.grid.charging_area;
  const rmin = cs.piles[0][0] - 2, rmax = cs.piles[0][0] + 2;
  const cmin = Math.min(...cs.entry.map(p => p[1])) - 4;
  const cmax = Math.max(...cs.exit.map(p => p[1]));
  c.fillRect(cmin * CELL, rmin * CELL, (cmax - cmin + 1) * CELL, (rmax - rmin + 1) * CELL);
  for (const p of cs.piles) { c.fillStyle = "#7a5cd6"; c.fillRect(p[1] * CELL + 1, p[0] * CELL + 1, CELL - 2, CELL - 2); }
  c.fillStyle = "#554a80";
  for (const p of cs.entry) c.fillRect(p[1] * CELL, p[0] * CELL, CELL, CELL);
  for (const p of cs.exit) c.fillRect(p[1] * CELL, p[0] * CELL, CELL, CELL);

  // 围墙
  c.fillStyle = "#4a5160";
  c.fillRect(0, 0, offscreen.width, CELL);
  c.fillRect(0, offscreen.height - CELL, offscreen.width, CELL);
  c.fillRect(0, 0, CELL, offscreen.height);
  c.fillRect(offscreen.width - CELL, 0, CELL, offscreen.height);
}

// ---- 视口尺寸 ----
function resize() {
  for (const cv of [cvStatic, cvObst, cvDyn]) {
    cv.width = wrap.clientWidth; cv.height = wrap.clientHeight;
  }
  fitView();
  drawObstacles();
}
window.addEventListener("resize", resize);

// ---- 渲染循环 ----
let lastFrame = performance.now();
function frame(now) {
  const dt = Math.min((now - lastFrame) / 1000, 0.1);
  lastFrame = now;
  // 车辆插值（10 格/秒，与 tick 同步）
  const step = 10 * dt;
  for (const v of vehicleVis.values()) {
    const dx = v.tx - v.x, dy = v.ty - v.y;
    const dist = Math.hypot(dx, dy);
    if (dist > 0.001) {
      const mv = Math.min(step, dist);
      v.x += dx / dist * mv; v.y += dy / dist * mv;
    }
  }
  render();
  requestAnimationFrame(frame);
}

function render() {
  // 每帧全清各层（FR-031：identity 变换下清全屏，杜绝残影/轨迹残留）
  ctxD.setTransform(1, 0, 0, 1, 0, 0);
  ctxD.clearRect(0, 0, cvDyn.width, cvDyn.height);
  ctxS.setTransform(1, 0, 0, 1, 0, 0);
  ctxS.clearRect(0, 0, cvStatic.width, cvStatic.height);
  drawObstacles(); // 障碍层每帧随相机重绘（旧实现仅变更时重绘 → 缩放残影根因）
  ctxS.imageSmoothingEnabled = cam.scale < 1;
  const [sx, sy] = worldToScreen(0, 0);
  ctxS.drawImage(offscreen, sx, sy, GRID * CELL * cam.scale, GRID * CELL * cam.scale);

  ctxD.setTransform(cam.scale, 0, 0, cam.scale, cam.x, cam.y);
  // 视口裁剪（culling）
  const [wx0, wy0] = screenToWorld(0, 0);
  const [wx1, wy1] = screenToWorld(cvDyn.width, cvDyn.height);
  const r0 = Math.max(0, Math.floor(wy0 / CELL) - 1), r1 = Math.min(GRID - 1, Math.ceil(wy1 / CELL) + 1);
  const c0 = Math.max(0, Math.floor(wx0 / CELL) - 1), c1 = Math.min(GRID - 1, Math.ceil(wx1 / CELL) + 1);

  // 轨迹尾迹（FR-032）：每车最近 N tick 移动路径，半透明渐隐，绘制在小车层之下
  if (showTrails) {
    ctxD.lineWidth = Math.max(1, 1.5 / cam.scale);
    for (const v of vehicleVis.values()) {
      const tr = v.trail;
      if (!tr || tr.length < 2) continue;
      if (tr[tr.length - 1].x < c0 - 2 || tr[tr.length - 1].x > c1 + 2 ||
          tr[tr.length - 1].y < r0 - 2 || tr[tr.length - 1].y > r1 + 2) continue;
      const n = tr.length;
      for (let i = 1; i < n; i++) {
        ctxD.strokeStyle = `rgba(140,180,255,${(i / n) * 0.30})`;
        ctxD.beginPath();
        ctxD.moveTo(tr[i - 1].x * CELL + CELL / 2, tr[i - 1].y * CELL + CELL / 2);
        ctxD.lineTo(tr[i].x * CELL + CELL / 2, tr[i].y * CELL + CELL / 2);
        ctxD.stroke();
      }
    }
  }

  // 静置料箱（半透明方块，FR-019）
  ctxD.fillStyle = "rgba(120,170,255,0.35)";
  for (const [key] of toteCells) {
    const [r, col] = key.split(",").map(Number);
    if (r < r0 || r > r1 || col < c0 || col > c1) continue;
    ctxD.fillRect(col * CELL + 1, r * CELL + 1, CELL - 2, CELL - 2);
  }

  // 小车（带编号圆点）
  const drawLabels = cam.scale > 0.5;
  for (const [id, v] of vehicleVis) {
    const px = v.x * CELL + CELL / 2, py = v.y * CELL + CELL / 2;
    if (v.x < c0 - 1 || v.x > c1 + 1 || v.y < r0 - 1 || v.y > r1 + 1) continue;
    let color = "#4cc38a";                      // idle
    if (v.state === "to_pickup") color = "#e8b84c";
    else if (v.state === "to_dropoff") color = "#4c9de8";
    else if (v.state === "to_charge") color = "#b07ce8";
    else if (v.state === "charging") color = "#8a5cd6";
    else if (v.state === "waiting") color = "#8b93a3";
    else if (v.state === "stalled") color = "#e85c5c";
    else if (v.state === "making_way") color = "#5cd6c0";
    // 携送料箱：车下画跟随料箱框（FR-019）
    if (v.carrying) {
      ctxD.fillStyle = "rgba(120,170,255,0.5)";
      ctxD.fillRect(v.x * CELL + 0.5, v.y * CELL + 0.5, CELL - 1, CELL - 1);
    }
    ctxD.beginPath();
    ctxD.arc(px, py, Math.max(2.4, CELL * 0.34), 0, Math.PI * 2);
    ctxD.fillStyle = color;
    ctxD.fill();
    if (v.state === "charging") { // 充电标识
      ctxD.strokeStyle = "#ffd76b"; ctxD.lineWidth = 1.2;
      ctxD.beginPath(); ctxD.arc(px, py, CELL * 0.5, 0, Math.PI * 2); ctxD.stroke();
    }
    if (v.state === "stalled") { // 受阻高亮
      ctxD.strokeStyle = "#ff8080"; ctxD.lineWidth = 1.5;
      ctxD.beginPath(); ctxD.arc(px, py, CELL * 0.55, 0, Math.PI * 2); ctxD.stroke();
    }
    if (watchedId === id) {
      ctxD.strokeStyle = "#fff"; ctxD.lineWidth = 1.5;
      ctxD.beginPath(); ctxD.arc(px, py, CELL * 0.6, 0, Math.PI * 2); ctxD.stroke();
    }
    if (drawLabels && cam.scale > 1.2) {
      ctxD.fillStyle = "#0b0d10";
      ctxD.font = "bold 5px sans-serif";
      ctxD.textAlign = "center"; ctxD.textBaseline = "middle";
      ctxD.fillText(String(id), px, py + 0.5);
    }
  }
}

// ---- 障碍层 ----
function drawObstacles() {
  ctxO.setTransform(1, 0, 0, 1, 0, 0);
  ctxO.clearRect(0, 0, cvObst.width, cvObst.height);
  ctxO.setTransform(cam.scale, 0, 0, cam.scale, cam.x, cam.y);
  ctxO.fillStyle = "#c2452f";
  ctxO.strokeStyle = "#e8705c";
  for (const key of obstacles) {
    const [r, col] = key.split(",").map(Number);
    ctxO.fillRect(col * CELL, r * CELL, CELL, CELL);
    ctxO.strokeRect(col * CELL + 0.5, r * CELL + 0.5, CELL - 1, CELL - 1);
  }
}

// ---- 交互：缩放/平移/点击智能区分（FR-030）----
let dragging = false, pressPos = null, pressTime = 0, moved = false;

cvDyn.addEventListener("wheel", (e) => {
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
  const [wx, wy] = screenToWorld(e.offsetX, e.offsetY);
  let ns = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, cam.scale * factor));
  // 光标锚点缩放
  cam.x = e.offsetX - wx * ns;
  cam.y = e.offsetY - wy * ns;
  cam.scale = ns;
  drawObstacles();
}, { passive: false });

cvDyn.addEventListener("mousedown", (e) => {
  dragging = true; moved = false;
  pressPos = [e.clientX, e.clientY]; pressTime = performance.now();
  cvDyn.classList.add("dragging");
});
window.addEventListener("mousemove", (e) => {
  if (!dragging) return;
  const dx = e.clientX - pressPos[0], dy = e.clientY - pressPos[1];
  if (Math.hypot(dx, dy) > DRAG_PX) moved = true;
  if (moved) {
    cam.x += e.movementX; cam.y += e.movementY;
    drawObstacles();
  }
});
window.addEventListener("mouseup", (e) => {
  if (!dragging) return;
  dragging = false;
  cvDyn.classList.remove("dragging");
  const quick = performance.now() - pressTime < DRAG_MS;
  if (!moved && quick) handleClick(e);
});

function handleClick(e) {
  const [wx, wy] = screenToWorld(e.offsetX, e.offsetY);
  const col = Math.floor(wx / CELL), row = Math.floor(wy / CELL);
  if (row < 0 || row >= GRID || col < 0 || col >= GRID) return;
  // 命中车辆（世界距离 < 0.7 格）
  let hit = null, bestD = 0.7;
  for (const [id, v] of vehicleVis) {
    const d = Math.hypot(v.x + 0.5 - (col + 0.5), v.y + 0.5 - (row + 0.5));
    if (d < bestD) { bestD = d; hit = id; }
  }
  if (hit !== null) { selectVehicle(hit); return; }
  selectVehicle(null);
  // 障碍物增删（US4）
  const key = row + "," + col;
  if (obstacles.has(key)) {
    api("DELETE", "/api/obstacle", { cell: [row, col] });
  } else {
    api("POST", "/api/obstacle", { cell: [row, col] }).then(res => {
      if (!res.ok) toast(res.error === "cell occupied or reserved"
        ? "无法添加：格子被车辆/料箱占用或为站点/充电区" : ("添加失败: " + (res.error || "")));
    });
  }
}

// ---- API 与 WS ----
async function api(method, url, body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(url, opt);
  return await r.json().catch(() => ({}));
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { document.getElementById("m-conn").textContent = "已连接"; };
  ws.onclose = () => {
    document.getElementById("m-conn").textContent = "断开";
    setTimeout(connectWS, 1000); // 断线重连：重连后收新 snapshot 全量重建
  };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "snapshot") applySnapshot(m);
    else if (m.type === "delta") applyDelta(m);
    else if (m.type === "obstacle_changed") applyObstacleChange(m);
    else if (m.type === "vehicle_detail") showVehicleDetail(m.vehicle);
    else if (m.type === "event") addEvent(m);
  };
  window._ws = ws;
}

function applySnapshot(m) {
  world = m;
  obstacles = new Set(m.grid.obstacles.map(p => p[0] + "," + p[1]));
  toteCells.clear(); carriedTotes.clear();
  for (const t of m.totes) {
    if (t.state === "docked" && Array.isArray(t.at)) toteCells.set(t.at[0] + "," + t.at[1], t.id);
    else if (t.state === "carried") carriedTotes.set(t.carried_by, t.id);
  }
  vehicleVis.clear();
  for (const v of m.vehicles) {
    vehicleVis.set(v.id, { x: v.cell[1], y: v.cell[0], tx: v.cell[1], ty: v.cell[0],
      trail: [{ x: v.cell[1], y: v.cell[0] }],
      state: v.state, battery: v.battery, completed: v.completed,
      carrying: carriedTotes.has(v.id) });
  }
  drawObstacles();
  updateMetrics(m.metrics);
  document.getElementById("tick-label").textContent = "tick " + m.tick;
  if (watchedId) sendWatch(watchedId);
}

function applyDelta(m) {
  for (const dv of m.vehicles) {
    let v = vehicleVis.get(dv.id);
    if (!v) { v = { x: dv.to[1], y: dv.to[0], tx: dv.to[1], ty: dv.to[0], trail: [] }; vehicleVis.set(dv.id, v); }
    v.tx = dv.to[1]; v.ty = dv.to[0];
    if (v.x === undefined || Math.abs(v.x - v.tx) + Math.abs(v.y - v.ty) > 3) { v.x = dv.from[1]; v.y = dv.from[0]; }
    // 轨迹采样（FR-032）：每 tick 到达格入队，超出 TRAIL_LEN 丢弃最旧
    if (!v.trail) v.trail = [];
    const last = v.trail[v.trail.length - 1];
    if (!last || last.x !== v.tx || last.y !== v.ty) {
      v.trail.push({ x: v.tx, y: v.ty });
      if (v.trail.length > TRAIL_LEN) v.trail.shift();
    }
    v.state = dv.state;
    if (dv.battery !== undefined) v.battery = dv.battery;
  }
  for (const dt of m.totes) {
    if (dt.state === "docked" && Array.isArray(dt.at)) {
      toteCells.set(dt.at[0] + "," + dt.at[1], dt.id);
    } else {
      for (const [k, id] of toteCells) if (id === dt.id) toteCells.delete(k);
    }
    if (dt.state === "carried") { carriedTotes.set(dt.carried_by, dt.id); if (vehicleVis.get(dt.carried_by)) vehicleVis.get(dt.carried_by).carrying = true; }
    else {
      for (const [vid] of carriedTotes) if (carriedTotes.get(vid) === dt.id) { carriedTotes.delete(vid); if (vehicleVis.get(vid)) vehicleVis.get(vid).carrying = false; }
    }
  }
  updateMetrics(m.metrics);
  document.getElementById("tick-label").textContent = "tick " + m.tick;
}

function applyObstacleChange(m) {
  for (const p of m.added || []) obstacles.add(p[0] + "," + p[1]);
  for (const p of m.removed || []) obstacles.delete(p[0] + "," + p[1]);
  drawObstacles();
}

function updateMetrics(mt) {
  document.getElementById("m-tasks").textContent = mt.tasks_done;
  document.getElementById("m-waiting").textContent = mt.waiting;
  document.getElementById("m-stalled").textContent = mt.stalled;
  document.getElementById("m-collision").textContent = mt.collision;
  document.getElementById("m-charging").textContent = mt.charging;
  document.getElementById("m-chargeq").textContent = mt.charge_queue;
  document.getElementById("m-battery").textContent = mt.avg_battery;
  const pc = mt.per_vehicle_completed || [];
  if (pc.length) {
    const sum = pc.reduce((a, b) => a + b, 0), avg = sum / pc.length;
    const mx = Math.max(...pc), mn = Math.min(...pc);
    document.getElementById("per-vehicle").textContent =
      `各车完成数：min ${mn} / avg ${avg.toFixed(1)} / max ${mx}（差 ${((mx - mn) / Math.max(avg, 1) * 100).toFixed(0)}%）`;
  }
}

// ---- 车辆详情 ----
function sendWatch(id) {
  if (window._ws && window._ws.readyState === 1)
    window._ws.send(JSON.stringify({ type: "watch", vehicle_id: id }));
}

function selectVehicle(id) {
  watchedId = id;
  const panel = document.getElementById("vehicle-panel");
  if (id === null) { panel.classList.add("hidden"); sendWatch(null); return; }
  panel.classList.remove("hidden");
  sendWatch(id);
}

function showVehicleDetail(v) {
  if (watchedId !== v.id) return;
  const stateNames = { idle: "空闲待命", to_pickup: "前往取货", to_dropoff: "送货中",
    to_charge: "前往充电", charging: "充电中", waiting: "等待放行",
    stalled: "受阻", making_way: "让路中" };
  const task = v.task
    ? `${v.task.type === "inbound" ? "入库" : "出库"} #${v.task.id} (${v.task.pickup}→${v.task.dropoff})`
    : "无";
  const pct = Math.round(v.battery / 10);
  document.getElementById("vehicle-info").innerHTML =
    `编号 <b>#${v.id}</b> · ${stateNames[v.state] || v.state}<br>` +
    `位置 (${v.cell}) · 电量 ${v.battery} (${pct}%)${v.need_charge ? " ⚠需充电" : ""}<br>` +
    `已完成任务 ${v.completed} 件<br>当前任务：${task}<br>路径剩余：${v.path ? v.path.length : 0} 格`;
}

document.getElementById("btn-close-vehicle").onclick = () => selectVehicle(null);

// ---- 事件面板 ----
function addEvent(m) {
  events.unshift(m);
  if (events.length > 30) events.pop();
  const kindNames = { stall_detected: "受阻", stall_recovered: "恢复", task_blocked: "任务受阻",
    task_unblocked: "任务恢复", charge_started: "开始充电", charge_done: "充电完成",
    charge_queued: "充电排队" };
  const el = document.getElementById("event-feed");
  el.innerHTML = events.map(e =>
    `<div>tick ${e.tick} · ${kindNames[e.kind] || e.kind}${e.vehicle_id ? ` · 车#${e.vehicle_id}` : ""}${e.detail ? ` (${e.detail})` : ""}</div>`
  ).join("");
}

// ---- 控制面板 ----
const btnPause = document.getElementById("btn-pause");
btnPause.onclick = async () => {
  const r = await api("POST", paused ? "/api/control/resume" : "/api/control/pause");
  if (r.ok) { paused = r.paused; btnPause.textContent = paused ? "恢复" : "暂停"; }
};
document.getElementById("btn-reset-view").onclick = fitView;
document.getElementById("btn-trails").onclick = (e) => {
  showTrails = !showTrails;
  e.target.textContent = showTrails ? "轨迹:开" : "轨迹:关";
};
document.getElementById("btn-reset-sim").onclick = async () => {
  await api("POST", "/api/reset");
  selectVehicle(null);
  toast("仿真已重置");
};

// ---- Toast ----
let toastTimer = null;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2500);
}

// ---- 启动 ----
resize();
fetch("/api/state").then(r => r.json()).then(s => {
  prerenderStatic(s);
  applySnapshot(s);
});
connectWS();
requestAnimationFrame(frame);
