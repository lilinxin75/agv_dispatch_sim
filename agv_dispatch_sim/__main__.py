"""入口：python -m agv_dispatch_sim [--check] [--layout NAME] [--port N] [--seed N] [--fast]。

--check 自检（章程 V / quickstart.md）：配置矛盾校验、布局生成（含矢量性）、
A* 可达性抽检、预约表互斥自测、API 端口可绑定。
"""
from __future__ import annotations

import argparse
import socket
import sys


def _check_layout(name: str) -> None:
    """单布局全量校验（002 T007/T026）：建图 + 可达性 + 矢量性 + 待命容量。"""
    from . import config as cfg
    from .sim.entities import CellKind
    from .sim.grid import Grid, load_profile
    from .sim.planner import Planner

    profile = load_profile(name) if name != "baseline" else None
    grid = Grid(profile)
    n_slots = sum(1 for k in grid.kind.values() if k == CellKind.SLOT)
    assert n_slots > 0 and grid.inbound_cells and grid.outbound_cells, "站点/点位缺失"
    assert len(grid.charging.piles) == grid.pile_count, "充电位数量与布局不符"
    assert grid.charging.entry_lane and grid.charging.exit_lane, "进/出站通道缺失"
    assert all(grid.kind[p] == CellKind.CHARGE_PILE for p in grid.charging.piles)

    # A* 可达性抽检（围墙内四角 + 地图中心）
    planner = Planner(grid, lambda c: False)
    n = grid.size
    start = (2, 2)
    for goal in [(2, n - 3), (n - 3, 2), (n // 2, n // 2), (n // 2, 2), (n // 2, n - 3)]:
        path = planner.plan(start, goal, "E", False, set())
        assert path is not None, f"A* 不可达: {start} -> {goal}"
        assert all(0 < r < n - 1 and 0 < c < n - 1 for r, c in path), "路径越界"

    # 充电桩矢量性（FR-008 倒车入位，2026-10-01 修订）
    for pile in grid.charging.piles:
        conn = (pile[0] - 1, pile[1])
        assert grid.kind[conn] == CellKind.CHARGE_IN, "上方连接格缺失"
        # 入桩：仅自北向南（经上方连接格）、且必须为目标
        assert grid.can_traverse(conn, pile, to_is_goal=True), "自上方连接格入桩应可行"
        assert not grid.can_traverse((pile[0], pile[1] - 1), pile, to_is_goal=True), \
            "自西侧入桩必须被拒"
        assert not grid.can_traverse((pile[0] + 1, pile[1]), pile, to_is_goal=True), \
            "自南侧入桩必须被拒"
        assert not grid.can_traverse(conn, pile, to_is_goal=False), "穿越桩格必须被拒"
        # 离桩：仅向北原路倒出
        assert grid.can_traverse(pile, conn), "自桩向北倒出应可行"
        assert not grid.can_traverse(pile, (pile[0], pile[1] + 1)), "自桩南离必须被拒"
        # 到桩可达（上方进桩通道存在路径）
        assert planner.plan_any_dir(conn, pile, False, set()) is not None, "到桩不可达"
        # 上方进桩通道可达每个桩位（从通道西端起规划）
        approach = (pile[0] - 2, grid.charge_cols[0] + 1)
        assert planner.plan_any_dir(approach, pile, False, set()) is not None, \
            f"上方进桩通道到桩 {pile} 不可达"
    # 上方进桩通道为普通双向通道：东西两端均可进入
    approach_row = grid.charging.approach_row
    c0, c1 = grid.charge_cols
    for start_col, d in ((c0 - 4, "E"), (c1 + 2, "W")):
        start = (approach_row, start_col)
        goal = (approach_row, c0 + 5)
        assert planner.plan_any_dir(start, goal, False, set()) is not None, \
            f"上方进桩通道自 {start} 不可达（双向通道被破坏）"
    # 待命区容量（FR-014）：空槽位数 ≥ 车辆数（空闲峰值需求上界）
    assert len(grid.slots) >= cfg.VEHICLES, \
        f"待命容量不足：slots {len(grid.slots)} < vehicles {cfg.VEHICLES}"
    print(f"  layout '{name}' OK ({grid.size}x{grid.size}, {n_slots} slots, "
          f"{grid.pile_count} piles, vector constraints valid)")


def _check(port: int, layout: str | None) -> int:
    """启动自检，全部通过输出 OK 并返回 0。"""
    from . import config as cfg
    from .sim.planner import ReservationTable

    errs = cfg.validate()
    assert not errs, "配置校验失败: " + "; ".join(errs)
    print("  config validate OK")

    names = cfg.available_layouts() if layout is None else [layout]
    for name in names:
        _check_layout(name)

    # 预约表互斥自测
    rt = ReservationTable()
    assert rt.try_reserve((10, 10), vid=1)
    assert not rt.try_reserve((10, 10), vid=2), "预约互斥失败"
    assert rt.try_reserve((10, 10), vid=1), "同车重复预约应成功"
    print("  reservation mutex OK")

    # API 端口可绑定
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
    print(f"  api port {port} bindable OK")
    print("OK: grid layout / planner / engine self-test passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="agv_dispatch_sim",
                                 description="AGV 多车料箱搬运调度仿真")
    ap.add_argument("--check", action="store_true", help="启动自检后退出")
    ap.add_argument("--layout", default=None,
                    help="布局预设（默认取 AGV_LAYOUT，再默认 baseline）；"
                         "与 --check 连用时缺省校验全部预设")
    ap.add_argument("--port", type=int, default=8000, help="HTTP 端口（默认 8000）")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址")
    ap.add_argument("--seed", type=int, default=None, help="固定随机种子复现场景")
    ap.add_argument("--fast", action="store_true", help="仿真加速（10 倍时钟）")
    args = ap.parse_args()

    if args.check:
        try:
            code = _check(args.port, args.layout)
        except AssertionError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 1
        except ValueError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 1
        return code

    import uvicorn

    from . import config as cfg
    from .api.routes import create_app
    from .sim.engine import Engine
    from .sim.grid import load_profile

    name = args.layout or cfg.LAYOUT
    layout = load_profile(name) if name != "baseline" else None
    engine = Engine(seed=args.seed, layout=layout)
    app = create_app(engine, fast=args.fast)
    print(f"agv-dispatch-sim serving on http://{args.host}:{args.port} "
          f"(layout={name}; Ctrl+C 停止；浏览器打开 http://localhost:{args.port})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
