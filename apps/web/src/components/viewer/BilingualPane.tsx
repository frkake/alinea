"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  annotationsList,
  translationsListUnits,
  viewerGetDocument,
  type LastPosition,
  type TocNode,
  type TranslationUnitItem,
} from "@alinea/api-client";
import type { HighlightColor } from "@/components/ui/HighlightMark";
import { useViewerStore, type TranslationStyle } from "@/stores/viewer-store";
import { useViewerChatStore } from "@/stores/viewer-chat-store";
import { useTableTranslation } from "@/hooks/use-table-translation";
import { EquationBlock } from "@/components/viewer/EquationBlock";
import { FigureTableBlock } from "@/components/viewer/FigureTableBlock";
import {
  FailedTranslationRetryBanner,
  useFailedTranslationRetry,
} from "@/components/viewer/FailedTranslationRetry";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import {
  isPaperFrontMatterBlock,
  PaperFrontMatterBlock,
} from "@/components/viewer/PaperFrontMatter";
import { ResumeBanner } from "@/components/viewer/ResumeBanner";
import { SectionHeading } from "@/components/viewer/SectionHeading";
import { TranslationColumnHeader } from "@/components/viewer/TranslationColumnHeader";
import { type PlacedHighlight } from "@/components/viewer/highlight-render";
import { isLatexSetupNoiseBlock } from "@/components/viewer/latex-noise";
import {
  buildReferenceTargetMap,
  resolveReferenceTarget,
} from "@/components/viewer/reference-targets";
import { sectionHeadingBlock } from "@/components/viewer/section-heading-block";
import {
  TranslationInlineContent,
  hasTranslatedText,
} from "@/components/viewer/translation-content";
import type { DocBlock, DocSection, DocumentResponse } from "@/components/viewer/document-types";

/** text_ja が null で返る翻訳失敗系フラグ(plans/06 §12)。 */
const FAILURE_FLAGS = new Set([
  "placeholder_mismatch",
  "provider_refusal",
  "context_overflow",
  "untranslated",
]);

export interface BilingualPaneProps {
  itemId: string;
  revisionId: string;
  style: TranslationStyle;
  translationSetId: string | null;
  translationStatus?: string | null;
  toc: TocNode[];
  lastPosition: LastPosition | null;
  /** 「✦ この式を説明」→ 引用を積んでチャットタブへ(1a §5.2)。 */
  onExplainEquation?: (block: DocBlock) => void;
  /** 引用 [n] クリック → 図表タブ参考文献展開(1a §5.2)。 */
  onCitationClick?: (refId: string) => void;
}

/** ブロック単位のハイライト(側別。M1 統合ポリッシュ: hl パリティ)。 */
interface HighlightsBySide {
  source: Map<string, PlacedHighlight[]>;
  translation: Map<string, PlacedHighlight[]>;
}

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

/**
 * 対訳モード本文(1a §4.4)。段落単位 2 カラム(左=原文 / 右=訳文)。
 * document(原文構造)+ units(訳文)を取得し、段落ペアを単一 grid の行として並べる。
 */
export function BilingualPane({
  itemId,
  revisionId,
  style,
  translationSetId,
  translationStatus = null,
  toc,
  lastPosition,
  onExplainEquation,
  onCitationClick,
}: BilingualPaneProps) {
  const setCurrentBlock = useViewerStore((s) => s.setCurrentBlock);
  const pendingScroll = useViewerStore((s) => s.pendingScrollTarget);
  const consumeScroll = useViewerStore((s) => s.consumeScroll);
  const pendingHighlightQuery = useViewerStore((s) => s.pendingHighlightQuery);
  const setPendingHighlightQuery = useViewerStore((s) => s.setPendingHighlightQuery);
  const setPanel = useViewerStore((s) => s.setPanel);
  const requestAnnotationFocus = useViewerStore((s) => s.requestAnnotationFocus);
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const chatEvidence = useViewerChatStore((s) => s.chatEvidence);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [pairSync, setPairSync] = useState(true);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  // `hl` を一発マークする対象ブロック(TranslationPane と同じ規約。plans/11 §7)。
  const [hlBlockId, setHlBlockId] = useState<string | null>(null);

  const docQuery = useQuery({
    queryKey: ["document", revisionId],
    queryFn: async () =>
      (await viewerGetDocument({ path: { revision_id: revisionId }, throwOnError: true }))
        .data as DocumentResponse,
    staleTime: Infinity,
  });

  // 注釈(ハイライト)。TranslationPane・AnnotationListPanel と同一キーでキャッシュを共有する。
  const annotationsQuery = useQuery({
    queryKey: ["annotations", itemId],
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

  // 側別のブロック単位ハイライト(原文=source 列 / 訳文=translation 列。M1 統合ポリッシュ)。
  // 文書順連番はどちらの側も含めて数える(AnnotationListPanel の番号と一致させる)。
  const highlightsBySide = useMemo<HighlightsBySide>(() => {
    const source = new Map<string, PlacedHighlight[]>();
    const translation = new Map<string, PlacedHighlight[]>();
    let seq = 0;
    for (const a of annotationsQuery.data?.items ?? []) {
      if (!a.placed) continue;
      seq += 1;
      if (a.anchor.start == null || a.anchor.end == null) continue;
      const map =
        a.anchor.side === "translation" ? translation : a.anchor.side === "source" ? source : null;
      if (!map) continue;
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
    for (const list of source.values()) list.sort((x, y) => x.start - y.start);
    for (const list of translation.values()) list.sort((x, y) => x.start - y.start);
    return { source, translation };
  }, [annotationsQuery.data]);

  // 本文の丸数字チップクリック → 注釈タブの該当カードへ(TranslationPane と同じ配線。1b §5.7)。
  const onAnnotationClick = (annotationId: string) => {
    setPanel(true, "annotations");
    requestAnnotationFocus(annotationId);
  };

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

  // 先頭可視ブロック追従(位置保存・目次同期。viewer-shell §5.4)。
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
        setCurrentBlock(blockId, blockSectionMap.get(blockId) ?? "");
      },
      { root, rootMargin: "0px 0px -70% 0px", threshold: 0 },
    );
    for (const el of els) observer.observe(el);
    return () => observer.disconnect();
  }, [doc, blockSectionMap, setCurrentBlock]);

  // pendingScrollTarget 消費(モード間位置引き継ぎ・根拠ジャンプ)。
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
    // `hl` の一発マークは遷移先ブロックのみ(TranslationPane と同じ規約。plans/11 §7)。
    if (pendingHighlightQuery && pendingScroll.kind === "block") {
      setHlBlockId(pendingScroll.blockId);
      window.setTimeout(() => {
        setHlBlockId(null);
        setPendingHighlightQuery(null);
      }, 4000);
    }
  }, [pendingScroll, doc, consumeScroll, pendingHighlightQuery, setPendingHighlightQuery]);

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
    content = doc.sections.map((section) => (
      <SectionColumns
        key={section.id}
        section={section}
        tocMap={tocMap}
        unitMap={unitMap}
        chatEvidenceBlockId={chatEvidence?.blockId ?? null}
        chatEvidenceDisplay={chatEvidence?.display ?? null}
        onExplainEquation={onExplainEquation}
        onCitationClick={onCitationClick}
        onRefClick={onRefClick}
        highlightsBySide={highlightsBySide}
        onAnnotationClick={onAnnotationClick}
        hlBlockId={hlBlockId}
        pendingHighlightQuery={pendingHighlightQuery}
        itemId={itemId}
        revisionId={revisionId}
        style={style}
        translationSetId={translationSetId}
      />
    ));
  }

  return (
    <div
      style={{ flex: 1, minWidth: 0, position: "relative", display: "flex", overflow: "hidden" }}
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
        data-pair-sync={pairSync ? "on" : "off"}
        style={{ flex: 1, overflowY: "auto", display: "flex", justifyContent: "center" }}
      >
        <div style={{ width: "100%", maxWidth: 1120, padding: "18px 34px 120px" }}>
          <FailedTranslationRetryBanner
            failedCount={failedRetry.failedCount}
            retrying={failedRetry.retrying}
            onRetry={() => void failedRetry.retryFailed(true)}
          />
          <TranslationColumnHeader
            style={style}
            pairSync={pairSync}
            onTogglePairSync={() => setPairSync((v) => !v)}
          />
          <div style={{ paddingTop: 16 }}>{content}</div>
        </div>
      </div>
    </div>
  );
}

interface SectionColumnsProps {
  section: DocSection;
  tocMap: Map<string, { number: string | null; titleJa: string | null }>;
  unitMap: Map<string, TranslationUnitItem>;
  chatEvidenceBlockId: string | null;
  chatEvidenceDisplay: string | null;
  onExplainEquation?: (block: DocBlock) => void;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
  /** 側別のブロック単位ハイライト(M1 統合ポリッシュ: hl パリティ)。 */
  highlightsBySide: HighlightsBySide;
  onAnnotationClick: (annotationId: string) => void;
  /** `hl` を一発マークする対象ブロック(plans/11 §7)。 */
  hlBlockId: string | null;
  pendingHighlightQuery: string | null;
  itemId: string;
  revisionId: string;
  style: TranslationStyle;
  translationSetId: string | null;
}

function SectionColumns({
  section,
  tocMap,
  unitMap,
  chatEvidenceBlockId,
  chatEvidenceDisplay,
  onExplainEquation,
  onCitationClick,
  onRefClick,
  highlightsBySide,
  onAnnotationClick,
  hlBlockId,
  pendingHighlightQuery,
  itemId,
  revisionId,
  style,
  translationSetId,
}: SectionColumnsProps) {
  const meta = tocMap.get(section.id);
  const number = meta?.number ?? section.heading?.number ?? null;
  const titleEn = section.heading?.title ?? "";
  const titleJa = meta?.titleJa ?? null;
  const headingBlock = sectionHeadingBlock(section);

  return (
    <section data-section-id={section.id}>
      {titleEn ? (
        <div data-block-id={headingBlock?.id}>
          <SectionHeading
            number={number}
            titleJa={titleJa}
            titleEn={titleEn}
            variant={number ? "heading" : "label"}
          />
        </div>
      ) : null}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          columnGap: 34,
          rowGap: 18,
          alignItems: "start",
        }}
      >
        {(section.blocks ?? [])
          .filter((block) => block.id !== headingBlock?.id && !isLatexSetupNoiseBlock(block))
          .map((block) => {
            if (block.type === "paragraph") {
              return (
                <BilingualParagraph
                  key={block.id}
                  block={block}
                  unit={unitMap.get(block.id) ?? null}
                  onCitationClick={onCitationClick}
                  onRefClick={onRefClick}
                  sourceHighlights={highlightsBySide.source.get(block.id) ?? []}
                  translationHighlights={highlightsBySide.translation.get(block.id) ?? []}
                  onAnnotationClick={onAnnotationClick}
                  searchHighlight={hlBlockId === block.id ? pendingHighlightQuery : null}
                />
              );
            }
            if (block.type === "equation") {
              return (
                <BilingualEquation
                  key={block.id}
                  block={block}
                  referenced={chatEvidenceBlockId === block.id}
                  referenceDisplay={chatEvidenceDisplay}
                  onExplain={onExplainEquation}
                />
              );
            }
            return (
              <div key={block.id} style={{ gridColumn: "1 / -1" }}>
                <OtherBlock
                  block={block}
                  unit={unitMap.get(block.id) ?? null}
                  itemId={itemId}
                  revisionId={revisionId}
                  style={style}
                  translationSetId={translationSetId}
                  sectionId={section.id}
                  onCitationClick={onCitationClick}
                  onRefClick={onRefClick}
                />
              </div>
            );
          })}
      </div>
      {(section.sections ?? []).map((sub) => (
        <SectionColumns
          key={sub.id}
          section={sub}
          tocMap={tocMap}
          unitMap={unitMap}
          chatEvidenceBlockId={chatEvidenceBlockId}
          chatEvidenceDisplay={chatEvidenceDisplay}
          onExplainEquation={onExplainEquation}
          onCitationClick={onCitationClick}
          onRefClick={onRefClick}
          highlightsBySide={highlightsBySide}
          onAnnotationClick={onAnnotationClick}
          hlBlockId={hlBlockId}
          pendingHighlightQuery={pendingHighlightQuery}
          itemId={itemId}
          revisionId={revisionId}
          style={style}
          translationSetId={translationSetId}
        />
      ))}
    </section>
  );
}

export interface BilingualParagraphProps {
  block: DocBlock;
  /** null=未翻訳(原文フォールバック)。 */
  unit: TranslationUnitItem | null;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
  /** 原文(左)列に配置する注釈ハイライト(1b §4.5-5 と対の side='source')。 */
  sourceHighlights?: PlacedHighlight[];
  /** 訳文(右)列に配置する注釈ハイライト(side='translation')。 */
  translationHighlights?: PlacedHighlight[];
  onAnnotationClick?: (annotationId: string) => void;
  /** 検索ヒット遷移の `?hl=`(plans/11 §7。遷移先ブロックのみ一発マーク)。 */
  searchHighlight?: string | null;
}

/** 段落ペア 1 行(左=原文セル / 右=訳文セル)。単一 grid の行として並ぶ(1a §4.4)。 */
export function BilingualParagraph({
  block,
  unit,
  onCitationClick,
  onRefClick,
  sourceHighlights = [],
  translationHighlights = [],
  onAnnotationClick,
  searchHighlight = null,
}: BilingualParagraphProps) {
  const inlines = block.inlines ?? [];
  const hasTranslation = hasTranslatedText(unit);
  const failed = !hasTranslation && (unit?.quality_flags ?? []).some((f) => FAILURE_FLAGS.has(f));

  if (isPaperFrontMatterBlock(block)) {
    return (
      <div data-block-id={block.id} data-side="source" style={{ gridColumn: "1 / -1" }}>
        <PaperFrontMatterBlock block={block} />
      </div>
    );
  }

  return (
    <>
      <div
        data-block-id={block.id}
        data-side="source"
        style={{
          fontFamily: "var(--pr-font-en)",
          // 設定 4f の本文サイズ。CSS 変数が未設定の間は既定値(13.8px)を維持する(§5.6)。
          fontSize: "var(--pr-content-font-size-px, 13.8px)",
          lineHeight: 1.72,
          color: "var(--pr-text-en)",
          minWidth: 0,
          maxWidth: "100%",
          overflowWrap: "anywhere",
          wordBreak: "break-word",
        }}
      >
        <InlineRenderer
          inlines={inlines}
          onCitationClick={onCitationClick}
          onRefClick={onRefClick}
          highlights={sourceHighlights}
          searchQuery={searchHighlight}
          onAnnotationClick={onAnnotationClick}
        />
      </div>
      <div
        data-side="translation"
        style={{
          fontFamily: "var(--pr-jp)",
          fontSize: "var(--pr-content-font-size-px, 14.8px)",
          lineHeight: 1.72,
          color: "var(--pr-text-body)",
          minWidth: 0,
          maxWidth: "100%",
          overflowWrap: "anywhere",
          wordBreak: "break-word",
        }}
      >
        {hasTranslation ? (
          <TranslationInlineContent
            unit={unit}
            highlights={translationHighlights}
            searchQuery={searchHighlight}
            onAnnotationClick={onAnnotationClick}
            onCitationClick={onCitationClick}
            onRefClick={onRefClick}
          />
        ) : failed ? (
          <span style={{ fontSize: 12, fontFamily: "var(--pr-font-ui)", color: "var(--pr-warn)" }}>
            この段落の翻訳に失敗しました
          </span>
        ) : (
          <span
            style={{ fontSize: 12, fontFamily: "var(--pr-font-ui)", color: "var(--pr-text-muted)" }}
          >
            翻訳中…
          </span>
        )}
      </div>
    </>
  );
}

/** 全幅の数式ブロック。チャット根拠として参照中はアクセント強調+浮きバッジ(1a §4.4)。 */
function BilingualEquation({
  block,
  referenced,
  referenceDisplay,
  onExplain,
}: {
  block: DocBlock;
  referenced: boolean;
  referenceDisplay: string | null;
  onExplain?: (block: DocBlock) => void;
}) {
  return (
    <div
      data-block-id={block.id}
      style={{
        gridColumn: "1 / -1",
        position: "relative",
        padding: "16px 20px",
        borderRadius: 8,
        background: referenced ? "var(--pr-acc-s)" : "transparent",
        border: referenced ? "1px solid var(--pr-acc-m)" : "1px solid transparent",
      }}
    >
      {referenced ? (
        <span
          style={{
            position: "absolute",
            top: -9,
            right: 14,
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            height: 18,
            padding: "0 7px",
            background: "var(--pr-acc)",
            color: "var(--pr-bg-app)",
            borderRadius: 4,
            fontSize: 10,
            fontWeight: 600,
          }}
        >
          ✦ チャットの根拠{referenceDisplay ? ` · ${referenceDisplay}` : ""}
        </span>
      ) : null}
      <EquationBlock
        latex={block.latex ?? ""}
        assetUrl={block.asset_url}
        number={block.number}
        onExplain={() => onExplain?.(block)}
      />
    </div>
  );
}

/** 段落・数式以外(見出し・図表・コード等)。全幅で簡素に描画。 */
function OtherBlock({
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
  if (block.type === "heading") {
    return (
      <SectionHeading number={block.number ?? null} titleJa={null} titleEn={block.title ?? ""} />
    );
  }
  if (block.type === "figure" || block.type === "table") {
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
  if (block.type === "code") {
    return (
      <pre
        style={{
          margin: "12px 0",
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
  }
  const text = unit?.text_ja;
  return (
    <p
      style={{
        fontSize: "var(--pr-content-font-size-px, 14.8px)",
        lineHeight: 1.72,
        color: "var(--pr-text-body)",
        margin: 0,
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
          inlines={block.inlines ?? []}
          onCitationClick={onCitationClick}
          onRefClick={onRefClick}
        />
      )}
    </p>
  );
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

function PaneSkeleton() {
  const bar = (w: number | string, h: number, mb = 8): ReactNode => (
    <div
      style={{
        width: w,
        height: h,
        marginBottom: mb,
        borderRadius: 4,
        background: "var(--pr-bg-muted)",
        animation: "alinea-pulse 1.2s ease-in-out infinite",
      }}
    />
  );
  return (
    <div
      aria-hidden
      style={{ display: "grid", gridTemplateColumns: "1fr 1fr", columnGap: 34, rowGap: 18 }}
    >
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div key={i}>
          {bar("100%", 13)}
          {bar("100%", 13)}
          {bar("62%", 13)}
        </div>
      ))}
    </div>
  );
}
