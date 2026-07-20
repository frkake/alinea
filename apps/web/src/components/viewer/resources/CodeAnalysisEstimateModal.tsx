"use client";

import { useRef } from "react";
import type { CodeAnalysisEstimateResponse } from "@alinea/api-client";
import { Modal } from "@/components/ui/Modal";

/**
 * コード対応解析の実行確認モーダル(設計 §7・§12)。
 *
 * 実行前に対象 commit・ファイル数・token・概算費用・当月予算の残額を示し、確認を取る。
 * 予算超過(概算費用 > 残額)のときは開始ボタンを無効化し、設定変更への導線を出す。
 */
export interface CodeAnalysisEstimateModalProps {
  open: boolean;
  repoTitle: string;
  estimate: CodeAnalysisEstimateResponse | null;
  /** 見積り取得中(estimate が来る前)。 */
  loading: boolean;
  /** 開始要求の送信中。 */
  starting: boolean;
  onConfirm: () => void;
  onClose: () => void;
  settingsHref?: string;
}

function usd(value: string | number): string {
  return `$${Number(value).toFixed(2)}`;
}

function tokens(n: number): string {
  return n.toLocaleString("en-US");
}

export function CodeAnalysisEstimateModal({
  open,
  repoTitle,
  estimate,
  loading,
  starting,
  onConfirm,
  onClose,
  settingsHref = "/settings?category=account",
}: CodeAnalysisEstimateModalProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const remaining = estimate ? Number(estimate.budget_remaining_usd) : 0;
  const cost = estimate ? Number(estimate.estimated_cost_usd) : 0;
  const overBudget = estimate != null && cost > remaining;

  return (
    <Modal open={open} onClose={onClose} labelledBy="code-analysis-estimate-title" width={440} initialFocusRef={closeRef}>
      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
        <h2 id="code-analysis-estimate-title" style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>
          コード対応を解析
        </h2>
        <p style={{ margin: 0, fontSize: 12, color: "var(--pr-text-sub)", lineHeight: 1.7 }}>
          {repoTitle} の公開コードを解析し、論文の主張とファイル・行範囲の対応を推定します。
        </p>

        {loading || estimate == null ? (
          <p style={{ margin: 0, fontSize: 12, color: "var(--pr-text-muted)" }}>対象規模を取得中…</p>
        ) : (
          <>
            <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", rowGap: 6, columnGap: 12 }}>
              <Row label="対象 commit">
                <span style={{ fontFamily: "'IBM Plex Mono', monospace" }}>
                  {estimate.commit_sha.slice(0, 10)}
                </span>
              </Row>
              <Row label="対象ファイル数">{estimate.files.toLocaleString("en-US")} 件</Row>
              <Row label="入力トークン(概算)">{tokens(estimate.estimated_input_tokens)}</Row>
              <Row label="出力トークン(概算)">{tokens(estimate.estimated_output_tokens)}</Row>
              <Row label="埋め込みトークン(概算)">{tokens(estimate.estimated_embedding_tokens)}</Row>
              <Row label="概算費用">
                <strong>{usd(estimate.estimated_cost_usd)}</strong>
              </Row>
              <Row label="当月予算の残額">
                <span style={{ color: overBudget ? "var(--pr-warn, #A05A42)" : undefined }}>
                  {usd(estimate.budget_remaining_usd)}
                </span>
              </Row>
            </dl>

            {overBudget ? (
              <p
                style={{
                  margin: 0,
                  fontSize: 11,
                  lineHeight: 1.7,
                  color: "var(--pr-warn, #A05A42)",
                  background: "var(--pr-bg-muted, rgba(160,90,66,0.08))",
                  borderRadius: 6,
                  padding: "8px 10px",
                }}
              >
                概算費用が当月予算の残額を超えています。解析を開始できません。
                <a href={settingsHref} style={{ color: "var(--pr-acc)", fontWeight: 600, marginLeft: 4 }}>
                  予算を見直す →
                </a>
              </p>
            ) : null}
          </>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button ref={closeRef} type="button" onClick={onClose} style={secondaryButtonStyle}>
            キャンセル
          </button>
          <button
            type="button"
            disabled={loading || estimate == null || overBudget || starting}
            onClick={onConfirm}
            style={{
              ...secondaryButtonStyle,
              background: "var(--pr-acc)",
              color: "#FFFFFF",
              borderColor: "var(--pr-acc)",
              opacity: loading || estimate == null || overBudget || starting ? 0.5 : 1,
              cursor:
                loading || estimate == null || overBudget || starting ? "default" : "pointer",
            }}
          >
            {starting ? "開始中…" : "解析を開始"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>{label}</dt>
      <dd style={{ margin: 0, fontSize: 11.5, color: "var(--pr-text-mid)", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
        {children}
      </dd>
    </>
  );
}

const secondaryButtonStyle = {
  height: 30,
  padding: "0 14px",
  borderRadius: 7,
  border: "1px solid var(--pr-border-control)",
  background: "var(--pr-bg-control)",
  color: "var(--pr-text)",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
} as const;
