"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  translationsListUnits,
  viewerGetDocument,
  type LastPosition,
  type TocNode,
  type TranslationUnitItem,
} from "@yakudoku/api-client";
import { useViewerStore, type TranslationStyle } from "@/stores/viewer-store";
import { useViewerChatStore } from "@/stores/viewer-chat-store";
import { EquationBlock } from "@/components/viewer/EquationBlock";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { ResumeBanner } from "@/components/viewer/ResumeBanner";
import { SectionHeading } from "@/components/viewer/SectionHeading";
import { TranslationColumnHeader } from "@/components/viewer/TranslationColumnHeader";
import type { DocBlock, DocSection, DocumentResponse } from "@/components/viewer/document-types";

/** text_ja が null で返る翻訳失敗系フラグ(plans/06 §12)。 */
const FAILURE_FLAGS = new Set(["placeholder_mismatch", "provider_refusal", "untranslated"]);

export interface BilingualPaneProps {
  itemId: string;
  revisionId: string;
  style: TranslationStyle;
  toc: TocNode[];
  lastPosition: LastPosition | null;
  /** 「✦ この式を説明」→ 引用を積んでチャットタブへ(1a §5.2)。 */
  onExplainEquation?: (block: DocBlock) => void;
  /** 引用 [n] クリック → 図表タブ参考文献展開(1a §5.2)。 */
  onCitationClick?: (refId: string) => void;
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

/**
 * 対訳モード本文(1a §4.4)。段落単位 2 カラム(左=原文 / 右=訳文)。
 * document(原文構造)+ units(訳文)を取得し、段落ペアを単一 grid の行として並べる。
 */
export function BilingualPane({
  revisionId,
  style,
  toc,
  lastPosition,
  onExplainEquation,
  onCitationClick,
}: BilingualPaneProps) {
  const setCurrentBlock = useViewerStore((s) => s.setCurrentBlock);
  const pendingScroll = useViewerStore((s) => s.pendingScrollTarget);
  const consumeScroll = useViewerStore((s) => s.consumeScroll);
  const chatEvidence = useViewerChatStore((s) => s.chatEvidence);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [pairSync, setPairSync] = useState(true);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  const docQuery = useQuery({
    queryKey: ["document", revisionId],
    queryFn: async () =>
      (await viewerGetDocument({ path: { revision_id: revisionId }, throwOnError: true }))
        .data as DocumentResponse,
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
  const blockSectionMap = useMemo(() => buildBlockSectionMap(doc?.sections ?? []), [doc]);

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
      el.classList.add("yk-block-flash");
      window.setTimeout(() => el.classList.remove("yk-block-flash"), 2000);
    }
    consumeScroll();
  }, [pendingScroll, doc, consumeScroll]);

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
      />
    ));
  }

  return (
    <div style={{ flex: 1, minWidth: 0, position: "relative", display: "flex", overflow: "hidden" }}>
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
}

function SectionColumns({
  section,
  tocMap,
  unitMap,
  chatEvidenceBlockId,
  chatEvidenceDisplay,
  onExplainEquation,
  onCitationClick,
}: SectionColumnsProps) {
  const meta = tocMap.get(section.id);
  const number = meta?.number ?? section.heading?.number ?? null;
  const titleEn = section.heading?.title ?? "";
  const titleJa = meta?.titleJa ?? null;

  return (
    <section data-section-id={section.id}>
      {titleEn ? (
        <SectionHeading number={number} titleJa={titleJa} titleEn={titleEn} variant={number ? "heading" : "label"} />
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
        {(section.blocks ?? []).map((block) => {
          if (block.type === "paragraph") {
            return (
              <BilingualParagraph
                key={block.id}
                block={block}
                unit={unitMap.get(block.id) ?? null}
                onCitationClick={onCitationClick}
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
            <div key={block.id} data-block-id={block.id} style={{ gridColumn: "1 / -1" }}>
              <OtherBlock block={block} unit={unitMap.get(block.id) ?? null} />
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
}

/** 段落ペア 1 行(左=原文セル / 右=訳文セル)。単一 grid の行として並ぶ(1a §4.4)。 */
export function BilingualParagraph({ block, unit, onCitationClick }: BilingualParagraphProps) {
  const inlines = block.inlines ?? [];
  const hasTranslation = unit != null && unit.text_ja != null;
  const failed = !hasTranslation && (unit?.quality_flags ?? []).some((f) => FAILURE_FLAGS.has(f));

  return (
    <>
      <div
        data-block-id={block.id}
        data-side="source"
        style={{
          fontFamily: "var(--pr-font-en)",
          fontSize: 13.8,
          lineHeight: 1.72,
          color: "var(--pr-text-en)",
        }}
      >
        <InlineRenderer inlines={inlines} onCitationClick={onCitationClick} />
      </div>
      <div
        data-side="translation"
        style={{
          fontFamily: "var(--pr-jp)",
          fontSize: 14.8,
          lineHeight: 2.0,
          color: "var(--pr-text-body)",
        }}
      >
        {hasTranslation ? (
          unit?.text_ja
        ) : failed ? (
          <span style={{ fontSize: 12, fontFamily: "var(--pr-font-ui)", color: "var(--pr-warn)" }}>
            この段落の翻訳に失敗しました
          </span>
        ) : (
          <span style={{ fontSize: 12, fontFamily: "var(--pr-font-ui)", color: "var(--pr-text-muted)" }}>
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
      <EquationBlock latex={block.latex ?? ""} number={block.number} onExplain={() => onExplain?.(block)} />
    </div>
  );
}

/** 段落・数式以外(見出し・図表・コード等)。全幅で簡素に描画。 */
function OtherBlock({ block, unit }: { block: DocBlock; unit: TranslationUnitItem | null }) {
  if (block.type === "heading") {
    return <SectionHeading number={block.number ?? null} titleJa={null} titleEn={block.title ?? ""} />;
  }
  if (block.type === "figure" || block.type === "table") {
    const caption = block.caption ?? [];
    return (
      <figure
        style={{
          margin: "12px 0",
          padding: "12px 14px",
          border: "1px solid var(--pr-border-card)",
          borderRadius: 8,
          fontFamily: "var(--pr-font-ui)",
          fontSize: 12.5,
          color: "var(--pr-text-mid)",
        }}
      >
        <span style={{ fontWeight: 600 }}>{block.label ?? (block.type === "figure" ? "図" : "表")}</span>{" "}
        <InlineRenderer inlines={caption} />
      </figure>
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
    <p style={{ fontSize: 14.8, lineHeight: 2.0, color: "var(--pr-text-body)", margin: 0 }}>
      {text != null ? text : <InlineRenderer inlines={block.inlines ?? []} />}
    </p>
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
        animation: "yk-pulse 1.2s ease-in-out infinite",
      }}
    />
  );
  return (
    <div aria-hidden style={{ display: "grid", gridTemplateColumns: "1fr 1fr", columnGap: 34, rowGap: 18 }}>
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
