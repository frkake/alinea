"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  translationsListUnits,
  viewerGetDocument,
  type LastPosition,
  type TocNode,
  type TranslationUnitItem,
} from "@yakudoku/api-client";
import { useToast } from "@/components/ui/Toast";
import { useViewerStore, type TranslationStyle } from "@/stores/viewer-store";
import { EquationBlock } from "@/components/viewer/EquationBlock";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { ResumeBanner } from "@/components/viewer/ResumeBanner";
import { SectionHeading } from "@/components/viewer/SectionHeading";
import { SelectionMenu } from "@/components/viewer/SelectionMenu";
import { SummaryCard } from "@/components/viewer/SummaryCard";
import { TranslatedParagraph } from "@/components/viewer/TranslatedParagraph";
import type {
  DocBlock,
  DocSection,
  DocumentResponse,
} from "@/components/viewer/document-types";

export interface TranslationPaneProps {
  itemId: string;
  revisionId: string;
  style: TranslationStyle;
  toc: TocNode[];
  summaryLines: string[] | null;
  lastPosition: LastPosition | null;
  /** 詳細要約 → チャットタブ導線(docs/04 §3)。 */
  onDetailedSummary?: () => void;
  /** ✦AIに質問 / ✦この式を説明 → チャットタブ導線。 */
  onAskAI?: (quote: string) => void;
}

/** section_id → { number, title_ja } を toc(2 階層)から引く。 */
function buildTocMap(toc: TocNode[]): Map<string, { number: string | null; titleJa: string | null }> {
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
  revisionId,
  style,
  toc,
  summaryLines,
  lastPosition,
  onDetailedSummary,
  onAskAI,
}: TranslationPaneProps) {
  // itemId は契約に含むが、読書位置保存は ViewerShell の useReadingPosition が担う。
  const toast = useToast();
  const panelOpen = useViewerStore((s) => s.panelOpen);
  const activeTab = useViewerStore((s) => s.activeTab);
  const setCurrentBlock = useViewerStore((s) => s.setCurrentBlock);
  const currentBlockId = useViewerStore((s) => s.currentBlockId);
  const popSignal = useViewerStore((s) => s.bilingualPopToggleSignal);
  const pendingScroll = useViewerStore((s) => s.pendingScrollTarget);
  const consumeScroll = useViewerStore((s) => s.consumeScroll);
  const selection = useViewerStore((s) => s.selection);
  const setSelection = useViewerStore((s) => s.setSelection);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [openPopBlockId, setOpenPopBlockId] = useState<string | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  const docQuery = useQuery({
    queryKey: ["document", revisionId],
    queryFn: async () =>
      (
        await viewerGetDocument({ path: { revision_id: revisionId }, throwOnError: true })
      ).data as DocumentResponse,
    staleTime: Infinity,
  });

  const doc = docQuery.data;
  const sectionIds = useMemo(() => collectSectionIds(doc?.sections ?? []), [doc]);

  const unitMap = useQueries({
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
      enabled: sectionIds.length > 0,
      staleTime: 60_000,
    })),
    combine: (results) => {
      const map = new Map<string, TranslationUnitItem>();
      for (const r of results) {
        for (const item of r.data?.items ?? []) map.set(item.block_id, item);
      }
      return map;
    },
  });

  const tocMap = useMemo(() => buildTocMap(toc), [toc]);
  const blockSectionMap = useMemo(
    () => buildBlockSectionMap(doc?.sections ?? []),
    [doc],
  );

  const colWidth = !panelOpen ? 720 : activeTab === "annotations" ? 720 : 680;

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
      el.classList.add("yk-block-flash");
      window.setTimeout(() => el.classList.remove("yk-block-flash"), 2000);
    }
    consumeScroll();
  }, [pendingScroll, doc, consumeScroll]);

  // テキスト選択 → 選択メニュー(1b §5.5。M0 は ✦AIに質問 / コピー)。
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
    const rect = range.getBoundingClientRect();
    setSelection({
      blockId: blockEl.dataset.blockId ?? "",
      side: "translation",
      quote: text.slice(0, 500),
      rect: { top: rect.top, left: rect.left, bottom: rect.bottom, right: rect.right },
    });
  }, [setSelection]);

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

  const showBanner = lastPosition != null && !bannerDismissed;

  let content: ReactNode;
  if (docQuery.isError) {
    content = (
      <div style={{ padding: "80px 0", textAlign: "center", color: "var(--pr-warn)" }}>
        論文を読み込めませんでした ·{" "}
        <button
          type="button"
          onClick={() => void docQuery.refetch()}
          style={{ border: "none", background: "transparent", color: "var(--pr-acc)", cursor: "pointer" }}
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
        style={{ flex: 1, overflowY: "auto", display: "flex", justifyContent: "center" }}
      >
        <div
          style={{
            width: colWidth,
            maxWidth: "100%",
            padding: "64px 0 120px",
            fontFamily: "var(--pr-jp)",
          }}
        >
          {content}
        </div>
      </div>
      {selection ? (
        <SelectionMenu
          milestone="M0"
          side={selection.side}
          position={{ top: selection.rect.bottom + 8, left: selection.rect.left }}
          onAskAI={() => {
            onAskAI?.(selection.quote);
            setSelection(null);
          }}
          onCopy={copySelection}
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
}: SectionViewProps) {
  const meta = tocMap.get(section.id);
  const number = meta?.number ?? section.heading?.number ?? null;
  const titleEn = section.heading?.title ?? "";
  const titleJa = meta?.titleJa ?? null;
  const isAbstract = !number;

  // セクション内 paragraph の 1 始まり序数(対訳ラベル用)。
  let paraOrdinal = 0;
  const sectionLabel = `${number ? `${number} ` : ""}${titleEn}`.trim();

  return (
    <section data-section-id={section.id}>
      {titleEn ? (
        <SectionHeading
          number={number}
          titleJa={titleJa}
          titleEn={titleEn}
          variant={isAbstract ? "label" : "heading"}
        />
      ) : null}
      {summary}
      {(section.blocks ?? []).map((block) => {
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
            />
          );
        }
        return (
          <BlockView
            key={block.id}
            block={block}
            unit={unitMap.get(block.id) ?? null}
            onExplainEquation={onExplainEquation}
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
}: {
  block: DocBlock;
  unit: TranslationUnitItem | null;
  onExplainEquation: (latex: string) => void;
}) {
  switch (block.type) {
    case "equation":
      return (
        <div data-block-id={block.id}>
          <EquationBlock latex={block.latex ?? ""} number={block.number} onExplain={onExplainEquation} />
        </div>
      );
    case "heading":
      return (
        <div data-block-id={block.id}>
          <SectionHeading number={block.number ?? null} titleJa={null} titleEn={block.title ?? ""} />
        </div>
      );
    case "figure":
    case "table": {
      const caption = block.caption ?? [];
      return (
        <figure
          data-block-id={block.id}
          style={{
            margin: "20px 0",
            padding: "12px 14px",
            border: "1px solid var(--pr-border-card)",
            borderRadius: 8,
            fontFamily: "var(--pr-font-ui)",
            fontSize: 12.5,
            color: "var(--pr-text-mid)",
          }}
        >
          <span style={{ fontWeight: 600 }}>{block.label ?? (block.type === "figure" ? "図" : "表")}</span>
          {caption.length ? (
            <span>
              {" "}
              <InlineRenderer inlines={caption} />
            </span>
          ) : null}
        </figure>
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
          style={{ fontSize: 16.5, lineHeight: 2.15, color: "var(--pr-text-body)", margin: "0 0 22px" }}
        >
          {text != null ? text : <InlineRenderer inlines={inlines} />}
        </p>
      );
    }
  }
}

/** 初期スケルトン(1b §5.9)。 */
function PaneSkeleton() {
  const bar = (w: number | string, h: number, mb = 12): ReactNode => (
    <div
      style={{
        width: w,
        height: h,
        marginBottom: mb,
        borderRadius: 4,
        background: "var(--pr-bg-muted)",
        animation: "yk-pulse 1.6s ease-in-out infinite",
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
          {[0, 1, 2, 3].map((i) => bar(i === 3 ? "70%" : "100%", 17))}
        </div>
      ))}
    </div>
  );
}
