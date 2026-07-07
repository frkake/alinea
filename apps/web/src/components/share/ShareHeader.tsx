import Link from "next/link";

/** 共有ページの縮退ヘッダ(plans/09-screens/4c §4.2)。ログイン導線なしのワードマークのみ。 */
export interface ShareHeaderProps {
  /**
   * CTA「訳読をはじめる」の `?next=` に使う共有トークン(§4.2 決定)。`not-found.tsx` /
   * `error.tsx`(§5.3・§5.4)は Next.js の仕様上 `params` を受け取れないため省略可能とし、
   * その場合は `next` なしの `/login` にフォールバックする。
   */
  token?: string;
}

export function ShareHeader({ token }: ShareHeaderProps) {
  return (
    <header
      style={{
        height: 52,
        flex: "none",
        background: "var(--pr-bg-card)",
        borderBottom: "1px solid var(--pr-border-header)",
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "0 24px",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 22,
          height: 22,
          borderRadius: 6,
          background: "var(--pr-acc)",
          color: "#FFFFFF",
          fontSize: 11.5,
          fontWeight: 700,
        }}
      >
        訳
      </span>
      <span style={{ fontSize: 14.5, fontWeight: 700, letterSpacing: 0.5 }}>訳読</span>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          height: 19,
          padding: "0 8px",
          borderRadius: 4,
          background: "var(--pr-bg-inset)",
          color: "var(--pr-text-sub2)",
          fontSize: 10.5,
          fontWeight: 600,
        }}
      >
        共有されたコレクション — 閲覧専用
      </span>
      <div style={{ flex: 1 }} />
      <span style={{ fontSize: 11.5, color: "var(--pr-text-sub)" }}>
        自分のライブラリで論文を読むには
      </span>
      <Link
        href={token ? `/login?next=${encodeURIComponent(`/c/${token}`)}` : "/login"}
        className="yk-share-cta"
        style={{
          display: "inline-flex",
          alignItems: "center",
          height: 28,
          padding: "0 13px",
          borderRadius: 6,
          border: "1px solid var(--pr-acc-m)",
          color: "var(--pr-acc)",
          background: "var(--pr-acc-s)",
          fontSize: 11.5,
          fontWeight: 600,
          textDecoration: "none",
          transition: "background-color 120ms ease-out",
        }}
      >
        訳読をはじめる
      </Link>
    </header>
  );
}
