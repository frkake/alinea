"""k6 の JSON サマリを前回結果と比較し、主要メトリクスが +30% 悪化したら警告する(plans/12 §12.2)。

perf.yml が artifact として保存する k6 summary(`--summary-export`)2 世代を突き合わせる。Grafana では
なく artifact 比較でトレンドを追う運用(§12.2 末尾)。

使用: python tools/perf/compare.py current.json [previous.json]
"""

from __future__ import annotations

import json
import sys

REGRESS_RATIO = 1.30  # 前回比 +30% で警告(§12.2)。
METRICS = ("http_req_duration",)


def _p95(summary: dict[str, object]) -> dict[str, float]:
    out: dict[str, float] = {}
    metrics = summary.get("metrics", {})
    if not isinstance(metrics, dict):
        return out
    for name in METRICS:
        m = metrics.get(name)
        if isinstance(m, dict):
            for key in ("p(95)", "p95"):
                if key in m and isinstance(m[key], (int, float)):
                    out[name] = float(m[key])
                    break
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: compare.py current.json [previous.json]", file=sys.stderr)
        return 2
    current = _p95(json.load(open(argv[1], encoding="utf-8")))
    print("current p95:", current)
    if len(argv) < 3:
        print("no previous baseline; skipping regression check")
        return 0
    previous = _p95(json.load(open(argv[2], encoding="utf-8")))
    warned = False
    for name, cur in current.items():
        prev = previous.get(name)
        if prev and cur > prev * REGRESS_RATIO:
            print(f"::warning::{name} p95 regressed: {prev:.1f} -> {cur:.1f} (>+30%)")
            warned = True
    if not warned:
        print("no >30% regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
