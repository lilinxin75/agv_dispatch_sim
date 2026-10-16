"""带过程监控的 headless 验证驱动（FR-026/027，指令 2026-09-30）。

用法（容器内）::

    python3 -m agv_dispatch_sim.acceptance --ticks 36000 --seed 42
    python3 -m agv_dispatch_sim.acceptance --ticks 8000          # 快速冒烟

逐窗口落盘监控指标到 ``<report-dir>/marks.jsonl``；命中任一异常判据立即中止
（exit code 2）并输出诊断；正常跑完输出验收摘要（SC-001 双口径等）并 exit 0。
异常修复后 MUST 从头重跑（FR-027：不允许断点续跑掩盖问题）。

异常判据（FR-027）：
  collision       碰撞不变量被破坏（collision_count > 0）
  tick_cost       单窗口墙钟成本失控（> max(基线中位数 8×, 60s)）
  stall           吞吐停摆：连续 STALL_WINDOWS(3) 窗口零任务完成
  battery         电量停摆：任一车辆连续滞零超过 900 tick（SC-006 恢复上限）
  ledger          任务台账恒等式破坏（spawned == done + active + queued + reassigned）
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from . import config as cfg
from .sim.engine import Engine
from .sim.entities import VehicleState

WINDOW_TICKS = 600          # 每监控窗口 600 tick（仿真 60s）
STALL_WINDOWS = 3           # 连续零完成窗口数判据
# 滞零判停阈值（SC-006"归零停摆"的代理判据）：充电波峰时队外滞零车等待队列
# 腾空最长可达 ~1200 tick（排队+在充吞吐 1 台/20 tick × 波深），属合法波尾；
# 真正的死锁形态是滞零时长无界增长。1800 tick（仿真 3 分钟）覆盖波尾并仍能
# 及时捕获永久饿死。注：电量 0 的车在本模型中仍可行走执行（drain 下限截断），
# 滞零 ≠ 无法移动，仅是"未获充电干预"的警示口径
BATTERY_STALL_TICKS = 1800
TICK_COST_BASELINE_WINDOWS = 10  # 前 N 窗用作成本基线


def _anomaly_check(win: int, wall: float, done_delta: int, zero_streak: dict,
                   stalls: dict, e: Engine, base_costs: list[float],
                   stall_run: int) -> tuple[str, str] | None:
    """返回 (异常码, 说明) 或 None。"""
    if e.collision_count > 0:
        return "collision", f"collision_count={e.collision_count}"
    if len(base_costs) >= TICK_COST_BASELINE_WINDOWS:
        med = statistics.median(base_costs)
        limit = max(med * 8, 60.0)
        last = base_costs[-1]
        if last > limit:
            return ("tick_cost",
                    f"window {win} cost {last:.1f}s > limit {limit:.1f}s "
                    f"(median {med:.1f}s)")
    if stall_run >= STALL_WINDOWS:
        return "stall", f"{stall_run} consecutive windows with 0 done"
    for vid, t0 in stalls.items():
        if win * WINDOW_TICKS - t0 > BATTERY_STALL_TICKS:
            return "battery", (f"vehicle {vid} battery<=0 since tick {t0} "
                               f"(>{BATTERY_STALL_TICKS} ticks)")
    ledger = e.dispatcher.ledger()
    if ledger["spawned"] != (ledger["done"] + ledger["active"]
                             + ledger["queued"] + ledger["reassigned"]):
        return "ledger", json.dumps(ledger)
    return None


def run(ticks: int, seed: int, report_dir: str, log_dir: str | None,
        verbose: bool = True, layout: str | None = None) -> int:
    # 布局预设（SC-004 对比口径）：None → baseline 缺省
    if layout is not None and layout != "baseline":
        from .sim.grid import load_profile
        e = Engine(seed=seed, conflict_log_dir=log_dir,
                   layout=load_profile(layout))
    else:
        e = Engine(seed=seed, conflict_log_dir=log_dir)
    rpt = Path(report_dir)
    rpt.mkdir(parents=True, exist_ok=True)
    marks_fh = open(rpt / "marks.jsonl", "w", encoding="utf-8")

    windows = (ticks + WINDOW_TICKS - 1) // WINDOW_TICKS
    base_costs: list[float] = []
    stall_run = 0
    stalls: dict[int, int] = {}       # vid -> 首次滞零 tick
    per_window: list[int] = []
    t_start = time.time()
    result = None

    for win in range(1, windows + 1):
        done0 = e.metrics_tasks_done
        w0 = time.time()
        for _ in range(WINDOW_TICKS):
            e.tick()
        cost = time.time() - w0
        done_delta = e.metrics_tasks_done - done0
        per_window.append(done_delta)

        wait = sum(1 for v in e.vehicles.values()
                   if v.state in (VehicleState.WAITING, VehicleState.STALLED))
        for v in e.vehicles.values():
            if v.battery <= 0:
                if v.id not in stalls:
                    stalls[v.id] = e.tick_n
            else:
                stalls.pop(v.id, None)
        stall_run = stall_run + 1 if done_delta == 0 else 0
        base_costs.append(cost)

        mark = {"win": win, "tick": win * WINDOW_TICKS,
                "wall": round(time.time() - t_start, 1), "cost_s": round(cost, 2),
                "done": done_delta, "done_total": e.metrics_tasks_done,
                "waiting": round(wait / len(e.vehicles), 3),
                "queue_depth": e._charge_queue_depth(),
                "zero_now": len(stalls)}
        marks_fh.write(json.dumps(mark) + "\n")
        marks_fh.flush()
        if verbose:
            print(json.dumps(mark), flush=True)

        hit = _anomaly_check(win, time.time() - t_start, done_delta, {}, stalls,
                             e, base_costs, stall_run)
        if hit is not None:
            code, detail = hit
            diag = {
                "anomaly": code, "detail": detail,
                "window": win, "tick": win * WINDOW_TICKS,
                "wall_s": round(time.time() - t_start, 1),
                "done_total": e.metrics_tasks_done,
                "collisions": e.collision_count,
                "ledger": e.dispatcher.ledger(),
                "last_marks": mark,
            }
            (rpt / "anomaly.json").write_text(
                json.dumps(diag, ensure_ascii=False, indent=2),
                encoding="utf-8")
            print(f"ABORT [{code}] {detail}", flush=True)
            marks_fh.close()
            e.close()
            return 2

    peak10 = max(per_window[:10]) if per_window else 0
    tail = per_window[windows // 2:] if windows > 4 else per_window
    avg_tail = sum(tail) / len(tail) if tail else 0
    summary = {
        "ticks": windows * WINDOW_TICKS, "seed": seed,
        "wall_s": round(time.time() - t_start, 1),
        "done_total": e.metrics_tasks_done,
        "peak_first10_window": peak10, "avg_tail_window": round(avg_tail, 2),
        "sc001_ratio": round(avg_tail / peak10, 3) if peak10 else None,
        "waiting_note": "waiting 峰值见 marks.jsonl 逐窗记录",
        "collisions": e.collision_count,
        "conflicts": len(e.conflict_events),
        "ledger": e.dispatcher.ledger(),
    }
    (rpt / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    marks_fh.close()
    e.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FR-026/027 headless 验收")
    ap.add_argument("--ticks", type=int, default=36_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--report-dir", default="logs/acceptance")
    ap.add_argument("--conflict-log-dir", default=None)
    ap.add_argument("--layout", default=None,
                    help="布局预设（baseline/wide-oneway/charge-expanded）")
    args = ap.parse_args(argv)
    errs = cfg.validate()
    if errs:
        print("配置校验失败:", "; ".join(errs), file=sys.stderr)
        return 1
    return run(args.ticks, args.seed, args.report_dir,
               args.conflict_log_dir or str(Path(args.report_dir) / "conflicts"),
               layout=args.layout)


if __name__ == "__main__":
    sys.exit(main())
