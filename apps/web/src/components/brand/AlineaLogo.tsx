import type { CSSProperties } from "react";

export interface AlineaMarkProps {
  size?: number;
  className?: string;
  style?: CSSProperties;
  title?: string;
}

export function AlineaMark({ size = 22, className, style, title }: AlineaMarkProps) {
  const labelled = Boolean(title);
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role={labelled ? "img" : undefined}
      aria-hidden={labelled ? undefined : true}
      aria-label={title}
      className={className}
      style={{ display: "block", flex: "none", ...style }}
    >
      <rect width="64" height="64" rx="14" fill="var(--pr-acc, #3E5C76)" />
      <path
        d="M19 47 31.8 17 45 47"
        fill="none"
        stroke="#FFFFFF"
        strokeWidth="7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M25.8 37.5h12.4"
        fill="none"
        stroke="#FFFFFF"
        strokeWidth="6"
        strokeLinecap="round"
      />
      <path
        d="M22 52h20"
        fill="none"
        stroke="#DDE8E1"
        strokeWidth="4"
        strokeLinecap="round"
      />
    </svg>
  );
}

export interface AlineaLogoProps {
  markSize?: number;
  wordSize?: number;
  width?: number | string;
  centered?: boolean;
}

export function AlineaLogo({
  markSize = 22,
  wordSize = 14.5,
  width,
  centered = false,
}: AlineaLogoProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: centered ? "center" : "flex-start",
        gap: 8,
        width,
      }}
    >
      <AlineaMark size={markSize} />
      <span style={{ fontSize: wordSize, fontWeight: 700, letterSpacing: 0 }}>Alinea</span>
    </div>
  );
}
