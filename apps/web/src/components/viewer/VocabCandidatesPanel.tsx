"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  vocabCandidatesAccept,
  vocabCandidatesDismiss,
  vocabCandidatesExtract,
  vocabCandidatesList,
  type VocabCandidateListResponse,
  type VocabCandidateOut,
} from "@alinea/api-client";
import { EmptyState } from "@/components/ui/EmptyState";
import { useViewerStore } from "@/stores/viewer-store";

/** キャッシュに保存するローカル拡張型(extractedフラグ付き)。 */
type CandidateCache = VocabCandidateListResponse & { extracted?: boolean };

/**
 * 単語候補タブ本体(viewer-shell §6.5: props なし)。
 * AI が提案する語彙候補を抽出・採用・破棄できる。
 */
export function VocabCandidatesPanel() {
  const itemId = useViewerStore((s) => s.itemId);
  const qc = useQueryClient();

  const candidatesKey = ["vocab-candidates", itemId] as const;

  const list = useQuery<CandidateCache>({
    queryKey: candidatesKey,
    queryFn: async () => {
      const res = await vocabCandidatesList({
        path: { item_id: itemId as string },
        throwOnError: true,
      });
      return res.data as CandidateCache;
    },
    enabled: Boolean(itemId),
    staleTime: 0,
  });

  const extract = useMutation({
    mutationFn: () =>
      vocabCandidatesExtract({ path: { item_id: itemId as string }, throwOnError: true }),
    onSuccess: () => {
      // Mark as extracted in cache, then invalidate to refetch
      qc.setQueryData<CandidateCache>(candidatesKey, (prev) =>
        prev ? { ...prev, extracted: true } : { items: [], count: 0, extracted: true },
      );
      void qc.invalidateQueries({ queryKey: candidatesKey });
    },
  });

  const accept = useMutation({
    mutationFn: (candidateId: string) =>
      vocabCandidatesAccept({ path: { candidate_id: candidateId }, throwOnError: true }),
    onSuccess: (_data, candidateId) => {
      // Optimistic removal
      qc.setQueryData<CandidateCache>(candidatesKey, (prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.filter((c) => c.id !== candidateId),
              count: Math.max(0, prev.count - 1),
            }
          : prev,
      );
      // Invalidate vocab list so the accepted term appears there
      void qc.invalidateQueries({ queryKey: ["vocab", itemId] });
      void qc.invalidateQueries({ queryKey: candidatesKey });
    },
  });

  const dismiss = useMutation({
    mutationFn: (candidateId: string) =>
      vocabCandidatesDismiss({ path: { candidate_id: candidateId }, throwOnError: true }),
    onSuccess: (_data, candidateId) => {
      // Optimistic removal
      qc.setQueryData<CandidateCache>(candidatesKey, (prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.filter((c) => c.id !== candidateId),
              count: Math.max(0, prev.count - 1),
            }
          : prev,
      );
      void qc.invalidateQueries({ queryKey: candidatesKey });
    },
  });

  if (!itemId) return null;

  if (list.isLoading) {
    return (
      <div style={{ padding: 16 }}>
        <div
          style={{
            height: 72,
            borderRadius: 8,
            background: "var(--pr-bg-muted)",
            marginBottom: 8,
          }}
        />
        <div style={{ height: 72, borderRadius: 8, background: "var(--pr-bg-muted)" }} />
      </div>
    );
  }

  if (list.isError) {
    return (
      <div style={{ padding: 16 }}>
        <EmptyState
          title="候補を読み込めませんでした"
          action={{ label: "再試行", onClick: () => void list.refetch() }}
        />
      </div>
    );
  }

  const data = list.data;
  const items: VocabCandidateOut[] = data?.items ?? [];
  const extracted = Boolean(data?.extracted);

  // not-extracted state: empty + not yet extracted
  if (items.length === 0 && !extracted) {
    return (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        <p style={{ fontSize: 12, color: "var(--pr-text-sub)", margin: 0, lineHeight: 1.6 }}>
          AI がこの論文から覚えるべき英単語・コロケーションの候補を抽出します。
        </p>
        {extract.isPending ? (
          <span
            style={{
              fontSize: 13,
              color: "var(--pr-text-sub2)",
              alignSelf: "flex-start",
            }}
          >
            抽出中…
          </span>
        ) : (
          <button
            type="button"
            onClick={() => extract.mutate()}
            style={extractButtonStyle}
          >
            単語候補を抽出
          </button>
        )}
        {extract.isError ? (
          <span style={{ fontSize: 11, color: "var(--pr-error, #c0392b)" }}>
            抽出に失敗しました。もう一度お試しください。
          </span>
        ) : null}
      </div>
    );
  }

  // empty after extraction
  if (items.length === 0 && extracted) {
    return (
      <div style={{ padding: 16 }}>
        <EmptyState title="候補がありません" description="この論文から新たな単語候補は見つかりませんでした。" />
      </div>
    );
  }

  // has-candidates state
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: 12,
        height: "100%",
        minHeight: 0,
        overflowY: "auto",
        background: "var(--pr-bg-feed, #FCFBF8)",
      }}
    >
      {items.map((cand) => (
        <CandidateCard
          key={cand.id}
          candidate={cand}
          onAccept={() => accept.mutate(cand.id)}
          onDismiss={() => dismiss.mutate(cand.id)}
          acceptPending={accept.isPending && accept.variables === cand.id}
          dismissPending={dismiss.isPending && dismiss.variables === cand.id}
        />
      ))}
    </div>
  );
}

function CandidateCard({
  candidate,
  onAccept,
  onDismiss,
  acceptPending,
  dismissPending,
}: {
  candidate: VocabCandidateOut;
  onAccept: () => void;
  onDismiss: () => void;
  acceptPending: boolean;
  dismissPending: boolean;
}) {
  const { term, kind, reason, context_sentence, highlight } = candidate;

  // Split context_sentence at highlight range for emphasis
  const before = context_sentence.slice(0, highlight.start);
  const marked = context_sentence.slice(highlight.start, highlight.end);
  const after = context_sentence.slice(highlight.end);

  return (
    <div
      style={{
        background: "var(--pr-bg-card)",
        border: "1px solid var(--pr-border-card)",
        borderRadius: 8,
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      {/* Term + kind row */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--pr-text)",
          }}
        >
          {term}
        </span>
        <span
          style={{
            fontSize: 10,
            color: "var(--pr-text-icon)",
            background: "var(--pr-bg-inset)",
            border: "1px solid var(--pr-border-control)",
            borderRadius: 3,
            padding: "0 5px",
            lineHeight: "16px",
          }}
        >
          {kind}
        </span>
      </div>

      {/* Reason */}
      {reason ? (
        <p
          style={{
            margin: 0,
            fontSize: 11.5,
            color: "var(--pr-text-sub)",
            lineHeight: 1.55,
          }}
        >
          {reason}
        </p>
      ) : null}

      {/* Context sentence with highlight */}
      <p
        style={{
          margin: 0,
          fontSize: 11,
          color: "var(--pr-text-sub2)",
          lineHeight: 1.6,
          fontStyle: "italic",
        }}
      >
        {before}
        <mark
          style={{
            background: "color-mix(in srgb, var(--pr-acc) 18%, transparent)",
            borderRadius: 2,
            padding: "0 1px",
            fontStyle: "normal",
            fontWeight: 600,
          }}
        >
          {marked}
        </mark>
        {after}
      </p>

      {/* Actions */}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button
          type="button"
          aria-label="破棄"
          disabled={dismissPending}
          onClick={onDismiss}
          style={dismissButtonStyle}
        >
          破棄
        </button>
        <button
          type="button"
          aria-label="採用"
          disabled={acceptPending}
          onClick={onAccept}
          style={acceptButtonStyle}
        >
          採用
        </button>
      </div>
    </div>
  );
}

const extractButtonStyle = {
  alignSelf: "flex-start",
  height: 30,
  padding: "0 16px",
  border: "none",
  borderRadius: 6,
  background: "var(--pr-acc)",
  color: "#FFFFFF",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
} as const;

const acceptButtonStyle = {
  height: 24,
  padding: "0 12px",
  border: "none",
  borderRadius: 5,
  background: "var(--pr-acc)",
  color: "#FFFFFF",
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
  opacity: 1,
} as const;

const dismissButtonStyle = {
  height: 24,
  padding: "0 12px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 5,
  background: "transparent",
  color: "var(--pr-text-icon)",
  fontSize: 11,
  cursor: "pointer",
  fontFamily: "inherit",
} as const;
