"""FastAPI 应用：REST 控制接口 + WebSocket 状态流 (contracts/)。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import pathlib

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from rcs_demo.sim.engine import Engine
from rcs_demo.sim.grid import ObstacleError

STATIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "static"


class Connection:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.watch_vehicle: int | None = None
        self.lock = asyncio.Lock()


def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="RCS Multi-Vehicle Dispatch Demo")
    app.state.engine = engine
    connections: set[Connection] = set()
    loop_task: asyncio.Task | None = None

    # ------------------------------------------------------------ REST
    @app.get("/api/state")
    async def get_state():
        return engine.snapshot()

    @app.post("/api/obstacle")
    async def add_obstacle(body: dict):
        cell = body.get("cell")
        if (
            not isinstance(cell, list)
            or len(cell) != 2
            or not all(isinstance(x, int) for x in cell)
        ):
            return JSONResponse({"error": "cell must be [row, col]"}, 400)
        try:
            added = engine.add_obstacle(cell[0], cell[1])
        except ObstacleError as exc:
            return JSONResponse({"error": str(exc)}, 409)
        await broadcast_obstacle(added=[cell] if added else [])
        return {"ok": True, "cell": cell}

    @app.delete("/api/obstacle")
    async def remove_obstacle(body: dict):
        cell = body.get("cell")
        if (
            not isinstance(cell, list)
            or len(cell) != 2
            or not all(isinstance(x, int) for x in cell)
        ):
            return JSONResponse({"error": "cell must be [row, col]"}, 400)
        try:
            removed = engine.remove_obstacle(cell[0], cell[1])
        except ObstacleError as exc:
            return JSONResponse({"error": str(exc)}, 404)
        await broadcast_obstacle(removed=[cell] if removed else [])
        return {"ok": True}

    @app.post("/api/control/pause")
    async def pause():
        engine.paused = True
        return {"ok": True, "paused": True}

    @app.post("/api/control/resume")
    async def resume():
        engine.paused = False
        return {"ok": True, "paused": False}

    @app.get("/api/vehicle/{vid}")
    async def vehicle(vid: int):
        detail = engine.vehicle_detail(vid)
        if detail is None:
            return JSONResponse({"error": f"unknown vehicle {vid}"}, 404)
        return detail["vehicle"]

    @app.post("/api/reset")
    async def reset():
        new_engine = Engine(engine.cfg, seed=None)
        engine.__dict__.update(new_engine.__dict__)
        for conn in list(connections):
            with contextlib.suppress(Exception):
                await send_json(conn, {"type": "snapshot",
                                       **engine.snapshot()})
        return {"ok": True}

    # ------------------------------------------------------------ WS 广播
    async def send_json(conn: Connection, payload: dict) -> None:
        async with conn.lock:
            await conn.ws.send_text(json.dumps(payload, separators=(",", ":")))

    async def broadcast(payload: dict) -> None:
        for conn in list(connections):
            with contextlib.suppress(Exception):
                await send_json(conn, payload)

    async def broadcast_obstacle(added=(), removed=()):
        if added or removed:
            await broadcast({
                "type": "obstacle_changed", "tick": engine.tick,
                "added": [list(a) for a in added],
                "removed": [list(r) for r in removed],
            })

    async def sim_loop(tick_interval: float) -> None:
        while True:
            if not engine.paused:
                engine.step()
                delta = engine.pop_delta()
                events = engine.pop_events()
                await broadcast(delta)
                for evt in events:
                    await broadcast(evt)
                # 订阅中的单车详情推送
                for conn in list(connections):
                    if conn.watch_vehicle is not None:
                        detail = engine.vehicle_detail(conn.watch_vehicle)
                        if detail is not None:
                            with contextlib.suppress(Exception):
                                await send_json(conn, detail)
            await asyncio.sleep(tick_interval)

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        nonlocal loop_task
        await ws.accept()
        conn = Connection(ws)
        connections.add(conn)
        try:
            await send_json(conn, {"type": "snapshot", **engine.snapshot()})
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                mtype = msg.get("type")
                if mtype == "watch":
                    conn.watch_vehicle = msg.get("vehicle_id")
                    if conn.watch_vehicle is not None:
                        detail = engine.vehicle_detail(conn.watch_vehicle)
                        if detail is not None:
                            await send_json(conn, detail)
                elif mtype == "ping":
                    await send_json(conn, {"type": "pong", "tick": engine.tick})
        except WebSocketDisconnect:
            pass
        finally:
            connections.discard(conn)

    @app.on_event("startup")
    async def startup():
        nonlocal loop_task
        interval = 1.0 / (engine.cfg.tick_rate *
                          (engine.cfg.fast_multiplier if engine.cfg.fast else 1))
        loop_task = asyncio.create_task(sim_loop(interval))

    @app.on_event("shutdown")
    async def shutdown():
        if loop_task is not None:
            loop_task.cancel()

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True),
              name="static")
    return app
