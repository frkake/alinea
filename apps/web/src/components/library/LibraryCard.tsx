"use client";

import type { CSSProperties, KeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { libraryItemsUpdate, type LibraryItemSummary } from "@yakudoku/api-client";
import type { ReadingStatus } from "@yakudoku/tokens";
import { Card } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { TagChip } from "@/components/ui/TagChip";
import { DeadlineBadge } from "@/components/ui/DeadlineBadge";
import { PriorityBadge } from "@/components/ui/PriorityBadge";
import { AiMark } from "@/components/ui/AIBadge";
import { useToast } from "@/components/ui/Toast";
import { cardBibLine, formatShortDate, toPriority, toQuality, toReadingStatus } from "@/components/library/format";
import { useFinishReadingStore } from "@/components/library/finishReadingStore";

/**
 * 論文カード(4a §4.7)。M0 スコープ:
 * - ✦ 3 行要約(summary_3line)
 * - パイプライン進捗(処理中は ProgressBar + 「読み始める →」= 部分読書導線 / readable-first)
 * - タグ提案チップ(suggested_tags、AI 生成マーク付き)
 * - フッタのステータス・締切・右端メタ(§4.8)
 * カード全体がリーダーへの導線(role="link")。
 */
export interface LibraryCardProps {
  item: LibraryItemSummary;
  /** カードクリック / 「読み始める」で /papers/{id} を開く。 */
  onOpen: (id: string) => void;
}

const MONOGRAM_TITLE: Record<"A" | "B", string> = {
  A: "品質レベルA: LaTeXソースから完全構造化",
  B: "品質レベルB: PDF由来",
};

function hours(seconds: number): string {
  return `${(seconds / 3600).toFixed(1)}h`;
}

type RightMeta =
  | { kind: "text"; text: string }
  | { kind: "priority"; priority: "high" | "mid" | "low" };

/** 右端メタ(4a §4.8 の確定規則)。表示なしは null。 */
function rightMeta(item: LibraryItemSummary): RightMeta | null {
  const priority = toPriority(item.priority);
  if (item.status === "done" && item.comprehension != null) {
    return { kind: "text", text: `理解 ${item.comprehension}/5 · ${hours(item.reading_seconds_total)}` };
  }
  if (item.status === "done") {
    return { kind: "text", text: hours(item.reading_seconds_total) };
  }
  if (priority) {
    return { kind: "priority", priority };
  }
  if (item.reading_seconds_total > 0) {
    return { kind: "text", text: hours(item.reading_seconds_total) };
  }
  return null;
}

export function LibraryCard({ item, onOpen }: LibraryCardProps) {
  const status = toReadingStatus(item.status);
  const quality = toQuality(item.quality_level);
  const summary = item.summary_3line && item.summary_3line.length > 0 ? item.summary_3line.join("") : null;
  const deadline = formatShortDate(item.deadline);
  const meta = rightMeta(item);
  const pipeline = item.pipeline && item.pipeline.status !== "failed" ? item.pipeline : null;
  const processing = pipeline != null;
  const progressPct = pipeline ? pipeline.progress_pct : item.progress_pct;
  const showProgress = processing || status === "reading";
  const qc = useQueryClient();
  const toast = useToast();

  const open = () => {
    onOpen(item.id);
  };
  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  };

  // ステータスピル(カード内)からの変更(1g §2.3 の発火規約: done への PATCH 成功で読了ダイアログを開く)。
  const onStatusChange = (next: ReadingStatus) => {
    if (next === status) return;
    const prevStatus = status;
    void libraryItemsUpdate({
      path: { item_id: item.id },
      body: { status: next },
      throwOnError: true,
    }).then(
      (res) => {
        void qc.invalidateQueries({ queryKey: ["library"] });
        void qc.invalidateQueries({ queryKey: ["dashboard"] });
        if (prevStatus !== "done" && next === "done" && res.data) {
          useFinishReadingStore.getState().open(res.data);
        }
      },
      () => {
        toast({ kind: "error", message: "ステータスを変更できませんでした" });
      },
    );
  };

  const clampStyle = (lines: number): CSSProperties => ({
    display: "-webkit-box",
    WebkitLineClamp: lines,
    WebkitBoxOrient: "vertical",
    overflow: "hidden",
  });

  return (
    <Card
      as="article"
      role="link"
      tabIndex={0}
      aria-label={item.paper.title}
      onClick={open}
      onKeyDown={onKey}
      style={{ display: "flex", flexDirection: "column", cursor: "pointer" }}
    >
      {/* サムネイル帯 */}
      <div
        style={{
          position: "relative",
          height: 108,
          flex: "none",
          background: "var(--pr-bg-thumb)",
          borderBottom: "1px solid var(--pr-border-pane)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--pr-text-thumb)",
          fontSize: 10,
        }}
      >
        {item.thumbnail_url ? (
          <img
            src={item.thumbnail_url}
            alt={item.paper.title}
            style={{ width: "100%", height: 108, objectFit: "cover" }}
          />
        ) : (
          <span>図なし</span>
        )}
        <span
          title={MONOGRAM_TITLE[quality]}
          style={{
            position: "absolute",
            top: 8,
            left: 8,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 17,
            height: 17,
            borderRadius: 4,
            background: "var(--pr-bg-card)",
            color: quality === "A" ? "var(--pr-acc)" : "var(--pr-text-sub2)",
            fontSize: 10,
            fontWeight: 700,
            boxShadow: "var(--pr-shadow-mono)",
          }}
        >
          {quality}
        </span>
      </div>

      {/* 本文 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "11px 13px", flex: 1 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.5, ...clampStyle(2) }}>
          {item.paper.title}
        </div>
        <div style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>{cardBibLine(item.paper)}</div>

        {summary ? (
          <div style={{ fontSize: 10.5, lineHeight: 1.6, color: "var(--pr-text-sub)", ...clampStyle(2) }}>
            <AiMark /> {summary}
          </div>
        ) : null}

        {item.suggested_tags.length > 0 ? (
          <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
            <span aria-label="タグ提案" title="AI生成のタグ提案">
              <AiMark />
            </span>
            {item.suggested_tags.slice(0, 3).map((tag) => (
              <TagChip key={tag} size="card">
                {tag}
              </TagChip>
            ))}
          </div>
        ) : null}

        {/* フッタ */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: "auto" }}>
          {showProgress ? <ProgressBar value={progressPct} color="accent" height={3} /> : null}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span onClick={(e) => e.stopPropagation()}>
              <StatusPill
                status={status}
                size="sm"
                variant="pill"
                interactive
                onChange={onStatusChange}
              />
            </span>
            {deadline ? (
              <DeadlineBadge date={deadline} variant="chip" withLabel />
            ) : item.tags.length > 0 ? (
              <TagChip size="card">{item.tags[0]}</TagChip>
            ) : null}

            {processing ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  open();
                }}
                style={{
                  marginLeft: "auto",
                  border: "none",
                  background: "transparent",
                  padding: 0,
                  fontSize: 10.5,
                  fontWeight: 600,
                  color: "var(--pr-acc)",
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                読み始める →
              </button>
            ) : meta ? (
              <span
                style={{
                  marginLeft: "auto",
                  fontSize: 9.5,
                  color: "var(--pr-text-muted)",
                }}
              >
                {meta.kind === "priority" ? (
                  <PriorityBadge priority={meta.priority} withPrefix />
                ) : (
                  <span>{meta.text}</span>
                )}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </Card>
  );
}
