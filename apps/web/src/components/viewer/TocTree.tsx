"use client";

import type { CSSProperties } from "react";
import type { TocNode } from "@yakudoku/api-client";
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
  const onDemand = toc.filter((n) => n.on_demand);
  const regular = toc.filter((n) => !n.on_demand);

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
        {regular.map((node) => (
          <TocRowGroup
            key={node.section_id}
            node={node}
            activeSectionId={activeSectionId}
            onSectionClick={onSectionClick}
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
            付録を一括翻訳
          </button>
        ) : null}

        {onDemand.map((node) => (
          <div
            key={node.section_id}
            role="button"
            tabIndex={0}
            onClick={() => {
              onSectionClick(node.section_id);
              onTranslateAppendix(node.section_id);
            }}
            style={{
              margin: "8px 6px 0",
              border: "1px dashed var(--pr-border-dashed)",
              borderRadius: 6,
              padding: "8px 9px",
              display: "flex",
              flexDirection: "column",
              gap: 5,
              cursor: "pointer",
            }}
          >
            <span style={{ fontSize: 11.5, color: "var(--pr-text-sub)" }}>
              {node.number ? `${node.number} ` : ""}
              {node.title_ja ?? node.title_en}{" "}
              <span style={{ color: "var(--pr-text-muted)" }}>— 未翻訳</span>
            </span>
            <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)", lineHeight: 1.5 }}>
              開くと翻訳します(オンデマンド)
            </span>
          </div>
        ))}
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
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
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
}: {
  node: TocNode;
  activeSectionId: string | null;
  onSectionClick: (sectionId: string) => void;
}) {
  return (
    <>
      <TocRow node={node} depth={0} activeSectionId={activeSectionId} onSectionClick={onSectionClick} />
      {(node.children ?? []).map((child) => (
        <TocRow
          key={child.section_id}
          node={child}
          depth={1}
          activeSectionId={activeSectionId}
          onSectionClick={onSectionClick}
        />
      ))}
    </>
  );
}

function TocRow({
  node,
  depth,
  activeSectionId,
  onSectionClick,
}: {
  node: TocNode;
  depth: number;
  activeSectionId: string | null;
  onSectionClick: (sectionId: string) => void;
}) {
  const active = node.section_id === activeSectionId;
  const outOfDenominator = !node.in_progress_denominator;
  const style: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: depth > 0 ? "4px 8px 4px 22px" : "4px 8px",
    borderRadius: 5,
    border: "none",
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
    background: active ? "var(--pr-acc-s)" : "transparent",
    color: active ? "var(--pr-acc)" : outOfDenominator ? "var(--pr-text-icon)" : "var(--pr-text-nav)",
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
      onClick={() => onSectionClick(node.section_id)}
    >
      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {node.number ? `${node.number} ` : ""}
        {node.title_ja ?? node.title_en}
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
