"use client";

import type { CSSProperties } from "react";
import type { SearchHitWithPaper } from "@yakudoku/api-client";
import { Z_INDEX } from "@yakudoku/tokens";
import { SourceBadge } from "@/components/search/SourceBadge";
import { jumpLabelForTarget, previewBadge, snippetFontVar } from "@/components/search/searchNav";

/**
 * グローバル検索ドロップダウン(1e §4.3・4a §3〜5)。位置はヘッダの検索ボックス直下
 * (呼び出し側の `position:relative` ラッパー内で `position:absolute` として重なる)。
 * 座標そのもの(560px 幅・キャレット無し)はデザイン仕様どおりだが、left オフセットは
 * ヘッダ実測値の固定 px ではなく検索ボックスからの相対配置とする(deviations 参照:
 * 見た目は同一だがレイアウト変更に追随できるようにするため)。
 */
export interface SearchDropdownProps {
  /** デバウンス後・トリム済みの確定クエリ。 */
  query: string;
  loading: boolean;
  isError: boolean;
  /** null = 未取得(2 文字未満、または初回リクエスト前)。 */
  total: number | null;
  items: SearchHitWithPaper[];
  /** -1 = 選択なし。`items.length` = フッタ「すべての結果を表示」を選択中。 */
  activeIndex: number;
  onHoverIndex: (index: number) => void;
  onSelect: (hit: SearchHitWithPaper) => void;
  onShowAll: () => void;
  onRetry: () => void;
}

const CONTAINER_STYLE: CSSProperties = {
  position: "absolute",
  top: "calc(100% + 4px)",
  left: 0,
  width: 560,
  background: "var(--pr-bg-pop)",
  border: "1px solid var(--pr-border-pop)",
  borderRadius: 10,
  boxShadow: "var(--pr-shadow-pop)",
  overflow: "hidden",
  zIndex: Z_INDEX.dropdown,
};

const HEADER_STYLE: CSSProperties = {
  display: "flex",
  gap: 8,
  padding: "10px 14px",
  borderBottom: "1px solid var(--pr-border-hair)",
  fontSize: 11,
  color: "var(--pr-text-sub2)",
};

const HINT_STYLE: CSSProperties = { marginLeft: "auto", flex: "none" };

const ROW_TEXT_STYLE: CSSProperties = {
  padding: "8px 10px",
  fontSize: 11.5,
  color: "var(--pr-text-muted)",
};

export interface SearchPreviewItemProps {
  hit: SearchHitWithPaper;
  active: boolean;
  onClick: () => void;
  onMouseEnter: () => void;
}

/** ドロップダウン結果 1 件(1e §4.3)。3 行目(ジャンプリンク)はアクティブ行のみ表示。 */
export function SearchPreviewItem({ hit, active, onClick, onMouseEnter }: SearchPreviewItemProps) {
  const badge = previewBadge(hit.source);
  return (
    <div
      role="option"
      aria-selected={active}
      tabIndex={-1}
      onMouseEnter={onMouseEnter}
      onClick={onClick}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "8px 10px",
        borderRadius: 7,
        cursor: "pointer",
        background: active ? "var(--pr-bg-hover)" : "transparent",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <SourceBadge tone={badge.tone} label={badge.label} size="md" />
        <span style={{ fontSize: 11.5, fontWeight: 600, minWidth: 0 }}>
          {hit.library_item.title}
          <span style={{ color: "var(--pr-text-muted)", fontWeight: 400 }}> · {hit.display}</span>
        </span>
      </div>
      <div
        style={{ fontSize: 11.5, lineHeight: 1.7, color: "var(--pr-text-mid)", fontFamily: snippetFontVar(hit) }}
        // サーバーがサニタイズ済み HTML(<mark class="yk-search-hit"> のみ。plans/03 §15.1)。
        dangerouslySetInnerHTML={{ __html: hit.snippet }}
      />
      {active ? (
        <div style={{ fontSize: 10, color: "var(--pr-acc)", fontWeight: 600 }}>
          {jumpLabelForTarget(hit.target.kind)}
        </div>
      ) : null}
    </div>
  );
}

export function SearchDropdown({
  query,
  loading,
  isError,
  total,
  items,
  activeIndex,
  onHoverIndex,
  onSelect,
  onShowAll,
  onRetry,
}: SearchDropdownProps) {
  const firstLoad = loading && total === null;
  const showBody = total !== null;
  const footerActive = activeIndex === items.length;

  return (
    // onMouseDown で preventDefault: 検索ボックスのフォーカスを保持したまま結果をクリックできる
    // ようにする(mousedown での blur が click より先に発火し、外側クリック判定と競合するため)。
    <div role="listbox" style={CONTAINER_STYLE} onMouseDown={(e) => e.preventDefault()}>
      <div style={HEADER_STYLE}>
        {firstLoad ? (
          <span>「{query}」を検索中…</span>
        ) : (
          <span>
            「<b style={{ color: "var(--pr-text)", fontWeight: 700 }}>{query}</b>」の結果{" "}
            {total ?? "…"} 件
          </span>
        )}
        <span style={HINT_STYLE}>本文・訳文・メモ・チャット・記事を横断</span>
      </div>

      {showBody ? (
        <div style={{ padding: "8px 6px" }}>
          {isError ? (
            <div style={ROW_TEXT_STYLE}>
              検索に失敗しました —{" "}
              <button
                type="button"
                onClick={onRetry}
                style={{
                  color: "var(--pr-acc)",
                  fontWeight: 600,
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  fontSize: "inherit",
                  padding: 0,
                }}
              >
                再試行
              </button>
            </div>
          ) : loading ? (
            <div style={ROW_TEXT_STYLE}>検索中…</div>
          ) : items.length === 0 ? (
            <div style={ROW_TEXT_STYLE}>一致する結果はありません</div>
          ) : (
            items.map((hit, i) => (
              <SearchPreviewItem
                key={`${hit.target.kind}-${hit.display}-${i}`}
                hit={hit}
                active={i === activeIndex}
                onClick={() => onSelect(hit)}
                onMouseEnter={() => onHoverIndex(i)}
              />
            ))
          )}
        </div>
      ) : null}

      {showBody && !isError && !loading && items.length > 0 ? (
        <button
          type="button"
          onClick={onShowAll}
          onMouseEnter={() => onHoverIndex(items.length)}
          style={{
            display: "block",
            width: "100%",
            textAlign: "left",
            padding: "9px 14px",
            borderTop: "1px solid var(--pr-border-hair)",
            fontSize: 11,
            color: "var(--pr-acc)",
            fontWeight: 600,
            background: footerActive ? "var(--pr-bg-hover)" : "transparent",
            border: "none",
            borderTopColor: "var(--pr-border-hair)",
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          すべての結果を表示({total} 件)→
        </button>
      ) : null}
    </div>
  );
}
