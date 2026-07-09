"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { MagnifierIcon } from "@/components/icons";

/**
 * 「語彙を検索」— 語彙帳内検索専用ボックス(4d §4.2.3)。
 * グローバル検索(⌘K、`AppHeader` の `SearchBox`)とは完全に別のローカル state を持ち、
 * 200ms デバウンス後にのみ `onChange` を呼ぶ(VT-VOC-02: store 分離)。
 */
export interface VocabSearchBoxProps {
  /** 現在確定しているクエリ値(URL の `?q=`)。外部からのリセット(フィルタ解除等)に追随する。 */
  value: string;
  /** デバウンス後の確定値。 */
  onChange: (value: string) => void;
  /** 一覧取得中(`isFetching`)の間だけスピナーを表示(決定: 入力中ではなくフェッチ中)。 */
  fetching?: boolean;
}

export function VocabSearchBox({ value, onChange, fetching = false }: VocabSearchBoxProps) {
  const [local, setLocal] = useState(value);
  const [focused, setFocused] = useState(false);

  // 外部から value が変わった(URL 直遷移・絞り込み解除等)場合は表示も追随させる。
  useEffect(() => {
    setLocal(value);
  }, [value]);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (local !== value) onChange(local);
    }, 200);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [local]);

  const containerStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    height: 28,
    padding: "0 10px",
    background: "#FFFFFF",
    border: focused ? "1.5px solid var(--pr-acc)" : "1px solid var(--pr-border-control)",
    boxShadow: focused ? "0 0 0 3px var(--pr-acc-s)" : undefined,
    borderRadius: 6,
    fontSize: 11.5,
    color: "var(--pr-text-icon)",
    width: 220,
    flex: "none",
  };

  return (
    <div style={containerStyle}>
      <MagnifierIcon size={11} style={{ flex: "none" }} />
      <input
        type="search"
        aria-label="語彙を検索"
        placeholder="語彙を検索"
        value={local}
        onChange={(e) => {
          setLocal(e.target.value);
        }}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={{
          flex: 1,
          border: "none",
          outline: "none",
          background: "transparent",
          font: "inherit",
          color: "var(--pr-text)",
          minWidth: 0,
        }}
      />
      {fetching ? <VocabSearchSpinner /> : null}
    </div>
  );
}

/** 10×10px の回転スピナー(4d §5.2)。グローバル keyframes は使わず自己完結させる。 */
function VocabSearchSpinner() {
  return (
    <>
      <style>{"@keyframes alinea-vocab-search-spin{to{transform:rotate(360deg)}}"}</style>
      <span
        role="status"
        aria-label="検索中"
        data-testid="vocab-search-spinner"
        style={{
          flex: "none",
          width: 10,
          height: 10,
          borderRadius: "50%",
          border: "1.5px solid var(--pr-text-muted)",
          borderTopColor: "transparent",
          animation: "alinea-vocab-search-spin 800ms linear infinite",
        }}
      />
    </>
  );
}
