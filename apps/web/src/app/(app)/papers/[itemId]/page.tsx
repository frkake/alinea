"use client";

import { useEffect, type ReactNode } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { viewerInit } from "@yakudoku/api-client";
import { EmptyState } from "@/components/ui/EmptyState";
import { ViewerShell, type ViewerMode } from "@/components/viewer/ViewerShell";
import { TranslationPane } from "@/components/viewer/TranslationPane";
import { BilingualPane } from "@/components/viewer/BilingualPane";
import { SourcePane } from "@/components/viewer/SourcePane";
import { ArticlePane } from "@/components/viewer/article/ArticlePane";
import { useViewerStore } from "@/stores/viewer-store";
import { useViewerChatStore } from "@/stores/viewer-chat-store";
import { useIsMobile } from "@/hooks/useMediaQuery";
import type { SidePanelTabId } from "@/components/ui/SidePanelTabs";
import type { DocBlock } from "@/components/viewer/document-types";

/** 表示・遷移可能な 5 モード(plans/13 §1.5・M1-20 で PDF、M2-07 で記事を追加)。 */
const VIEWER_MODES: readonly ViewerMode[] = ["translation", "parallel", "source", "pdf", "article"];

function normalizeMode(raw: string | null, fallback: ViewerMode): ViewerMode {
  if (raw && (VIEWER_MODES as readonly string[]).includes(raw)) return raw as ViewerMode;
  return fallback;
}

/**
 * ビューアルート `/papers/{itemId}`(viewer-shell §3)。
 * クエリ `?mode=` を正規化し、補助クエリ `?block=`/`?section=`/`?panel=` を 1 回消費して
 * URL から除去する。M1 は 訳文 / 対訳 / 原文 / PDF の 4 モード(`?page=` は PDF 固有。
 * PdfPane 側が読み書きする — 2a §1.1)。
 */
export default function ViewerPage() {
  const params = useParams<{ itemId: string }>();
  const itemId = params.itemId;
  const router = useRouter();
  const searchParams = useSearchParams();
  const setPanel = useViewerStore((s) => s.setPanel);
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const style = useViewerStore((s) => s.style);
  const requestAnnotationFocus = useViewerStore((s) => s.requestAnnotationFocus);
  const requestNoteFocus = useViewerStore((s) => s.requestNoteFocus);
  const setPendingHighlightQuery = useViewerStore((s) => s.setPendingHighlightQuery);
  const requestChatFocus = useViewerStore((s) => s.requestChatFocus);
  const addPendingAnchor = useViewerChatStore((s) => s.addPendingAnchor);
  const isMobile = useIsMobile();

  const viewerQuery = useQuery({
    queryKey: ["viewer", itemId],
    queryFn: async () =>
      (await viewerInit({ path: { item_id: itemId }, throwOnError: true })).data,
    staleTime: 30_000,
    enabled: Boolean(itemId),
  });

  const viewer = viewerQuery.data;
  const rawMode = searchParams.get("mode");
  const fallbackMode = normalizeMode(
    viewer?.last_position?.mode ?? null,
    "translation",
  );
  const mode = normalizeMode(rawMode, fallbackMode);

  // mode 未指定・無効値は last_position.mode(既定 translation)へ replace(履歴を汚さない)。
  useEffect(() => {
    if (!viewer) return;
    if (rawMode !== mode) {
      router.replace(`/papers/${itemId}?mode=${mode}`, { scroll: false });
    }
  }, [viewer, rawMode, mode, itemId, router]);

  // 補助クエリの 1 回消費(plans/11 §7 の検索ヒット遷移: ?block/?section/?panel/?hl/
  // ?annotation/?note/?thread/?message)→ URL から除去する。
  useEffect(() => {
    if (!viewer) return;
    const block = searchParams.get("block");
    const section = searchParams.get("section");
    const panel = searchParams.get("panel");
    const hl = searchParams.get("hl");
    const annotationId = searchParams.get("annotation");
    const noteId = searchParams.get("note");
    const threadId = searchParams.get("thread");
    const messageId = searchParams.get("message");
    if (!block && !section && !panel && !hl && !annotationId && !noteId && !threadId && !messageId) {
      return;
    }
    if (block) requestScroll({ kind: "block", blockId: block });
    else if (section) requestScroll({ kind: "section", sectionId: section });
    if (panel) setPanel(true, panel as SidePanelTabId);
    if (hl) setPendingHighlightQuery(hl);
    if (annotationId) requestAnnotationFocus(annotationId);
    if (noteId) requestNoteFocus(noteId);
    // thread/message(チャットの深リンク。plans/11 §7・plans/09 1e §5.3-4「チャット」行)は
    // ChatPanel が viewer-store 経由で消費する(該当スレッド選択+メッセージへスクロール)。
    if (threadId || messageId) requestChatFocus({ threadId, messageId });
    router.replace(`/papers/${itemId}?mode=${mode}`, { scroll: false });
    // 初期化時 1 回のみ。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewer]);

  const onModeChange = (next: ViewerMode) => {
    router.replace(`/papers/${itemId}?mode=${next}`, { scroll: false });
  };

  if (viewerQuery.isError) {
    return (
      <div style={{ position: "fixed", inset: 0, display: "grid", placeItems: "center" }}>
        <EmptyState
          title="論文を読み込めませんでした"
          description="時間をおいて再度お試しください。"
          action={{ label: "再読み込み", onClick: () => void viewerQuery.refetch() }}
        />
      </div>
    );
  }

  if (!viewer) {
    return (
      <div
        style={{ position: "fixed", inset: 0, display: "grid", placeItems: "center", color: "var(--pr-text-muted)" }}
      >
        読み込み中…
      </div>
    );
  }

  const revisionId = viewer.revision.id;

  // 「✦ この式を説明」: 式ブロック全体を引用に積み、チャットタブへ切替(1a §5.2)。
  const onExplainEquation = (block: DocBlock) => {
    addPendingAnchor({
      anchor: {
        revision_id: revisionId,
        block_id: block.id,
        start: null,
        end: null,
        quote: block.latex ?? null,
        side: "source",
      },
      display: block.number ? `式${block.number}` : "式",
    });
    setPanel(true, "chat");
  };

  // 引用 [n] クリック: 図表タブ(参考文献)へ切替(1a §5.2)。
  const onCitationClick = () => setPanel(true, "figures");

  // モバイル縮退(mobile.md §4.1): mode を translation に固定して描画する。URL(`?mode=`)は
  // 書き換えない(デスクトップに戻れば元モードで開けるため。ViewerShell 側も同じ判定で
  // effectiveMode を用いる)。
  const effectiveMode = isMobile ? "translation" : mode;

  let paneContent: ReactNode;
  if (effectiveMode === "translation") {
    paneContent = (
      <TranslationPane
        itemId={itemId}
        revisionId={revisionId}
        style={style}
        toc={viewer.toc}
        summaryLines={viewer.library_item.summary_3line ?? null}
        lastPosition={viewer.last_position}
        onDetailedSummary={() => setPanel(true, "chat")}
        onAskAI={() => setPanel(true, "chat")}
      />
    );
  } else if (effectiveMode === "parallel") {
    paneContent = (
      <BilingualPane
        itemId={itemId}
        revisionId={revisionId}
        style={style}
        toc={viewer.toc}
        lastPosition={viewer.last_position}
        onExplainEquation={onExplainEquation}
        onCitationClick={onCitationClick}
      />
    );
  } else if (effectiveMode === "pdf") {
    // PDF モードの本文(PdfPane)は ViewerShell が自前で描画する(mode==='pdf' 分岐。
    // 2a §3.1「ViewerShell.tsx(mode=pdf で PdfPane 描画)」)。children は使われない。
    paneContent = null;
  } else if (effectiveMode === "article") {
    paneContent = (
      <ArticlePane libraryItemId={itemId} revisionId={revisionId} lastPosition={viewer.last_position} />
    );
  } else {
    paneContent = (
      <SourcePane
        itemId={itemId}
        revisionId={revisionId}
        toc={viewer.toc}
        lastPosition={viewer.last_position}
        onExplainEquation={onExplainEquation}
        onCitationClick={onCitationClick}
      />
    );
  }

  return (
    <ViewerShell itemId={itemId} viewer={viewer} mode={mode} onModeChange={onModeChange}>
      {paneContent}
    </ViewerShell>
  );
}
