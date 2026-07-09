"use client";

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ingestArxiv,
  viewerListFigures,
  viewerListReferences,
  type FigureItem,
  type ReferenceItem,
} from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";
import { EmptyState } from "@/components/ui/EmptyState";
import { useViewerStore } from "@/stores/viewer-store";
import { ReferencesList, type ReferenceImportState } from "@/components/viewer/ReferencesList";

export interface FiguresPanelProps {
  itemId: string;
  revisionId: string;
}

function normalizeReferenceKey(value: string | null | undefined): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  return raw
    .replace(/^#/, "")
    .replace(/^\[/, "")
    .replace(/\]$/, "")
    .toLowerCase()
    .replace(/[.:_\s]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function referenceAliases(ref: ReferenceItem): string[] {
  const aliases = [ref.ref_id, ref.number, ...(ref.aliases ?? [])];
  const numeric = ref.number.match(/\d+/)?.[0] ?? null;
  if (numeric) {
    aliases.push(
      numeric,
      `[${numeric}]`,
      `ref-${numeric}`,
      `ref.${numeric}`,
      `ref${numeric}`,
      `bib-${numeric}`,
      `bib.${numeric}`,
      `bib${numeric}`,
    );
  }
  return aliases;
}

/** 図表タブ本体(1c §4.6)。図表一覧+参考文献一覧。 */
export function FiguresPanel({ revisionId }: FiguresPanelProps) {
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const pendingReferenceId = useViewerStore((s) => s.pendingReferenceId);
  const consumeReferenceFocus = useViewerStore((s) => s.consumeReferenceFocus);

  const [activeFigureBlockId, setActiveFigureBlockId] = useState<string | null>(null);
  const [expandedRefId, setExpandedRefId] = useState<string | null>(null);
  const [unresolvedReferenceId, setUnresolvedReferenceId] = useState<string | null>(null);
  const [importStates, setImportStates] = useState<Record<string, ReferenceImportState>>({});

  const figuresQuery = useQuery({
    queryKey: ["figures", revisionId],
    queryFn: async () =>
      (await viewerListFigures({ path: { revision_id: revisionId }, throwOnError: true })).data,
    staleTime: Infinity,
  });

  const referencesQuery = useQuery({
    queryKey: ["references", revisionId],
    queryFn: async () =>
      (await viewerListReferences({ path: { revision_id: revisionId }, throwOnError: true })).data,
    staleTime: Infinity,
  });

  const onSelectFigure = useCallback(
    (fig: FigureItem) => {
      setActiveFigureBlockId(fig.block_id);
      requestScroll({ kind: "block", blockId: fig.block_id });
    },
    [requestScroll],
  );

  const onImport = useCallback(
    (ref: ReferenceItem) => {
      if (!ref.arxiv_id) return;
      setImportStates((s) => ({ ...s, [ref.ref_id]: "importing" }));
      void ingestArxiv({ body: { url: `https://arxiv.org/abs/${ref.arxiv_id}` } }).then(
        () => {
          setImportStates((s) => ({ ...s, [ref.ref_id]: "imported" }));
          void qc.invalidateQueries({ queryKey: ["references", revisionId] });
          toast({ kind: "success", message: "✓ ライブラリに追加しました" });
        },
        () => {
          setImportStates((s) => ({ ...s, [ref.ref_id]: "idle" }));
          toast({ kind: "error", message: "取り込みに失敗しました" });
        },
      );
    },
    [qc, revisionId, toast],
  );

  const figures = figuresQuery.data?.items ?? [];
  const references = useMemo(
    () => referencesQuery.data?.items ?? [],
    [referencesQuery.data?.items],
  );

  const resolveReferenceId = useCallback((target: string, refs: ReferenceItem[]): string | null => {
    const wanted = normalizeReferenceKey(target);
    if (!wanted) return null;
    for (const ref of refs) {
      const aliases = referenceAliases(ref);
      if (aliases.some((alias) => normalizeReferenceKey(alias) === wanted)) return ref.ref_id;
    }
    return null;
  }, []);

  // 引用クリックから来た場合は該当参考文献を展開する。データ到着後にも解決できるよう
  // pendingReferenceId と references の両方を依存に入れる。
  useEffect(() => {
    if (!pendingReferenceId || referencesQuery.isLoading) return;
    const refId = resolveReferenceId(pendingReferenceId, references);
    if (refId) {
      setExpandedRefId(refId);
      setUnresolvedReferenceId(null);
    } else {
      setUnresolvedReferenceId(pendingReferenceId);
    }
    consumeReferenceFocus();
  }, [
    pendingReferenceId,
    references,
    referencesQuery.isLoading,
    resolveReferenceId,
    consumeReferenceFocus,
  ]);

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
      <PanelSectionHeading label="図表一覧" />
      {figuresQuery.isError ? (
        <EmptyState
          title="読み込みに失敗しました"
          action={{ label: "再試行", onClick: () => void figuresQuery.refetch() }}
        />
      ) : figuresQuery.isLoading ? (
        <FigureSkeleton />
      ) : figures.length === 0 ? (
        <EmptyState
          title="図表がありません"
          description="この論文からは図表を抽出できませんでした。"
        />
      ) : (
        figures.map((fig) => (
          <FigureCard
            key={fig.block_id}
            figure={fig}
            selected={activeFigureBlockId === fig.block_id}
            onClick={() => onSelectFigure(fig)}
          />
        ))
      )}

      <div style={{ height: 1, background: "var(--pr-border-soft)", margin: "6px 0" }} />

      <PanelSectionHeading label="参考文献" />
      {unresolvedReferenceId ? (
        <div
          role="status"
          style={{
            padding: "6px 8px",
            borderRadius: 6,
            background: "var(--pr-bg-inset)",
            color: "var(--pr-text-muted)",
            fontSize: 10.5,
            overflowWrap: "anywhere",
          }}
        >
          引用 {unresolvedReferenceId} に対応する参考文献が見つかりません。
        </div>
      ) : null}
      {referencesQuery.isError ? (
        <EmptyState
          title="読み込みに失敗しました"
          action={{ label: "再試行", onClick: () => void referencesQuery.refetch() }}
        />
      ) : referencesQuery.isLoading ? (
        <ReferenceSkeleton />
      ) : references.length === 0 ? (
        <EmptyState
          title="参考文献がありません"
          description="参考文献リストを抽出できませんでした。"
        />
      ) : (
        <ReferencesList
          references={references}
          expandedRefId={expandedRefId}
          onToggle={(id) => setExpandedRefId((cur) => (cur === id ? null : id))}
          importStates={importStates}
          onImport={onImport}
          onOpenInLibrary={(libraryItemId) => router.push(`/papers/${libraryItemId}`)}
        />
      )}
    </div>
  );
}

function PanelSectionHeading({ label }: { label: string }) {
  return (
    <div
      style={{
        fontSize: 10.5,
        fontWeight: 600,
        letterSpacing: "0.4px",
        color: "var(--pr-text-muted)",
      }}
    >
      {label}
    </div>
  );
}

const thumbStyle: CSSProperties = {
  width: 52,
  height: 38,
  flex: "none",
  borderRadius: 4,
  background: "var(--pr-bg-thumb)",
  border: "1px solid var(--pr-border-thumb)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  color: "var(--pr-text-thumb)",
  fontSize: 9,
  overflow: "hidden",
};

/** 図表カード(1c §4.6・§5.4)。選択中はアクセント面+「(表示中)」付記。 */
function FigureCard({
  figure,
  selected,
  onClick,
}: {
  figure: FigureItem;
  selected: boolean;
  onClick: () => void;
}) {
  const caption = figure.caption_ja ?? figure.caption_en;
  const captionText = selected ? `${caption}(表示中)` : caption;
  const sub =
    figure.position.page != null
      ? `${figure.position.section_display} · p.${figure.position.page}`
      : figure.position.section_display;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      style={{
        display: "flex",
        gap: 10,
        padding: 8,
        borderRadius: 7,
        cursor: "pointer",
        background: selected ? "var(--pr-acc-s)" : "transparent",
        border: selected ? "1px solid var(--pr-acc-m)" : "1px solid transparent",
      }}
    >
      <div style={thumbStyle}>
        {figure.image_url ? (
          <img
            src={figure.image_url}
            alt={figure.display}
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          figure.display
        )}
      </div>
      <div style={{ overflow: "hidden" }}>
        <div style={{ fontSize: 9.5, fontWeight: 700, color: "var(--pr-acc)", lineHeight: 1.3 }}>
          {figure.display}
        </div>
        <div
          style={{
            fontSize: 11,
            lineHeight: 1.6,
            color: selected ? "var(--pr-text)" : "var(--pr-text-mid)",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {captionText}
        </div>
        <div style={{ fontSize: 9.5, color: "var(--pr-text-muted)" }}>{sub}</div>
      </div>
    </div>
  );
}

function FigureSkeleton() {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <div key={i} style={{ display: "flex", gap: 10, padding: 8 }}>
          <div style={{ ...thumbStyle, animation: "alinea-pulse 1.2s ease-in-out infinite" }} />
          <div
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              gap: 6,
              justifyContent: "center",
            }}
          >
            <div
              style={{
                height: 10,
                width: "88%",
                borderRadius: 3,
                background: "var(--pr-bg-thumb)",
                animation: "alinea-pulse 1.2s ease-in-out infinite",
              }}
            />
            <div
              style={{
                height: 10,
                width: "40%",
                borderRadius: 3,
                background: "var(--pr-bg-thumb)",
                animation: "alinea-pulse 1.2s ease-in-out infinite",
              }}
            />
          </div>
        </div>
      ))}
    </>
  );
}

function ReferenceSkeleton() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {["96%", "92%", "88%", "90%"].map((w, i) => (
        <div
          key={i}
          style={{
            height: 12,
            width: w,
            borderRadius: 3,
            background: "var(--pr-bg-thumb)",
            animation: "alinea-pulse 1.2s ease-in-out infinite",
          }}
        />
      ))}
    </div>
  );
}
