"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { vocabReview, type VocabEntryDetail } from "@alinea/api-client";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { VocabKindBadge } from "@/components/vocab/VocabKindBadge";
import { renderMarkdownLite } from "@/components/vocab/markdown-lite";
import type { ReviewResult } from "@/components/vocab/types";
import {
  countFirstAttemptGood,
  countResolved,
  useVocabReviewStore,
} from "@/components/vocab/review-store";

/**
 * 復習セッションモーダル(4d §5.9。デザイン未描画・本書で確定)。
 * フラッシュカード: 表(語義伏せ)→「答えを見る」→ 裏(語義+コツ)→ 評価 → 次カード。
 */
export function VocabReviewModal() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const open = useVocabReviewStore((s) => s.open);
  const queue = useVocabReviewStore((s) => s.queue);
  const total = useVocabReviewStore((s) => s.total);
  const flipped = useVocabReviewStore((s) => s.flipped);
  const results = useVocabReviewStore((s) => s.results);
  const flip = useVocabReviewStore((s) => s.flip);
  const answer = useVocabReviewStore((s) => s.answer);
  const close = useVocabReviewStore((s) => s.close);
  const requeueAfterFailure = useVocabReviewStore((s) => s.requeueAfterFailure);

  const current = queue[0] ?? null;
  const finished = open && queue.length === 0 && total > 0;

  const handleClose = () => {
    close();
    void queryClient.invalidateQueries({ queryKey: ["vocab"] });
  };

  const handleAnswer = (result: ReviewResult) => {
    const entry = current;
    if (!entry) return;
    answer(result);
    void vocabReview({ path: { vocab_id: entry.id }, body: { result }, throwOnError: true }).catch(() => {
      toast({ kind: "error", message: "評価を保存できませんでした" });
      requeueAfterFailure(entry);
    });
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") return; // Modal 側が処理
      if (!current) return;
      if (e.key === " " && !flipped) {
        e.preventDefault();
        flip();
      } else if (flipped && e.key === "1") {
        handleAnswer("again");
      } else if (flipped && e.key === "2") {
        handleAnswer("good");
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, current, flipped]);

  if (!open) return null;

  return (
    <Modal open={open} onClose={handleClose} width={460} labelledBy="vocab-review-title">
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div
          style={{
            padding: "14px 16px",
            borderBottom: "1px solid var(--pr-border-hair)",
            display: "flex",
            alignItems: "center",
          }}
        >
          <span id="vocab-review-title" style={{ fontSize: 12.5, fontWeight: 700 }}>
            復習
          </span>
          {!finished ? (
            <span
              style={{
                marginLeft: "auto",
                fontSize: 10.5,
                color: "var(--pr-text-muted)",
                fontFamily: "var(--pr-font-mono)",
              }}
            >
              {countResolved(results) + 1} / {total}
            </span>
          ) : null}
        </div>

        {finished ? (
          <CompletionScreen total={total} results={results} onClose={handleClose} />
        ) : current ? (
          <ReviewCard
            entry={current}
            flipped={flipped}
            onFlip={flip}
            onAnswer={handleAnswer}
          />
        ) : null}
      </div>
    </Modal>
  );
}

function ReviewCard({
  entry,
  flipped,
  onFlip,
  onAnswer,
}: {
  entry: VocabEntryDetail;
  flipped: boolean;
  onFlip: () => void;
  onAnswer: (result: ReviewResult) => void;
}) {
  const before = entry.context_sentence.slice(0, entry.highlight.start);
  const marked = entry.context_sentence.slice(entry.highlight.start, entry.highlight.end);
  const after = entry.context_sentence.slice(entry.highlight.end);

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "18px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontSize: 16, fontWeight: 700, fontFamily: "var(--pr-font-en)" }}>{entry.term}</span>
          {entry.ipa ? (
            <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)", fontFamily: "var(--pr-font-mono)" }}>
              {entry.ipa}
            </span>
          ) : null}
          <VocabKindBadge kind={entry.kind} size="detail" />
        </div>

        <div
          style={{
            fontSize: 11,
            lineHeight: 1.7,
            color: "#5B6067",
            borderLeft: "2px solid var(--pr-border-card)",
            paddingLeft: 9,
            fontFamily: "var(--pr-font-en)",
            fontStyle: "italic",
          }}
        >
          {before}
          <mark
            style={{
              background: "var(--pr-ann-important-chip-bg)",
              borderRadius: 2,
              padding: "0 1px",
              fontStyle: "normal",
            }}
          >
            {marked}
          </mark>
          {after}
        </div>

        {flipped ? (
          <>
            <div style={{ fontSize: 12, lineHeight: 1.7, color: "var(--pr-text-body)" }}>
              {renderMarkdownLite(entry.ai.context_meaning?.long ?? "")}
            </div>
            {entry.ai.mnemonic ? (
              <div
                style={{
                  fontSize: 11.5,
                  lineHeight: 1.75,
                  color: "#3C4046",
                  background: "#FFF9F0",
                  border: "1px solid #EEDDB8",
                  borderRadius: 7,
                  padding: "9px 11px",
                }}
              >
                {renderMarkdownLite(entry.ai.mnemonic)}
              </div>
            ) : null}
          </>
        ) : (
          <div style={{ display: "flex", justifyContent: "center" }}>
            <button
              type="button"
              onClick={onFlip}
              style={{
                height: 28,
                padding: "0 16px",
                borderRadius: 6,
                border: "none",
                background: "var(--pr-acc)",
                color: "#FFFFFF",
                fontSize: 11.5,
                fontWeight: 600,
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              答えを見る
            </button>
          </div>
        )}
      </div>

      {flipped ? (
        <div
          style={{
            padding: "11px 16px",
            borderTop: "1px solid var(--pr-border-hair)",
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
          }}
        >
          <button
            type="button"
            onClick={() => onAnswer("again")}
            style={{
              height: 26,
              padding: "0 12px",
              border: "1px solid var(--pr-border-control)",
              borderRadius: 6,
              fontSize: 11,
              color: "var(--pr-text-sub)",
              background: "#FFFFFF",
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            まだあやしい
          </button>
          <button
            type="button"
            onClick={() => onAnswer("good")}
            style={{
              height: 26,
              padding: "0 12px",
              border: "none",
              borderRadius: 6,
              fontSize: 11,
              fontWeight: 600,
              color: "#FFFFFF",
              background: "var(--pr-acc)",
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            ✓ 覚えた
          </button>
        </div>
      ) : null}
    </div>
  );
}

function CompletionScreen({
  total,
  results,
  onClose,
}: {
  total: number;
  results: { id: string; result: ReviewResult }[];
  onClose: () => void;
}) {
  const goodCount = countFirstAttemptGood(results);
  return (
    <div
      style={{
        padding: "24px 16px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
      }}
    >
      <span style={{ fontSize: 14, fontWeight: 700 }}>復習が終わりました</span>
      <span style={{ fontSize: 11.5, color: "var(--pr-text-sub)" }}>
        {total} 語中 {goodCount} 語 ✓ 覚えた
      </span>
      <button
        type="button"
        onClick={onClose}
        style={{
          marginTop: 8,
          height: 28,
          padding: "0 18px",
          border: "none",
          borderRadius: 6,
          background: "var(--pr-acc)",
          color: "#FFFFFF",
          fontSize: 11.5,
          fontWeight: 600,
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        閉じる
      </button>
    </div>
  );
}
