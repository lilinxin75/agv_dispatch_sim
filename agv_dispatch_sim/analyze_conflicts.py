"""互堵冲突事件分析工具（FR-023，供开发迭代使用，非观看者功能）。

用法：
    python -m agv_dispatch_sim.analyze_conflicts logs/conflicts_20260929_120000.jsonl
    python -m agv_dispatch_sim.analyze_conflicts logs/ --top 20

输出：热点位置 TOP-N、互堵频次（次/千 tick）、单次时长分布统计。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_events(path: Path) -> list[dict]:
    events = []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                try:
                    events.append(json.loads(ln))
                except json.JSONDecodeError as e:
                    print(f"警告：跳过无法解析的行: {e}", file=sys.stderr)
    return events


def analyze(events: list[dict], top: int = 10) -> dict:
    if not events:
        return {"count": 0}
    total_ticks = max(e.get("end_tick", 0) for e in events)
    durations = [e.get("duration_ticks", 0) for e in events]
    hotspot_cells = Counter()
    hotspot_vehicle = Counter()
    for e in events:
        for p in e.get("positions", []):
            hotspot_cells[tuple(p)] += 1
        for v in e.get("vehicles", []):
            hotspot_vehicle[v] += 1
    durations.sort()
    n = len(durations)

    def pct(p):
        i = min(n - 1, int(n * p))
        return durations[i]

    return {
        "count": n,
        "total_ticks": total_ticks,
        "freq_per_1000_ticks": round(n / max(total_ticks / 1000, 1e-9), 2),
        "duration": {
            "mean": round(sum(durations) / n, 1),
            "p50": pct(0.5), "p90": pct(0.9), "p99": pct(0.99), "max": durations[-1],
        },
        "hotspot_cells": [[list(c), cnt] for c, cnt in hotspot_cells.most_common(top)],
        "hotspot_vehicles": hotspot_vehicle.most_common(top),
        "carried_ratio": round(sum(1 for e in events for c in e.get("carried", [])
                                   if c) / max(sum(len(e.get("carried", []))
                                                    for e in events), 1), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="互堵冲突事件批次分析")
    ap.add_argument("path", help="批次 .jsonl 文件或目录")
    ap.add_argument("--top", type=int, default=10, help="热点 TOP-N（默认 10）")
    args = ap.parse_args()

    p = Path(args.path)
    files = sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    if not files:
        print(f"未找到批次文件: {p}", file=sys.stderr)
        return 1

    for f in files:
        events = load_events(f)
        r = analyze(events, args.top)
        print(f"\n=== {f.name} ===")
        if r["count"] == 0:
            print("  无冲突事件")
            continue
        print(f"  事件数: {r['count']}   覆盖 tick: {r['total_ticks']}")
        print(f"  频次: {r['freq_per_1000_ticks']} 次/千 tick")
        d = r["duration"]
        print(f"  时长(tick): mean={d['mean']} p50={d['p50']} p90={d['p90']} "
              f"p99={d['p99']} max={d['max']}")
        print(f"  涉事车辆载货比例: {r['carried_ratio']}")
        print(f"  热点位置 TOP{args.top}: {r['hotspot_cells']}")
        print(f"  高发车辆: {r['hotspot_vehicles']}")
    print("\n提示：热点格可用于路径代价加权优化（FR-024，配置 AGV_TURN_PENALTY 等）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
