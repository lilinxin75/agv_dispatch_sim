"""入口：python -m rcs_demo [--check] [--port N] [--seed N] [--fast]。

--check 自检（章程 V / quickstart.md）：固定布局生成、A* 可达性抽检、
预约表互斥自测、API 端口可绑定。
"""
from __future__ import annotations

import argparse
import socket
import sys


def _check(port: int) -> int:
    """启动自检，全部通过输出 OK 并返回 0。"""
    from . import config as cfg
    from .sim.entities import CellKind
    from .sim.grid import Grid
    from .sim.planner import Planner, ReservationTable

    # 1. 固定布局生成
    grid = Grid()
    n_slots = sum(1 for k in grid.kind.values() if k == CellKind.SLOT)
    assert cfg.GRID == 200 and len(grid.kind) == cfg.GRID ** 2, "网格尺寸错误"
    assert n_slots == 2400, f"存储点位应为 2400，实际 {n_slots}"
    assert len(grid.inbound_cells) > 0 and len(grid.outbound_cells) > 0, "站点缺失"
    assert len(grid.charging.piles) == cfg.CHARGE_PILES, "充电位数量错误"
    assert all(grid.kind[p] == CellKind.CHARGE_PILE for p in grid.charging.piles)
    assert grid.charging.entry_lane and grid.charging.exit_lane, "进/出站通道缺失"
    print(f"  grid layout OK ({cfg.GRID}x{cfg.GRID}, {n_slots} slots, "
          f"{cfg.CHARGE_PILES} piles)")

    # 2. A* 可达性抽检
    engine_side_probe = Grid()  # 与引擎同构布局
    planner = Planner(grid, lambda c: False)
    start = (2, 2)
    for goal in [(2, 197), (197, 2), (99, 99), (96, 2), (96, 197)]:
        path = planner.plan(start, goal, "E", False, set())
        assert path is not None, f"A* 不可达: {start} -> {goal}"
        # 环场约束：路径不得使用最外圈贴边格（FR-020）
        assert all(0 < r < cfg.GRID - 1 and 0 < c < cfg.GRID - 1
                   for r, c in path), "路径越界"
    print("  planner A* probe OK (5 targets reachable)")

    # 3. 预约表互斥自测
    rt = ReservationTable()
    assert rt.try_reserve((10, 10), vid=1)
    assert not rt.try_reserve((10, 10), vid=2), "预约互斥失败"
    assert rt.try_reserve((10, 10), vid=1), "同车重复预约应成功"
    print("  reservation mutex OK")

    # 4. API 端口可绑定
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
    print(f"  api port {port} bindable OK")
    print("OK: grid layout / planner / engine self-test passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="rcs_demo",
                                 description="RCS 多车料箱搬运调度演示")
    ap.add_argument("--check", action="store_true", help="启动自检后退出")
    ap.add_argument("--port", type=int, default=8000, help="HTTP 端口（默认 8000）")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址")
    ap.add_argument("--seed", type=int, default=None, help="固定随机种子复现场景")
    ap.add_argument("--fast", action="store_true", help="仿真加速（10 倍时钟）")
    args = ap.parse_args()

    if args.check:
        try:
            code = _check(args.port)
        except AssertionError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 1
        return code

    import uvicorn

    from .api.routes import create_app
    from .sim.engine import Engine

    engine = Engine(seed=args.seed)
    app = create_app(engine, fast=args.fast)
    print(f"RCS demo serving on http://{args.host}:{args.port} "
          f"(Ctrl+C 停止；浏览器打开 http://localhost:{args.port})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
