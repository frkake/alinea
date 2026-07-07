"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { libraryItemsUpdate, translationsSectionTranslate, type ViewerInit } from "@yakudoku/api-client";
import type { ReadingStatus } from "@yakudoku/tokens";
import { useToast } from "@/components/ui/Toast";
import { useViewerStore } from "@/stores/viewer-store";
import { usePdfAvailability } from "@/hooks/use-pdf-availability";
import { ViewerHeader } from "@/components/viewer/ViewerHeader";
import { TocTree } from "@/components/viewer/TocTree";
import { SidePanel } from "@/components/viewer/SidePanel";
import { InfoPanel } from "@/components/viewer/InfoPanel";
import { FiguresPanel } from "@/components/viewer/FiguresPanel";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { PdfSidebar } from "@/components/viewer/pdf/PdfSidebar";
import { PdfPane } from "@/components/viewer/pdf/PdfPane";
import { PdfDocumentProvider } from "@/components/viewer/pdf/use-pdf-document";
import { useReadingPosition } from "@/hooks/use-reading-position";
import { useViewerKeymap } from "@/hooks/use-viewer-keymap";
import { useSSE } from "@/lib/sse";

/** 表示モード(LastPosition.mode と同一トークン)。URL クエリ ?mode= の値域。 */
export type ViewerMode = "translation" | "parallel" | "source" | "pdf" | "article";

export interface ViewerShellProps {
  itemId: string;
  viewer: ViewerInit;
  mode: ViewerMode;
  onModeChange: (mode: ViewerMode) => void;
  children: ReactNode;
  /** 2a 専用。M0 未使用。 */
  leftPane?: ReactNode;
  /** 読書時間計測の設定(M0 は位置保存のみ。表示分岐用)。 */
  trackReadingTime?: boolean;
}

/**
 * ビューアシェル(viewer-shell §1)。ヘッダ・左レール/目次・サイドパネル枠を所有。
 * (app) レイアウトのグローバルクロムを覆う全画面読解体験のため fixed オーバーレイで描画する
 * (共有レイアウト非改変での全画面化。z-index は Popover/Toast より下)。
 */
export function ViewerShell({
  itemId,
  viewer,
  mode,
  onModeChange,
  children,
  leftPane,
  trackReadingTime = true,
}: ViewerShellProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const qc = useQueryClient();
  const toast = useToast();

  const tocOpen = useViewerStore((s) => s.tocOpen);
  const setTocOpen = useViewerStore((s) => s.setTocOpen);
  const initViewer = useViewerStore((s) => s.initViewer);
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const openSearch = useViewerStore((s) => s.openSearch);
  const activeSectionId = useViewerStore((s) => s.activeSectionId);

  const revisionId = viewer.revision.id;
  const paperId = viewer.library_item.paper.id;

  useEffect(() => {
    initViewer(itemId, revisionId);
  }, [initViewer, itemId, revisionId]);

  useReadingPosition({ itemId, revisionId, mode });
  useViewerKeymap({ mode, onModeChange, onFocusSearch: () => openSearch() });

  // PDF アセット無し論文(2a §5.3): ヘッダの「PDF」セグメントを disabled にし、
  // URL 直打ちで mode=pdf に来た場合は訳文へフォールバックする(黙って壊さない。P3)。
  const pdfAvailable = usePdfAvailability(paperId);
  useEffect(() => {
    if (mode === "pdf" && pdfAvailable === false) {
      onModeChange("translation");
    }
  }, [mode, pdfAvailable, onModeChange]);

  const onOpenInTranslation = (blockId: string) => {
    router.replace(`/papers/${itemId}?mode=translation&block=${blockId}`, { scroll: false });
  };

  // 部分読書: SSE で翻訳完了/失敗を受けたら該当クエリを invalidate し本文を差し替える
  // (viewer-shell §2.3。translation.unit_completed → units + viewer 進捗)。
  useSSE({
    onEvent: (e) => {
      if (e.type === "translation.unit_completed") {
        void qc.invalidateQueries({ queryKey: ["units", revisionId] });
        void qc.invalidateQueries({ queryKey: ["viewer", itemId] });
      } else if (e.type === "job.failed") {
        void qc.invalidateQueries({ queryKey: ["viewer", itemId] });
      }
    },
  });

  const onBack = () => {
    if (typeof window !== "undefined" && window.history.length <= 1) {
      router.push("/library");
    } else {
      router.back();
    }
  };

  const onStatusChange = (status: ReadingStatus) => {
    // done は本来 1g 読了フローモーダル経由(別レーン)。M0 は PATCH のみ。
    const prev = qc.getQueryData<ViewerInit>(["viewer", itemId]);
    if (prev) {
      qc.setQueryData<ViewerInit>(["viewer", itemId], {
        ...prev,
        library_item: { ...prev.library_item, status },
      });
    }
    void libraryItemsUpdate({ path: { item_id: itemId }, body: { status } }).then(
      () => qc.invalidateQueries({ queryKey: ["viewer", itemId] }),
      () => {
        if (prev) qc.setQueryData(["viewer", itemId], prev);
        toast({ kind: "error", message: "ステータスを変更できませんでした" });
      },
    );
  };

  const onTranslateAppendix = (sectionId: string) => {
    const setId = viewer.translation?.set_id;
    if (!setId) return;
    void translationsSectionTranslate({
      path: { set_id: setId, section_id: sectionId },
      body: {},
    }).then(
      () => toast({ kind: "info", message: "この付録の翻訳を開始しました" }),
      () => toast({ kind: "error", message: "翻訳を開始できませんでした" }),
    );
  };

  const progressPct = viewer.translation?.progress_pct ?? 0;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1,
        display: "flex",
        flexDirection: "column",
        background: "var(--pr-bg-app)",
        color: "var(--pr-text)",
        fontFamily: "var(--pr-font-ui)",
      }}
    >
      <ViewerHeader
        title={viewer.library_item.paper.title}
        qualityLevel={viewer.library_item.quality_level === "B" ? "B" : "A"}
        status={viewer.library_item.status as ReadingStatus}
        mode={mode}
        onModeChange={onModeChange}
        onStatusChange={onStatusChange}
        onBack={onBack}
        pdfDisabled={pdfAvailable === false}
      />
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {mode === "pdf" ? (
          <PdfDocumentProvider paperId={paperId}>
            <PdfSidebar
              toc={viewer.toc}
              activeSectionId={activeSectionId}
              onSectionClick={(sectionId) => requestScroll({ kind: "section", sectionId })}
              onTranslateAppendix={onTranslateAppendix}
              pageCountFallback={viewer.revision.page_count}
              pdfDownloadHref={`/api/papers/${paperId}/pdf`}
            />
            <PdfPane
              itemId={itemId}
              revisionId={revisionId}
              initialPage={initialPdfPage(searchParams)}
              lastPositionBlockId={
                viewer.last_position?.mode === "pdf" ? viewer.last_position.block_id : null
              }
              onOpenInTranslation={onOpenInTranslation}
            />
          </PdfDocumentProvider>
        ) : (
          <>
            {leftPane ?? (
              <TocTree
                toc={viewer.toc}
                progressPct={progressPct}
                todayReadingMinutes={viewer.today_reading_minutes}
                trackReadingTime={trackReadingTime}
                open={tocOpen}
                onToggle={setTocOpen}
                activeSectionId={activeSectionId}
                onSectionClick={(sectionId) => requestScroll({ kind: "section", sectionId })}
                onTranslateAppendix={onTranslateAppendix}
                onFocusSearch={() => openSearch()}
              />
            )}
            {children}
          </>
        )}
        <SidePanel
          milestone="M0"
          counts={{}}
          renderTab={(tab) => {
            if (tab === "chat") return <ChatPanel itemId={itemId} />;
            if (tab === "figures") return <FiguresPanel itemId={itemId} revisionId={revisionId} />;
            if (tab === "info")
              return (
                <InfoPanel
                  itemId={itemId}
                  paper={viewer.library_item.paper}
                  revision={viewer.revision}
                  licenseCard={viewer.license_card}
                  ingestTimeline={viewer.ingest_timeline}
                />
              );
            return null;
          }}
        />
      </div>
    </div>
  );
}

/** `?page=` の初期値解決(2a §5.10 優先順②④。①③は PdfPane 側で document 到着後に解決)。 */
function initialPdfPage(searchParams: ReturnType<typeof useSearchParams>): number {
  const raw = searchParams.get("page");
  const n = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n >= 1 ? n : 1;
}
