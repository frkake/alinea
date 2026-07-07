"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  annotationsCreate,
  annotationsDelete,
  annotationsUpdate,
  annotationsList,
  type Annotation,
} from "@yakudoku/api-client";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterChip } from "@/components/ui/FilterChip";
import { useToast } from "@/components/ui/Toast";
import { useViewerStore } from "@/stores/viewer-store";
import type { HighlightColor } from "@/components/ui/HighlightMark";

/** 注釈タブのフィルタ(1b §3.2 AnnFilter。クライアントサイドのみ)。 */
export type AnnFilter = "all" | "important" | "question" | "idea" | "with_comment";

const COLOR_LABEL: Record<HighlightColor, string> = {
  important: "重要",
  question: "疑問",
  idea: "アイデア",
  term: "用語",
};

const COLOR_HEX: Record<HighlightColor, string> = {
  important: "#C49432",
  question: "#5884AA",
  idea: "#659471",
  term: "#82827E",
};

/** 相対日時(1b §5.11)。今日/昨日/M・D/年・M・D。 */
function formatRelativeDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const startOfDay = (dt: Date) => new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime();
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  const hm = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (diffDays === 0) return `今日 ${hm}`;
  if (diffDays === 1) return `昨日 ${hm}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

export interface AnnotationListPanelProps {
  /**
   * モバイル縮退のボトムシート(mobile.md §4.5)から閲覧専用で再利用する場合 true。
   * コメント編集・削除ボタンを非描画にする(決定)。ジャンプ・フィルタ・エクスポートは維持。
   */
  readOnly?: boolean;
}

/**
 * 注釈タブ本体(1b §3.1 AnnotationsTab。viewer-shell §6.5: SidePanel からは props なし、
 * useViewerStore() から itemId を取得)。フィルタ+一覧+未配置+Markdown エクスポート導線。
 */
export function AnnotationListPanel({ readOnly = false }: AnnotationListPanelProps = {}) {
  const itemId = useViewerStore((s) => s.itemId);
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const pendingAnnotationId = useViewerStore((s) => s.pendingAnnotationId);
  const consumeAnnotationFocus = useViewerStore((s) => s.consumeAnnotationFocus);
  const toast = useToast();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<AnnFilter>("all");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  const queryKey = ["annotations", itemId];
  const query = useQuery({
    queryKey,
    queryFn: async () =>
      (
        await annotationsList({
          path: { item_id: itemId as string },
          query: { kind: "highlight" },
          throwOnError: true,
        })
      ).data,
    enabled: Boolean(itemId),
    staleTime: 0,
  });

  const data = query.data;
  const counts = data?.counts;

  const items = useMemo(() => data?.items ?? [], [data]);

  const filtered = useMemo(() => {
    switch (filter) {
      case "all":
        return items;
      case "with_comment":
        return items.filter((a) => a.comment != null);
      default:
        return items.filter((a) => a.color === filter);
    }
  }, [items, filter]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey });
    if (itemId) void qc.invalidateQueries({ queryKey: ["viewer", itemId] });
  };

  // 検索ヒット遷移「注釈」(plans/11 §7 `?annotation=`)。該当カードへスクロール+2000ms 強調。
  useEffect(() => {
    if (!pendingAnnotationId) return;
    if (query.isLoading) return;
    const el = listRef.current?.querySelector<HTMLElement>(
      `[data-annotation-id="${pendingAnnotationId}"]`,
    );
    if (el) {
      el.scrollIntoView({ block: "center" });
      el.classList.add("yk-block-flash");
      window.setTimeout(() => el.classList.remove("yk-block-flash"), 2000);
    }
    consumeAnnotationFocus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingAnnotationId, query.isLoading, items]);

  const onJump = (ann: Annotation) => {
    if (!ann.placed) return;
    requestScroll({ kind: "block", blockId: ann.anchor.block_id });
  };

  const onDelete = (ann: Annotation) => {
    qc.setQueryData(queryKey, (prev: typeof query.data) =>
      prev ? { ...prev, items: prev.items.filter((a) => a.id !== ann.id) } : prev,
    );
    void annotationsDelete({ path: { annotation_id: ann.id } }).then(
      () => {
        invalidate();
        toast({
          kind: "success",
          message: "注釈を削除しました",
          action: {
            label: "元に戻す",
            onClick: () => {
              // 「元に戻す」= 同じ kind/color/anchor/comment で再作成(DELETE に undelete API は無い。1b §5.6)。
              void annotationsCreate({
                path: { item_id: itemId as string },
                body: {
                  kind: ann.kind,
                  color: ann.color,
                  anchor: {
                    revision_id: ann.anchor.revision_id,
                    block_id: ann.anchor.block_id,
                    start: ann.anchor.start,
                    end: ann.anchor.end,
                    quote: ann.anchor.quote,
                    side: ann.anchor.side,
                  },
                  comment: ann.comment,
                },
              }).then(invalidate, () => toast({ kind: "error", message: "復元できませんでした" }));
            },
          },
        });
      },
      () => {
        invalidate();
        toast({ kind: "error", message: "削除できませんでした" });
      },
    );
  };

  const onSaveComment = (ann: Annotation) => {
    const next = editingText.trim();
    setEditingId(null);
    if (next === (ann.comment ?? "")) return;
    void annotationsUpdate({
      path: { annotation_id: ann.id },
      body: { comment: next.length > 0 ? next : null },
    }).then(invalidate, () => toast({ kind: "error", message: "コメントを保存できませんでした" }));
  };

  if (!itemId) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 5,
          padding: "10px 12px",
          borderBottom: "1px solid var(--pr-border-hair)",
        }}
      >
        <FilterChip
          label="すべて"
          count={counts?.all}
          size="sm"
          selected={filter === "all"}
          onClick={() => setFilter("all")}
        />
        {(["important", "question", "idea"] as const).map((c) => (
          <FilterChip
            key={c}
            label={COLOR_LABEL[c]}
            count={counts?.[c]}
            size="sm"
            dotColor={COLOR_HEX[c]}
            selected={filter === c}
            onClick={() => setFilter(c)}
          />
        ))}
        <FilterChip
          label="コメントのみ"
          size="sm"
          selected={filter === "with_comment"}
          onClick={() => setFilter("with_comment")}
        />
      </div>

      <div
        ref={listRef}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: 10,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          background: "var(--pr-bg-feed)",
        }}
      >
        {query.isLoading ? (
          <AnnotationSkeleton />
        ) : filtered.length === 0 ? (
          items.length === 0 ? (
            <EmptyState
              title="注釈はまだありません"
              description="本文を選択して4色ハイライトやコメントを付けられます"
            />
          ) : (
            <EmptyState title="該当する注釈がありません" description="フィルタを変更してください" />
          )
        ) : (
          filtered.map((ann) => (
            <AnnotationCard
              key={ann.id}
              annotation={ann}
              readOnly={readOnly}
              editing={editingId === ann.id}
              editingText={editingText}
              onStartEditComment={() => {
                setEditingId(ann.id);
                setEditingText(ann.comment ?? "");
              }}
              onEditingTextChange={setEditingText}
              onSaveComment={() => onSaveComment(ann)}
              onJump={() => onJump(ann)}
              onDelete={() => onDelete(ann)}
            />
          ))
        )}
      </div>

      <div
        style={{
          padding: "10px 12px",
          borderTop: "1px solid var(--pr-border-soft)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 11,
        }}
      >
        <span style={{ color: "var(--pr-text-muted)" }}>未配置 {counts?.unplaced ?? 0} 件</span>
        <a
          href={`/api/library-items/${itemId}/export/annotations`}
          download
          style={{ color: "var(--pr-acc)", fontWeight: 600, textDecoration: "none" }}
        >
          ⤓ Markdown エクスポート
        </a>
      </div>
    </div>
  );
}

interface AnnotationCardProps {
  annotation: Annotation;
  /** 閲覧専用(mobile.md §4.5)。コメント編集・削除ボタンを非描画にする。 */
  readOnly?: boolean;
  editing: boolean;
  editingText: string;
  onStartEditComment: () => void;
  onEditingTextChange: (text: string) => void;
  onSaveComment: () => void;
  onJump: () => void;
  onDelete: () => void;
}

function AnnotationCard({
  annotation,
  readOnly = false,
  editing,
  editingText,
  onStartEditComment,
  onEditingTextChange,
  onSaveComment,
  onJump,
  onDelete,
}: AnnotationCardProps) {
  const [hovered, setHovered] = useState(false);
  const color = (annotation.color ?? "term") as HighlightColor;
  const unplaced = !annotation.placed;

  return (
    <div
      data-annotation-id={annotation.id}
      role={unplaced ? undefined : "button"}
      tabIndex={unplaced ? undefined : 0}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={unplaced ? undefined : onJump}
      onKeyDown={(e) => {
        if (!unplaced && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onJump();
        }
      }}
      style={{
        display: "flex",
        gap: 9,
        padding: "9px 11px",
        background: hovered ? "var(--pr-bg-hover)" : "var(--pr-bg-card)",
        border: "1px solid var(--pr-border-card)",
        borderRadius: 8,
        opacity: unplaced ? 0.6 : 1,
        cursor: unplaced ? "default" : "pointer",
        position: "relative",
      }}
    >
      <div
        style={{
          width: 3,
          borderRadius: 2,
          flex: "none",
          background: COLOR_HEX[color],
        }}
      />
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: annotation.comment != null ? 5 : 4 }}>
        <div
          style={{
            fontFamily: "var(--pr-jp)",
            fontSize: 12,
            lineHeight: 1.7,
            color: "var(--pr-text-en)",
          }}
        >
          「{annotation.anchor.quote ?? ""}」
        </div>
        {editing && !readOnly ? (
          <textarea
            autoFocus
            aria-label="コメントを編集"
            value={editingText}
            onChange={(e) => onEditingTextChange(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={onSaveComment}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSaveComment();
              }
            }}
            style={{
              fontSize: 11.5,
              lineHeight: 1.65,
              fontFamily: "inherit",
              color: "var(--pr-text-body)",
              background: "var(--pr-bg-comment)",
              borderRadius: 5,
              padding: "6px 8px",
              border: "1px solid var(--pr-border-control)",
              resize: "none",
            }}
            rows={2}
          />
        ) : annotation.comment != null ? (
          <div
            onClick={(e) => {
              e.stopPropagation();
              if (!readOnly) onStartEditComment();
            }}
            style={{
              fontSize: 11.5,
              lineHeight: 1.65,
              color: "var(--pr-text-body)",
              background: "var(--pr-bg-comment)",
              borderRadius: 5,
              padding: "6px 8px",
              cursor: readOnly ? "default" : "text",
            }}
          >
            💬 {annotation.comment}
          </div>
        ) : null}
        <div style={{ fontSize: 10, color: unplaced ? "var(--pr-warn)" : "var(--pr-text-muted)" }}>
          {annotation.anchor.display} · {formatRelativeDay(annotation.created_at)}
          {unplaced ? " · 未配置" : ""}
        </div>
      </div>
      {hovered && !unplaced && !readOnly ? (
        <button
          type="button"
          aria-label="注釈を削除"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          style={{
            position: "absolute",
            top: 6,
            right: 6,
            border: "none",
            background: "transparent",
            color: "var(--pr-text-muted)",
            fontSize: 10,
            cursor: "pointer",
          }}
        >
          ×
        </button>
      ) : null}
    </div>
  );
}

function AnnotationSkeleton() {
  const barStyle: CSSProperties = {
    height: 64,
    borderRadius: 8,
    background: "var(--pr-bg-card)",
    border: "1px solid var(--pr-border-card)",
    animation: "yk-pulse 1.6s ease-in-out infinite",
  };
  return (
    <>
      {[0, 1, 2].map((i) => (
        <div key={i} style={barStyle} />
      ))}
    </>
  );
}
