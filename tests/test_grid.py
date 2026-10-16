"""布局约束与障碍物校验（T007 / T036）。"""
from agv_dispatch_sim import config as cfg
from agv_dispatch_sim.sim.entities import CellKind
from agv_dispatch_sim.sim.grid import Grid


def test_grid_dimensions_and_boundary():
    g = Grid()
    assert len(g.kind) == cfg.GRID ** 2
    # 外圈围墙不可通行
    for i in range(cfg.GRID):
        for c in ((0, i), (cfg.GRID - 1, i), (i, 0), (i, cfg.GRID - 1)):
            assert g.kind[c] == CellKind.BOUNDARY
            assert not g.passable(c)


def test_slot_count_2400():
    g = Grid()
    slots = [c for c, k in g.kind.items() if k == CellKind.SLOT]
    assert len(slots) == 2400  # FR-021：点位数不减少


def test_stations_position():
    g = Grid()
    for r in range(95, 105):
        assert g.kind[(r, 1)] == CellKind.INBOUND   # 西侧（col 0 为围墙）
        assert g.kind[(r, 198)] == CellKind.OUTBOUND  # 东侧（col 199 为围墙）


def test_charging_area():
    g = Grid()
    assert len(g.charging.piles) == cfg.CHARGE_PILES  # FR-026：10 充电位
    assert all(g.kind[p] == CellKind.CHARGE_PILE for p in g.charging.piles)
    assert g.charging.entry_lane and g.charging.exit_lane
    assert g.charging.queue_cells
    # 充电位互斥初始化
    assert all(v is None for v in g.charging.pile_owner.values())


def test_ring_road_two_wide():
    g = Grid()
    # 环场主干道 2 格宽（FR-020）：紧贴围墙内侧两圈可通行
    for i in range(1, cfg.GRID - 1):
        for d in (1, 2, cfg.GRID - 2, cfg.GRID - 3):
            assert g.passable((i, d)) and g.passable((d, i))


def test_obstacle_validation():
    g = Grid()
    # 空 floor 可加
    ok, _ = g.can_add_obstacle((5, 5), occupant=None, docked_tote=False)
    assert ok
    # 有车格拒绝（US4 场景 4）
    ok, reason = g.can_add_obstacle((5, 5), occupant=3, docked_tote=False)
    assert not ok and "occupied" in reason
    # 有静置料箱的 slot 拒绝
    slot = g.slots[0]
    ok, reason = g.can_add_obstacle(slot, occupant=None, docked_tote=True)
    assert not ok
    # 边界格拒绝
    ok, _ = g.can_add_obstacle((0, 10), occupant=None, docked_tote=False)
    assert not ok
    # 站点格拒绝
    inbound = g.inbound_cells[0]
    ok, _ = g.can_add_obstacle(inbound, occupant=None, docked_tote=False)
    assert not ok
    # 充电区格（pile/上方连接格/queue）拒绝；entry/exit 段已降级为普通通道可设障
    pile0 = g.charging.piles[0]
    for c in (pile0, (pile0[0] - 1, pile0[1]), g.charging.queue_cells[0]):
        ok, _ = g.can_add_obstacle(c, occupant=None, docked_tote=False)
        assert not ok


def test_obstacle_add_remove_idempotent():
    g = Grid()
    assert g.add_obstacle((5, 5), tick=1) is True
    assert g.add_obstacle((5, 5), tick=2) is False  # 幂等：不重复计数
    assert not g.passable((5, 5))
    assert g.remove_obstacle((5, 5)) is True
    assert g.remove_obstacle((5, 5)) is False  # 重复删除 → 404 语义
    assert g.passable((5, 5))
