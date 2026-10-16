"""规划器：A* 可达/绕障、预约互斥、转弯代价（T028）。"""
from agv_dispatch_sim.sim.grid import Grid
from agv_dispatch_sim.sim.planner import Planner, ReservationTable


def make_planner(docked=()):
    g = Grid()
    p = Planner(g, lambda c: c in set(docked))
    p.docked = set(docked)  # 热路径集合（与引擎同构）
    return g, p


def test_reachability_and_shortest():
    g, p = make_planner()
    path = p.plan((2, 2), (2, 50), "E", False, set())
    assert path is not None and len(path) == 48  # 曼哈顿最短
    assert path[-1] == (2, 50)


def test_obstacle_avoidance():
    g, p = make_planner()
    for r in range(1, 6):
        g.add_obstacle((r, 10), tick=0)  # 竖墙阻断直线
    path = p.plan((3, 5), (3, 15), "E", False, set())
    assert path is not None
    assert all((r, c) not in g.obstacles for r, c in path)  # 绝不穿障（FR-011）
    assert len(path) > 10  # 绕行变长


def test_unreachable_returns_none():
    g, p = make_planner()
    for c in range(0, 200):  # 完全隔断左右
        g.add_obstacle((100, c), tick=0) if g.kind.get((100, c)) not in (None,) else None
    path = p.plan((50, 50), (150, 50), "E", False, set())
    assert path is None


def test_carried_tote_cannot_enter_docked_slot():
    g, p = make_planner(docked=[(50, 50)])
    # 载货车不得进入静置料箱格（FR-015）
    assert p.plan((50, 48), (50, 50), "E", True, set()) is None
    # 无载车可穿行
    assert p.plan((50, 48), (50, 52), "E", False, set()) is not None
    # 载货车可绕行到料箱后方
    assert p.plan((50, 48), (50, 52), "E", True, set()) is not None


def test_avoids_occupied_cells():
    g, p = make_planner()
    occupied = {(10, 10)}
    path = p.plan((10, 8), (10, 12), "E", False, occupied)
    assert path is not None
    assert (10, 10) not in path


def test_turn_penalty_prefers_straight():
    g, p = make_planner()
    # 同起终点：直达直线 vs 强制绕行后同长度路径，直线代价更低
    straight = p.plan((20, 20), (40, 40), "E", False, set())
    assert straight is not None
    # 起终点同距但更少拐弯的路径长度（代价含转弯惩罚）由 A* 直接体现：
    # 直线路径拐弯次数 ≤ 1（先直行后一次转向）
    turns = 0
    d = None
    for (r1, c1), (r2, c2) in zip([(20, 20)] + straight[:-1], straight):
        nd = (r2 - r1, c2 - c1)
        if d is not None and nd != d:
            turns += 1
        d = nd
    assert turns <= 1


def test_reservation_mutex():
    rt = ReservationTable()
    assert rt.try_reserve((10, 10), vid=1)
    assert not rt.try_reserve((10, 10), vid=2)  # 同格同刻双预约必失败（quickstart）
    assert rt.try_reserve((10, 10), vid=1)      # 同车重入成功
    assert rt.owner((10, 10)) == 1
