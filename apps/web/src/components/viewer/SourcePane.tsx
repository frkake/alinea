"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { viewerGetDocument, type LastPosition, type TocNode } from "@yakudoku/api-client";
import { useViewerStore } from "@/stores/viewer-store";
import { EquationBlock } from "@/components/viewer/EquationBlock";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { ResumeBanner } from "@/components/viewer/ResumeBanner";
import { SectionHeading } from "@/components/viewer/SectionHeading";
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
 * 原文モード本文(viewer-shell §11。3 モードの 1 つ)。英語原文のみを 1 カラムで通読する。
 * 訳文ユニットは取得せず、document の inlines を Source Serif で描画する。
 */
export function SourcePane({
  revisionId,
  toc,
  lastPosition,
  onExplainEquation,
  onCitationClick,
}: SourcePaneProps) {
  const setCurrentBlock = useViewerStore((s) => s.setCurrentBlock);
  const pendingScroll = useViewerStore((s) => s.pendingScrollTarget);
  const consumeScroll = useViewerStore((s) => s.consumeScroll);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  const docQuery = useQuery({
    queryKey: ["document", revisionId],
    queryFn: async () =>
      (await viewerGetDocument({ path: { revision_id: revisionId }, throwOnError: true }))
        .data as DocumentResponse,
    staleTime: Infinity,
  });

  const doc = docQuery.data;
  const tocMap = useMemo(() => buildTocMap(toc), [toc]);
  const blockSectionMap = useMemo(() => buildBlockSectionMap(doc?.sections ?? []), [doc]);

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
    content = <SkeletonLines />;
  } else {
    content = doc.sections.map((section) => (
      <SourceSection
        key={section.id}
        section={section}
        tocMap={tocMap}
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
}

function SourceSection({ section, tocMap, onExplainEquation, onCitationClick }: SourceSectionProps) {
  const meta = tocMap.get(section.id);
  const number = meta?.number ?? section.heading?.number ?? null;
  const titleEn = section.heading?.title ?? "";

  return (
    <section data-section-id={section.id}>
      {titleEn ? (
        <SectionHeading number={number} titleJa={null} titleEn={titleEn} variant={number ? "heading" : "label"} />
      ) : null}
      {(section.blocks ?? []).map((block) => (
        <SourceBlock
          key={block.id}
          block={block}
          onExplainEquation={onExplainEquation}
          onCitationClick={onCitationClick}
        />
      ))}
      {(section.sections ?? []).map((sub) => (
        <SourceSection
          key={sub.id}
          section={sub}
          tocMap={tocMap}
          onExplainEquation={onExplainEquation}
          onCitationClick={onCitationClick}
        />
      ))}
    </section>
  );
}

function SourceBlock({
  block,
  onExplainEquation,
  onCitationClick,
}: {
  block: DocBlock;
  onExplainEquation?: (block: DocBlock) => void;
  onCitationClick?: (refId: string) => void;
}) {
  if (block.type === "equation") {
    return (
      <div data-block-id={block.id}>
        <EquationBlock latex={block.latex ?? ""} number={block.number} onExplain={() => onExplainEquation?.(block)} />
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
  return (
    <p
      data-block-id={block.id}
      style={{
        fontSize: 15,
        lineHeight: 1.8,
        color: "var(--pr-text-en)",
        margin: "0 0 20px",
      }}
    >
      <InlineRenderer inlines={block.inlines ?? []} onCitationClick={onCitationClick} />
    </p>
  );
}

function SkeletonLines() {
  const bar = (w: number | string): ReactNode => (
    <div
      style={{
        width: w,
        height: 15,
        marginBottom: 10,
        borderRadius: 4,
        background: "var(--pr-bg-muted)",
        animation: "yk-pulse 1.2s ease-in-out infinite",
      }}
    />
  );
  return (
    <div aria-hidden>
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => bar(i % 4 === 3 ? "70%" : "100%"))}
    </div>
  );
}
