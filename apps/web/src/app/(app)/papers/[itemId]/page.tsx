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
import { useViewerStore } from "@/stores/viewer-store";
import { useViewerChatStore } from "@/stores/viewer-chat-store";
import type { SidePanelTabId } from "@/components/ui/SidePanelTabs";
import type { DocBlock } from "@/components/viewer/document-types";

/** M0 で表示・遷移可能な 3 モード(plans/13 §1.5)。 */
const M0_MODES: readonly ViewerMode[] = ["translation", "parallel", "source"];

function normalizeMode(raw: string | null, fallback: ViewerMode): ViewerMode {
  if (raw && (M0_MODES as readonly string[]).includes(raw)) return raw as ViewerMode;
  return fallback;
}

/**
 * ビューアルート `/papers/{itemId}`(viewer-shell §3)。
 * クエリ `?mode=` を正規化し、補助クエリ `?block=`/`?section=`/`?panel=` を 1 回消費して
 * URL から除去する。M0 は 訳文 / 対訳 / 原文 の 3 モード。
 */
export default function ViewerPage() {
  const params = useParams<{ itemId: string }>();
  const itemId = params.itemId;
  const router = useRouter();
  const searchParams = useSearchParams();
  const setPanel = useViewerStore((s) => s.setPanel);
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const style = useViewerStore((s) => s.style);
  const addPendingAnchor = useViewerChatStore((s) => s.addPendingAnchor);

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

  // 補助クエリの 1 回消費(?block / ?section / ?panel)→ URL から除去。
  useEffect(() => {
    if (!viewer) return;
    const block = searchParams.get("block");
    const section = searchParams.get("section");
    const panel = searchParams.get("panel");
    if (!block && !section && !panel) return;
    if (block) requestScroll({ kind: "block", blockId: block });
    else if (section) requestScroll({ kind: "section", sectionId: section });
    if (panel) setPanel(true, panel as SidePanelTabId);
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

  let paneContent: ReactNode;
  if (mode === "translation") {
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
  } else if (mode === "parallel") {
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
