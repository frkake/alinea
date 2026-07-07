/** フッター注記行(plans/09-screens/4c §4.6)。逐語固定文言。 */
export function ShareFooterNote() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontSize: 10.5,
        color: "var(--pr-text-muted)",
        padding: "2px 4px 20px",
      }}
    >
      このページは閲覧専用です · アカウント不要 · 検索エンジンには登録されません(noindex) ·
      メモは共有者が許可したもののみ表示
    </div>
  );
}
