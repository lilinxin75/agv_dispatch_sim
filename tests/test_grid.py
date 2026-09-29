"""布局与障碍物约束测试 (T011/T030)。"""

import pytest

from rcs_demo.config import Config
from rcs_demo.sim.engine import Engine
from rcs_demo.sim.grid import CellKind, CollisionError, Grid, ObstacleError


def full_cfg():
    return Config()


def scaled_cfg():
    return Config.scaled()


def test_full_layout_counts():
    g = Grid(full_cfg())
    assert len(g.slots) == 2400
    assert len(g.inbound_cells) == 50
    assert len(g.outbound_cells) == 50
    assert g.kind_at(60, 40) == CellKind.SLOT
    assert g.kind_at(95, 2) == CellKind.INBOUND
    assert g.kind_at(95, 197) == CellKind.OUTBOUND


def test_boundary_not_passable():
    g = Grid(full_cfg())
    # 围墙 2 格厚（FR-020）
    for cell in [(0, 0), (0, 100), (199, 199), (100, 199), (100, 0),
                 (1, 1), (1, 100), (100, 1), (198, 100), (100, 198)]:
        assert not g.is_passable(*cell)
    # 环场主干道（2 格宽）可通行
    assert g.is_passable(2, 2)
    assert g.is_passable(2, 100)
    assert g.is_passable(197, 100)
    assert g.is_passable(100, 2)
    assert g.is_passable(100, 197)
    assert g.is_passable(30, 30)  # 货架区外的通道


def test_no_vehicle_reaches_outer_ring():
    """任何车不得出现在距画布边缘 2 格以内 (FR-020)。"""
    eng = Engine(scaled_cfg(), seed=7)
    for _ in range(300):
        eng.step()
    n = eng.grid.size
    for v in eng.vehicles.values():
        r, c = v.cell
        assert r >= 2 and c >= 2 and r <= n - 3 and c <= n - 3, (
            f"vehicle {v.id} too close to edge: {v.cell}"
        )


def test_occupant_mutex():
    g = Grid(scaled_cfg())
    g.set_occupant(5, 5, 1)
    with pytest.raises(CollisionError):
        g.set_occupant(5, 5, 2)
    g.set_occupant(5, 5, None)
    g.set_occupant(5, 5, 2)  # 释放后可再占


def test_initial_half_fill():
    eng = Engine(scaled_cfg(), seed=7)
    docked_slots = sum(
        1 for t in eng.totes.values()
        if t.state == "docked" and eng.grid.kind_at(*t.location) == CellKind.SLOT
    )
    assert docked_slots == int(len(eng.grid.slots) * 0.5)


# ------------------------------------------------------------- 障碍物校验
def test_obstacle_reject_vehicle_cell():
    eng = Engine(scaled_cfg(), seed=3)
    v = next(iter(eng.vehicles.values()))
    with pytest.raises(ObstacleError):
        eng.add_obstacle(*v.cell)


def test_obstacle_reject_slot_with_tote():
    eng = Engine(scaled_cfg(), seed=3)
    slot_with_tote = next(
        t.location for t in eng.totes.values()
        if t.state == "docked" and eng.grid.kind_at(*t.location) == CellKind.SLOT
    )
    with pytest.raises(ObstacleError):
        eng.add_obstacle(*slot_with_tote)


def test_obstacle_reject_boundary_and_stations():
    eng = Engine(scaled_cfg(), seed=3)
    with pytest.raises(ObstacleError):
        eng.add_obstacle(0, 10)  # 边界外墙
    with pytest.raises(ObstacleError):
        eng.add_obstacle(1, 10)  # 围墙第二圈
    with pytest.raises(ObstacleError):
        eng.add_obstacle(*eng.grid.inbound_cells[0])
    with pytest.raises(ObstacleError):
        eng.add_obstacle(*eng.grid.outbound_cells[0])


def test_obstacle_idempotent_add_and_remove():
    eng = Engine(scaled_cfg(), seed=3)
    cell = (2, 2)
    assert eng.add_obstacle(*cell) is True
    assert eng.add_obstacle(*cell) is False  # 幂等
    assert eng.grid.is_passable(*cell) is False
    assert eng.remove_obstacle(*cell) is True
    assert eng.grid.is_passable(*cell) is True
    with pytest.raises(ObstacleError):
        eng.remove_obstacle(*cell)  # 重复删除报错
