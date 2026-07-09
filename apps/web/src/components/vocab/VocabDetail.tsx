"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  vocabGet,
  vocabRegenerate,
  vocabReview,
  vocabUpdate,
  type VocabEntryDetail,
  type VocabPatch,
  type VocabPatchAi,
} from "@alinea/api-client";
import { Modal } from "@/components/ui/Modal";
import { Popover } from "@/components/ui/Popover";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { VocabKindBadge, VOCAB_KIND_LABEL } from "@/components/vocab/VocabKindBadge";
import { ContextSentenceSection, extractSectionRef } from "@/components/vocab/ContextSentenceSection";
import { EditableVocabSection } from "@/components/vocab/EditableVocabSection";
import { ReviewFooter } from "@/components/vocab/ReviewFooter";
import { formatDetailMetaLine, parseInterpretation } from "@/components/vocab/format";
import { vocabEntryQueryKey } from "@/components/vocab/queryKeys";
import type { ReviewResult, VocabKind } from "@/components/vocab/types";

export class VocabApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function fetchVocabDetail(id: string): Promise<VocabEntryDetail> {
  const res = await vocabGet({ path: { vocab_id: id } });
  if (res.error !== undefined) {
    const body = res.error as { code?: string; detail?: string; title?: string };
    throw new VocabApiError(
      res.response.status,
      body.code ?? "error",
      body.detail ?? body.title ?? "取得に失敗しました",
    );
  }
  return res.data;
}

function buildAiPatch(fieldKey: string, value: string, meaningShort: string): VocabPatchAi {
  switch (fieldKey) {
    case "context_meaning":
      return { context_meaning: { short: meaningShort, long: value } };
    case "interpretation":
      return { interpretation: value };
    case "etymology":
      return { etymology: value };
    case "mnemonic":
      return { mnemonic: value };
    case "related_expressions":
      return { related_expressions: value };
    default:
      return {};
  }
}

export interface VocabDetailProps {
  vocabId: string | null;
  onOpenSource: (libraryItemId: string, blockId: string) => void;
  onDeleteRequested: (id: string, term: string) => void;
  onNotFound: (id: string) => void;
}

/** 詳細パネル(4d §4.2.6)。ヘッダ・本文 6 セクション・フッタ(SRS 評価)。 */
export function VocabDetail({ vocabId, onOpenSource, onDeleteRequested, onNotFound }: VocabDetailProps) {
  if (vocabId === null) {
    return (
      <PanelShell>
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <EmptyState title="語彙が選択されていません" />
        </div>
      </PanelShell>
    );
  }
  return <VocabDetailLoaded key={vocabId} vocabId={vocabId} onOpenSource={onOpenSource} onDeleteRequested={onDeleteRequested} onNotFound={onNotFound} />;
}

function VocabDetailLoaded({
  vocabId,
  onOpenSource,
  onDeleteRequested,
  onNotFound,
}: {
  vocabId: string;
  onOpenSource: (libraryItemId: string, blockId: string) => void;
  onDeleteRequested: (id: string, term: string) => void;
  onNotFound: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [menuOpen, setMenuOpen] = useState(false);
  const [headingModalOpen, setHeadingModalOpen] = useState(false);
  const [reviewOverride, setReviewOverride] = useState<string | undefined>(undefined);
  const menuAnchorRef = useRef<HTMLButtonElement>(null);

  const detailQuery = useQuery({
    queryKey: vocabEntryQueryKey(vocabId),
    queryFn: () => fetchVocabDetail(vocabId),
    staleTime: 30_000,
    refetchInterval: (q) => (q.state.data?.generation === "pending" ? 2_000 : false),
    retry: false,
  });

  const invalidateList = () => {
    void queryClient.invalidateQueries({ queryKey: ["vocab", "list"] });
  };

  const patchMutation = useMutation({
    mutationFn: (body: VocabPatch) =>
      vocabUpdate({ path: { vocab_id: vocabId }, body, throwOnError: true }).then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(vocabEntryQueryKey(vocabId), data);
      invalidateList();
    },
    onError: () => {
      toast({ kind: "error", message: "保存できませんでした" });
    },
  });

  const regenerateMutation = useMutation({
    mutationFn: (fields?: string[]) =>
      vocabRegenerate({ path: { vocab_id: vocabId }, body: { fields }, throwOnError: true }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: vocabEntryQueryKey(vocabId) });
    },
    onError: () => {
      toast({ kind: "error", message: "再生成を開始できませんでした" });
    },
  });

  const reviewMutation = useMutation({
    mutationFn: (result: ReviewResult) =>
      vocabReview({ path: { vocab_id: vocabId }, body: { result }, throwOnError: true }).then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(vocabEntryQueryKey(vocabId), (prev: VocabEntryDetail | undefined) =>
        prev ? { ...prev, srs: data.srs } : prev,
      );
      setReviewOverride(data.next_review_display);
      void queryClient.invalidateQueries({ queryKey: ["vocab"] });
    },
    onError: () => {
      toast({ kind: "error", message: "評価を保存できませんでした" });
    },
  });

  const notFound = detailQuery.isError && detailQuery.error instanceof VocabApiError && detailQuery.error.status === 404;

  useEffect(() => {
    if (notFound) onNotFound(vocabId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notFound, vocabId]);

  if (detailQuery.isPending) {
    return (
      <PanelShell>
        <div style={{ padding: "14px 16px", fontSize: 11.5, color: "var(--pr-text-muted)" }}>
          読み込み中…
        </div>
      </PanelShell>
    );
  }

  if (notFound) {
    return null;
  }

  if (detailQuery.isError) {
    return (
      <PanelShell>
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <EmptyState
            title="この語彙を読み込めませんでした"
            action={{ label: "再読み込み", onClick: () => void detailQuery.refetch() }}
          />
        </div>
      </PanelShell>
    );
  }

  const entry = detailQuery.data;
  const generating = entry.generation === "pending";
  const failed = entry.generation === "failed";
  const kindLabel = VOCAB_KIND_LABEL[entry.kind];
  const interpretation = entry.ai.interpretation !== null && entry.ai.interpretation !== undefined
    ? parseInterpretation(entry.ai.interpretation)
    : null;

  const savePatch = (fieldKey: string, value: string) => {
    patchMutation.mutate({ ai: buildAiPatch(fieldKey, value, entry.meaning_short ?? "") });
  };

  return (
    <PanelShell>
      <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--pr-border-hair)", display: "flex", flexDirection: "column", gap: 5, flex: "none" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontSize: 16, fontWeight: 700, fontFamily: "var(--pr-font-en)" }}>{entry.term}</span>
          {entry.ipa ? (
            <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)", fontFamily: "var(--pr-font-mono)" }}>{entry.ipa}</span>
          ) : null}
          <VocabKindBadge kind={entry.kind} size="detail" />
          <button
            ref={menuAnchorRef}
            type="button"
            aria-label="その他の操作"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
            style={{
              marginLeft: "auto",
              width: 20,
              height: 20,
              borderRadius: 5,
              border: "none",
              background: "transparent",
              color: "var(--pr-text-sub)",
              fontSize: 15,
              letterSpacing: "1px",
              cursor: "pointer",
            }}
          >
            ⋯
          </button>
          <Popover open={menuOpen} onClose={() => setMenuOpen(false)} anchorRef={menuAnchorRef} width={180} caret={false} placement="bottom-end">
            <OverflowMenu
              onEditHeading={() => {
                setMenuOpen(false);
                setHeadingModalOpen(true);
              }}
              onRegenerate={() => {
                setMenuOpen(false);
                regenerateMutation.mutate(undefined);
                toast({ kind: "info", message: "再生成をはじめました" });
              }}
              onDelete={() => {
                setMenuOpen(false);
                onDeleteRequested(entry.id, entry.term);
              }}
            />
          </Popover>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10.5, color: "var(--pr-text-muted)" }}>
          <span>{formatDetailMetaLine(entry.pos_label ?? null, kindLabel, entry.source.display)}</span>
          {entry.generation === "done" ? (
            <span
              style={{
                height: 14,
                padding: "0 5px",
                border: "1px solid var(--pr-border-control)",
                borderRadius: 3,
                fontSize: 8.5,
                fontWeight: 600,
                color: "var(--pr-text-icon)",
                display: "inline-flex",
                alignItems: "center",
              }}
            >
              AI生成 · 編集可
            </span>
          ) : null}
        </div>
      </div>

      <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 12, flex: 1, overflowY: "auto" }}>
        {failed ? (
          <FailureCard
            reason={entry.ai.generation_error ?? "不明なエラー"}
            onRetry={() => regenerateMutation.mutate(undefined)}
          />
        ) : (
          <EditableVocabSection
            heading="文脈での語義"
            variant="plain"
            state={generating ? "generating" : "content"}
            text={entry.ai.context_meaning?.long ?? ""}
            fieldKey="context_meaning"
            onSave={savePatch}
            saving={patchMutation.isPending}
          />
        )}

        <ContextSentenceSection
          contextSentence={entry.context_sentence}
          highlight={entry.highlight}
          sectionRef={extractSectionRef(entry.source.display)}
          onOpenSource={() => onOpenSource(entry.source.library_item_id, entry.anchor.block_id)}
        />

        {!failed ? (
          <>
            <EditableVocabSection
              heading={interpretation?.headingSuffix ? `解釈のしかた${interpretation.headingSuffix}` : "解釈のしかた"}
              variant="card"
              state={generating ? "generating" : "content"}
              text={interpretation?.body ?? entry.ai.interpretation ?? ""}
              fieldKey="interpretation"
              onSave={savePatch}
              saving={patchMutation.isPending}
            />
            <EditableVocabSection
              heading="語源メモ"
              variant="plain"
              state={generating ? "generating" : "content"}
              text={entry.ai.etymology ?? ""}
              fieldKey="etymology"
              onSave={savePatch}
              saving={patchMutation.isPending}
            />
            <EditableVocabSection
              heading="✦ 覚えるコツ"
              headingColor="#8A6A24"
              variant="amber"
              state={generating ? "generating" : "content"}
              text={entry.ai.mnemonic ?? ""}
              fieldKey="mnemonic"
              onSave={savePatch}
              saving={patchMutation.isPending}
            />
            <EditableVocabSection
              heading="よく出る形・近い表現"
              variant="plain"
              state={generating ? "generating" : "content"}
              text={entry.ai.related_expressions ?? ""}
              fieldKey="related_expressions"
              onSave={savePatch}
              saving={patchMutation.isPending}
            />
          </>
        ) : null}
      </div>

      <ReviewFooter
        srs={entry.srs}
        nextReviewDisplayOverride={reviewOverride}
        pending={reviewMutation.isPending}
        onReview={(result) => {
          setReviewOverride(undefined);
          reviewMutation.mutate(result);
        }}
      />

      <EditHeadingModal
        key={headingModalOpen ? "open" : "closed"}
        open={headingModalOpen}
        entry={entry}
        onClose={() => setHeadingModalOpen(false)}
        onSave={(patch) => {
          patchMutation.mutate(patch);
          setHeadingModalOpen(false);
        }}
      />
    </PanelShell>
  );
}

function PanelShell({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        width: 400,
        flex: "none",
        display: "flex",
        flexDirection: "column",
        background: "#FFFFFF",
        border: "1px solid var(--pr-border-card)",
        borderRadius: 10,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      {children}
    </div>
  );
}

function FailureCard({ reason, onRetry }: { reason: string; onRetry: () => void }) {
  return (
    <div
      style={{
        background: "var(--pr-warn-bg)",
        borderRadius: 7,
        padding: "9px 11px",
        fontSize: 11.5,
        color: "var(--pr-warn)",
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <span style={{ flex: 1 }}>学習コンテンツの生成に失敗しました — {reason}</span>
      <button
        type="button"
        onClick={onRetry}
        style={{
          flex: "none",
          fontSize: 11,
          fontWeight: 600,
          color: "var(--pr-warn)",
          textDecoration: "underline",
          background: "none",
          border: "none",
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        生成を再試行
      </button>
    </div>
  );
}

function OverflowMenu({
  onEditHeading,
  onRegenerate,
  onDelete,
}: {
  onEditHeading: () => void;
  onRegenerate: () => void;
  onDelete: () => void;
}) {
  const itemStyle: CSSProperties = {
    display: "block",
    width: "100%",
    textAlign: "left",
    padding: "8px 12px",
    fontSize: 11.5,
    color: "var(--pr-text-mid)",
    background: "none",
    border: "none",
    cursor: "pointer",
    fontFamily: "inherit",
  };
  return (
    <div role="menu" aria-label="その他の操作" style={{ padding: "4px 0" }}>
      <button type="button" role="menuitem" style={itemStyle} onClick={onEditHeading}>
        見出しを編集
      </button>
      <button type="button" role="menuitem" style={itemStyle} onClick={onRegenerate}>
        AI 生成をやり直す
      </button>
      <div style={{ height: 1, background: "var(--pr-border-hair)", margin: "4px 0" }} />
      <button type="button" role="menuitem" style={{ ...itemStyle, color: "var(--pr-warn)" }} onClick={onDelete}>
        削除
      </button>
    </div>
  );
}

const KIND_OPTIONS: ReadonlyArray<{ value: VocabKind; label: string }> = [
  { value: "word", label: "単語" },
  { value: "collocation", label: "コロケーション" },
  { value: "idiom", label: "イディオム" },
];

function EditHeadingModal({
  open,
  entry,
  onClose,
  onSave,
}: {
  open: boolean;
  entry: VocabEntryDetail;
  onClose: () => void;
  onSave: (patch: { term: string; ipa: string | null; pos_label: string | null; kind: VocabKind }) => void;
}) {
  const [term, setTerm] = useState(entry.term);
  const [ipa, setIpa] = useState(entry.ipa ?? "");
  const [posLabel, setPosLabel] = useState(entry.pos_label ?? "");
  const [kind, setKind] = useState<VocabKind>(entry.kind);

  if (!open) return null;

  const fieldStyle: CSSProperties = {
    width: "100%",
    boxSizing: "border-box",
    height: 30,
    padding: "0 10px",
    border: "1px solid var(--pr-border-control)",
    borderRadius: 6,
    fontSize: 12,
    fontFamily: "inherit",
  };

  return (
    <Modal open={open} onClose={onClose} width={460} labelledBy="vocab-edit-title">
      <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: 12 }}>
        <h2 id="vocab-edit-title" style={{ fontSize: 13, fontWeight: 700, margin: 0 }}>
          見出しを編集
        </h2>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--pr-text-sub)" }}>
          見出し語
          <input value={term} onChange={(e) => setTerm(e.target.value)} style={fieldStyle} />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--pr-text-sub)" }}>
          発音記号(IPA)
          <input
            value={ipa}
            onChange={(e) => setIpa(e.target.value)}
            style={{ ...fieldStyle, fontFamily: "var(--pr-font-mono)" }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--pr-text-sub)" }}>
          分類ラベル
          <input value={posLabel} onChange={(e) => setPosLabel(e.target.value)} style={fieldStyle} />
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--pr-text-sub)" }}>
          種別
          <SegmentedControl options={KIND_OPTIONS} value={kind} onChange={setKind} ariaLabel="種別" />
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            onClick={onClose}
            style={{ height: 28, padding: "0 14px", border: "1px solid var(--pr-border-control)", borderRadius: 6, background: "#FFFFFF", fontSize: 11.5, cursor: "pointer", fontFamily: "inherit" }}
          >
            キャンセル
          </button>
          <button
            type="button"
            disabled={term.trim().length === 0}
            onClick={() =>
              onSave({
                term: term.trim(),
                ipa: ipa.trim().length > 0 ? ipa.trim() : null,
                pos_label: posLabel.trim().length > 0 ? posLabel.trim() : null,
                kind,
              })
            }
            style={{ height: 28, padding: "0 14px", border: "none", borderRadius: 6, background: "var(--pr-acc)", color: "#FFFFFF", fontSize: 11.5, fontWeight: 600, cursor: "pointer", fontFamily: "inherit" }}
          >
            保存
          </button>
        </div>
      </div>
    </Modal>
  );
}
