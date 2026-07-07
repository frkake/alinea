"use client";

import type { CSSProperties } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { papersReingest, type LibraryItemSummary, type PipelineState } from "@yakudoku/api-client";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { QualityBadge } from "@/components/ui/QualityBadge";
import { TagChip } from "@/components/ui/TagChip";
import { AiMark } from "@/components/ui/AIBadge";
import { useToast } from "@/components/ui/Toast";
import { cardBibLine, toQuality } from "@/components/library/format";

/** `GET /api/dashboard` と同一のキー(構造比較のため配列リテラルで揃える。1d 専用の簡易キー)。 */
const DASHBOARD_QUERY_KEY = ["dashboard"] as const;

/** 追加日時の表記(§3.3)。当日=「今日 H:mm」/1日前=「昨日 H:mm」/2日以上前=「M/D H:mm」。 */
export function formatAddedAt(iso: string, now: Date = new Date()): string {
  const target = new Date(iso);
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((startOf(now) - startOf(target)) / 86_400_000);
  const time = `${target.getHours()}:${String(target.getMinutes()).padStart(2, "0")}`;
  if (diffDays <= 0) return `今日 ${time}`;
  if (diffDays === 1) return `昨日 ${time}`;
  return `${target.getMonth() + 1}/${target.getDate()} ${time}`;
}

function isToday(iso: string, now: Date): boolean {
  const d = new Date(iso);
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

type Variant = "processing" | "failed" | "expanded" | "condensed";

/**
 * §5.5 は「pipeline?.stage === 'failed'」と表記するが、実装済みの `jobs.stage`(plans/05 §2.1)
 * は 8 値の処理段階のみを持ち、失敗は `status='failed'`(段階名はそのまま保持)で表す
 * (plans/05 §2.1 の決定・schemas/ingest.py `build_pipeline_state` の実装)。ここでは実際の
 * API 契約に合わせ `status` で判定する(deviations 記載)。
 */
function variantOf(item: LibraryItemSummary, now: Date): Variant {
  const pipeline = item.pipeline;
  if (pipeline) {
    if (pipeline.status === "failed") return "failed";
    if (pipeline.stage !== "complete") return "processing";
  }
  return isToday(item.added_at, now) ? "expanded" : "condensed";
}

/**
 * 「最近追加」セクション(plans/09-screens/1d-dashboard.md §4.6)。
 * `recent.items`(今週追加、最大6件)を変種決定規則(§5.5)に従って描画する。
 */
export interface RecentlyAddedProps {
  weekCount: number;
  items: LibraryItemSummary[];
  onOpen: (id: string) => void;
  now?: Date;
}

export function RecentlyAdded({ weekCount, items, onOpen, now = new Date() }: RecentlyAddedProps) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10, flex: 1, minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <h2 style={{ fontSize: 12, fontWeight: 700, color: "var(--pr-text-sub)", margin: 0 }}>
          最近追加
        </h2>
        <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>今週 {weekCount} 本</span>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="今週追加された論文はありません"
          description="取り込みはブラウザ拡張から行えます"
        />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 12,
            overflowY: "auto",
            minHeight: 0,
            alignContent: "start",
            paddingRight: 2,
          }}
        >
          {items.map((item) => (
            <RecentCard key={item.id} item={item} variant={variantOf(item, now)} onOpen={onOpen} />
          ))}
        </div>
      )}
    </section>
  );
}

function RecentCard({
  item,
  variant,
  onOpen,
}: {
  item: LibraryItemSummary;
  variant: Variant;
  onOpen: (id: string) => void;
}) {
  const clickable = variant !== "processing" && variant !== "failed";
  return (
    <Card
      as="article"
      role={clickable ? "link" : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-label={clickable ? item.paper.title : undefined}
      onClick={clickable ? () => onOpen(item.id) : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onOpen(item.id);
              }
            }
          : undefined
      }
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "12px 14px",
        cursor: clickable ? "pointer" : "default",
      }}
    >
      <CardHeader item={item} processing={variant === "processing"} />
      {variant === "processing" ? (
        <ProcessingBody item={item} onOpen={onOpen} />
      ) : variant === "failed" ? (
        <FailedBody item={item} />
      ) : variant === "expanded" ? (
        <ExpandedBody item={item} />
      ) : (
        <CondensedBody item={item} />
      )}
    </Card>
  );
}

function CardHeader({ item, processing }: { item: LibraryItemSummary; processing: boolean }) {
  const quality = toQuality(item.quality_level);
  return (
    <div style={{ display: "flex", gap: 11 }}>
      <div
        style={{
          width: 48,
          height: 64,
          flex: "none",
          borderRadius: 5,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 9,
          background: processing ? "var(--pr-bg-app-alt)" : "var(--pr-bg-thumb)",
          border: processing ? "1px dashed var(--pr-border-dashed)" : "1px solid var(--pr-border-thumb)",
          color: "var(--pr-text-muted)",
        }}
      >
        {processing ? "…" : "—"}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.5 }}>{item.paper.title}</div>
        <div style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
          {cardBibLine(item.paper)}
          {!processing ? (
            <>
              {" · "}
              <QualityBadge level={quality} size={17} />
              {item.source === "upload" ? " PDF 取り込み" : ""}
            </>
          ) : null}
          {" · "}
          {formatAddedAt(item.added_at)}
        </div>
      </div>
    </div>
  );
}

const STAGE_ORDER = [
  "queued",
  "fetching",
  "parsing",
  "structuring",
  "translating_abstract",
  "readable",
  "translating_body",
  "complete",
] as const;

function stageAtLeast(stage: string, target: (typeof STAGE_ORDER)[number]): boolean {
  const idx = STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number]);
  const targetIdx = STAGE_ORDER.indexOf(target);
  return idx >= 0 && idx >= targetIdx;
}

const statusLineStyle: CSSProperties = { fontSize: 10.5, color: "var(--pr-text-sub)" };
const doneLabelStyle: CSSProperties = { color: "var(--pr-green)" };
const activeLabelStyle: CSSProperties = { color: "var(--pr-acc)", fontWeight: 600 };

function ProcessingBody({
  item,
  onOpen,
}: {
  item: LibraryItemSummary;
  onOpen: (id: string) => void;
}) {
  const pipeline = item.pipeline as PipelineState;
  const bibDone = stageAtLeast(pipeline.stage, "structuring");
  const abstractDone = stageAtLeast(pipeline.stage, "readable");
  let thirdLine: string;
  if (pipeline.status === "waiting_quota") {
    thirdLine = "クォータ待機中";
  } else if (pipeline.stage === "translating_body") {
    thirdLine = `本文翻訳中 ${pipeline.progress_pct}%`;
  } else {
    thirdLine = "解析中…";
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 7, ...statusLineStyle }}>
        {bibDone ? <span style={doneLabelStyle}>✓ 書誌</span> : null}
        {abstractDone ? <span style={doneLabelStyle}>✓ アブスト訳・要約</span> : null}
        <span style={activeLabelStyle}>{thirdLine}</span>
      </div>
      <ProgressBar value={pipeline.progress_pct} color="accent" height={3} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        {pipeline.readable_upto ? (
          <>
            <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>
              {pipeline.readable_upto} まで読めます · 開いたセクションを優先翻訳
            </span>
            <button
              type="button"
              onClick={() => {
                onOpen(item.id);
              }}
              style={{
                display: "inline-flex",
                alignItems: "center",
                height: 24,
                padding: "0 12px",
                borderRadius: 6,
                border: "none",
                background: "var(--pr-acc)",
                color: "#FFFFFF",
                fontSize: 11,
                fontWeight: 600,
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              読み始める
            </button>
          </>
        ) : (
          <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>解析中です</span>
        )}
      </div>
    </div>
  );
}

function FailedBody({ item }: { item: LibraryItemSummary }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: async () =>
      (await papersReingest({ path: { paper_id: item.paper.id }, throwOnError: true })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: DASHBOARD_QUERY_KEY });
    },
    onError: () => {
      toast({ kind: "error", message: "再試行を開始できませんでした" });
    },
  });

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ fontSize: 10.5, color: "var(--pr-warn)", fontWeight: 600, flex: 1 }}>
        × 取り込みに失敗しました
      </span>
      <button
        type="button"
        disabled={retry.isPending}
        onClick={(e) => {
          e.stopPropagation();
          retry.mutate();
        }}
        style={{
          height: 24,
          padding: "0 12px",
          borderRadius: 6,
          border: "1px solid var(--pr-border-control)",
          background: "transparent",
          color: "var(--pr-text-mid)",
          fontSize: 11,
          fontWeight: 600,
          cursor: retry.isPending ? "default" : "pointer",
          opacity: retry.isPending ? 0.6 : 1,
          fontFamily: "inherit",
        }}
      >
        再試行
      </button>
    </div>
  );
}

function ExpandedBody({ item }: { item: LibraryItemSummary }) {
  const lines = item.summary_3line;
  const suggested = item.suggested_tags[0];
  return (
    <>
      {lines && lines.length > 0 ? (
        <div
          style={{
            fontSize: 11,
            lineHeight: 1.7,
            color: "var(--pr-text-mid)",
            background: "var(--pr-bg-hover)",
            borderRadius: 6,
            padding: "8px 10px",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          <AiMark /> <strong style={{ color: "var(--pr-acc)" }}>3行要約</strong> — ①{" "}
          {lines[0]} ② {lines[1]} ③ {lines[2]}
        </div>
      ) : null}
      {item.tags.length > 0 || suggested ? (
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
          {item.tags.map((tag) => (
            <TagChip key={tag}>{tag}</TagChip>
          ))}
          {suggested ? <SuggestedTagText tag={suggested} withPrefix /> : null}
        </div>
      ) : null}
    </>
  );
}

function CondensedBody({ item }: { item: LibraryItemSummary }) {
  const suggested = item.suggested_tags[0];
  return (
    <div style={{ fontSize: 10.5, color: "var(--pr-text-sub)" }}>
      <span style={doneLabelStyle}>✓ 翻訳完了</span>
      {suggested ? (
        <>
          {" · 提案タグ: "}
          <SuggestedTagText tag={suggested} withPrefix={false} />
        </>
      ) : (
        " — 読める状態です"
      )}
    </div>
  );
}

/**
 * ● SuggestedTagChip(§4.6)。1d では承認クリック(§5.8)は実装しない(deviations 記載。
 * 表示のみ・非対話)。
 */
function SuggestedTagText({ tag, withPrefix }: { tag: string; withPrefix: boolean }) {
  if (withPrefix) {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          height: 18,
          padding: "0 7px",
          borderRadius: 3,
          border: "1px dashed var(--pr-border-dashed)",
          color: "var(--pr-text-muted)",
          fontSize: 10,
        }}
      >
        提案: {tag} +
      </span>
    );
  }
  return (
    <span style={{ color: "var(--pr-acc)", fontWeight: 600 }}>{tag} +</span>
  );
}
