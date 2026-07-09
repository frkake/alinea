"use client";

import { ShareFooterNote } from "@/components/share/ShareFooterNote";
import { ShareHeader } from "@/components/share/ShareHeader";
import { ShareThemeScope } from "@/components/share/ShareThemeScope";

/**
 * 共有ページのエラー状態(5xx/429。plans/09-screens/4c §5.4)。
 * Next.js の要求により Client Component(`"use client"`)。§5.3 と同一枠を使う。
 * `error` は Next.js の規約上必須の props だが、本画面では追加ログを送らない(決定:
 * サービスを持たないため。P3「サイレント劣化しない」は見出し・再読み込みボタンで満たす)。
 */
export default function ShareError({ reset }: { error: Error; reset: () => void }) {
  return (
    <ShareThemeScope>
      <div
        className="alinea-share-scope"
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          background: "#E3E1D9",
          color: "var(--pr-text)",
        }}
      >
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
              ページを表示できません
            </h1>
            <p style={{ fontSize: 12, color: "var(--pr-text-sub)", lineHeight: 1.7, margin: 0 }}>
              一時的な問題が発生しました。しばらく待ってからもう一度お試しください。
            </p>
            <button
              type="button"
              onClick={reset}
              style={{
                height: 26,
                padding: "0 12px",
                border: "1px solid var(--pr-border-control)",
                borderRadius: 6,
                fontSize: 11,
                color: "var(--pr-text-mid)",
                background: "var(--pr-bg-control)",
                cursor: "pointer",
                fontFamily: "inherit",
                marginTop: 4,
              }}
            >
              再読み込み
            </button>
          </div>
        </main>
        <ShareFooterNote />
      </div>
    </ShareThemeScope>
  );
}
