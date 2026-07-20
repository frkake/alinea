"use client";

import { SettingsControlRow } from "@/components/settings/SettingsControlRow";
import { Stepper } from "@/components/settings/Stepper";
import type { CodeAnalysisMode } from "@/components/settings/types";

/**
 * GitHub コード対応解析の設定(Task 22・設計 §6-§7・§12)。
 *
 * アカウント設定のモデルルーティング付近へ置く。三モード(使用しない / 必要なときだけ /
 * 取り込み後に自動)と月額予算(0.00〜100.00 USD・0.50 刻み)、当月の code_analysis 実費を
 * 表示する。API キーは一切表示しない(選択モデル id と可用性のみ他所で扱う)。
 */
export interface CodeAnalysisSettingsProps {
  mode: CodeAnalysisMode;
  monthlyBudgetUsd: number;
  /** 当月の code_analysis 実費(USD)。取得できないときは null。 */
  currentMonthCostUsd: number | null;
  onModeChange: (mode: CodeAnalysisMode) => void;
  onBudgetChange: (usd: number) => void;
}

const MODE_CARDS: ReadonlyArray<{
  value: CodeAnalysisMode;
  title: string;
  description: string;
}> = [
  {
    value: "off",
    title: "使用しない",
    description: "新しい解析を開始しません。既存の結果は引き続き閲覧できます",
  },
  {
    value: "on_demand",
    title: "必要なときだけ(既定)",
    description: "Resources の「コード対応を解析」から見積もりを確認してから実行します",
  },
  {
    value: "automatic",
    title: "取り込み後に自動",
    description: "論文の本文が準備でき次第、対象の GitHub 実装を自動で解析します",
  },
];

function formatUsd(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

export function CodeAnalysisSettings({
  mode,
  monthlyBudgetUsd,
  currentMonthCostUsd,
  onModeChange,
  onBudgetChange,
}: CodeAnalysisSettingsProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 10,
          padding: "14px 18px",
          borderBottom: "1px solid var(--pr-border-hair)",
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600 }}>解析モード</span>
        <div role="radiogroup" aria-label="解析モード" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {MODE_CARDS.map((card) => {
            const selected = card.value === mode;
            const markMode = selected ? "●" : "○";
            return (
              <button
                key={card.value}
                type="button"
                role="radio"
                aria-checked={selected}
                aria-label={card.title}
                onClick={() => {
                  if (!selected) onModeChange(card.value);
                }}
                style={{
                  textAlign: "left",
                  display: "flex",
                  flexDirection: "column",
                  gap: 3,
                  padding: "11px 13px",
                  borderRadius: 8,
                  border: selected ? "1px solid var(--pr-acc)" : "1px solid var(--pr-border-control)",
                  boxShadow: selected ? "inset 0 0 0 0.5px var(--pr-acc)" : undefined,
                  background: selected ? "var(--pr-acc-s)" : "var(--pr-bg-card)",
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: selected ? 700 : 600,
                    color: selected ? "var(--pr-acc)" : "var(--pr-text-mid)",
                  }}
                >
                  {markMode} {card.title}
                </span>
                <span
                  style={{
                    fontSize: 10.5,
                    color: selected ? "var(--pr-text-sub)" : "var(--pr-text-muted)",
                    lineHeight: 1.6,
                  }}
                >
                  {card.description}
                </span>
              </button>
            );
          })}
        </div>
        {mode === "automatic" ? (
          <p
            style={{
              margin: 0,
              fontSize: 10.5,
              lineHeight: 1.7,
              color: "var(--pr-text-muted)",
              background: "var(--pr-bg-muted, rgba(0,0,0,0.03))",
              borderRadius: 6,
              padding: "8px 10px",
            }}
          >
            自動解析の対象は、高信頼で検出した公式 GitHub 実装と、採用済み(active)の GitHub
            リソースだけです。根拠の弱い候補(suggested)や却下した候補は解析しません。予算内で
            実行し、超過時は待機して通知します。切り替えても既存ライブラリをまとめて解析することは
            なく、過去分は別途バックフィルの確認が必要です。
          </p>
        ) : null}
      </div>

      <SettingsControlRow
        title="月額予算"
        description="この上限を超える解析は開始しません(0.00〜100.00 USD・0.50 刻み)"
        divider
      >
        <Stepper
          value={monthlyBudgetUsd}
          min={0}
          max={100}
          step={0.5}
          onChange={onBudgetChange}
          formatValue={formatUsd}
          ariaLabel="月額予算"
        />
      </SettingsControlRow>

      <SettingsControlRow title="今月のコード解析費用" description="BYOK・運営キーを問わず当月の実費を集計">
        <span
          style={{
            fontSize: 12,
            color: "var(--pr-text-mid)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {currentMonthCostUsd == null ? "—" : formatUsd(currentMonthCostUsd)}
        </span>
      </SettingsControlRow>
    </div>
  );
}
