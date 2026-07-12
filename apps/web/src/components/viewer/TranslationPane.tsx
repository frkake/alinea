"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  annotationsCreate,
  annotationsDelete,
  annotationsList,
  translationsListUnits,
  vocabCreate,
  viewerGetDocument,
  type Annotation,
  type AnnotationListResponse,
  type LastPosition,
  type TocNode,
  type TranslationUnitItem,
} from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";
import type { HighlightColor } from "@/components/ui/HighlightMark";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { useTableTranslation } from "@/hooks/use-table-translation";
import { useViewerStore, type TranslationStyle } from "@/stores/viewer-store";
import { EquationBlock } from "@/components/viewer/EquationBlock";
import { FigureTableBlock } from "@/components/viewer/FigureTableBlock";
import {
  FailedTranslationRetryBanner,
  useFailedTranslationRetry,
} from "@/components/viewer/FailedTranslationRetry";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { ResumeBanner } from "@/components/viewer/ResumeBanner";
import { SectionHeading } from "@/components/viewer/SectionHeading";
import { SelectionMenu } from "@/components/viewer/SelectionMenu";
import { SummaryCard } from "@/components/viewer/SummaryCard";
import { TranslatedParagraph, type PlacedHighlight } from "@/components/viewer/TranslatedParagraph";
import {
  buildReferenceTargetMap,
  resolveReferenceTarget,
} from "@/components/viewer/reference-targets";
import { isLatexSetupNoiseBlock } from "@/components/viewer/latex-noise";
import { sectionHeadingBlock } from "@/components/viewer/section-heading-block";
import { SOURCE_TEXT_ATTR, textOffsetWithin } from "@/components/viewer/text-offset";
import { TranslationInlineContent } from "@/components/viewer/translation-content";
import { extractVocabContext } from "@/components/viewer/vocab-context";
import type { DocBlock, DocSection, DocumentResponse } from "@/components/viewer/document-types";

function tmpId(): string {
  return `tmp_${typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Date.now()}`;
}

export interface TranslationPaneProps {
  itemId: string;
  revisionId: string;
  style: TranslationStyle;
  translationSetId: string | null;
  translationStatus?: string | null;
  toc: TocNode[];
  summaryLines: string[] | null;
  lastPosition: LastPosition | null;
  /** 詳細要約 → チャットタブ導線(docs/04 §3)。 */
  onDetailedSummary?: () => void;
  /** ✦AIに質問 / ✦この式を説明 → チャットタブ導線。 */
  onAskAI?: (quote: string) => void;
  /** 引用 [n] クリック → 図表タブ参考文献展開。 */
  onCitationClick?: (refId: string) => void;
}

/** section_id → { number, title_ja } を toc(2 階層)から引く。 */
function buildTocMap(
  toc: TocNode[],
): Map<string, { number: string | null; titleJa: string | null }> {
  const map = new Map<string, { number: string | null; titleJa: string | null }>();
  const walk = (nodes: TocNode[]) => {
    for (const n of nodes) {
      map.set(n.section_id, { number: n.number, titleJa: n.title_ja });
      if (n.children?.length) walk(n.children);
    }
  };
  walk(toc);
  return map;
}

/** ドキュメント全セクション ID(入れ子含む)を収集。 */
function collectSectionIds(sections: DocSection[]): string[] {
  const ids: string[] = [];
  const walk = (secs: DocSection[]) => {
    for (const s of secs) {
      ids.push(s.id);
      if (s.sections?.length) walk(s.sections);
    }
  };
  walk(sections);
  return ids;
}

/** block_id → 所属 section_id。 */
function buildBlockSectionMap(sections: DocSection[]): Map<string, string> {
  const map = new Map<string, string>();
  const walk = (secs: DocSection[]) => {
    for (const s of secs) {
      for (const b of s.blocks ?? []) map.set(b.id, s.id);
      if (s.sections?.length) walk(s.sections);
    }
  };
  walk(sections);
  return map;
}

/**
 * 訳文モード本文(1b)。ゆったり組版(16.5px / 行間 2.15 / 本文幅 720|680px)。
 * document(原文構造)+ units(訳文)を取得し、段落=訳文・数式=KaTeX で描画する。
 */
export function TranslationPane({
  itemId,
  revisionId,
  style,
  translationSetId,
  translationStatus = null,
  toc,
  summaryLines,
  lastPosition,
  onDetailedSummary,
  onAskAI,
  onCitationClick,
}: TranslationPaneProps) {
  // 読書位置保存は ViewerShell の useReadingPosition が担う(itemId は注釈・ブックマーク用)。
  const toast = useToast();
  const router = useRouter();
  const qc = useQueryClient();
  const panelOpen = useViewerStore((s) => s.panelOpen);
  const activeTab = useViewerStore((s) => s.activeTab);
  const setCurrentBlock = useViewerStore((s) => s.setCurrentBlock);
  const currentBlockId = useViewerStore((s) => s.currentBlockId);
  const activeSectionId = useViewerStore((s) => s.activeSectionId);
  const popSignal = useViewerStore((s) => s.bilingualPopToggleSignal);
  const bookmarkSignal = useViewerStore((s) => s.bookmarkToggleSignal);
  const pendingScroll = useViewerStore((s) => s.pendingScrollTarget);
  const consumeScroll = useViewerStore((s) => s.consumeScroll);
  const pendingHighlightQuery = useViewerStore((s) => s.pendingHighlightQuery);
  const setPendingHighlightQuery = useViewerStore((s) => s.setPendingHighlightQuery);
  const selection = useViewerStore((s) => s.selection);
  const setSelection = useViewerStore((s) => s.setSelection);
  const setPanel = useViewerStore((s) => s.setPanel);
  const requestAnnotationFocus = useViewerStore((s) => s.requestAnnotationFocus);
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const isMobile = useIsMobile();

  const scrollRef = useRef<HTMLDivElement>(null);
  const [openPopBlockId, setOpenPopBlockId] = useState<string | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  // `hl` を一発マークする対象ブロック(pendingScroll 消費と同時に確定。plans/11 §7)。
  const [hlBlockId, setHlBlockId] = useState<string | null>(null);

  const docQuery = useQuery({
    queryKey: ["document", revisionId],
    queryFn: async () =>
      (await viewerGetDocument({ path: { revision_id: revisionId }, throwOnError: true }))
        .data as DocumentResponse,
    staleTime: Infinity,
  });

  // 注釈(ハイライト。M1-02/03)。AnnotationListPanel と同一キーでキャッシュを共有する。
  const annotationsQueryKey = ["annotations", itemId];
  const annotationsQuery = useQuery({
    queryKey: annotationsQueryKey,
    queryFn: async () =>
      (
        await annotationsList({
          path: { item_id: itemId },
          query: { kind: "highlight" },
          throwOnError: true,
        })
      ).data,
    enabled: Boolean(itemId),
    staleTime: 0,
  });

  // ブックマーク(kind='bookmark')。キー `b`(1b §5.4)の対象セクション判定に使う。
  const bookmarksQueryKey = ["annotations", itemId, "bookmark"];
  const bookmarksQuery = useQuery({
    queryKey: bookmarksQueryKey,
    queryFn: async () =>
      (
        await annotationsList({
          path: { item_id: itemId },
          query: { kind: "bookmark" },
          throwOnError: true,
        })
      ).data,
    enabled: Boolean(itemId),
    staleTime: 0,
  });

  // 本文に配置するハイライト範囲(訳文側のみ。1b §4.5-5)+ 文書順の注釈番号(1b §5.6)。
  const highlightsByBlock = useMemo(() => {
    const map = new Map<string, PlacedHighlight[]>();
    let seq = 0;
    for (const a of annotationsQuery.data?.items ?? []) {
      if (!a.placed) continue;
      seq += 1;
      if (a.anchor.side !== "translation") continue;
      if (a.anchor.start == null || a.anchor.end == null) continue;
      const list = map.get(a.anchor.block_id) ?? [];
      list.push({
        id: a.id,
        start: a.anchor.start,
        end: a.anchor.end,
        color: (a.color ?? "term") as HighlightColor,
        number: seq,
      });
      map.set(a.anchor.block_id, list);
    }
    for (const list of map.values()) list.sort((x, y) => x.start - y.start);
    return map;
  }, [annotationsQuery.data]);

  // 本文の丸数字チップクリック → 注釈タブの該当カードへ(1b §5.7)。
  const onAnnotationClick = useCallback(
    (annotationId: string) => {
      setPanel(true, "annotations");
      requestAnnotationFocus(annotationId);
    },
    [setPanel, requestAnnotationFocus],
  );

  const doc = docQuery.data;
  const sectionIds = useMemo(() => collectSectionIds(doc?.sections ?? []), [doc]);

  const shouldPollUnits = translationSetId != null && translationStatus !== "complete";
  const unitsRefetchInterval: number | false = shouldPollUnits ? 2_500 : false;
  const unitsStaleTime = shouldPollUnits ? 2_000 : 60_000;
  const unitQueries = useQueries({
    queries: sectionIds.map((sid) => ({
      queryKey: ["units", revisionId, style, sid],
      queryFn: async () =>
        (
          await translationsListUnits({
            path: { revision_id: revisionId, style },
            query: { section_id: sid },
            throwOnError: true,
          })
        ).data,
      enabled: Boolean(translationSetId) && sectionIds.length > 0,
      staleTime: unitsStaleTime,
      refetchInterval: unitsRefetchInterval,
    })),
  });
  const unitMap = useMemo(() => {
    const map = new Map<string, TranslationUnitItem>();
    for (const r of unitQueries) {
      for (const item of r.data?.items ?? []) map.set(item.block_id, item);
    }
    return map;
  }, [unitQueries]);

  const failedRetry = useFailedTranslationRetry({
    itemId,
    revisionId,
    translationSetId,
    unitMap,
  });
  const tocMap = useMemo(() => buildTocMap(toc), [toc]);
  const blockSectionMap = useMemo(() => buildBlockSectionMap(doc?.sections ?? []), [doc]);
  const refTargets = useMemo(() => buildReferenceTargetMap(doc?.sections ?? []), [doc]);
  const onRefClick = useCallback(
    (ref: string) => {
      const blockId = resolveReferenceTarget(refTargets, ref);
      if (blockId) requestScroll({ kind: "block", blockId });
    },
    [refTargets, requestScroll],
  );

  // モバイル縮退(mobile.md §4.4): 1 カラム・本文幅 100%(720px 固定カラムは解除)。
  const colWidth: number | string = isMobile
    ? "100%"
    : !panelOpen
      ? 720
      : activeTab === "annotations"
        ? 720
        : 680;

  const togglePop = useCallback((blockId: string) => {
    setOpenPopBlockId((cur) => (cur === blockId ? null : blockId));
  }, []);

  // キー `t`(viewer-shell §10)。currentBlockId の対訳ポップをトグル。
  const firstSignal = useRef(popSignal);
  useEffect(() => {
    if (popSignal === firstSignal.current) return;
    firstSignal.current = popSignal;
    if (currentBlockId) togglePop(currentBlockId);
  }, [popSignal, currentBlockId, togglePop]);

  // 先頭可視ブロックの追従(viewer-shell §5.4 / §8)。
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || !doc) return;
    const els = Array.from(root.querySelectorAll<HTMLElement>("[data-block-id]"));
    if (els.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (!visible) return;
        const blockId = visible.target.getAttribute("data-block-id");
        if (!blockId) return;
        const sectionId = blockSectionMap.get(blockId) ?? "";
        setCurrentBlock(blockId, sectionId);
      },
      { root, rootMargin: "0px 0px -70% 0px", threshold: 0 },
    );
    for (const el of els) observer.observe(el);
    return () => {
      observer.disconnect();
    };
  }, [doc, blockSectionMap, setCurrentBlock]);

  // pendingScrollTarget の消費(モード間位置引き継ぎ・前回位置・目次クリック)。
  useEffect(() => {
    if (!pendingScroll || !doc) return;
    const root = scrollRef.current;
    if (!root) return;
    const selector =
      pendingScroll.kind === "block"
        ? `[data-block-id="${pendingScroll.blockId}"]`
        : `[data-section-id="${pendingScroll.sectionId}"]`;
    const el = root.querySelector<HTMLElement>(selector);
    if (el) {
      el.scrollIntoView({ block: "start" });
      el.classList.add("alinea-block-flash");
      window.setTimeout(() => el.classList.remove("alinea-block-flash"), 2000);
    }
    consumeScroll();
    // `hl` の一発マークは遷移先ブロックのみ(plans/11 §7)。数秒後に消す(本来は次ナビゲーションで
    // 消える契約だが、同一ページに留まる場合の保険として一定時間後に解除する)。
    if (pendingHighlightQuery && pendingScroll.kind === "block") {
      setHlBlockId(pendingScroll.blockId);
      window.setTimeout(() => {
        setHlBlockId(null);
        setPendingHighlightQuery(null);
      }, 4000);
    }
  }, [pendingScroll, doc, consumeScroll, pendingHighlightQuery, setPendingHighlightQuery]);

  // テキスト選択 → 選択メニュー(1b §5.5)。アンカーのブロック内文字オフセットも構築する。
  const onPointerUp = useCallback(() => {
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
    let node: Node | null = range.commonAncestorContainer;
    let blockEl: HTMLElement | null = null;
    while (node) {
      if (node instanceof HTMLElement && node.dataset.blockId) {
        blockEl = node;
        break;
      }
      node = node.parentNode;
    }
    if (!blockEl) {
      setSelection(null);
      return;
    }
    // 選択元の判定: 対訳ポップ内原文・未訳フォールバック原文([data-alinea-source-text])なら
    // 'source'、それ以外(訳文段落)は 'translation'(1b §5.5)。
    const ancestorEl =
      range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
        ? (range.commonAncestorContainer as Element)
        : range.commonAncestorContainer.parentElement;
    const sourceRoot = ancestorEl?.closest(`[${SOURCE_TEXT_ATTR}]`) ?? null;
    const side: "source" | "translation" =
      sourceRoot && blockEl.contains(sourceRoot) ? "source" : "translation";
    const offsetRoot = side === "source" && sourceRoot ? sourceRoot : blockEl;
    const start = textOffsetWithin(offsetRoot, range.startContainer, range.startOffset);
    const end = start + text.length;
    const rect = range.getBoundingClientRect();
    setSelection({
      blockId: blockEl.dataset.blockId ?? "",
      side,
      quote: text.slice(0, 500),
      start,
      end,
      rect: { top: rect.top, left: rect.left, bottom: rect.bottom, right: rect.right },
      // 「語彙に追加」の文脈センテンス抽出用(vocab-context.ts)。'source' のみ意味を持つ。
      sourceFullText: side === "source" ? (sourceRoot?.textContent ?? undefined) : undefined,
    });
  }, [setSelection]);

  // 「語彙に追加」(1b §5.5・M2-12/M2-17。SelectionMenu の onAddVocab は呼び出し側の責務)。
  const addToVocab = useCallback(async () => {
    const sel = selection;
    if (!sel || sel.side !== "source" || sel.start == null || sel.end == null) return;
    setSelection(null);
    const { contextSentence, highlightStart, highlightEnd } = extractVocabContext(
      sel.sourceFullText ?? sel.quote,
      sel.start,
      sel.end,
    );
    try {
      const res = await vocabCreate({
        body: {
          library_item_id: itemId,
          term: sel.quote,
          anchor: {
            revision_id: revisionId,
            block_id: sel.blockId,
            start: sel.start,
            end: sel.end,
            quote: sel.quote,
            side: "source",
          },
          context_sentence: contextSentence,
          highlight: { start: highlightStart, end: highlightEnd },
        },
      });
      if (res.response.status === 409) {
        const existingId = (res.error as { existing?: { vocab_id?: string } } | undefined)?.existing
          ?.vocab_id;
        toast({ kind: "info", message: "すでに語彙帳にあります" });
        if (existingId) router.push(`/vocab/${existingId}`);
        return;
      }
      if (!res.data) throw new Error("vocab create failed");
      toast({ kind: "success", message: `「${sel.quote}」を語彙に追加しました` });
      router.push(`/vocab/${res.data.entry.id}`);
    } catch {
      toast({ kind: "error", message: "語彙に追加できませんでした" });
    }
  }, [selection, itemId, revisionId, router, toast, setSelection]);

  const copySelection = useCallback(
    (format: "citation" | "plain") => {
      const quote = selection?.quote ?? "";
      const text = format === "plain" ? quote : `"${quote}"`;
      void navigator.clipboard?.writeText(text).then(
        () => toast({ kind: "success", message: "コピーしました" }),
        () => toast({ kind: "error", message: "コピーできませんでした" }),
      );
      setSelection(null);
    },
    [selection, toast, setSelection],
  );

  // 選択メニューの色ドット/コメント保存 → 注釈作成(楽観的更新。1b §5.6)。
  const createHighlight = useCallback(
    (color: HighlightColor, comment: string | null) => {
      const sel = selection;
      if (!sel || !itemId) return;
      setSelection(null);
      const anchor = {
        revision_id: revisionId,
        block_id: sel.blockId,
        start: sel.start,
        end: sel.end,
        quote: sel.quote,
        side: sel.side,
      };
      const optimistic: Annotation = {
        id: tmpId(),
        kind: "highlight",
        color,
        anchor: { ...anchor, display: "" },
        comment,
        placed: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      const prev = qc.getQueryData<AnnotationListResponse>(annotationsQueryKey);
      qc.setQueryData<AnnotationListResponse>(annotationsQueryKey, (old) =>
        old ? { ...old, items: [...old.items, optimistic] } : old,
      );
      void annotationsCreate({
        path: { item_id: itemId },
        body: {
          kind: "highlight",
          color,
          anchor,
          comment: comment && comment.length > 0 ? comment : null,
        },
      }).then(
        () => {
          void qc.invalidateQueries({ queryKey: annotationsQueryKey });
          void qc.invalidateQueries({ queryKey: ["viewer", itemId] });
        },
        () => {
          if (prev) qc.setQueryData(annotationsQueryKey, prev);
          toast({
            kind: "error",
            message: "注釈を保存できませんでした",
            action: { label: "再試行", onClick: () => createHighlight(color, comment) },
          });
        },
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selection, itemId, revisionId, qc, toast, setSelection],
  );

  // ブックマーク切替(viewer-shell §10 キー `b`。1b §5.4 が実処理を担う)。
  const firstBookmarkSignal = useRef(bookmarkSignal);
  useEffect(() => {
    if (bookmarkSignal === firstBookmarkSignal.current) return;
    firstBookmarkSignal.current = bookmarkSignal;
    if (!itemId || !activeSectionId) return;
    const existing = bookmarksQuery.data?.items.find((a) => a.anchor.block_id === activeSectionId);
    const after = () => {
      void qc.invalidateQueries({ queryKey: bookmarksQueryKey });
      void qc.invalidateQueries({ queryKey: ["viewer", itemId] });
    };
    if (existing) {
      void annotationsDelete({ path: { annotation_id: existing.id } }).then(
        () => {
          after();
          toast({ kind: "success", message: "ブックマークを解除しました" });
        },
        () => toast({ kind: "error", message: "ブックマークを更新できませんでした" }),
      );
    } else {
      void annotationsCreate({
        path: { item_id: itemId },
        body: {
          kind: "bookmark",
          anchor: {
            revision_id: revisionId,
            block_id: activeSectionId,
            start: null,
            end: null,
            quote: null,
            side: "source",
          },
        },
      }).then(
        () => {
          after();
          toast({ kind: "success", message: "ブックマークしました" });
        },
        () => toast({ kind: "error", message: "ブックマークを更新できませんでした" }),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookmarkSignal]);

  const showBanner = lastPosition != null && !bannerDismissed;

  let content: ReactNode;
  if (docQuery.isError) {
    content = (
      <div style={{ padding: "80px 0", textAlign: "center", color: "var(--pr-warn)" }}>
        論文を読み込めませんでした ·{" "}
        <button
          type="button"
          onClick={() => void docQuery.refetch()}
          style={{
            border: "none",
            background: "transparent",
            color: "var(--pr-acc)",
            cursor: "pointer",
          }}
        >
          再読み込み
        </button>
      </div>
    );
  } else if (!doc) {
    content = <PaneSkeleton />;
  } else {
    content = doc.sections.map((section, i) => (
      <SectionView
        key={section.id}
        section={section}
        depth={0}
        unitMap={unitMap}
        tocMap={tocMap}
        openPopBlockId={openPopBlockId}
        onTogglePop={togglePop}
        summary={
          i === 0 ? (
            <SummaryCard lines={summaryLines} onDetailedSummary={onDetailedSummary} />
          ) : null
        }
        onExplainEquation={(latex) => onAskAI?.(latex)}
        highlightsByBlock={highlightsByBlock}
        onAnnotationClick={onAnnotationClick}
        onCitationClick={onCitationClick}
        onRefClick={onRefClick}
        hlBlockId={hlBlockId}
        pendingHighlightQuery={pendingHighlightQuery}
        isMobile={isMobile}
        itemId={itemId}
        revisionId={revisionId}
        style={style}
        translationSetId={translationSetId}
      />
    ));
  }

  return (
    <div
      style={{
        flex: 1,
        minWidth: 0,
        position: "relative",
        display: "flex",
        overflow: "hidden",
      }}
    >
      {showBanner ? (
        <ResumeBanner
          sectionDisplay={lastPosition.section_display}
          savedAt={lastPosition.saved_at}
          onResume={() => {
            useViewerStore
              .getState()
              .requestScroll({ kind: "block", blockId: lastPosition.block_id });
            setBannerDismissed(true);
          }}
          onDismiss={() => setBannerDismissed(true)}
        />
      ) : null}
      <div
        ref={scrollRef}
        onPointerUp={onPointerUp}
        data-testid="translation-scroll-region"
        style={{
          flex: 1,
          minWidth: 0,
          width: "100%",
          overflowY: "auto",
          overflowX: "hidden",
          display: "flex",
          justifyContent: "center",
        }}
      >
        <div
          data-testid="translation-content-column"
          style={{
            width: colWidth,
            maxWidth: "100%",
            minWidth: 0,
            boxSizing: "border-box",
            padding: isMobile ? "24px 16px 120px" : "64px 0 120px",
            fontFamily: "var(--pr-jp)",
          }}
        >
          <FailedTranslationRetryBanner
            failedCount={failedRetry.failedCount}
            retrying={failedRetry.retrying}
            onRetry={() => void failedRetry.retryFailed(true)}
          />
          {content}
        </div>
      </div>
      {/* テキスト選択メニュー(mobile.md §4.4): モバイルでは注釈作成・AI質問・語彙追加が
          対象外のため表示しない(決定)。 */}
      {selection && !isMobile ? (
        <SelectionMenu
          milestone="M2"
          side={selection.side}
          position={{ top: selection.rect.bottom + 8, left: selection.rect.left }}
          onAskAI={() => {
            onAskAI?.(selection.quote);
            setSelection(null);
          }}
          onCopy={copySelection}
          onHighlight={(color) => createHighlight(color, null)}
          onComment={(color, comment) =>
            createHighlight(color, comment.length > 0 ? comment : null)
          }
          onAddVocab={() => void addToVocab()}
        />
      ) : null}
    </div>
  );
}

interface SectionViewProps {
  section: DocSection;
  depth: number;
  unitMap: Map<string, TranslationUnitItem>;
  tocMap: Map<string, { number: string | null; titleJa: string | null }>;
  openPopBlockId: string | null;
  onTogglePop: (blockId: string) => void;
  summary: ReactNode;
  onExplainEquation: (latex: string) => void;
  /** ブロック単位の注釈ハイライト(1b §4.5-5)。 */
  highlightsByBlock: Map<string, PlacedHighlight[]>;
  onAnnotationClick: (annotationId: string) => void;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
  /** `hl` を一発マークする対象ブロック(plans/11 §7)。 */
  hlBlockId: string | null;
  pendingHighlightQuery: string | null;
  /** モバイル縮退(mobile.md §4.4): 段落タップで対訳ポップを開閉する。 */
  isMobile?: boolean;
  itemId: string;
  revisionId: string;
  style: TranslationStyle;
  translationSetId: string | null;
}

function SectionView({
  section,
  depth,
  unitMap,
  tocMap,
  openPopBlockId,
  onTogglePop,
  summary,
  onExplainEquation,
  highlightsByBlock,
  onAnnotationClick,
  onCitationClick,
  onRefClick,
  hlBlockId,
  pendingHighlightQuery,
  isMobile = false,
  itemId,
  revisionId,
  style,
  translationSetId,
}: SectionViewProps) {
  const meta = tocMap.get(section.id);
  const number = meta?.number ?? section.heading?.number ?? null;
  const titleEn = section.heading?.title ?? "";
  const titleJa = meta?.titleJa ?? null;
  const isAbstract = !number;
  const headingBlock = sectionHeadingBlock(section);

  // セクション内 paragraph の 1 始まり序数(対訳ラベル用)。
  let paraOrdinal = 0;
  const sectionLabel = `${number ? `${number} ` : ""}${titleEn}`.trim();

  return (
    <section data-section-id={section.id}>
      {titleEn ? (
        <div data-block-id={headingBlock?.id}>
          <SectionHeading
            number={number}
            titleJa={titleJa}
            titleEn={titleEn}
            variant={isAbstract ? "label" : "heading"}
          />
        </div>
      ) : null}
      {summary}
      {(section.blocks ?? [])
        .filter((block) => block.id !== headingBlock?.id && !isLatexSetupNoiseBlock(block))
        .map((block) => {
          if (block.type === "paragraph") {
            paraOrdinal += 1;
            const label = `¶${paraOrdinal} / ${sectionLabel}`;
            return (
              <TranslatedParagraph
                key={block.id}
                block={block}
                unit={unitMap.get(block.id) ?? null}
                parallelLabel={label}
                popOpen={openPopBlockId === block.id}
                onTogglePop={() => onTogglePop(block.id)}
                highlights={highlightsByBlock.get(block.id) ?? []}
                onAnnotationClick={onAnnotationClick}
                onCitationClick={onCitationClick}
                onRefClick={onRefClick}
                searchHighlight={hlBlockId === block.id ? pendingHighlightQuery : null}
                isMobile={isMobile}
              />
            );
          }
          return (
            <BlockView
              key={block.id}
              block={block}
              unit={unitMap.get(block.id) ?? null}
              onExplainEquation={onExplainEquation}
              onCitationClick={onCitationClick}
              onRefClick={onRefClick}
              itemId={itemId}
              revisionId={revisionId}
              style={style}
              translationSetId={translationSetId}
              sectionId={section.id}
            />
          );
        })}
      {(section.sections ?? []).map((sub) => (
        <SectionView
          key={sub.id}
          section={sub}
          depth={depth + 1}
          unitMap={unitMap}
          tocMap={tocMap}
          openPopBlockId={openPopBlockId}
          onTogglePop={onTogglePop}
          summary={null}
          onExplainEquation={onExplainEquation}
          highlightsByBlock={highlightsByBlock}
          onAnnotationClick={onAnnotationClick}
          onCitationClick={onCitationClick}
          onRefClick={onRefClick}
          hlBlockId={hlBlockId}
          pendingHighlightQuery={pendingHighlightQuery}
          isMobile={isMobile}
          itemId={itemId}
          revisionId={revisionId}
          style={style}
          translationSetId={translationSetId}
        />
      ))}
    </section>
  );
}

/** 段落以外のブロック(数式・見出し・図表・その他)。 */
function BlockView({
  block,
  unit,
  onExplainEquation,
  onCitationClick,
  onRefClick,
  itemId,
  revisionId,
  style,
  translationSetId,
  sectionId,
}: {
  block: DocBlock;
  unit: TranslationUnitItem | null;
  onExplainEquation: (latex: string) => void;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
  itemId: string;
  revisionId: string;
  style: TranslationStyle;
  translationSetId: string | null;
  sectionId: string;
}) {
  switch (block.type) {
    case "equation":
      return (
        <div data-block-id={block.id}>
          <EquationBlock
            latex={block.latex ?? ""}
            assetUrl={block.asset_url}
            number={block.number}
            onExplain={onExplainEquation}
          />
        </div>
      );
    case "heading":
      return (
        <div data-block-id={block.id}>
          <SectionHeading
            number={block.number ?? null}
            titleJa={null}
            titleEn={block.title ?? ""}
          />
        </div>
      );
    case "figure":
    case "table": {
      return (
        <TranslatableFigureTableBlock
          block={block}
          unit={unit}
          itemId={itemId}
          revisionId={revisionId}
          style={style}
          translationSetId={translationSetId}
          sectionId={sectionId}
          onCitationClick={onCitationClick}
          onRefClick={onRefClick}
        />
      );
    }
    case "code":
      return (
        <pre
          data-block-id={block.id}
          style={{
            margin: "18px 0",
            padding: "12px 14px",
            background: "var(--pr-bg-inset)",
            borderRadius: 8,
            fontFamily: "var(--pr-font-mono)",
            fontSize: 13,
            overflowX: "auto",
          }}
        >
          <code>{block.code}</code>
        </pre>
      );
    default: {
      const inlines = block.inlines ?? [];
      const text = unit?.text_ja;
      return (
        <p
          data-block-id={block.id}
          style={{
            fontSize: "var(--pr-content-font-size-px, 16.5px)",
            lineHeight: 1.8,
            color: "var(--pr-text-body)",
            margin: "0 0 22px",
            minWidth: 0,
            maxWidth: "100%",
            overflowWrap: "anywhere",
            wordBreak: "break-word",
          }}
        >
          {text != null ? (
            <TranslationInlineContent
              unit={unit}
              onCitationClick={onCitationClick}
              onRefClick={onRefClick}
            />
          ) : (
            <InlineRenderer
              inlines={inlines}
              onCitationClick={onCitationClick}
              onRefClick={onRefClick}
            />
          )}
        </p>
      );
    }
  }
}

function TranslatableFigureTableBlock({
  block,
  unit,
  itemId,
  revisionId,
  style,
  translationSetId,
  sectionId,
  onCitationClick,
  onRefClick,
}: {
  block: DocBlock;
  unit: TranslationUnitItem | null;
  itemId: string;
  revisionId: string;
  style: TranslationStyle;
  translationSetId: string | null;
  sectionId: string;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
}) {
  const tableTranslation = useTableTranslation({
    itemId,
    revisionId,
    style,
    translationSetId,
    sectionId,
    blockId: block.id,
  });
  return (
    <FigureTableBlock
      block={block}
      unit={unit}
      tableTranslation={block.type === "table" ? tableTranslation : null}
      onCitationClick={onCitationClick}
      onRefClick={onRefClick}
    />
  );
}

/** 初期スケルトン(1b §5.9)。 */
function PaneSkeleton() {
  const bar = (w: number | string, h: number, mb = 12, key?: number): ReactNode => (
    <div
      key={key}
      style={{
        width: w,
        height: h,
        marginBottom: mb,
        borderRadius: 4,
        background: "var(--pr-bg-muted)",
        animation: "alinea-pulse 1.6s ease-in-out infinite",
      }}
    />
  );
  return (
    <div aria-hidden>
      {bar(160, 12, 8)}
      <div
        style={{
          border: "1px solid var(--pr-border-card)",
          borderRadius: 10,
          height: 118,
          marginBottom: 26,
        }}
      />
      {[0, 1, 2].map((g) => (
        <div key={g} style={{ marginBottom: 24 }}>
          {[0, 1, 2, 3].map((i) => bar(i === 3 ? "70%" : "100%", 17, 12, i))}
        </div>
      ))}
    </div>
  );
}
