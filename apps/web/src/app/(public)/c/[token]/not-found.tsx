import { ShareFooterNote } from "@/components/share/ShareFooterNote";
import { ShareHeader } from "@/components/share/ShareHeader";
import { ShareThemeScope } from "@/components/share/ShareThemeScope";

/**
 * 共有ページの 404(無効・失効リンク。plans/09-screens/4c §5.3)。
 * revoked/不存在 token・token 形式不一致のいずれでも表示する(区別しない)。
 */
export default function ShareNotFound() {
  return (
    <ShareThemeScope>
      <div
        className="yk-share-scope"
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          background: "#E3E1D9",
          color: "var(--pr-text)",
        }}
      >
        {/* not-found.tsx は Next.js の仕様上 params(token)を受け取れない(§5.3)。 */}
        <ShareHeader />
        <main
          style={{
            flex: 1,
            display: "flex",
            justifyContent: "center",
            paddingTop: 96,
          }}
        >
          <div
            style={{
              width: 820,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 8,
              textAlign: "center",
            }}
          >
            <h1 style={{ fontSize: 16, fontWeight: 700, color: "var(--pr-text)", margin: 0 }}>
              このリンクは無効です
            </h1>
            <p style={{ fontSize: 12, color: "var(--pr-text-sub)", lineHeight: 1.7, margin: 0 }}>
              共有リンクが無効化されたか、URL が間違っています。共有した相手に新しいリンクを確認してください。
            </p>
          </div>
        </main>
        <ShareFooterNote />
      </div>
    </ShareThemeScope>
  );
}
