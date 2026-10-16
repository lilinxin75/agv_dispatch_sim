"""互堵冲突事件检测与 JSONL 记录 (FR-022/FR-023)。

判定：同一时刻处于等待环中的车辆（互为阻挡、成团互堵）视为一次冲突
事件；单车普通排队（阻挡者不在等待态）不记录。事件从首次出现开启，
至互堵完全解开后闭合，逐行追加写入
``<data_dir>/conflict_events_<批次>.jsonl``。
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from agv_dispatch_sim.sim import entities as E


def find_mutual_blocks(edges: dict[int, int]) -> set[int]:
    """等待边 ``waiter -> blocker`` 中处于等待环内的车辆集合。

    功能图上每个节点至多一条出边；沿链走到已见节点即发现环，
    环上节点全部计入（链尾排队车辆随后由 observe 补齐）。
    """
    result: set[int] = set()
    for start in edges:
        pos: dict[int, int] = {}
        seen: list[int] = []
        cur = start
        while cur in edges and cur not in pos:
            pos[cur] = len(seen)
            seen.append(cur)
            cur = edges[cur]
        if cur in pos:  # 本次游走命中环
            result.update(seen[pos[cur]:])
    return result


class ConflictRecorder:
    """每 tick 观察 Engine，检测互堵成团并落盘 JSONL 事件。"""

    def __init__(self, data_dir: str = "data", enabled: bool = True):
        self.enabled = enabled
        self.data_dir = data_dir
        self.batch = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.open_event: dict | None = None
        self.closed_events: list[dict] = []
        self._path: str | None = None

    # ------------------------------------------------------------ 检测
    def observe(self, eng) -> None:
        if not self.enabled:
            return
        waiting = {
            v.id: v for v in eng.vehicles.values()
            if v.state in (E.V_WAITING, E.V_STALLED)
        }
        edges: dict[int, int] = {}
        for vid, v in waiting.items():
            if not v.path:
                continue
            blocker = eng.grid.occupant_at(*v.path[0])
            if blocker is not None and blocker in waiting and blocker != vid:
                edges[vid] = blocker
        conflicted = find_mutual_blocks(edges)
        if conflicted:
            # 等待链尾：阻挡者已入团的排队车一并计入（成团互堵的成员）
            changed = True
            while changed:
                changed = False
                for vid, blocker in edges.items():
                    if vid not in conflicted and blocker in conflicted:
                        conflicted.add(vid)
                        changed = True
        if conflicted:
            if self.open_event is None:
                self.open_event = {
                    "start_tick": eng.tick, "vehicles": set(),
                    "cells": set(), "carrying": {},
                }
            self.open_event["vehicles"].update(conflicted)
            self.open_event["carrying"].update(
                (i, eng._carrying(eng.vehicles[i])) for i in conflicted
            )
            self.open_event["cells"].update(
                eng.vehicles[i].cell for i in conflicted
            )
        elif self.open_event is not None:
            self._close(eng.tick)

    def _close(self, end_tick: int) -> None:
        ev = self.open_event
        self.open_event = None
        record = {
            "batch": self.batch,
            "start_tick": ev["start_tick"],
            "end_tick": end_tick,
            "duration": end_tick - ev["start_tick"],
            "vehicles": sorted(
                (
                    {"id": vid, "carrying": ev["carrying"].get(vid, False)}
                    for vid in ev["vehicles"]
                ),
                key=lambda x: x["id"],
            ),
            "cells": sorted(list(c) for c in ev["cells"]),
        }
        self.closed_events.append(record)
        self._append(record)

    # ------------------------------------------------------------ 落盘
    def _append(self, record: dict) -> None:
        if self._path is None:
            os.makedirs(self.data_dir, exist_ok=True)
            self._path = os.path.join(
                self.data_dir, f"conflict_events_{self.batch}.jsonl"
            )
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @property
    def total_closed(self) -> int:
        return len(self.closed_events)
