"use client";

import { useRef, useState } from "react";
import { ArticleEvidenceChips } from "@/components/viewer/article/ArticleEvidenceChips";
import { FigureVersionPopover } from "@/components/viewer/article/FigureVersionPopover";
import { RewriteInstructionPopover } from "@/components/viewer/article/RewriteInstructionPopover";
import type { AnchorRef, OverviewFigureRef } from "@/components/viewer/article/types";

export interface OverviewFigureFrameProps {
  figure: OverviewFigureRef;
  articleId: string;
  rewriting: boolean;
  rewritingProgressPct?: number;
  onRewrite: (instruction?: string) => void;
  onRestoreVersion: (version: number) => void;
  onJumpToAnchor: (anchor: AnchorRef) => void;
}

/** 全体概要図ブロック(1h §4.6・§5.4。work-breakdown 成果物名 `OverviewFigureFrame`)。 */
export function OverviewFigureFrame({
  figure,
  articleId,
  rewriting,
  rewritingProgressPct = 0,
  onRewrite,
  onRestoreVersion,
  onJumpToAnchor,
}: OverviewFigureFrameProps) {
  const versionAnchorRef = useRef<HTMLButtonElement>(null);
  const rewriteAnchorRef = useRef<HTMLButtonElement>(null);
  const [versionOpen, setVersionOpen] = useState(false);
  const [rewriteOpen, setRewriteOpen] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [cacheBuster, setCacheBuster] = useState(0);

  const src = figure.raster_url ?? figure.svg_url;
  const imgSrc = cacheBuster > 0 ? `${src}${src.includes("?") ? "&" : "?"}r=${cacheBuster}` : src;

  return (
    <div
      style={{
        border: "1px solid var(--pr-border-card)",
        borderRadius: 10,
        background: "var(--pr-bg-card)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          borderBottom: "1px solid var(--pr-border-hair)",
        }}
      >
        <span style={{ fontSize: 10.5, fontWeight: 700, color: "var(--pr-a)" }}>✦ 全体概要図</span>
        <button
          ref={versionAnchorRef}
          type="button"
          onClick={() => setVersionOpen((v) => !v)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            height: 15,
            padding: "0 5px",
            border: "1px solid var(--pr-border-control)",
            borderRadius: 3,
            fontSize: 9,
            fontWeight: 600,
            color: "var(--pr-text-icon)",
            background: "transparent",
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          AI生成 · 版 {figure.version}
        </button>
        <div style={{ flex: 1 }} />
        <button
          ref={rewriteAnchorRef}
          type="button"
          onClick={() => setRewriteOpen((v) => !v)}
          style={{
            fontSize: 10.5,
            color: "var(--pr-a)",
            fontWeight: 600,
            border: "none",
            background: "transparent",
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          ✦ 書き直し指示
        </button>
        <a
          href={`${figure.svg_url}?download=true`}
          download
          style={{ fontSize: 10.5, color: "var(--pr-text-sub)", fontFamily: "inherit" }}
        >
          SVG ⤓
        </a>
      </div>

      <div style={{ padding: "18px 20px", position: "relative" }}>
        {imgError ? (
          <div
            style={{
              height: 180,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
              fontSize: 11,
              color: "var(--pr-text-muted)",
            }}
          >
            概要図を読み込めませんでした ·{" "}
            <button
              type="button"
              onClick={() => {
                setImgError(false);
                setCacheBuster(Date.now());
              }}
              style={{
                border: "none",
                background: "transparent",
                padding: 0,
                cursor: "pointer",
                fontFamily: "inherit",
                color: "var(--pr-a)",
                fontSize: 11,
              }}
            >
              再読み込み
            </button>
          </div>
        ) : (
          <img
            src={imgSrc}
            alt="概要図(課題→提案→結果)"
            style={{ width: "100%", display: "block" }}
            onError={() => setImgError(true)}
          />
        )}
        {rewriting ? (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "rgba(251,250,247,0.7)",
            }}
          >
            <span style={{ fontSize: 11, color: "var(--pr-a)" }}>✦ 書き直し中… {rewritingProgressPct}%</span>
          </div>
        ) : null}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "7px 12px",
          borderTop: "1px solid var(--pr-border-hair)",
          background: "var(--pr-bg-app)",
        }}
      >
        <span style={{ fontSize: 9.5, color: "var(--pr-text-muted)" }}>
          {figure.dsl.footer.generated_by} · {figure.dsl.footer.date}
        </span>
        <div style={{ flex: 1 }} />
        {figure.evidence.length > 0 ? (
          <>
            <span style={{ fontSize: 9.5, color: "var(--pr-text-muted)" }}>根拠:</span>
            <ArticleEvidenceChips
              evidence={figure.evidence.map((ev, i) => ({ ref: i, display: ev.display, anchor: ev.anchor }))}
              onJumpToAnchor={onJumpToAnchor}
              size="figure-footer"
            />
          </>
        ) : null}
      </div>

      <FigureVersionPopover
        open={versionOpen}
        onClose={() => setVersionOpen(false)}
        anchorRef={versionAnchorRef}
        articleId={articleId}
        currentVersion={figure.version}
        onRestore={(version) => {
          setVersionOpen(false);
          onRestoreVersion(version);
        }}
      />
      <RewriteInstructionPopover
        open={rewriteOpen}
        onClose={() => setRewriteOpen(false)}
        anchorRef={rewriteAnchorRef}
        placeholder="例: 実験の部分を削って手法を厚く"
        pending={rewriting}
        onSubmit={(instruction) => {
          setRewriteOpen(false);
          onRewrite(instruction.length > 0 ? instruction : undefined);
        }}
      />
    </div>
  );
}
