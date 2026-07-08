"use client";

import { useEffect, useState } from "react";

export interface ResourceAddFooterProps {
  onAdd: (url: string) => void;
  /** POST 実行中(「追加」→「追加中…」、入力 disabled)。 */
  pending: boolean;
  /** 422 時のインラインエラー(plans/09-screens/5a §5.6)。 */
  errorMessage: string | null;
  /**
   * 追加成功のたびに変わる値(呼び出し側のカウンタ等)。変化を検知して入力欄をクリアする
   * (連続して複数 URL を追加できるようにする。PW-21・M2-17 followup)。省略時は
   * クリアしない(既存の呼び出し元互換)。
   */
  clearSignal?: number;
}

/** サイドパネル フッター(URL 入力+追加+ヘルプ文。plans/09-screens/5a §4.6)。 */
export function ResourceAddFooter({ onAdd, pending, errorMessage, clearSignal }: ResourceAddFooterProps) {
  const [value, setValue] = useState("");

  // 追加成功(clearSignal の変化)で入力欄をクリアする。422 エラー時は clearSignal が
  // 変化しないため、ユーザーが入力を訂正できるよう値を保持する。
  useEffect(() => {
    if (clearSignal !== undefined) setValue("");
  }, [clearSignal]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || pending) return;
    onAdd(trimmed);
  };

  return (
    <div
      style={{
        padding: "10px 12px",
        borderTop: "1px solid var(--pr-border-soft)",
        display: "flex",
        flexDirection: "column",
        gap: 7,
        background: "var(--pr-bg-card)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          border: "1px solid var(--pr-border-control)",
          borderRadius: 7,
          padding: "7px 10px",
        }}
      >
        <input
          type="url"
          value={value}
          disabled={pending}
          placeholder="URL を貼り付け — 種類を自動判定"
          aria-label="リソースの URL"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          style={{
            flex: 1,
            fontSize: 11.5,
            border: "none",
            background: "transparent",
            outline: "none",
            fontFamily: "inherit",
          }}
        />
        <button
          type="button"
          onClick={submit}
          disabled={pending || !value.trim()}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            height: 22,
            padding: "0 11px",
            borderRadius: 5,
            border: "none",
            background: "var(--pr-acc)",
            color: "#FFFFFF",
            fontSize: 10.5,
            fontWeight: 600,
            fontFamily: "inherit",
            cursor: pending || !value.trim() ? "default" : "pointer",
            opacity: pending || !value.trim() ? 0.5 : 1,
          }}
        >
          {pending ? "追加中…" : "追加"}
        </button>
      </div>
      {errorMessage ? (
        <div style={{ fontSize: 9.5, lineHeight: 1.6, color: "var(--pr-warn, #A05A42)" }}>
          {errorMessage}
        </div>
      ) : (
        <div style={{ fontSize: 9.5, lineHeight: 1.6, color: "var(--pr-text-muted)" }}>
          GitHub・YouTube・スライド・解説記事など。タイトルとサムネイルは自動取得、ひとことメモを添えられます。
        </div>
      )}
    </div>
  );
}
