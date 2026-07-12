"use client";

import type { CSSProperties, ReactNode } from "react";
import type { TocNode } from "@alinea/api-client";
import { BookmarkIcon, MagnifierIcon } from "@/components/icons";
import { CountBadge } from "@/components/ui/CountBadge";

export interface TocTreeProps {
  toc: TocNode[];
  /** viewer.translation.progress_pct(四捨五入整数)。 */
  progressPct: number;
  /** 今日の読書分(viewer.today_reading_minutes)。 */
  todayReadingMinutes: number;
  /** reading.track_reading_time。false で「今日の読書 N分」を非表示。 */
  trackReadingTime?: boolean;
  open: boolean;
  onToggle: (open: boolean) => void;
  activeSectionId: string | null;
  onSectionClick: (sectionId: string) => void;
  /** 未翻訳付録(on_demand)を開いたときのオンデマンド翻訳発火。 */
  onTranslateAppendix: (sectionId: string) => void;
  /** レールの虫眼鏡・ヘッダ検索へフォーカス。 */
  onFocusSearch: () => void;
}

const railIconBtn: CSSProperties = {
  border: "none",
  background: "transparent",
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
};

/** 左レール(44px)⇄ 目次ペイン(232px)(viewer-shell §5)。 */
export function TocTree(props: TocTreeProps) {
  return props.open ? <TocPane {...props} /> : <TocRail {...props} />;
}

function TocRail({ onToggle, onFocusSearch }: TocTreeProps) {
  return (
    <nav
      aria-label="目次"
      style={{
        width: 44,
        flex: "none",
        background: "var(--pr-bg-pane)",
        borderRight: "1px solid var(--pr-border-pane)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "12px 0",
        gap: 14,
      }}
    >
      <button
        type="button"
        aria-label="目次を開く"
        onClick={() => onToggle(true)}
        style={{ ...railIconBtn, fontSize: 13, color: "var(--pr-text-sub)" }}
      >
        ☰
      </button>
      <button
        type="button"
        aria-label="ブックマーク"
        onClick={() => onToggle(true)}
        style={{ ...railIconBtn, color: "var(--pr-text-muted)" }}
      >
        <BookmarkIcon size={12} />
      </button>
      <button
        type="button"
        aria-label="論文内を検索"
        onClick={onFocusSearch}
        style={{ ...railIconBtn, color: "var(--pr-text-muted)" }}
      >
        <MagnifierIcon size={12} />
      </button>
    </nav>
  );
}

function TocPane({
  toc,
  progressPct,
  todayReadingMinutes,
  trackReadingTime = true,
  onToggle,
  activeSectionId,
  onSectionClick,
  onTranslateAppendix,
}: TocTreeProps) {
  const onDemand = collectOnDemandSections(toc);

  return (
    <nav
      aria-label="目次"
      style={{
        width: 232,
        flex: "none",
        background: "var(--pr-bg-pane)",
        borderRight: "1px solid var(--pr-border-pane)",
        display: "flex",
        flexDirection: "column",
        padding: "10px 8px 8px",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "0 8px 8px",
        }}
      >
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--pr-text-icon)" }}>目次</span>
        <span style={{ display: "inline-flex", alignItems: "center" }}>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-icon)" }}>
            翻訳 {Math.round(progressPct)}%
          </span>
          <button
            type="button"
            aria-label="目次を折りたたむ"
            onClick={() => onToggle(false)}
            style={{
              marginLeft: 6,
              border: "none",
              background: "transparent",
              cursor: "pointer",
              color: "var(--pr-text-faint)",
            }}
          >
            ⟨⟨
          </button>
        </span>
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          fontSize: 12.3,
          color: "var(--pr-text-nav)",
          flex: 1,
          overflowY: "auto",
        }}
      >
        {toc.map((node) => (
          <TocRowGroup
            key={node.section_id}
            node={node}
            activeSectionId={activeSectionId}
            onSectionClick={onSectionClick}
            onTranslateSection={onTranslateAppendix}
          />
        ))}

        {onDemand.length > 0 ? (
          <button
            type="button"
            onClick={() => onDemand.forEach((node) => onTranslateAppendix(node.section_id))}
            style={{
              margin: "8px 6px 0",
              border: "1px solid var(--pr-border-control)",
              borderRadius: 6,
              padding: "7px 9px",
              background: "var(--pr-bg-inset)",
              color: "var(--pr-acc)",
              cursor: "pointer",
              fontFamily: "var(--pr-font-ui)",
              fontSize: 11.5,
              fontWeight: 600,
              textAlign: "left",
            }}
          >
            未翻訳セクションを一括翻訳
          </button>
        ) : null}
      </div>

      <div
        style={{
          padding: "8px 8px 2px",
          borderTop: "1px solid var(--pr-border-pane)",
          display: "flex",
          gap: 8,
          justifyContent: "space-between",
          fontSize: 10.5,
          color: "var(--pr-text-muted)",
          minWidth: 0,
        }}
      >
        {trackReadingTime ? (
          <span
            style={{
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            今日の読書 {todayReadingMinutes}分
          </span>
        ) : (
          <span />
        )}
        <span style={{ flex: "none", whiteSpace: "nowrap" }}>位置は自動保存</span>
      </div>
    </nav>
  );
}

/**
 * 目次 1 節+子節分の行グループ。PDF モード左サイドバー(2a §4.2.2・viewer-shell §5.5)が
 * 「目次」タブの内部リストとして再利用する(export)。
 */
export function TocRowGroup({
  node,
  activeSectionId,
  onSectionClick,
  onTranslateSection,
}: {
  node: TocNode;
  activeSectionId: string | null;
  onSectionClick: (sectionId: string) => void;
  onTranslateSection?: (sectionId: string) => void;
}) {
  const renderNode = (entry: TocNode, depth: number): ReactNode => (
    <div key={entry.section_id}>
      <TocRow
        node={entry}
        depth={depth}
        activeSectionId={activeSectionId}
        onSectionClick={onSectionClick}
        onTranslateSection={onTranslateSection}
      />
      {(entry.children ?? []).map((child) => renderNode(child, depth + 1))}
    </div>
  );
  return <>{renderNode(node, 0)}</>;
}

function TocRow({
  node,
  depth,
  activeSectionId,
  onSectionClick,
  onTranslateSection,
}: {
  node: TocNode;
  depth: number;
  activeSectionId: string | null;
  onSectionClick: (sectionId: string) => void;
  onTranslateSection?: (sectionId: string) => void;
}) {
  const active = node.section_id === activeSectionId;
  const outOfDenominator = !node.in_progress_denominator;
  const style: CSSProperties = {
    display: "flex",
    alignItems: "flex-start",
    gap: 6,
    padding: `4px 8px 4px ${8 + depth * 14}px`,
    borderRadius: 5,
    border: "none",
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
    background: active ? "var(--pr-acc-s)" : "transparent",
    color: active
      ? "var(--pr-acc)"
      : outOfDenominator
        ? "var(--pr-text-icon)"
        : "var(--pr-text-nav)",
    fontWeight: active ? 600 : 400,
    boxShadow: active ? "inset 2px 0 var(--pr-acc)" : undefined,
    fontFamily: "var(--pr-font-ui)",
    fontSize: 12.3,
  };
  return (
    <button
      type="button"
      style={style}
      aria-current={active ? "true" : undefined}
      onClick={() => {
        onSectionClick(node.section_id);
        if (node.on_demand) onTranslateSection?.(node.section_id);
      }}
    >
      <span style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
        <span
          style={{
            display: "block",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {node.number ? `${node.number} ` : ""}
          {node.title_ja ?? node.title_en}
          {node.on_demand ? <span style={{ color: "var(--pr-text-muted)" }}> — 未翻訳</span> : null}
        </span>
        {node.on_demand ? (
          <span
            style={{
              display: "block",
              marginTop: 2,
              fontSize: 10.5,
              color: "var(--pr-text-muted)",
              lineHeight: 1.4,
              whiteSpace: "normal",
              overflowWrap: "anywhere",
            }}
          >
            開くと翻訳します(オンデマンド)
          </span>
        ) : null}
      </span>
      {!outOfDenominator && node.annotation_count > 0 ? (
        <CountBadge count={node.annotation_count} variant="annotation" />
      ) : null}
      {!outOfDenominator && node.bookmarked ? (
        <span style={{ color: "var(--pr-acc)", display: "inline-flex" }}>
          <BookmarkIcon size={11} />
        </span>
      ) : null}
      {!outOfDenominator && node.translated ? (
        <span style={{ fontSize: 10, color: "var(--pr-green-check)" }}>✓</span>
      ) : null}
    </button>
  );
}

export function collectOnDemandSections(nodes: TocNode[]): TocNode[] {
  return nodes.flatMap((node) => [
    ...(node.on_demand ? [node] : []),
    ...collectOnDemandSections(node.children ?? []),
  ]);
}
