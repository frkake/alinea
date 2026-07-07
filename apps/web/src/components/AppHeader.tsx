"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { authMe, searchPreview, type SearchHitWithPaper } from "@yakudoku/api-client";
import { SearchBox } from "@/components/ui/SearchBox";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { meQueryKey, notificationsQueryKey } from "@/components/notifications/queryKeys";
import { SearchDropdown } from "@/components/search/SearchDropdown";
import { hrefForSearchTarget } from "@/components/search/searchNav";
import { useSSE } from "@/lib/sse";

/** アプリ共通トップバー(確定デザイン「トップバー」逐語 / plans/08 §5.13・§6.2)。 */
export interface AppHeaderProps {
  /** 検索ボックスを隠す場合(ビューアなど別ヘッダを使う画面)。 */
  showSearch?: boolean;
  /** アバターのイニシャル。 */
  initials?: string;
}

const MIN_QUERY_LENGTH = 2; // 1e §5.3・§6.3: 正規化後 2 文字未満では発火しない
const DEBOUNCE_MS = 250; // 1e §6.3・plans/01 §3.4

export function AppHeader({ showSearch = true, initials = "YK" }: AppHeaderProps) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();

  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [debouncedQ, setDebouncedQ] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const [snapshot, setSnapshot] = useState<{ total: number; items: SearchHitWithPaper[] } | null>(
    null,
  );
  const [fallbackActive, setFallbackActive] = useState(false);
  const searchWrapRef = useRef<HTMLDivElement>(null);

  // 未読数(ベルの琥珀ドット)。qk.me() 相当。SSE 復帰時はポーリングフォールバック(plans/01 §5)。
  const meQuery = useQuery({
    queryKey: meQueryKey,
    queryFn: async () => (await authMe({ throwOnError: true })).data,
    staleTime: 60_000,
    refetchInterval: fallbackActive ? 30_000 : false,
  });

  useSSE({
    onEvent: (event) => {
      if (event.type === "notification.created") {
        void queryClient.invalidateQueries({ queryKey: meQueryKey });
        void queryClient.invalidateQueries({ queryKey: notificationsQueryKey });
      }
    },
    onFallbackChange: setFallbackActive,
  });

  // ⌘K / Ctrl+K でグローバル検索へフォーカス(docs/06 §8.1)。
  useEffect(() => {
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchWrapRef.current?.querySelector("input")?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  // 200ms デバウンス。トリム後 2 文字未満は発火しない(直前の結果は snapshot に保持したまま)。
  useEffect(() => {
    const trimmed = value.trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      setDebouncedQ("");
      return;
    }
    const t = setTimeout(() => setDebouncedQ(trimmed), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [value]);

  const previewQuery = useQuery({
    queryKey: ["search", "preview", debouncedQ],
    queryFn: async () =>
      (await searchPreview({ query: { q: debouncedQ }, throwOnError: true })).data,
    enabled: debouncedQ.length >= MIN_QUERY_LENGTH,
  });

  // 新着結果を snapshot に保存し、先頭行をアクティブにする(1e §5.3 の決定)。
  useEffect(() => {
    if (!previewQuery.data) return;
    setSnapshot({ total: previewQuery.data.total, items: previewQuery.data.items });
    setActiveIndex(previewQuery.data.items.length > 0 ? 0 : -1);
  }, [previewQuery.data]);

  const trimmedValue = value.trim();
  const isSearchPage = pathname === "/search"; // 4e ではドロップダウンを開かない(決定)
  const open = showSearch && focused && trimmedValue.length > 0 && !isSearchPage;
  const items = snapshot?.items ?? [];
  const total = snapshot?.total ?? null;
  const footerShown = total !== null && !previewQuery.isError && !previewQuery.isFetching && items.length > 0;
  const maxIndex = footerShown ? items.length : items.length - 1;

  const closeDropdown = () => {
    setFocused(false);
  };

  const goToItem = (hit: SearchHitWithPaper) => {
    closeDropdown();
    router.push(hrefForSearchTarget(hit.target, trimmedValue));
  };

  const goToAllResults = () => {
    if (trimmedValue.length === 0) return;
    closeDropdown();
    router.push(`/search?q=${encodeURIComponent(trimmedValue)}`);
  };

  const onWrapperKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!open) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeDropdown();
      (document.activeElement as HTMLElement | null)?.blur();
      return;
    }
    if (maxIndex < 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1 > maxIndex ? 0 : i + 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 < 0 ? maxIndex : i - 1));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const activeHit = activeIndex >= 0 ? items[activeIndex] : undefined;
      if (activeHit) {
        goToItem(activeHit);
      } else {
        goToAllResults();
      }
    }
  };

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
        <div
          ref={searchWrapRef}
          style={{ position: "relative" }}
          onKeyDown={onWrapperKeyDown}
        >
          <SearchBox
            variant="global"
            value={value}
            onChange={setValue}
            onFocusChange={setFocused}
            placeholder="ライブラリ全体を検索 — 本文・訳文・メモ・チャット"
            shortcutLabel="⌘K"
          />
          {open ? (
            <SearchDropdown
              query={trimmedValue}
              loading={previewQuery.isFetching}
              isError={previewQuery.isError}
              total={total}
              items={items}
              activeIndex={activeIndex}
              onHoverIndex={setActiveIndex}
              onSelect={goToItem}
              onShowAll={goToAllResults}
              onRetry={() => void previewQuery.refetch()}
            />
          ) : null}
        </div>
      ) : null}

      <div style={{ flex: 1 }} />

      <NotificationBell unreadCount={meQuery.data?.unread_notifications ?? 0} />

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
