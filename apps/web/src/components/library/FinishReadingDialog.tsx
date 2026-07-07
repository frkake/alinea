"use client";

import { useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { libraryItemsUpdate, notesCreate, type LibraryItemSummary } from "@yakudoku/api-client";
import { Modal } from "@/components/ui/Modal";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { useToast } from "@/components/ui/Toast";
import {
  COMPREHENSION_LABELS,
  formatFinishedDate,
  formatReadingDuration,
  toImportance,
} from "@/components/library/format";

/**
 * 読了フロー(1g)の中央モーダル(plans/09-screens/1g・docs/06 §3)。
 *
 * 本タスクの縮小スコープ(orchestrator 指示):
 * - 「記事モードで読み返す →」カード(1g §4.5.2 カード2・§5.7)は M2-07 で表示化済み。
 *   常時表示・無条件遷移(記事未生成時の生成 CTA は 1h 側の責務。loading/done 状態を持たない)。
 * - 「✦ 要約をメモに保存」は 1g 全文仕様のチャット詳細要約 SSE 生成フローではなく、
 *   `LibraryItemSummary.summary_3line`(既存の ✦3行要約)をそのままメモ化する簡易版
 *   (`POST /api/library-items/{id}/notes`)。summary_3line が無い場合はカード非表示。
 * - 全項目スキップ可(P6)。読了日・累計読書時間は表示のみで編集 UI を持たない。
 */
export interface FinishReadingDialogProps {
  /** `PATCH {status:"done"}` の成功レスポンス。 */
  item: LibraryItemSummary;
  onClose: () => void;
}

type Importance = "low" | "mid" | "high";
type SummaryCardState = "idle" | "loading" | "done" | "error";

const IMPORTANCE_OPTIONS: ReadonlyArray<{ value: Importance | "__none__"; label: string }> = [
  { value: "low", label: "低" },
  { value: "mid", label: "中" },
  { value: "high", label: "高" },
];

const LABEL_STYLE: CSSProperties = {
  fontSize: 11.5,
  fontWeight: 600,
  color: "var(--pr-text-sub)",
  width: 56,
  flex: "none",
};

function normalizeNote(value: string): string | null {
  const v = value.trim().slice(0, 500);
  return v === "" ? null : v;
}

export function FinishReadingDialog({ item, onClose }: FinishReadingDialogProps) {
  const router = useRouter();
  const qc = useQueryClient();
  const toast = useToast();
  const noteRef = useRef<HTMLTextAreaElement>(null);

  const [comprehension, setComprehension] = useState<number | null>(item.comprehension ?? null);
  const [importance, setImportance] = useState<Importance | null>(toImportance(item.importance));
  const [note, setNote] = useState(item.one_line_note ?? "");
  const [saving, setSaving] = useState(false);
  const [summaryState, setSummaryState] = useState<SummaryCardState>("idle");

  const finishedDate = formatFinishedDate(item.finished_at);
  const duration = formatReadingDuration(item.reading_seconds_total);
  const metaLine = finishedDate
    ? duration
      ? `読了日 ${finishedDate} · 累計読書時間 ${duration}(自動記録)`
      : `読了日 ${finishedDate}(自動記録)`
    : null;

  const onSave = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const res = await libraryItemsUpdate({
        path: { item_id: item.id },
        body: {
          comprehension,
          importance,
          one_line_note: normalizeNote(note),
        },
        throwOnError: true,
      });
      qc.setQueryData(["library-item", item.id], res.data);
      void qc.invalidateQueries({ queryKey: ["library"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast({ kind: "success", message: "読了メモを保存しました" });
      onClose();
    } catch {
      setSaving(false);
      toast({ kind: "error", message: "保存に失敗しました — もう一度お試しください" });
    }
  };

  const onDialogKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void onSave();
    }
  };

  const summaryLines = item.summary_3line ?? [];
  const hasSummary = summaryLines.length > 0;

  const onSaveSummaryToNote = async () => {
    if (summaryState === "loading" || summaryState === "done") return;
    setSummaryState("loading");
    try {
      await notesCreate({
        path: { item_id: item.id },
        body: { content_md: summaryLines.join("\n") },
        throwOnError: true,
      });
      setSummaryState("done");
      void qc.invalidateQueries({ queryKey: ["notes", item.id] });
      toast({ kind: "success", message: "要約をメモに保存しました" });
    } catch {
      setSummaryState("error");
    }
  };

  // カード 2「記事モードで読み返す →」(1g §5.7): 常に idle・無条件遷移。記事未生成時の
  // 生成 CTA は記事モード画面(1h)側の責務(本カードは判定を持たない)。
  const onOpenArticle = () => {
    onClose();
    router.push(`/papers/${item.id}?mode=article`);
  };

  return (
    <Modal open width={460} onClose={onClose} labelledBy="finish-dialog-title" initialFocusRef={noteRef}>
      <div onKeyDown={onDialogKeyDown}>
        {/* ヘッダ部(FinishDialogHeader) */}
        <div style={{ padding: "20px 24px 0", display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span
              aria-hidden="true"
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 24,
                height: 24,
                borderRadius: "50%",
                background: "var(--pr-src-note-bg)",
                color: "var(--pr-src-note-fg)",
                fontSize: 12,
                fontWeight: 700,
                flex: "none",
              }}
            >
              ✓
            </span>
            <span id="finish-dialog-title" style={{ fontSize: 15, fontWeight: 700 }}>
              「読んだ」にしました
            </span>
            <button
              type="button"
              aria-label="閉じる"
              onClick={onClose}
              style={{
                marginLeft: "auto",
                width: 24,
                height: 24,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 14,
                color: "var(--pr-text-muted)",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              ×
            </button>
          </div>
          {metaLine ? (
            <div style={{ fontSize: 11, color: "var(--pr-text-muted)", paddingLeft: 33 }}>
              {metaLine}
            </div>
          ) : null}
        </div>

        {/* 本体部 */}
        <div style={{ padding: "18px 24px", display: "flex", flexDirection: "column", gap: 16 }}>
          {/* 理解度 */}
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={LABEL_STYLE}>理解度</span>
            <div
              role="radiogroup"
              aria-label="理解度"
              style={{ display: "flex", gap: 6, alignItems: "center" }}
              onKeyDown={(e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const current = comprehension ?? 0;
                const delta = e.key === "ArrowRight" ? 1 : -1;
                const next = Math.min(5, Math.max(1, current + delta));
                setComprehension(next);
              }}
            >
              {[1, 2, 3, 4, 5].map((n) => {
                const filled = comprehension != null && n <= comprehension;
                const label = `${n}/5 — ${COMPREHENSION_LABELS[n as 1 | 2 | 3 | 4 | 5]}`;
                return (
                  <button
                    key={n}
                    type="button"
                    role="radio"
                    aria-checked={comprehension === n}
                    aria-label={label}
                    tabIndex={comprehension === n || (comprehension == null && n === 1) ? 0 : -1}
                    onClick={() => {
                      setComprehension((prev) => (prev === n ? null : n));
                    }}
                    style={{
                      width: 22,
                      height: 22,
                      borderRadius: "50%",
                      border: filled ? "none" : "1.5px solid var(--pr-border-dashed)",
                      background: filled ? "var(--pr-acc)" : "transparent",
                      boxSizing: "border-box",
                      cursor: "pointer",
                      padding: 0,
                    }}
                  />
                );
              })}
              <span style={{ fontSize: 11, color: "var(--pr-text-sub2)", marginLeft: 4 }}>
                {comprehension != null
                  ? `${comprehension}/5 — ${COMPREHENSION_LABELS[comprehension as 1 | 2 | 3 | 4 | 5]}`
                  : "未選択"}
              </span>
            </div>
          </div>

          {/* 重要度 */}
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={LABEL_STYLE}>重要度</span>
            <SegmentedControl
              ariaLabel="重要度"
              size="md"
              options={IMPORTANCE_OPTIONS}
              value={importance ?? "__none__"}
              onChange={(v) => {
                if (v === "__none__") return;
                setImportance((prev) => (prev === v ? null : v));
              }}
            />
          </div>

          {/* ひとことメモ */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--pr-text-sub)" }}>
              ひとことメモ <span style={{ color: "var(--pr-text-muted)", fontWeight: 400 }}>— 何に使えるか</span>
            </span>
            <textarea
              ref={noteRef}
              aria-label="ひとことメモ"
              value={note}
              onChange={(e) => {
                setNote(e.target.value);
              }}
              rows={2}
              style={{
                border: "1px solid var(--pr-border-control)",
                borderRadius: 8,
                padding: "10px 12px",
                fontSize: 12.5,
                lineHeight: 1.7,
                color: "var(--pr-text-body)",
                caretColor: "var(--pr-acc)",
                resize: "none",
                outline: "none",
                fontFamily: "inherit",
                minHeight: 21,
                maxHeight: 106,
              }}
            />
          </div>

          {/* 導線カード(FollowupActionCard ×2。1g §4.5.2)。カード1は summary_3line が
              無い場合は非表示、カード2(記事モードで読み返す →)は常時表示・無条件遷移。 */}
          <div style={{ display: "flex", gap: 8 }}>
            {hasSummary ? (
              <button
                type="button"
                onClick={() => void onSaveSummaryToNote()}
                disabled={summaryState === "loading" || summaryState === "done"}
                style={{
                  flex: 1,
                  border: "1px solid var(--pr-border-card)",
                  borderRadius: 8,
                  padding: "10px 12px",
                  display: "flex",
                  flexDirection: "column",
                  gap: 3,
                  background: "var(--pr-bg-app)",
                  cursor:
                    summaryState === "loading" || summaryState === "done" ? "default" : "pointer",
                  opacity: summaryState === "loading" ? 0.7 : 1,
                  textAlign: "left",
                  fontFamily: "inherit",
                }}
              >
                <span
                  style={{
                    fontSize: 11.5,
                    fontWeight: 600,
                    color: summaryState === "done" ? "var(--pr-green)" : "var(--pr-acc)",
                  }}
                >
                  {summaryState === "loading"
                    ? "✦ 要約を保存中…"
                    : summaryState === "done"
                      ? "✓ メモに保存しました"
                      : "✦ 要約をメモに保存"}
                </span>
                <span
                  style={{
                    fontSize: 10,
                    lineHeight: 1.5,
                    color: summaryState === "error" ? "var(--pr-warn)" : "var(--pr-text-muted)",
                  }}
                >
                  {summaryState === "error"
                    ? "保存できませんでした — もう一度お試しください"
                    : "この論文の ✦3行要約をメモとして保存します"}
                </span>
              </button>
            ) : null}
            <button
              type="button"
              onClick={onOpenArticle}
              style={{
                flex: 1,
                border: "1px solid var(--pr-border-card)",
                borderRadius: 8,
                padding: "10px 12px",
                display: "flex",
                flexDirection: "column",
                gap: 3,
                background: "var(--pr-bg-app)",
                cursor: "pointer",
                textAlign: "left",
                fontFamily: "inherit",
              }}
            >
              <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--pr-acc)" }}>
                記事モードで読み返す →
              </span>
              <span style={{ fontSize: 10, lineHeight: 1.5, color: "var(--pr-text-muted)" }}>
                メモとチャットから読み物を自動構成
              </span>
            </button>
          </div>
        </div>

        {/* フッタ部 */}
        <div
          style={{
            padding: "14px 24px",
            borderTop: "1px solid var(--pr-border-hair)",
            display: "flex",
            alignItems: "center",
          }}
        >
          <button
            type="button"
            onClick={onClose}
            style={{
              fontSize: 11.5,
              color: "var(--pr-text-muted)",
              background: "transparent",
              border: "none",
              cursor: "pointer",
              fontFamily: "inherit",
              padding: 0,
            }}
          >
            すべてスキップ
          </button>
          <button
            type="button"
            onClick={() => void onSave()}
            disabled={saving}
            style={{
              marginLeft: "auto",
              display: "inline-flex",
              alignItems: "center",
              height: 32,
              padding: "0 18px",
              borderRadius: 7,
              background: "var(--pr-acc)",
              color: "#FFFFFF",
              fontSize: 12.5,
              fontWeight: 600,
              border: "none",
              cursor: saving ? "default" : "pointer",
              opacity: saving ? 0.6 : 1,
              fontFamily: "inherit",
            }}
          >
            保存
          </button>
        </div>
      </div>
    </Modal>
  );
}
