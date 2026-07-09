"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  annotationsList,
  viewerGetDocument,
  type LastPosition,
  type TocNode,
} from "@alinea/api-client";
import type { HighlightColor } from "@/components/ui/HighlightMark";
import { useViewerStore } from "@/stores/viewer-store";
import { EquationBlock } from "@/components/viewer/EquationBlock";
import { FigureTableBlock } from "@/components/viewer/FigureTableBlock";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import {
  isPaperFrontMatterBlock,
  PaperFrontMatterBlock,
} from "@/components/viewer/PaperFrontMatter";
import { ResumeBanner } from "@/components/viewer/ResumeBanner";
import { SectionHeading } from "@/components/viewer/SectionHeading";
import type { PlacedHighlight } from "@/components/viewer/highlight-render";
import { isLatexSetupNoiseBlock } from "@/components/viewer/latex-noise";
import {
  buildReferenceTargetMap,
  resolveReferenceTarget,
} from "@/components/viewer/reference-targets";
import { sectionHeadingBlock } from "@/components/viewer/section-heading-block";
import type { DocBlock, DocSection, DocumentResponse } from "@/components/viewer/document-types";

export interface SourcePaneProps {
  itemId: string;
  revisionId: string;
  toc: TocNode[];
  lastPosition: LastPosition | null;
  onExplainEquation?: (block: DocBlock) => void;
  onCitationClick?: (refId: string) => void;
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
 * 原文モード本文(viewer-shell §11。3 モードの 1 つ)。英語原文のみを 1 カラムで通読する。
 * 訳文ユニットは取得せず、document の inlines を Source Serif で描画する。
 */
export function SourcePane({
  itemId,
  revisionId,
  toc,
  lastPosition,
  onExplainEquation,
  onCitationClick,
}: SourcePaneProps) {
  const setCurrentBlock = useViewerStore((s) => s.setCurrentBlock);
  const pendingScroll = useViewerStore((s) => s.pendingScrollTarget);
  const consumeScroll = useViewerStore((s) => s.consumeScroll);
  const pendingHighlightQuery = useViewerStore((s) => s.pendingHighlightQuery);
  const setPendingHighlightQuery = useViewerStore((s) => s.setPendingHighlightQuery);
  const setPanel = useViewerStore((s) => s.setPanel);
  const requestAnnotationFocus = useViewerStore((s) => s.requestAnnotationFocus);
  const requestScroll = useViewerStore((s) => s.requestScroll);

  const scrollRef = useRef<HTMLDivElement>(null);
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

  // 注釈(ハイライト。原文モードは side='source' のみ配置対象)。TranslationPane・
  // AnnotationListPanel と同一キーでキャッシュを共有する。
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

  // ブロック単位の原文側ハイライト(1b §4.5-5 と対の side='source')+ 文書順の注釈番号
  // (AnnotationListPanel の番号と一致させる。訳文側の注釈も番号を消費する)。
  const highlightsByBlock = useMemo(() => {
    const map = new Map<string, PlacedHighlight[]>();
    let seq = 0;
    for (const a of annotationsQuery.data?.items ?? []) {
      if (!a.placed) continue;
      seq += 1;
      if (a.anchor.side !== "source") continue;
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

  // 本文の丸数字チップクリック → 注釈タブの該当カードへ(TranslationPane と同じ配線。1b §5.7)。
  const onAnnotationClick = (annotationId: string) => {
    setPanel(true, "annotations");
    requestAnnotationFocus(annotationId);
  };

  const doc = docQuery.data;
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
    content = <SkeletonLines />;
  } else {
    content = doc.sections.map((section) => (
      <SourceSection
        key={section.id}
        section={section}
        tocMap={tocMap}
        onExplainEquation={onExplainEquation}
        onCitationClick={onCitationClick}
        onRefClick={onRefClick}
        highlightsByBlock={highlightsByBlock}
        onAnnotationClick={onAnnotationClick}
        hlBlockId={hlBlockId}
        pendingHighlightQuery={pendingHighlightQuery}
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
        style={{ flex: 1, overflowY: "auto", display: "flex", justifyContent: "center" }}
      >
        <div
          style={{
            width: 720,
            maxWidth: "100%",
            padding: "40px 0 120px",
            fontFamily: "var(--pr-font-en)",
          }}
        >
          {content}
        </div>
      </div>
    </div>
  );
}

interface SourceSectionProps {
  section: DocSection;
  tocMap: Map<string, { number: string | null; titleJa: string | null }>;
  onExplainEquation?: (block: DocBlock) => void;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
  /** ブロック単位の原文側ハイライト(M1 統合ポリッシュ: hl パリティ)。 */
  highlightsByBlock: Map<string, PlacedHighlight[]>;
  onAnnotationClick: (annotationId: string) => void;
  /** `hl` を一発マークする対象ブロック(plans/11 §7)。 */
  hlBlockId: string | null;
  pendingHighlightQuery: string | null;
}

function SourceSection({
  section,
  tocMap,
  onExplainEquation,
  onCitationClick,
  onRefClick,
  highlightsByBlock,
  onAnnotationClick,
  hlBlockId,
  pendingHighlightQuery,
}: SourceSectionProps) {
  const meta = tocMap.get(section.id);
  const number = meta?.number ?? section.heading?.number ?? null;
  const titleEn = section.heading?.title ?? "";
  const headingBlock = sectionHeadingBlock(section);

  return (
    <section data-section-id={section.id}>
      {titleEn ? (
        <div data-block-id={headingBlock?.id}>
          <SectionHeading
            number={number}
            titleJa={null}
            titleEn={titleEn}
            variant={number ? "heading" : "label"}
          />
        </div>
      ) : null}
      {(section.blocks ?? [])
        .filter((block) => block.id !== headingBlock?.id && !isLatexSetupNoiseBlock(block))
        .map((block) => (
          <SourceBlock
            key={block.id}
            block={block}
            onExplainEquation={onExplainEquation}
            onCitationClick={onCitationClick}
            onRefClick={onRefClick}
            highlights={highlightsByBlock.get(block.id) ?? []}
            onAnnotationClick={onAnnotationClick}
            searchHighlight={hlBlockId === block.id ? pendingHighlightQuery : null}
          />
        ))}
      {(section.sections ?? []).map((sub) => (
        <SourceSection
          key={sub.id}
          section={sub}
          tocMap={tocMap}
          onExplainEquation={onExplainEquation}
          onCitationClick={onCitationClick}
          onRefClick={onRefClick}
          highlightsByBlock={highlightsByBlock}
          onAnnotationClick={onAnnotationClick}
          hlBlockId={hlBlockId}
          pendingHighlightQuery={pendingHighlightQuery}
        />
      ))}
    </section>
  );
}

function SourceBlock({
  block,
  onExplainEquation,
  onCitationClick,
  onRefClick,
  highlights = [],
  onAnnotationClick,
  searchHighlight = null,
}: {
  block: DocBlock;
  onExplainEquation?: (block: DocBlock) => void;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
  /** この段落に配置された原文側ハイライト(1b §4.5-5 と対の side='source')。 */
  highlights?: PlacedHighlight[];
  onAnnotationClick?: (annotationId: string) => void;
  /** 検索ヒット遷移の `?hl=`(plans/11 §7。遷移先ブロックのみ一発マーク)。 */
  searchHighlight?: string | null;
}) {
  if (isPaperFrontMatterBlock(block)) {
    return (
      <div data-block-id={block.id}>
        <PaperFrontMatterBlock block={block} />
      </div>
    );
  }

  if (block.type === "equation") {
    return (
      <div data-block-id={block.id}>
        <EquationBlock
          latex={block.latex ?? ""}
          assetUrl={block.asset_url}
          number={block.number}
          onExplain={() => onExplainEquation?.(block)}
        />
      </div>
    );
  }
  if (block.type === "heading") {
    return (
      <div data-block-id={block.id}>
        <SectionHeading number={block.number ?? null} titleJa={null} titleEn={block.title ?? ""} />
      </div>
    );
  }
  if (block.type === "code") {
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
  }
  if (block.type === "figure" || block.type === "table") {
    return (
      <FigureTableBlock
        block={block}
        showTranslatedCaption={false}
        onCitationClick={onCitationClick}
        onRefClick={onRefClick}
      />
    );
  }
  return (
    <p
      data-block-id={block.id}
      style={{
        // 設定 4f の本文サイズ。CSS 変数が未設定の間は既定値(15px)を維持する(§5.6)。
        fontSize: "var(--pr-content-font-size-px, 15px)",
        lineHeight: 1.8,
        color: "var(--pr-text-en)",
        margin: "0 0 20px",
        overflowWrap: "anywhere",
      }}
    >
      <InlineRenderer
        inlines={block.inlines ?? []}
        onCitationClick={onCitationClick}
        onRefClick={onRefClick}
        highlights={highlights}
        searchQuery={searchHighlight}
        onAnnotationClick={onAnnotationClick}
      />
    </p>
  );
}

function SkeletonLines() {
  const bar = (w: number | string, key?: number): ReactNode => (
    <div
      key={key}
      style={{
        width: w,
        height: 15,
        marginBottom: 10,
        borderRadius: 4,
        background: "var(--pr-bg-muted)",
        animation: "alinea-pulse 1.2s ease-in-out infinite",
      }}
    />
  );
  return (
    <div aria-hidden>
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => bar(i % 4 === 3 ? "70%" : "100%", i))}
    </div>
  );
}
