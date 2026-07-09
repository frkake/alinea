import type { AttributionContentOut } from "@alinea/api-client";
import {
  AuthorGlyph,
  CalendarGlyph,
  LicenseGlyph,
  MetadataChip,
  SmartInlineLink,
} from "@/components/viewer/SmartInlineLink";

interface ParsedAttribution {
  authors: string | null;
  title: string | null;
  arxivId: string | null;
  year: string | null;
  license: string | null;
  summary: string;
}

const ARXIV_RE = /arXiv:\s*([A-Za-z0-9./-]+(?:v\d+)?)/i;

function parseAttribution(text: string): ParsedAttribution {
  const normalized = text.replace(/\s+/g, " ").trim();
  const arxivId = normalized.match(ARXIV_RE)?.[1] ?? null;
  const year = normalized.match(/\((\d{4}|年不明)\)/)?.[1] ?? null;
  const license = normalized.match(/ライセンス\s+(.+)$/)?.[1]?.trim() ?? null;
  const sourceMatch = normalized.match(/^出典:\s*(.*?)\.\s*"([^"]+?)\.?"\s*(.*)$/);

  if (sourceMatch) {
    return {
      authors: sourceMatch[1]?.trim() || null,
      title: sourceMatch[2]?.trim() || null,
      arxivId,
      year,
      license,
      summary: "記事中の引用・図表はこの論文を出典として自動構成しています。",
    };
  }

  return {
    authors: null,
    title: null,
    arxivId,
    year,
    license,
    summary: arxivId
      ? normalized.replace(
          new RegExp(
            `\\s*arXiv:\\s*${arxivId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:\\([^)]*\\))?`,
            "i",
          ),
          " 原論文",
        )
      : normalized,
  };
}

/** 出典ブロック(1h §4.11)。`locked: true` — 削除不可・ホバーツールバー非表示。 */
export function AttributionBlock({ attribution }: { attribution: AttributionContentOut }) {
  const parsed = parseAttribution(attribution.text);

  return (
    <div
      style={{
        border: "1px solid var(--pr-border-card)",
        borderRadius: 8,
        background: "var(--pr-bg-inset)",
        padding: "11px 14px",
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
      }}
    >
      <div style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <div
            style={{
              fontSize: 9.5,
              fontWeight: 700,
              color: "var(--pr-text-muted)",
              letterSpacing: "0.3px",
            }}
          >
            出典
          </div>
          {parsed.title ? (
            <div
              style={{ fontSize: 12, lineHeight: 1.55, fontWeight: 650, color: "var(--pr-text)" }}
            >
              {parsed.title}
            </div>
          ) : null}
          <div style={{ fontSize: 10.5, lineHeight: 1.7, color: "var(--pr-text-sub)" }}>
            {parsed.summary}
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {parsed.authors ? (
            <MetadataChip icon={<AuthorGlyph />} label="著者">
              {parsed.authors}
            </MetadataChip>
          ) : null}
          {parsed.arxivId ? (
            <SmartInlineLink
              href={`https://arxiv.org/abs/${parsed.arxivId}`}
              label={`arXiv:${parsed.arxivId}`}
            />
          ) : null}
          {parsed.year ? (
            <MetadataChip icon={<CalendarGlyph />} label="年">
              {parsed.year}
            </MetadataChip>
          ) : null}
          {parsed.license ? (
            <MetadataChip icon={<LicenseGlyph />} label="ライセンス">
              {parsed.license}
            </MetadataChip>
          ) : null}
        </div>
      </div>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          height: 18,
          padding: "0 8px",
          borderRadius: 4,
          background: "var(--pr-bg-locked-badge)",
          color: "var(--pr-text-icon)",
          fontSize: 9.5,
          fontWeight: 600,
          flex: "none",
          marginTop: 1,
        }}
      >
        自動挿入 · 削除不可
      </span>
    </div>
  );
}
