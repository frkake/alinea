"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { LastPosition } from "@alinea/api-client";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { SelectionMenu } from "@/components/viewer/SelectionMenu";
import { useViewerStore } from "@/stores/viewer-store";
import { articleKeys, fetchArticle, isArticleNotFound } from "@/components/viewer/article/queries";
import { ArticleGenerateCTA } from "@/components/viewer/article/ArticleGenerateCTA";
import { ArticleSkeleton } from "@/components/viewer/article/ArticleSkeleton";
import { ArticleBody } from "@/components/viewer/article/ArticleBody";
import type { AnchorRef } from "@/components/viewer/article/types";

export interface ArticlePaneProps {
  libraryItemId: string;
  revisionId: string;
  lastPosition?: LastPosition | null;
}

/**
 * 記事モードの中央領域(1h §3.1 `ArticlePane`。viewer-shell §11 の「1h」行)。
 * ローディング/未生成 CTA/エラー/本体の分岐、記事内スクロール追従、根拠ジャンプ、
 * テキスト選択メニュー(✦AIに質問/コピーの 2 項目のみ — §5.8)を所有する。
 */
export function ArticlePane({ libraryItemId, revisionId, lastPosition }: ArticlePaneProps) {
  const router = useRouter();
  const qc = useQueryClient();
  const toast = useToast();
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const pendingScroll = useViewerStore((s) => s.pendingScrollTarget);
  const consumeScroll = useViewerStore((s) => s.consumeScroll);
  const setCurrentBlock = useViewerStore((s) => s.setCurrentBlock);
  const setPanel = useViewerStore((s) => s.setPanel);
  const selection = useViewerStore((s) => s.selection);
  const setSelection = useViewerStore((s) => s.setSelection);

  const scrollRef = useRef<HTMLDivElement>(null);
  const didInitialScroll = useRef(false);

  const articleQuery = useQuery({
    queryKey: articleKeys.article(libraryItemId),
    queryFn: () => fetchArticle(libraryItemId),
    retry: false,
    staleTime: Infinity,
  });

  const article = articleQuery.data;

  // 再訪時の即時スクロール(1h §2.1 #15 決定): 前回位置バナーは出さず該当ブロック先頭へ即時
  // スクロールする。ブロック ID が現行記事に存在しない(記事再生成で ID が変わった)場合は
  // 記事先頭から表示する(何もしない)。
  useEffect(() => {
    if (didInitialScroll.current || !article) return;
    didInitialScroll.current = true;
    if (
      lastPosition?.mode === "article" &&
      lastPosition.block_id &&
      article.blocks.some((b) => b.id === lastPosition.block_id)
    ) {
      requestScroll({ kind: "block", blockId: lastPosition.block_id });
    }
  }, [article, lastPosition, requestScroll]);

  // pendingScrollTarget の消費(モード間位置引き継ぎ・前回位置)。ここで見つからない場合は
  // 消費しない(決定): 根拠ジャンプ(§5.6)は別モード(mode=source)へ遷移するため、
  // 遷移完了前に本ペインが不用意に消費してしまうと遷移先ペインが取りこぼす。
  useEffect(() => {
    if (!pendingScroll || pendingScroll.kind !== "block" || !article) return;
    const root = scrollRef.current;
    if (!root) return;
    const el = root.querySelector<HTMLElement>(`[data-block-id="${pendingScroll.blockId}"]`);
    if (!el) return;
    el.scrollIntoView({ block: "start" });
    el.classList.add("alinea-block-flash");
    window.setTimeout(() => el.classList.remove("alinea-block-flash"), 2000);
    consumeScroll();
  }, [pendingScroll, article, consumeScroll]);

  // 先頭可視ブロックの追従(viewer-shell §5.4/§8)。記事は独自セクションを持たないため
  // sectionId は空文字を渡す(§5.7 決定: 記事の目次クリックは mode=translation へ遷移する)。
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || !article) return;
    const els = Array.from(root.querySelectorAll<HTMLElement>("[data-article-block]"));
    if (els.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (!visible) return;
        const blockId = visible.target.getAttribute("data-block-id");
        if (blockId) setCurrentBlock(blockId, "");
      },
      { root, rootMargin: "0px 0px -70% 0px", threshold: 0 },
    );
    for (const el of els) observer.observe(el);
    return () => observer.disconnect();
  }, [article, setCurrentBlock]);

  // 根拠チップ・「原文で見る →」(1h §5.6)。 mode=source へ遷移して該当ブロックへスクロール。
  const onJumpToAnchor = (anchor: AnchorRef) => {
    requestScroll({ kind: "block", blockId: anchor.block_id });
    router.replace(`/papers/${libraryItemId}?mode=source`, { scroll: false });
  };

  const onPointerUp = () => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      setSelection(null);
      return;
    }
    const text = sel.toString().trim();
    if (!text) {
      setSelection(null);
      return;
    }
    const range = sel.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    let node: Node | null = range.commonAncestorContainer;
    let blockId = "";
    while (node) {
      if (node instanceof HTMLElement && node.dataset.blockId) {
        blockId = node.dataset.blockId;
        break;
      }
      node = node.parentNode;
    }
    setSelection({
      blockId,
      side: "translation",
      quote: text.slice(0, 500),
      start: null,
      end: null,
      rect: { top: rect.top, left: rect.left, bottom: rect.bottom, right: rect.right },
    });
  };

  const copySelection = (format: "citation" | "plain") => {
    const quote = selection?.quote ?? "";
    const text = format === "plain" ? quote : `"${quote}"`;
    void navigator.clipboard?.writeText(text).then(
      () => toast({ kind: "success", message: "コピーしました" }),
      () => toast({ kind: "error", message: "コピーできませんでした" }),
    );
    setSelection(null);
  };

  return (
    <div
      ref={scrollRef}
      onMouseUp={onPointerUp}
      style={{
        flex: 1,
        minWidth: 0,
        display: "flex",
        justifyContent: "center",
        background: "var(--pr-bg-app)",
        overflowY: "auto",
      }}
    >
      {articleQuery.isLoading ? (
        <ArticleSkeleton />
      ) : articleQuery.isError && isArticleNotFound(articleQuery.error) ? (
        <ArticleGenerateCTA
          libraryItemId={libraryItemId}
          onGenerated={() => void qc.invalidateQueries({ queryKey: articleKeys.article(libraryItemId) })}
        />
      ) : articleQuery.isError ? (
        <div style={{ marginTop: 120 }}>
          <EmptyState
            title="読み込みに失敗しました"
            description="時間をおいて再度お試しください。"
            action={{ label: "再試行", onClick: () => void articleQuery.refetch() }}
          />
        </div>
      ) : article ? (
        <ArticleBody
          article={article}
          libraryItemId={libraryItemId}
          revisionId={revisionId}
          onJumpToAnchor={onJumpToAnchor}
        />
      ) : null}
      {selection ? (
        <SelectionMenu
          milestone="M0"
          position={{ top: selection.rect.bottom + 8, left: selection.rect.left }}
          onAskAI={() => {
            setSelection(null);
            setPanel(true, "chat");
          }}
          onCopy={copySelection}
        />
      ) : null}
    </div>
  );
}
