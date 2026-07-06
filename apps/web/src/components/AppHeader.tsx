"use client";

import { useState } from "react";
import { SearchBox } from "@/components/ui/SearchBox";

/** アプリ共通トップバー(確定デザイン「トップバー」逐語 / plans/08 §5.13・§6.2)。 */
export interface AppHeaderProps {
  /** 検索ボックスを隠す場合(ビューアなど別ヘッダを使う画面)。 */
  showSearch?: boolean;
  /** 通知の未読の有無(未読ドット #C49432)。 */
  hasUnread?: boolean;
  /** アバターのイニシャル。 */
  initials?: string;
}

export function AppHeader({
  showSearch = true,
  hasUnread = true,
  initials = "YK",
}: AppHeaderProps) {
  const [search, setSearch] = useState("");

  return (
    <header
      style={{
        height: 52,
        flex: "none",
        background: "var(--pr-bg-card)",
        borderBottom: "1px solid var(--pr-border-header)",
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "0 18px",
      }}
    >
      {/* ワードマーク */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, width: 198 }}>
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
        <span style={{ fontSize: 14.5, fontWeight: 700, letterSpacing: "0.5px" }}>訳読</span>
        <span
          style={{
            fontSize: 9.5,
            color: "var(--pr-text-faint)",
            letterSpacing: "1.2px",
            marginTop: 2,
          }}
        >
          YAKUDOKU
        </span>
      </div>

      {showSearch ? (
        <SearchBox
          variant="global"
          value={search}
          onChange={setSearch}
          placeholder="ライブラリ全体を検索 — 本文・訳文・メモ・チャット"
          shortcutLabel="⌘K"
        />
      ) : null}

      <div style={{ flex: 1 }} />

      {/* 通知ベル */}
      <span
        role="button"
        tabIndex={0}
        aria-label="通知"
        style={{
          position: "relative",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 30,
          height: 30,
          borderRadius: 7,
          border: "1px solid var(--pr-border-card)",
          color: "var(--pr-text-sub)",
          fontSize: 13,
          cursor: "pointer",
        }}
      >
        ◷
        {hasUnread ? (
          <span
            style={{
              position: "absolute",
              top: 5,
              right: 5,
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--pr-amber)",
            }}
          />
        ) : null}
      </span>

      {/* アバター */}
      <span
        aria-label="アカウント"
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 30,
          height: 30,
          borderRadius: "50%",
          background: "var(--pr-acc-s)",
          color: "var(--pr-acc)",
          fontSize: 11,
          fontWeight: 700,
        }}
      >
        {initials}
      </span>
    </header>
  );
}
