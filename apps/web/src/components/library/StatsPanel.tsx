"use client";

import type { CSSProperties } from "react";
import { Card } from "@/components/ui/Card";

/** 棒グラフの高さ算出(§4.7 の決定): 基準 max(5h, 週最大)・最小4%・全0週は全バー4%。 */
export function barHeightPct(hours: number, weeklyHours: readonly number[]): number {
  const basis = Math.max(5, ...weeklyHours);
  if (basis <= 0) return 4;
  const pct = Math.round((hours / basis) * 100);
  return Math.max(4, pct);
}

/**
 * 「今週」統計カード(plans/09-screens/1d-dashboard.md §4.7 右列)。
 * `stats.week` と `stats.weekly_hours`(12要素・古→新)を描画する。依存追加なしの CSS バー。
 */
export interface StatsPanelProps {
  finishedCount: number;
  readingHours: number;
  weeklyHours: number[];
}

export function StatsPanel({ finishedCount, readingHours, weeklyHours }: StatsPanelProps) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 style={{ fontSize: 12, fontWeight: 700, color: "var(--pr-text-sub)", margin: 0 }}>
        今週
      </h2>
      <Card style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", gap: 18 }}>
          <span>
            <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.3px" }}>
              {finishedCount}
            </span>
            <span style={{ fontSize: 11, fontWeight: 500, color: "var(--pr-text-muted)", marginLeft: 3 }}>
              本 読了
            </span>
          </span>
          <span>
            <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.3px" }}>
              {readingHours.toFixed(1)}
            </span>
            <span style={{ fontSize: 11, fontWeight: 500, color: "var(--pr-text-muted)", marginLeft: 3 }}>
              時間
            </span>
          </span>
        </div>

        <WeeklyBars values={weeklyHours} />

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--pr-text-muted)" }}>
          <span>直近 12 週の読書時間</span>
          <a href="/settings?category=reading" style={{ color: "var(--pr-acc)", fontWeight: 600 }}>
            詳細 →
          </a>
        </div>
      </Card>
    </section>
  );
}

function WeeklyBars({ values }: { values: number[] }) {
  const barStyle = (hours: number, isCurrent: boolean): CSSProperties => ({
    flex: 1,
    borderRadius: 2,
    height: `${barHeightPct(hours, values)}%`,
    background: isCurrent ? "var(--pr-acc)" : "var(--pr-bg-locked-badge)",
  });

  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 44 }}>
      {values.map((hours, index) => {
        const isCurrent = index === values.length - 1;
        const weeksAgo = values.length - 1 - index;
        const title = isCurrent
          ? `今週 · ${hours.toFixed(1)}h`
          : `${weeksAgo}週前 · ${hours.toFixed(1)}h`;
        return <div key={index} title={title} style={barStyle(hours, isCurrent)} />;
      })}
    </div>
  );
}
