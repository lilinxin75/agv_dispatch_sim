"""REST 控制接口 + WebSocket 状态流（契约 rest-api.md / websocket.md）。

由本模块创建 FastAPI 应用并托管仿真 tick 循环（单进程 asyncio，R5）
与静态前端（章程 II：Python 服务直接托管）。
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config as cfg
from ..sim.engine import Engine
from ..sim.entities import Cell

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def _run_engine(engine: Engine, fast: bool = False):
    interval = 1.0 / cfg.TICK_RATE * (0.1 if fast else 1.0)
    while True:
        t0 = time.perf_counter()
        if not engine.paused:
            engine.tick()
        spent = time.perf_counter() - t0
        await asyncio.sleep(max(0.0, interval - spent))


def create_app(engine: Engine, fast: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(_run_engine(engine, fast))
        yield
        task.cancel()

    app = FastAPI(title="RCS Multi-Vehicle Dispatch Demo", version="1.0.0",
                  lifespan=lifespan)

    # ---- REST（契约 rest-api.md）----

    @app.get("/api/state")
    async def get_state():
        return engine.snapshot()

    @app.get("/api/config")
    async def get_config():
        return cfg.all_params()

    @app.get("/api/vehicle/{vid}")
    async def get_vehicle(vid: int):
        d = engine.vehicle_detail(vid)
        if d is None:
            return JSONResponse({"error": "unknown vehicle id"}, status_code=404)
        return d

    @app.post("/api/obstacle")
    async def add_obstacle(body: dict):
        cell = _parse_cell(body)
        if cell is None:
            return JSONResponse({"error": "invalid cell"}, status_code=400)
        ok, reason = engine.add_obstacle(cell)
        if not ok:
            return JSONResponse({"error": reason}, status_code=409)
        return {"ok": True, "cell": list(cell)}

    @app.delete("/api/obstacle")
    async def remove_obstacle(body: dict):
        cell = _parse_cell(body)
        if cell is None:
            return JSONResponse({"error": "invalid cell"}, status_code=400)
        if not engine.remove_obstacle(cell):
            return JSONResponse({"error": "no obstacle at cell"}, status_code=404)
        return {"ok": True}

    @app.post("/api/control/pause")
    async def pause():
        engine.paused = True
        return {"ok": True, "paused": True}

    @app.post("/api/control/resume")
    async def resume():
        engine.paused = False
        return {"ok": True, "paused": False}

    @app.post("/api/reset")
    async def reset():
        engine.reset()
        return {"ok": True}

    # ---- WebSocket（契约 websocket.md）----

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        watched: int | None = None
        last_tick = engine.tick_n
        try:
            await ws.send_text(json.dumps({"type": "snapshot", **engine.snapshot()},
                                          ensure_ascii=False))
            while True:
                # 非阻塞收客户端消息（watch / ping）
                try:
                    msg = await asyncio.wait_for(ws.receive_text(), timeout=0.02)
                    data = json.loads(msg)
                    if data.get("type") == "watch":
                        watched = data.get("vehicle_id")
                        if watched is not None:
                            detail = engine.vehicle_detail(watched)
                            if detail is not None:
                                await ws.send_text(json.dumps(
                                    {"type": "vehicle_detail", "vehicle": detail},
                                    ensure_ascii=False))
                    elif data.get("type") == "ping":
                        await ws.send_text(json.dumps(
                            {"type": "pong", "tick": engine.tick_n}))
                except asyncio.TimeoutError:
                    pass
                # 增量推送（≤100ms 一条；paused 时仅事件型消息）
                if engine.tick_n != last_tick:
                    last_tick = engine.tick_n
                    await ws.send_text(json.dumps(
                        {"type": "delta", **engine.last_delta()},
                        ensure_ascii=False))
                    if watched is not None:
                        detail = engine.vehicle_detail(watched)
                        if detail is not None:
                            await ws.send_text(json.dumps(
                                {"type": "vehicle_detail", "vehicle": detail},
                                ensure_ascii=False))
                for ev in engine.drain_ws_events():
                    await ws.send_text(json.dumps(ev, ensure_ascii=False))
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            try:
                await ws.close()
            except Exception:
                pass

    # ---- 静态前端（章程 II）----

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True),
              name="static")
    return app


def _parse_cell(body: dict) -> Cell | None:
    cell = body.get("cell") if isinstance(body, dict) else None
    if (isinstance(cell, list) and len(cell) == 2
            and all(isinstance(x, int) and 0 <= x < cfg.GRID for x in cell)):
        return (cell[0], cell[1])
    return None
