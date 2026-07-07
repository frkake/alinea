// 状態L: ローディング(3a §5.1)。check 取得中のスケルトン。
export function Loading() {
  return (
    <div className="ext-body ext-skeleton">
      <div className="ext-sk-line" style={{ width: "100%" }} />
      <div className="ext-sk-line" style={{ width: "72%" }} />
      <div className="ext-sk-line ext-sk-meta" style={{ width: "55%" }} />
      <div className="ext-sk-block" />
      <div className="ext-sk-block" />
      <div className="ext-sk-block" />
    </div>
  );
}
