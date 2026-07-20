"use client";

import { useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  viewerListRevisions,
  viewerRevisionDiff,
  type RevisionDiffBlock,
} from "@alinea/api-client";

export interface RevisionDiffPanelProps {
  paperId: string;
}

type DiffSelection = { from: string; to: string };

const headingStyle: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 700,
  color: "var(--pr-text-muted)",
  letterSpacing: "0.4px",
};

const statBadgeBase: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: 32,
  height: 18,
  borderRadius: 4,
  fontSize: 10.5,
  fontWeight: 700,
  padding: "0 5px",
};

const blockRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "5px 6px",
  borderRadius: 5,
  cursor: "pointer",
  fontSize: 10.5,
  border: "1px solid var(--pr-border-hair)",
  background: "var(--pr-bg-inset)",
};

function statusColor(status: string): string {
  if (status === "added") return "#4c7458";
  if (status === "removed") return "var(--pr-warn)";
  return "var(--pr-acc)";
}

function DiffBlockRow({ block }: { block: RevisionDiffBlock }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div
        data-block-row
        role="button"
        tabIndex={0}
        style={{
          ...blockRowStyle,
          borderColor: expanded ? "var(--pr-border-control)" : "var(--pr-border-hair)",
        }}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") setExpanded((v) => !v);
        }}
      >
        <span
          style={{
            ...statBadgeBase,
            background: "transparent",
            border: `1px solid ${statusColor(block.status)}`,
            color: statusColor(block.status),
            minWidth: "auto",
            padding: "0 4px",
          }}
        >
          {block.status}
        </span>
        <span style={{ color: "var(--pr-text-sub)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
          {block.section_id}
        </span>
        <span style={{ color: "var(--pr-text-muted)", fontSize: 10, flex: "none" }}>
          {expanded ? "▲" : "▼"}
        </span>
      </div>
      {expanded ? (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 4,
            padding: "6px 8px",
            background: "var(--pr-bg-inset)",
            borderRadius: 5,
            border: "1px solid var(--pr-border-hair)",
            fontSize: 10.5,
          }}
        >
          {block.old_text != null ? (
            <div>
              <div style={{ fontWeight: 600, color: "var(--pr-warn)", marginBottom: 2 }}>旧</div>
              <div style={{ color: "var(--pr-text-sub)", whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
                {block.old_text}
              </div>
            </div>
          ) : null}
          {block.new_text != null ? (
            <div>
              <div style={{ fontWeight: 600, color: "#4c7458", marginBottom: 2 }}>新</div>
              <div style={{ color: "var(--pr-text)", whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
                {block.new_text}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * 改版差分パネル(Task 9)。
 * 隣接最新2リビジョンをデフォルト選択し、追加/削除/変更件数と変更ブロック一覧を表示する。
 * リビジョンが1つだけの場合はセクション全体を非表示にする(読み取り専用、採用操作なし)。
 */
export function RevisionDiffPanel({ paperId }: RevisionDiffPanelProps) {
  const revisionsQuery = useQuery({
    queryKey: ["revisions", paperId],
    queryFn: async () => {
      const res = await viewerListRevisions({ path: { paper_id: paperId }, throwOnError: true });
      return res.data;
    },
    staleTime: 30_000,
  });

  const revisions = revisionsQuery.data?.items ?? [];

  // Sort by created_at ascending to get chronological order
  const sorted = [...revisions].sort((a, b) => a.created_at.localeCompare(b.created_at));

  // Default-select the two adjacent latest revisions
  const defaultSelection: DiffSelection | null =
    sorted.length >= 2
      ? { from: sorted[sorted.length - 2]!.id, to: sorted[sorted.length - 1]!.id }
      : null;

  const [selection, setSelection] = useState<DiffSelection | null>(null);
  const activeSelection = selection ?? defaultSelection;

  const diffQuery = useQuery({
    queryKey: ["revision-diff", paperId, activeSelection?.from, activeSelection?.to],
    queryFn: async () => {
      if (!activeSelection) return null;
      const res = await viewerRevisionDiff({
        path: { paper_id: paperId },
        query: { from: activeSelection.from, to: activeSelection.to },
        throwOnError: true,
      });
      return res.data;
    },
    enabled: activeSelection != null,
    staleTime: 60_000,
  });

  // Hide section when only one revision exists
  if (revisionsQuery.isSuccess && sorted.length < 2) {
    return null;
  }

  // Don't render until we know the revision count
  if (!revisionsQuery.isSuccess) {
    return null;
  }

  const diff = diffQuery.data;
  const stats = diff?.stats;
  const changes = diff?.changes ?? [];

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={headingStyle}>改版差分</div>

      {/* Version selector */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        <select
          aria-label="比較元リビジョン"
          value={activeSelection?.from ?? ""}
          onChange={(e) => {
            const fromId = e.target.value;
            const currentTo = activeSelection?.to ?? "";
            setSelection({ from: fromId, to: currentTo });
          }}
          style={{
            fontSize: 10.5,
            border: "1px solid var(--pr-border-control)",
            borderRadius: 4,
            padding: "2px 4px",
            background: "var(--pr-bg-inset)",
            color: "var(--pr-text)",
            fontFamily: "inherit",
          }}
        >
          {sorted.map((r) => (
            <option key={r.id} value={r.id}>
              {r.source_version ?? r.id}
            </option>
          ))}
        </select>
        <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>→</span>
        <select
          aria-label="比較先リビジョン"
          value={activeSelection?.to ?? ""}
          onChange={(e) => {
            const toId = e.target.value;
            const currentFrom = activeSelection?.from ?? "";
            setSelection({ from: currentFrom, to: toId });
          }}
          style={{
            fontSize: 10.5,
            border: "1px solid var(--pr-border-control)",
            borderRadius: 4,
            padding: "2px 4px",
            background: "var(--pr-bg-inset)",
            color: "var(--pr-text)",
            fontFamily: "inherit",
          }}
        >
          {sorted.map((r) => (
            <option key={r.id} value={r.id}>
              {r.source_version ?? r.id}
            </option>
          ))}
        </select>
      </div>

      {/* Stats row */}
      {stats != null ? (
        <div style={{ display: "flex", gap: 5 }}>
          <span
            style={{
              ...statBadgeBase,
              background: "rgba(76,116,88,0.12)",
              color: "#4c7458",
            }}
          >
            +{stats.added}
          </span>
          <span
            style={{
              ...statBadgeBase,
              background: "rgba(176,104,79,0.12)",
              color: "var(--pr-warn)",
            }}
          >
            -{stats.removed}
          </span>
          <span
            style={{
              ...statBadgeBase,
              background: "var(--pr-acc-s)",
              color: "var(--pr-acc)",
            }}
          >
            ~{stats.changed}
          </span>
        </div>
      ) : null}

      {/* Changes list */}
      {changes.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {changes.map((block) => (
            <DiffBlockRow key={block.block_id} block={block} />
          ))}
        </div>
      ) : null}
    </section>
  );
}
