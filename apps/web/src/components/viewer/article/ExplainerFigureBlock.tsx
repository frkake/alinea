"use client";

import { useEffect, useState } from "react";
import type { ExplainerContentOut } from "@alinea/api-client";
import { AIBadge } from "@/components/ui/AIBadge";

/** 解説図ブロック。生成待ち・画像読込中・読込失敗を空白にせず明示する。 */
export function ExplainerFigureBlock({ explainer }: { explainer: ExplainerContentOut }) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setLoaded(false);
    setFailed(false);
  }, [explainer.image_url]);

  const pendingGeneration = !explainer.image_url;
  const showStatus = pendingGeneration || !loaded || failed;

  return (
    <div
      style={{
        border: "1px solid var(--pr-border-card)",
        borderRadius: 10,
        background: "var(--pr-bg-card)",
        overflow: "hidden",
      }}
    >
      <div style={{ margin: "14px 16px 0", minHeight: 180, position: "relative" }}>
        {explainer.image_url ? (
          <img
            src={explainer.image_url}
            alt={explainer.caption}
            onLoad={() => setLoaded(true)}
            onError={() => setFailed(true)}
            style={{
              display: "block",
              width: "100%",
              height: "auto",
              borderRadius: 6,
              border: "1px solid var(--pr-border-thumb)",
              visibility: loaded && !failed ? "visible" : "hidden",
            }}
          />
        ) : null}
        {showStatus ? (
          <div
            role="status"
            style={{
              position: explainer.image_url ? "absolute" : "relative",
              inset: explainer.image_url ? 0 : undefined,
              minHeight: 180,
              borderRadius: 6,
              border: "1px dashed var(--pr-border-thumb)",
              background: "var(--pr-bg-thumb)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              color: failed ? "var(--pr-text-muted)" : "var(--pr-a)",
              fontSize: 11,
            }}
          >
            <span style={{ fontSize: 18 }}>{failed ? "×" : "✦"}</span>
            <span style={{ fontWeight: 700 }}>
              {failed
                ? "AI 解説図を読み込めませんでした"
                : pendingGeneration
                  ? "AI 解説図を生成しています…"
                  : "AI 解説図を読み込んでいます…"}
            </span>
            {!failed ? (
              <span style={{ color: "var(--pr-text-muted)" }}>完成すると自動的に表示されます</span>
            ) : null}
          </div>
        ) : null}
      </div>
      <div style={{ padding: "10px 16px 12px", display: "flex", flexDirection: "column", gap: 5 }}>
        <div
          style={{
            fontFamily: "var(--pr-jp, 'Noto Serif JP'), serif",
            fontSize: 12,
            lineHeight: 1.75,
            color: "var(--pr-text-body)",
          }}
        >
          {explainer.caption}
        </div>
        <AIBadge variant="generated" />
      </div>
    </div>
  );
}
