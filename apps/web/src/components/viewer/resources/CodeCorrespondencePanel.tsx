"use client";

import { useState } from "react";
import type { CorrespondenceOut, RunOut } from "@alinea/api-client";

/**
 * コード対応結果パネル(Task 22・設計 §5・§12・§13)。
 *
 * 論文側の主張とコード側の対応を一行の対応として表示する。high / medium は通常表示、
 * low は「関連候補」に折り畳む。論文 anchor はビューア内 block へ移動(onJumpBlock)、
 * GitHub anchor は解析時 commit に固定した `#Lx-Ly` を新規タブで開く。
 *
 * status によって succeeded(対応 0 件は「対応箇所を特定できませんでした」)・stale(古い結果)・
 * failed(途中結果を出さず失敗表示)・waiting_budget(予算超過・設定リンク)を出し分ける。
 */
export interface CodeCorrespondencePanelProps {
  /** 対象 GitHub リポジトリの URL(fixed-commit blob link の組み立てに使う)。 */
  repoUrl: string;
  run: RunOut;
  correspondences: CorrespondenceOut[];
  stale: boolean;
  /** 論文側 anchor(block_id)へビューアをスクロールさせる。 */
  onJumpBlock: (blockId: string) => void;
  /** 設定画面(コード解析)への遷移先。既定はアカウント設定。 */
  settingsHref?: string;
}

const CONFIDENCE_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

/**
 * 解析時 commit に固定した GitHub blob URL(`#Lx-Ly`)。branch 名ではなく commit SHA を使う
 * ことで、リポジトリが更新されても行範囲がずれない(設計 §5)。
 */
export function githubBlobUrl(
  repoUrl: string,
  commitSha: string,
  path: string,
  startLine: number,
  endLine: number,
): string {
  const base = repoUrl.replace(/\/+$/, "").replace(/\.git$/, "");
  const cleanPath = path.replace(/^\/+/, "");
  const range = endLine > startLine ? `#L${startLine}-L${endLine}` : `#L${startLine}`;
  return `${base}/blob/${commitSha}/${cleanPath}${range}`;
}

function CorrespondenceRow({
  correspondence,
  repoUrl,
  commitSha,
  onJumpBlock,
}: {
  correspondence: CorrespondenceOut;
  repoUrl: string;
  commitSha: string;
  onJumpBlock: (blockId: string) => void;
}) {
  const c = correspondence;
  const blockId = (c.paper_anchor?.block_id as string | undefined) ?? "";
  const blobUrl = githubBlobUrl(repoUrl, commitSha, c.path, c.start_line, c.end_line);
  const lineRange = c.end_line > c.start_line ? `L${c.start_line}-L${c.end_line}` : `L${c.start_line}`;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "10px 12px",
        border: "1px solid var(--pr-border-card)",
        borderRadius: 8,
        background: "var(--pr-bg-card)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span
          aria-label={`確信度 ${CONFIDENCE_LABELS[c.confidence] ?? c.confidence}`}
          style={{
            height: 15,
            padding: "0 6px",
            borderRadius: 3,
            fontSize: 8.5,
            fontWeight: 700,
            display: "inline-flex",
            alignItems: "center",
            background:
              c.confidence === "high"
                ? "var(--pr-official-bg, rgba(101,148,113,0.16))"
                : "var(--pr-acc-s)",
            color: c.confidence === "high" ? "var(--pr-official-fg, #4C7458)" : "var(--pr-acc)",
          }}
        >
          確信度 {CONFIDENCE_LABELS[c.confidence] ?? c.confidence}
        </span>
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            fontFamily: "'IBM Plex Mono', monospace",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {c.symbol || c.path}
        </span>
      </div>

      <p style={{ margin: 0, fontSize: 11, lineHeight: 1.7, color: "var(--pr-text-mid)" }}>
        {c.explanation_ja}
      </p>

      {c.claim_text ? (
        <p
          style={{
            margin: 0,
            fontSize: 10.5,
            lineHeight: 1.6,
            color: "var(--pr-text-muted)",
            fontStyle: "italic",
          }}
        >
          「{c.claim_text}」
        </p>
      ) : null}

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          aria-label="論文の該当箇所へ移動"
          disabled={!blockId}
          onClick={() => {
            if (blockId) onJumpBlock(blockId);
          }}
          style={{
            fontSize: 11,
            color: "var(--pr-acc)",
            fontWeight: 600,
            border: "none",
            background: "transparent",
            cursor: blockId ? "pointer" : "default",
            padding: 0,
            fontFamily: "inherit",
          }}
        >
          論文の該当箇所 ↑
        </button>
        <a
          href={blobUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            fontSize: 11,
            color: "var(--pr-acc)",
            fontWeight: 600,
            fontFamily: "'IBM Plex Mono', monospace",
          }}
        >
          {c.path} #{lineRange} ↗
        </a>
      </div>
    </div>
  );
}

export function CodeCorrespondencePanel({
  repoUrl,
  run,
  correspondences,
  stale,
  onJumpBlock,
  settingsHref = "/settings?category=account",
}: CodeCorrespondencePanelProps) {
  const [showLow, setShowLow] = useState(false);

  if (run.status === "failed") {
    return (
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 6 }}>
        <p style={{ margin: 0, fontSize: 12, fontWeight: 600, color: "var(--pr-warn, #A05A42)" }}>
          解析に失敗しました
        </p>
        <p style={{ margin: 0, fontSize: 11, lineHeight: 1.7, color: "var(--pr-text-muted)" }}>
          途中結果は表示していません。もう一度お試しください。
        </p>
      </div>
    );
  }

  if (run.status === "waiting_budget") {
    return (
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 6 }}>
        <p style={{ margin: 0, fontSize: 12, fontWeight: 600 }}>月額予算を超えるため待機中です</p>
        <p style={{ margin: 0, fontSize: 11, lineHeight: 1.7, color: "var(--pr-text-muted)" }}>
          予算内に収まると自動で再開します。今すぐ実行するには予算を見直してください。
        </p>
        <a href={settingsHref} style={{ fontSize: 11, color: "var(--pr-acc)", fontWeight: 600 }}>
          設定を開く →
        </a>
      </div>
    );
  }

  const high = correspondences.filter((c) => c.confidence === "high");
  const medium = correspondences.filter((c) => c.confidence === "medium");
  const low = correspondences.filter((c) => c.confidence === "low");
  const primary = [...high, ...medium];

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 9 }}>
      {stale ? (
        <div
          role="status"
          style={{
            fontSize: 11,
            lineHeight: 1.6,
            color: "var(--pr-warn, #A05A42)",
            background: "var(--pr-bg-muted, rgba(0,0,0,0.03))",
            borderRadius: 6,
            padding: "8px 10px",
          }}
        >
          古い結果です。リポジトリが更新されています。再解析すると最新の commit で対応を取り直せます。
        </div>
      ) : null}

      {correspondences.length === 0 ? (
        <p style={{ margin: 0, fontSize: 12, color: "var(--pr-text-mid)", lineHeight: 1.7 }}>
          対応箇所を特定できませんでした
        </p>
      ) : (
        <>
          {primary.map((c, i) => (
            <CorrespondenceRow
              key={`p-${i}`}
              correspondence={c}
              repoUrl={repoUrl}
              commitSha={run.commit_sha}
              onJumpBlock={onJumpBlock}
            />
          ))}

          {low.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <button
                type="button"
                aria-expanded={showLow}
                onClick={() => setShowLow((v) => !v)}
                style={{
                  alignSelf: "flex-start",
                  fontSize: 11,
                  fontWeight: 600,
                  color: "var(--pr-text-mid)",
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  padding: 0,
                }}
              >
                {showLow ? "▾" : "▸"} 関連候補({low.length})
              </button>
              {showLow
                ? low.map((c, i) => (
                    <CorrespondenceRow
                      key={`l-${i}`}
                      correspondence={c}
                      repoUrl={repoUrl}
                      commitSha={run.commit_sha}
                      onJumpBlock={onJumpBlock}
                    />
                  ))
                : null}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
