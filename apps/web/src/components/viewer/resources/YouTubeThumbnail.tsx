"use client";

import { useState } from "react";
import { formatDuration } from "./format";

export interface YouTubeThumbnailProps {
  /** null=ダークプレースホルダのみ。 */
  thumbnailUrl: string | null;
  /** null=時間バッジ非表示。 */
  durationSeconds: number | null;
  /** 再生ボタンクリックで新規タブ(埋め込み再生はしない。docs/12 §6)。 */
  url: string;
}

/** YouTube サムネイル(高さ96px+右下再生時間バッジ。plans/09-screens/5a §4.5-c)。 */
export function YouTubeThumbnail({ thumbnailUrl, durationSeconds, url }: YouTubeThumbnailProps) {
  const [broken, setBroken] = useState(false);
  const duration = formatDuration(durationSeconds);
  const showImage = Boolean(thumbnailUrl) && !broken;

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label="YouTube で開く"
      style={{
        position: "relative",
        display: "block",
        height: 96,
        borderRadius: 5,
        background: "var(--pr-elev-bg, #26292E)",
        overflow: "hidden",
        cursor: "pointer",
      }}
    >
      {showImage ? (
        <img
          src={thumbnailUrl ?? undefined}
          alt=""
          onError={() => setBroken(true)}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : null}
      <span
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: 34,
          height: 24,
          borderRadius: 5,
          background: "var(--pr-youtube, #B3423A)",
          color: "#FFFFFF",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 11,
        }}
      >
        ▶
      </span>
      {duration ? (
        <span
          style={{
            position: "absolute",
            bottom: 6,
            right: 8,
            fontSize: 9,
            color: "#FFFFFF",
            background: "rgba(0,0,0,0.7)",
            borderRadius: 3,
            padding: "1px 5px",
            fontFamily: "'IBM Plex Mono', monospace",
          }}
        >
          {duration}
        </span>
      ) : null}
    </a>
  );
}
