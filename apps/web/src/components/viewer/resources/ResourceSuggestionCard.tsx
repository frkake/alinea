"use client";

import { formatCompact, HF_REPO_LABELS, stripScheme } from "./format";
import type { ResourceSuggestion } from "./types";

export interface ResourceSuggestionCardProps {
  suggestion: ResourceSuggestion;
  onAccept: () => void;
  onDismiss: () => void;
  /** accept/dismiss の実行中(両ボタン disabled)。 */
  pending: boolean;
}

/** 候補の見出し(kind 別・設計 §4)。 */
function headline(s: ResourceSuggestion): { icon: string; label: string } {
  const kind = s.kind ?? "github";
  if (kind === "huggingface") {
    const repoType = (s.meta?.repo_type as string | undefined) ?? s.relation ?? "model";
    return { icon: "🤗", label: `Hugging Face ${HF_REPO_LABELS[repoType] ?? repoType}` };
  }
  if (kind === "project") {
    return { icon: "🌐", label: "公式プロジェクトページ" };
  }
  // github(arXiv 由来の公式実装 または HF paper-level githubRepo)。
  return { icon: "✦", label: "公式実装を検出しました" };
}

/** Hugging Face カードの補助行(repo ID・downloads・likes・pipeline tag)。 */
function hfDetail(s: ResourceSuggestion): string | null {
  if (s.kind !== "huggingface") return null;
  const meta = s.meta ?? {};
  const segments: string[] = [];
  const repoId = (meta.repo_id as string | undefined) ?? s.title ?? undefined;
  if (repoId) segments.push(repoId);
  if (meta.pipeline_tag) segments.push(String(meta.pipeline_tag));
  if (meta.downloads != null) segments.push(`⬇ ${formatCompact(meta.downloads as number)}`);
  if (meta.likes != null) segments.push(`♥ ${formatCompact(meta.likes as number)}`);
  return segments.length ? segments.join(" · ") : null;
}

/** 関連ソース候補カード(破線。plans/09-screens/5a §4.5-a・docs/12 §5・設計 §3-§4)。 */
export function ResourceSuggestionCard({
  suggestion,
  onAccept,
  onDismiss,
  pending,
}: ResourceSuggestionCardProps) {
  const { icon, label } = headline(suggestion);
  const detail = hfDetail(suggestion);
  const isOfficial = suggestion.official_candidate;

  return (
    <div
      data-suggestion-url={suggestion.url}
      style={{
        border: "1px dashed var(--pr-border-dashed-suggest, #CBC7BA)",
        borderRadius: 8,
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 7,
        background: "var(--pr-bg-app, #FBFAF7)",
      }}
    >
      <div style={{ fontSize: 11, lineHeight: 1.65, color: "var(--pr-text-mid)" }}>
        <span style={{ color: "var(--pr-acc)", fontWeight: 700 }}>
          {icon} {label}
        </span>
        {isOfficial ? (
          <span
            style={{
              marginLeft: 6,
              padding: "0 5px",
              borderRadius: 3,
              background: "var(--pr-official-bg, rgba(101,148,113,0.16))",
              color: "var(--pr-official-fg, #4C7458)",
              fontSize: 8,
              fontWeight: 700,
            }}
          >
            公式候補
          </span>
        ) : null}
        <br />
        {detail ? (
          <>
            <span style={{ color: "var(--pr-text-sub)", fontSize: 10.5 }}>{detail}</span>
            <br />
          </>
        ) : null}
        <span
          style={{
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: 10.5,
            color: "var(--pr-text-sub)",
          }}
        >
          {stripScheme(suggestion.url)}
        </span>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button
          type="button"
          onClick={onAccept}
          disabled={pending}
          style={{
            display: "inline-flex",
            alignItems: "center",
            height: 22,
            padding: "0 11px",
            borderRadius: 5,
            border: "none",
            background: "var(--pr-acc)",
            color: "#FFFFFF",
            fontSize: 10.5,
            fontWeight: 600,
            fontFamily: "inherit",
            cursor: pending ? "default" : "pointer",
            opacity: pending ? 0.5 : 1,
          }}
        >
          + 追加
        </button>
        <button
          type="button"
          onClick={onDismiss}
          disabled={pending}
          style={{
            height: 22,
            padding: "0 10px",
            border: "none",
            background: "transparent",
            color: "var(--pr-text-muted)",
            fontSize: 10.5,
            fontFamily: "inherit",
            cursor: pending ? "default" : "pointer",
            opacity: pending ? 0.5 : 1,
          }}
        >
          無視
        </button>
      </div>
    </div>
  );
}
