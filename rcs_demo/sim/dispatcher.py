"""任务生成（1:1 成对）、least-loaded 均匀分配与死锁恢复 (research.md R3/R4)。"""

from __future__ import annotations

from rcs_demo.sim import entities as E
from rcs_demo.sim.planner import astar


class Dispatcher:
    """无状态策略对象：全部状态挂在 Engine 上。"""

    # ------------------------------------------------------------ 任务生成
    def generate(self, eng) -> None:
        """每 task_interval tick 尝试成对生成一组入库+出库任务 (FR-018)。

        both-or-neither：两侧资源齐备才成对生成，保证严格 1:1。
        """
        if eng.tick % eng.cfg.task_interval != 0:
            return
        if len(eng.task_queue) >= eng.cfg.max_queued_tasks:
            return
        if eng.cfg.conflict_optimization:
            # 拥堵背压：等待车辆过多时暂停生成，先消化存量车流 (FR-024)
            waiting = sum(
                1 for v in eng.vehicles.values()
                if v.state in (E.V_WAITING, E.V_STALLED)
            )
            if waiting > max(2, int(len(eng.vehicles) * 0.2)):
                return

        out_tote = eng.pick_outbound_tote()
        if out_tote is None:
            return
        out_cell = eng.pick_free_outbound_cell()
        if out_cell is None:
            return
        in_tote = eng.pick_inbound_tote()
        if in_tote is None:
            return
        slot = eng.pick_free_slot()
        if slot is None:
            return

        t_out = eng.create_task(E.OUTBOUND, out_tote.location, out_cell, out_tote)
        t_in = eng.create_task(E.INBOUND, in_tote.location, slot, in_tote)
        if t_out is None or t_in is None:  # 声明冲突等极端情况
            return

    # ------------------------------------------------------------ 任务分配
    def assign(self, eng) -> None:
        """least-loaded + 均衡节流：累计分配数最少的车优先，
        且不得超过全体均值 + 容差（FR-004 的 ±20% 保证）。

        节流不会卡死分配：全体最小值恒 ≤ 均值，只要最少先完成
        手头任务即可继续；队列在此期间保持任务不丢失。
        """
        if not eng.task_queue:
            return
        idle = [
            v for v in eng.vehicles.values()
            if v.task_id is None and v.state in (E.V_IDLE, E.V_WAITING)
            and not v.making_way
        ]
        totals = [v.assigned_total for v in eng.vehicles.values()]
        mean_total = sum(totals) / len(totals)
        min_total = min(totals)
        tolerance = max(1.0, 0.15 * mean_total)
        still_queued = []
        for tid in eng.task_queue:
            task = eng.tasks[tid]
            if task.state != E.K_QUEUED:
                continue
            if not idle:
                still_queued.append(tid)
                continue
            best = min(idle, key=lambda v: (v.assigned_total,))
            if best.assigned_total > min_total + tolerance:
                still_queued.append(tid)  # 领先太多，等落后车空出来
                continue
            lowest = best.assigned_total
            pool = [v for v in idle if v.assigned_total == lowest]
            if eng.cfg.conflict_optimization:
                # 拥堵感知指派：同负载池中取距取货点最近的车，
                # 减少跨图对角车流 (FR-024 任务安排优化)
                veh = min(
                    pool,
                    key=lambda v: (
                        abs(v.cell[0] - task.pickup[0])
                        + abs(v.cell[1] - task.pickup[1]),
                    ),
                )
            else:
                veh = eng.rnd.choice(pool)
            idle.remove(veh)
            path = eng._astar(veh, task.pickup)
            if path is None:
                self._block(eng, task, veh, "pickup unreachable")
                still_queued.append(tid)
                continue
            task.state = E.K_ASSIGNED
            task.assigned_vehicle = veh.id
            veh.task_id = task.id
            veh.assigned_total += 1
            veh.state = E.V_TO_PICKUP
            veh.target = task.pickup
            veh.path = path
            veh.stall_ticks = 0
            eng.touch_vehicle(veh.id)
        eng.task_queue = still_queued

    def _block(self, eng, task, veh, reason: str) -> None:
        task.state = E.K_BLOCKED
        eng.blocked.add(task.id)
        if veh.task_id == task.id:
            veh.task_id = None
            veh.state = E.V_IDLE
            veh.target = None
            veh.path = []
        eng.push_event("task_blocked", detail=f"task {task.id}: {reason}")

    def unblock_all(self, eng) -> int:
        """障碍物删除后重试全部 blocked 任务 (US4/T035)。"""
        ids = list(eng.blocked)
        for tid in ids:
            eng.blocked.discard(tid)
            eng.tasks[tid].state = E.K_QUEUED
            eng.task_queue.append(tid)
            eng.push_event("task_unblocked", detail=f"task {tid} retried")
        return len(ids)

    # ------------------------------------------------------------ 死锁恢复
    def recovery(self, eng) -> None:
        """恢复策略：级联让路 + 三层兜底（重规划/让路/改派）(research.md R3)。

        级联让路：等待超过 proactive 阈值的车辆，可要求挡在面前的
        **任何等待中的车**（含任务车）向邻近空格侧移——等待链像拉链
        一样从头解开；点位格不阻挡穿行，侧移几乎总有落脚点。
        """
        thr = eng.cfg.stall_threshold
        if eng.cfg.conflict_optimization:
            # 拥堵感知：更早触发级联让路，缩短互堵持续时间 (SC-009)
            # 绝对上限 10 tick，避免大阈值地图上成团时间过长
            proactive = max(3, min(10, thr // 8))
        else:
            proactive = max(5, thr // 5)
        for veh in list(eng.vehicles.values()):
            if veh.state == E.V_WAITING and veh.stall_ticks >= proactive:
                if veh.stall_ticks % proactive == 0:
                    self._cascading_make_way(eng, veh)
            s = veh.stall_ticks
            if s < thr or s % thr != 0:
                continue
            stage = s // thr  # 1: replan, 2: make-way, 3: reassign
            if stage == 1:
                self._replan(eng, veh)
            elif stage == 2:
                self._make_way(eng, veh)
            else:
                self._reassign(eng, veh)

    def _cascading_make_way(self, eng, veh) -> None:
        if not veh.path:
            return
        blocker_id = eng.grid.occupant_at(*veh.path[0])
        if blocker_id is None:
            return
        blocker = eng.vehicles[blocker_id]
        if blocker.state != E.V_WAITING or blocker.making_way:
            return
        self._sidestep(eng, blocker, veh.id)

    def _replan(self, eng, veh) -> None:
        target = veh.target
        if target is None or not eng.grid.is_passable(*target):
            target = eng.random_target()
        if target is None:
            return
        path = eng._astar(veh, target)
        if path:
            veh.path = path
            eng.touch_vehicle(veh.id)

    def _make_way(self, eng, veh) -> None:
        """让挡路车向邻近空格 sidestep（兜底层，不限挡路车状态）。"""
        if not veh.path:
            return
        blocker_id = eng.grid.occupant_at(*veh.path[0])
        if blocker_id is None:
            return
        self._sidestep(eng, eng.vehicles[blocker_id], veh.id)

    def _sidestep(self, eng, blocker, requester: int) -> None:
        if blocker.making_way:
            return
        side = eng.free_adjacent(
            blocker.cell, avoid_totes=eng._carrying(blocker),
        )
        if side is None:
            return
        blocker.path = [side]
        blocker.target = side
        blocker.state = E.V_MAKING_WAY
        blocker.making_way = True
        blocker.stall_ticks = 0
        eng.push_event(
            "make_way", vehicle_id=blocker.id,
            detail=f"sidestep for vehicle {requester}",
        )
        eng.touch_vehicle(blocker.id)

    def _reassign(self, eng, veh) -> None:
        """兜底：任务回收重派 / 漫游车换目标。"""
        if veh.task_id is not None:
            task = eng.tasks[veh.task_id]
            task.state = E.K_QUEUED
            task.assigned_vehicle = None
            eng.task_queue.append(task.id)
            veh.task_id = None
        veh.target = None
        veh.path = []
        veh.state = E.V_IDLE
        veh.stall_ticks = 0
        eng.push_event(
            "stall_recovered", vehicle_id=veh.id,
            detail="reassigned after deadlock",
        )
        eng.touch_vehicle(veh.id)
