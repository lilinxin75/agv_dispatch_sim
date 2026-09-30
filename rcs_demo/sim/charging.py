"""电量模型与集中充电区管理（FR-025/026/029，research.md R10）。

充电行程为车级内部状态（to_charge/charging），不生成 Task、不计 completed_count；
充电位互斥；桩满时在进站通道外排队格有序等待（电量更低者优先出队）；
电量低于临界阈值直接前往充电区入口待命兜底（不电量归零趴窝，SC-010）。
"""
from __future__ import annotations

from .entities import Cell, ChargingArea, Vehicle
from .. import config as cfg


class ChargingManager:
    def __init__(self, area: ChargingArea):
        self.area = area
        self.events: list[tuple[str, int]] = []  # (kind, vehicle_id) 由引擎消费

    # ---- 状态查询 ----

    def free_pile(self) -> Cell | None:
        for pile, owner in self.area.pile_owner.items():
            if owner is None:
                return pile
        return None

    def at_pile(self, vehicle: Vehicle) -> Cell | None:
        for pile, owner in self.area.pile_owner.items():
            if owner == vehicle.id:
                return pile
        return None

    def charge_tick(self, vehicle: Vehicle) -> bool:
        """充电推进；达到返回阈值返回 True（应离开）。"""
        vehicle.battery = min(cfg.BATTERY_FULL,
                              vehicle.battery + cfg.charge_rate_per_tick())
        return vehicle.battery >= cfg.BATTERY_FULL * cfg.CHARGE_RETURN_RATIO

    # ---- 排队管理（FR-026）----

    def enqueue(self, vid: int, battery: float):
        self.area.queue.append(vid)
        self.events.append(("charge_queued", vid))

    def dequeue_for_pile(self, batteries: dict[int, float]) -> tuple[int, Cell] | None:
        """桩空出时取队首：电量更低者优先（FR-026）。"""
        pile = self.free_pile()
        if pile is None or not self.area.queue:
            return None
        best = min(self.area.queue, key=lambda v: batteries.get(v, 0.0))
        self.area.queue.remove(best)
        return best, pile

    def assign_queue_cell(self, vehicle: Vehicle, taken: set[Cell]) -> Cell | None:
        """为排队车分配一个空闲排队格。"""
        for cell in self.area.queue_cells:
            if cell not in taken:
                return cell
        return None

    # ---- 事件消费 ----

    def drain_events(self):
        ev = self.events
        self.events = []
        return ev
