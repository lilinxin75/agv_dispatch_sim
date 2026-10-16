"""SC-008/009 基准对比：热点优化开/关下的互堵频次与时长（FR-024 验收）。

用法：python -m agv_dispatch_sim.bench_conflicts [--ticks N] [--seed N]
缩尺快速对比（与全量共享引擎代码）；结果打印为对比表。
"""
from __future__ import annotations

import argparse
import tempfile

from . import config as cfg
from .sim.engine import Engine


def run(seed: int, ticks: int, optimization: bool) -> dict:
    cfg.CONFLICT_OPTIMIZATION = optimization
    e = Engine(seed=seed, conflict_log_dir=tempfile.mkdtemp())
    for _ in range(ticks):
        e.tick()
    e.close()
    events = e.conflict_events
    durations = [ev.end_tick - ev.start_tick for ev in events if ev.end_tick]
    done = e.metrics_tasks_done
    return {
        "conflicts": len(events),
        "freq": round(len(events) / max(ticks / 1000, 1e-9), 2),
        "avg_duration": round(sum(durations) / len(durations), 1) if durations else 0,
        "tasks_done": done,
        "collision": e.collision_count,
        "hotspots": len(e.planner.hotspots),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    base = run(args.seed, args.ticks, optimization=False)
    opt = run(args.seed, args.ticks, optimization=True)
    print(f"对比（ticks={args.ticks}, seed={args.seed}）")
    print(f"{'指标':<24}{'基线':>10}{'优化后':>10}{'变化':>12}")
    for key, label, better_lower in (
            ("conflicts", "互堵事件数", True),
            ("freq", "频次(次/千tick)", True),
            ("avg_duration", "平均时长(tick)", True),
            ("tasks_done", "完成任务数(吞吐)", False),
            ("collision", "碰撞数", True),
            ("hotspots", "热点格数", None)):
        b, o = base[key], opt[key]
        if b:
            delta = (o - b) / b * 100
            sign = "+" if delta > 0 else ""
            print(f"{label:<24}{b:>10}{o:>10}{sign}{delta:>10.1f}%")
        else:
            print(f"{label:<24}{b:>10}{o:>10}")
    if base["conflicts"]:
        fr = (base["freq"] - opt["freq"]) / base["freq"] * 100
        dr = ((base["avg_duration"] - opt["avg_duration"])
              / base["avg_duration"] * 100) if base["avg_duration"] else 0
        print(f"\nSC-008 频次下降: {fr:.1f}%（目标 ≥30%）")
        print(f"SC-009 时长下降: {dr:.1f}%（目标 ≥30%）")
        print(f"约束保持: 碰撞={opt['collision']}（须为 0），"
              f"吞吐变化={(opt['tasks_done'] - base['tasks_done']) / max(base['tasks_done'],1) * 100:.1f}%")
    else:
        print("基线无冲突事件：提高负载或延长 ticks 后重试")
    return 0


if __name__ == "__main__":
    main()
