export interface ArticleRegenBannerProps {
  progressPct: number;
  kind: "generate" | "regenerate";
}

/** 生成・再生成ジョブ進行中の進捗行(1h §3.2・§5.3)。 */
export function ArticleRegenBanner({ progressPct, kind }: ArticleRegenBannerProps) {
  const label = kind === "generate" ? "✦ 記事を生成しています…" : "✦ 記事を再生成しています…";
  return (
    <div style={{ height: 24, display: "flex", alignItems: "center", fontSize: 11, color: "var(--pr-a)" }}>
      {label} {progressPct}%
    </div>
  );
}
