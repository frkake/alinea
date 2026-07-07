function SkeletonBar({ width, height }: { width: number | string; height: number }) {
  return (
    <div
      style={{
        width,
        height,
        background: "var(--pr-bg-muted)",
        borderRadius: 6,
      }}
    />
  );
}

/** 記事ローディングスケルトン(1h §5.1)。 */
export function ArticleSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="記事を読み込み中"
      style={{ width: 760, padding: "34px 0 0", display: "flex", flexDirection: "column", gap: 16 }}
    >
      <SkeletonBar width={520} height={27} />
      <SkeletonBar width={420} height={11} />
      <div style={{ border: "1px solid var(--pr-border-card)", borderRadius: 10, overflow: "hidden" }}>
        <SkeletonBar width="100%" height={180} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {[0, 1, 2, 3, 4].map((i) => (
          <SkeletonBar key={i} width={760} height={14} />
        ))}
      </div>
    </div>
  );
}
