"use client";

import type { CSSProperties } from "react";
import type { LicenseCard, PaperBib, RevisionInfo, TimelineEntry } from "@yakudoku/api-client";

export interface InfoPanelProps {
  paper: PaperBib;
  revision: RevisionInfo;
  licenseCard: LicenseCard;
  ingestTimeline: TimelineEntry[];
  /** エクスポート導線用。 */
  itemId: string;
}

/** 品質レベルの説明文(逐語。2a §4.2-b。docs/02 の品質定義)。 */
const QUALITY_DESCRIPTION: Record<"A" | "B", string> = {
  A: "LaTeX ソースから完全構造化。数式・相互参照・図表・脚注を保持しています。",
  B: "PDF から抽出して構造化。レイアウト由来の誤りが残る可能性があります。",
};

const headingStyle: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 700,
  color: "var(--pr-text-muted)",
  letterSpacing: "0.4px",
};

const chipStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  height: 19,
  padding: "0 8px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 4,
  fontSize: 10.5,
};

/** 2 行目以降は同一日付なら HH:mm のみ、初回は M/DD HH:mm(2a §4.2-b)。 */
function formatTimeline(entries: TimelineEntry[]): { text: string; label: string }[] {
  let prevDate = "";
  return entries.map((e) => {
    const d = new Date(e.at);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const dateKey = `${d.getMonth() + 1}/${String(d.getDate()).padStart(2, "0")}`;
    const time = prevDate === dateKey ? `${hh}:${mm}` : `${dateKey} ${hh}:${mm}`;
    prevDate = dateKey;
    return { text: time, label: e.label };
  });
}

/** 情報タブ(M0 分。2a §4.2)。書誌・品質と取り込み・ライセンス・エクスポート。 */
export function InfoPanel({ paper, revision, licenseCard, ingestTimeline, itemId }: InfoPanelProps) {
  const level: "A" | "B" = revision.quality_level === "B" ? "B" : "A";
  const timeline = formatTimeline(ingestTimeline);
  const reuse = licenseCard.figure_reuse;
  const licenseTone =
    reuse === "allowed"
      ? { border: "rgba(101,148,113,0.4)", bg: "rgba(101,148,113,0.10)", title: "#4C7458" }
      : reuse === "forbidden"
        ? { border: "rgba(176,104,79,0.4)", bg: "rgba(176,104,79,0.10)", title: "var(--pr-warn)" }
        : { border: "var(--pr-border-control)", bg: "var(--pr-bg-inset)", title: "var(--pr-text-mid)" };

  return (
    <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14, fontSize: 12 }}>
      {/* (a) 書誌情報 */}
      <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={headingStyle}>書誌情報</div>
        <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.55, color: "var(--pr-text)" }}>
          {paper.title}
        </div>
        <div style={{ fontSize: 11, color: "var(--pr-text-sub)", lineHeight: 1.6 }}>
          {paper.authors.join(", ")}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, paddingTop: 2 }}>
          {paper.venue ? <span style={{ ...chipStyle, color: "var(--pr-text-mid)" }}>{paper.venue}</span> : null}
          {paper.arxiv_id ? (
            <a
              href={`https://arxiv.org/abs/${paper.arxiv_id}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ ...chipStyle, color: "var(--pr-acc)", fontWeight: 600, textDecoration: "none" }}
            >
              arXiv:{paper.arxiv_id} ↗
            </a>
          ) : null}
          {paper.doi ? (
            <a
              href={`https://doi.org/${paper.doi}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ ...chipStyle, color: "var(--pr-acc)", fontWeight: 600, textDecoration: "none" }}
            >
              DOI ↗
            </a>
          ) : null}
        </div>
      </section>

      <div role="separator" style={{ height: 1, background: "var(--pr-border-hair)" }} />

      {/* (b) 品質レベルと取り込み */}
      <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={headingStyle}>品質レベルと取り込み</div>
        <div style={{ display: "flex", gap: 9, alignItems: "flex-start" }}>
          <span
            style={{
              width: 26,
              height: 26,
              flex: "none",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: 6,
              fontSize: 13,
              fontWeight: 700,
              background: level === "A" ? "var(--pr-acc-s)" : "var(--pr-bg-inset)",
              color: level === "A" ? "var(--pr-acc)" : "var(--pr-text-sub2)",
            }}
          >
            {level}
          </span>
          <span style={{ fontSize: 11, color: "var(--pr-text-sub)", lineHeight: 1.65 }}>
            {QUALITY_DESCRIPTION[level]}
          </span>
        </div>
        {timeline.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", paddingLeft: 3 }}>
            {timeline.map((t, i) => {
              const last = i === timeline.length - 1;
              return (
                <div key={i} style={{ display: "flex", gap: 9, fontSize: 10.5, color: "var(--pr-text-sub)" }}>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--pr-green)", marginTop: 3 }} />
                    {!last ? <span style={{ width: 1.5, flex: 1, background: "var(--pr-border-pane)" }} /> : null}
                  </div>
                  <div style={{ paddingBottom: last ? 0 : 10 }}>
                    {t.text} — {t.label}
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}
      </section>

      <div role="separator" style={{ height: 1, background: "var(--pr-border-hair)" }} />

      {/* (c) ライセンス */}
      <section style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        <div style={headingStyle}>ライセンス</div>
        <div
          style={{
            border: `1px solid ${licenseTone.border}`,
            background: licenseTone.bg,
            borderRadius: 8,
            padding: "9px 11px",
            display: "flex",
            flexDirection: "column",
            gap: 3,
          }}
        >
          <div style={{ fontSize: 11.5, fontWeight: 700, color: licenseTone.title }}>{licenseCard.license}</div>
          <div style={{ fontSize: 10, color: "var(--pr-text-sub)", lineHeight: 1.6 }}>{licenseCard.message}</div>
        </div>
      </section>

      <div role="separator" style={{ height: 1, background: "var(--pr-border-hair)" }} />

      {/* (d) エクスポート */}
      <section style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        <div style={headingStyle}>エクスポート</div>
        <div style={{ display: "flex", gap: 6 }}>
          <a
            download
            href={`/api/library-items/${itemId}/export/annotations`}
            style={exportBtnStyle}
          >
            注釈 Markdown ⤓
          </a>
          <a download href={`/api/papers/${paper.id}/pdf`} style={exportBtnStyle}>
            原文 PDF ⤓
          </a>
        </div>
      </section>
    </div>
  );
}

const exportBtnStyle: CSSProperties = {
  flex: 1,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  height: 28,
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  fontSize: 11,
  color: "var(--pr-text-mid)",
  textDecoration: "none",
};
