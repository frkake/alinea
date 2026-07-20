"use client";

import { useState } from "react";

/** エクスポート形式カード 1 枚(4f §4.6)。 */
export interface ExportFormatCardProps {
  title: string;
  description: string;
  onExport: () => void;
  /** null 以外なら「エクスポート ⤓」の代わりに表示(「準備中…」用)。 */
  busyLabel?: string | null;
  /** ジョブ失敗時のエラーメッセージ。カード内に赤字で表示される。 */
  errorLabel?: string | null;
}

export function ExportFormatCard({
  title,
  description,
  onExport,
  busyLabel = null,
  errorLabel = null,
}: ExportFormatCardProps) {
  const busy = busyLabel != null;
  const [hover, setHover] = useState(false);
  return (
    <div
      role="article"
      aria-label={title}
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "11px 13px",
        border: "1px solid var(--pr-border-control)",
        borderRadius: 8,
      }}
    >
      <span style={{ fontSize: 12, fontWeight: 700 }}>{title}</span>
      <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)", lineHeight: 1.6 }}>
        {description}
      </span>
      <button
        type="button"
        disabled={busy}
        onClick={onExport}
        aria-label={busy ? undefined : `${title} をエクスポート`}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          marginTop: 2,
          alignSelf: "flex-start",
          border: "none",
          background: "transparent",
          padding: 0,
          fontSize: 11,
          fontWeight: 600,
          color: busy ? "var(--pr-text-muted)" : "var(--pr-acc)",
          textDecoration: !busy && hover ? "underline" : "none",
          cursor: busy ? "default" : "pointer",
          pointerEvents: busy ? "none" : "auto",
          fontFamily: "inherit",
        }}
      >
        {busy ? busyLabel : "エクスポート ⤓"}
      </button>
      {errorLabel != null && (
        <span style={{ fontSize: 10.5, color: "var(--pr-warn)", marginTop: 2 }}>
          {errorLabel}
        </span>
      )}
    </div>
  );
}
