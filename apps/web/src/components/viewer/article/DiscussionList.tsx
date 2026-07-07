import type { DiscussionContentOut } from "@yakudoku/api-client";

/**
 * 「議論したい点」ブロック(1h §4.10)。VT-VIEW-16: `origin==='user_highlight'` の項目にのみ
 * 由来バッジ「あなたの疑問ハイライトから」を付ける。
 */
export function DiscussionList({ discussion }: { discussion: DiscussionContentOut }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 19, fontWeight: 700, color: "var(--pr-text)" }}>議論したい点</span>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            height: 16,
            padding: "0 7px",
            borderRadius: 3,
            background: "var(--pr-bg-inset)",
            color: "var(--pr-text-sub2)",
            fontSize: 9.5,
            fontWeight: 600,
          }}
        >
          ✦ AI構成
        </span>
      </div>
      {discussion.items.map((item, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            gap: 9,
            fontFamily: "var(--pr-jp, 'Noto Serif JP'), serif",
            fontSize: 13.5,
            lineHeight: 1.9,
            color: "var(--pr-text-body)",
          }}
        >
          <span style={{ color: "var(--pr-text-muted)" }}>{i + 1}.</span>
          <span>
            {item.text}
            {item.origin === "user_highlight" ? (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  height: 15,
                  padding: "0 6px",
                  borderRadius: 3,
                  background: "rgba(88,132,170,0.16)",
                  color: "#4A6E8E",
                  fontSize: 9.5,
                  fontWeight: 600,
                  fontFamily: "var(--pr-font-ui)",
                  verticalAlign: 2,
                  marginLeft: 4,
                }}
              >
                あなたの疑問ハイライトから
              </span>
            ) : null}
          </span>
        </div>
      ))}
    </div>
  );
}
