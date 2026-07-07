"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchInPaper, type InPaperSearchItem } from "@yakudoku/api-client";
import { SearchBox } from "@/components/ui/SearchBox";
import { Popover } from "@/components/ui/Popover";
import { EmptyState } from "@/components/ui/EmptyState";
import { useViewerStore } from "@/stores/viewer-store";

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable;
}

/** クエリ一致部分を `.yk-search-hit` の `<mark>` で強調(viewer-shell §7)。 */
function highlightSnippet(snippet: string, query: string): ReactNode {
  if (!query) return snippet;
  const idx = snippet.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return snippet;
  return (
    <>
      {snippet.slice(0, idx)}
      <mark className="yk-search-hit">{snippet.slice(idx, idx + query.length)}</mark>
      {snippet.slice(idx + query.length)}
    </>
  );
}

export interface InPaperSearchProps {
  /** 未指定時は useViewerStore().revisionId を使う(自己完結。テスト用に上書き可)。 */
  revisionId?: string;
}

/**
 * 論文内検索(viewer-shell §7 / 1b §5.8)。キー `/` で自身の検索ボックスへフォーカスし、
 * 2 文字以上・300ms デバウンスで `GET /api/revisions/{revisionId}/search` を実行、
 * 結果ドロップダウン(↓/↑/Enter で連続ジャンプ)を表示する。
 *
 * 自己完結コンポーネント(キーボード登録も自前の window keydown で行い、
 * ViewerShell/ViewerHeader/viewer-store には手を入れない)。
 */
export function InPaperSearch({ revisionId: revisionIdProp }: InPaperSearchProps = {}) {
  const storeRevisionId = useViewerStore((s) => s.revisionId);
  const revisionId = revisionIdProp ?? storeRevisionId;
  const requestScroll = useViewerStore((s) => s.requestScroll);

  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const [debounced, setDebounced] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const boxWrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(query), 300);
    return () => window.clearTimeout(t);
  }, [query]);

  const trimmed = debounced.trim();
  const enabled = Boolean(revisionId) && trimmed.length >= 2;
  const resultsQuery = useQuery({
    queryKey: ["in-paper-search", revisionId, trimmed],
    queryFn: async () =>
      (
        await searchInPaper({
          path: { revision_id: revisionId as string },
          query: { q: trimmed, limit: 50 },
          throwOnError: true,
        })
      ).data,
    enabled,
    staleTime: 30_000,
  });

  const items = enabled ? resultsQuery.data?.items ?? [] : [];
  const dropdownOpen = focused && trimmed.length >= 2;

  useEffect(() => {
    setActiveIndex(0);
  }, [trimmed, items.length]);

  // キー `/`: 自コンポーネントの入力へフォーカス(入力中・IME変換中は無効)。
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (isEditableTarget(e.target) || e.isComposing) return;
      e.preventDefault();
      boxWrapRef.current?.querySelector<HTMLInputElement>("input")?.focus();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const onJump = useCallback(
    (item: InPaperSearchItem) => {
      requestScroll({ kind: "block", blockId: item.block_id });
    },
    [requestScroll],
  );

  return (
    <div
      ref={containerRef}
      style={{ position: "relative" }}
      onKeyDown={(e) => {
        if (!dropdownOpen) return;
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setActiveIndex((i) => Math.min(i + 1, Math.max(items.length - 1, 0)));
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          setActiveIndex((i) => Math.max(i - 1, 0));
        } else if (e.key === "Enter") {
          e.preventDefault();
          const item = items[activeIndex];
          if (item) onJump(item);
        } else if (e.key === "Escape") {
          // 検索ドロップダウンのみ閉じる(viewer-shell §10 の Esc 優先順)。
          e.stopPropagation();
          setFocused(false);
          boxWrapRef.current?.querySelector<HTMLInputElement>("input")?.blur();
        }
      }}
    >
      <div ref={boxWrapRef}>
        <SearchBox
          variant="in-paper"
          value={query}
          onChange={setQuery}
          onFocusChange={setFocused}
          placeholder="この論文内を検索"
          shortcutLabel="/"
        />
      </div>
      <Popover
        open={dropdownOpen}
        onClose={() => setFocused(false)}
        anchorRef={boxWrapRef}
        width={300}
        placement="bottom-end"
        caret={false}
      >
        <div role="listbox" aria-label="論文内検索結果">
          {items.length === 0 ? (
            <div style={{ padding: 16 }}>
              <EmptyState title="一致なし" />
            </div>
          ) : (
            items.map((item, i) => (
              <button
                key={item.block_id}
                type="button"
                role="option"
                aria-selected={i === activeIndex}
                onClick={() => onJump(item)}
                onMouseEnter={() => setActiveIndex(i)}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  border: "none",
                  borderBottom: "1px solid var(--pr-border-hair)",
                  background: i === activeIndex ? "var(--pr-bg-hover)" : "transparent",
                  padding: "8px 12px",
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                <div style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>{item.display}</div>
                <div
                  style={{
                    fontSize: 11.5,
                    color: "var(--pr-text-body)",
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {highlightSnippet(item.snippet, trimmed)}
                </div>
              </button>
            ))
          )}
        </div>
      </Popover>
    </div>
  );
}
