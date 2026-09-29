"""冲突事件批次文件分析工具 (FR-023)。

加载 JSONL 批次文件，输出互堵冲突统计报告：
事件总数与总时长、热点格子 Top-N、带箱/无箱组合占比、
单次事件时长与规模分布。供开发迭代定位拥堵成因、验证优化效果。

用法::

    python -m rcs_demo.analyze_conflicts data/conflict_events_<batch>.jsonl
    python -m rcs_demo.analyze_conflicts data/*.jsonl        # 多批次合并
    python rcs_demo/analyze_conflicts.py <file>              # 直接执行
"""

from __future__ import annotations

import glob
import json
import sys
from collections import Counter


def load_events(paths: list[str]) -> list[dict]:
    events: list[dict] = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    return events


def carrying_mix(ev: dict) -> str:
    flags = sorted(v["carrying"] for v in ev["vehicles"])
    n = len(ev["vehicles"])
    k = sum(flags)
    if k == 0:
        return f"{n}车全无箱"
    if k == n:
        return f"{n}车全带箱"
    return f"{n}车混合({k}带箱)"


def report(events: list[dict], top_n: int = 10) -> str:
    if not events:
        return "无冲突事件记录。"

    durations = [ev["duration"] for ev in events]
    sizes = [len(ev["vehicles"]) for ev in events]
    total_dur = sum(durations)

    hot_cells: Counter = Counter()
    for ev in events:
        hot_cells.update(tuple(c) for c in ev["cells"])

    mixes: Counter = Counter(carrying_mix(ev) for ev in events)

    lines = [
        f"事件总数: {len(events)}"
        f"（总阻塞时长 {total_dur} tick，均值 {total_dur / len(events):.1f}）",
        "",
        f"热点格子 Top-{top_n}（涉事车辆曾停留的格子）:",
    ]
    for (r, c), n in hot_cells.most_common(top_n):
        bar = "#" * min(n, 40)
        lines.append(f"  ({r:>3},{c:>3})  {n:>4} 次  {bar}")
    lines.append("")
    lines.append("带箱/无箱组合占比:")
    for mix, n in mixes.most_common():
        pct = n / len(events) * 100
        lines.append(f"  {mix:<14} {n:>4} 次  {pct:5.1f}%")

    def dist(name, vals):
        svals = sorted(vals)
        med = svals[len(svals) // 2]
        lines.append(
            f"{name}: min {min(vals)} / 中位 {med} / "
            f"max {max(vals)} / 均值 {sum(vals) / len(vals):.1f}"
        )

    lines.append("")
    dist("单次事件时长 (tick)", durations)
    dist("单次事件车辆数", sizes)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        return 2
    paths: list[str] = []
    for a in args:
        matched = glob.glob(a)
        paths.extend(matched if matched else [a])
    events = load_events(paths)
    print(report(events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
