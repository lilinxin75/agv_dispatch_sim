"""A* 与预约表测试 (T006/T024)。"""

from rcs_demo.config import Config
from rcs_demo.sim.grid import Grid
from rcs_demo.sim.planner import Reservations, astar


def make_grid():
    return Grid(Config.scaled())


def test_path_found_and_adjacent():
    g = make_grid()
    path = astar(g, (5, 5), (5, 10))
    assert path is not None
    assert path[0] == (5, 6)  # 不含起点
    assert path[-1] == (5, 10)
    assert len(path) == 5
    # 4 邻接：每步仅差一格
    prev = (5, 5)
    for cell in path:
        assert abs(cell[0] - prev[0]) + abs(cell[1] - prev[1]) == 1
        prev = cell


def test_unreachable_returns_none():
    g = make_grid()
    # 用障碍物把 (5,5) 围死（远离围墙）
    for r, c in [(4, 4), (4, 5), (4, 6), (5, 4), (5, 6),
                 (6, 4), (6, 5), (6, 6)]:
        g.add_obstacle(r, c)
    assert astar(g, (5, 5), (20, 20)) is None
    assert astar(g, (20, 20), (5, 5)) is None  # 目标不可达同样返回 None


def test_obstacle_avoided():
    g = make_grid()
    g.add_obstacle(5, 7)
    path = astar(g, (5, 5), (5, 10))
    assert path is not None
    assert (5, 7) not in path


def test_reservation_mutex():
    res = Reservations()
    assert res.claim(5, 5, 10, 1) is True
    assert res.claim(5, 5, 10, 2) is False  # 同格同 tick 互斥
    assert res.claim(5, 5, 10, 1) is True   # 同车重复声明 OK
    assert res.claim(5, 5, 11, 2) is True   # 不同 tick OK（跟随语义）
    assert res.held_by(5, 5, 10) == 1


def test_astar_avoids_others_reservation():
    g = make_grid()
    res = Reservations()
    # 他车预约了直线路径上的格子
    res.data[(5, 7, 1)] = 99
    path = astar(g, (5, 5), (5, 10), reservations=res, now=0,
                 horizon=5, self_id=1)
    assert path is not None
    assert (5, 7) not in path
    # 自己的预约不阻挡自己
    res2 = Reservations()
    res2.data[(5, 7, 1)] = 1
    path2 = astar(g, (5, 5), (5, 10), reservations=res2, now=0,
                  horizon=5, self_id=1)
    assert path2 is not None
