"use client";

import type { CSSProperties, ReactNode } from "react";

type SmartLinkKind = "github" | "huggingface" | "arxiv" | "doi" | "external";

interface LinkDisplay {
  kind: SmartLinkKind;
  sourceLabel: string;
  label: string;
  chip: boolean;
}

export interface SmartInlineLinkProps {
  href: string;
  label?: string | null;
  className?: string;
}

const RAW_URL_RE = /^https?:\/\//i;

const chipStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  minHeight: 21,
  maxWidth: "100%",
  padding: "1px 7px 1px 4px",
  border: "1px solid var(--pr-border-card)",
  borderRadius: 5,
  background: "var(--pr-bg-inset)",
  color: "var(--pr-text)",
  textDecoration: "none",
  fontFamily: "var(--pr-font-ui)",
  fontSize: "0.86em",
  fontWeight: 600,
  lineHeight: 1.35,
  verticalAlign: "baseline",
  overflowWrap: "anywhere",
};

const textLinkStyle: CSSProperties = {
  color: "var(--pr-acc)",
  textDecoration: "none",
  fontWeight: 600,
  overflowWrap: "anywhere",
};

const iconStyle: CSSProperties = {
  width: 17,
  height: 17,
  borderRadius: 4,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  flex: "none",
  fontFamily: "var(--pr-font-ui)",
  fontSize: 8.5,
  fontWeight: 800,
  lineHeight: 1,
};

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function truncateMiddle(value: string, max = 46): string {
  if (value.length <= max) return value;
  const head = Math.max(12, Math.floor((max - 1) * 0.62));
  const tail = Math.max(8, max - head - 1);
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

function stripScheme(value: string): string {
  return value.replace(/^https?:\/\//i, "").replace(/\/$/, "");
}

function isRawishLabel(label: string, host: string): boolean {
  const text = label.trim();
  if (!text) return true;
  if (RAW_URL_RE.test(text)) return true;
  return text === host || text.startsWith(`${host}/`);
}

function githubLabel(pathParts: string[]): string {
  if (pathParts.length >= 2) return `${pathParts[0]}/${pathParts[1]}`;
  return "GitHub";
}

function huggingFaceLabel(pathParts: string[]): string {
  if (pathParts[0] === "spaces" && pathParts.length >= 3) {
    return `${pathParts[1]}/${pathParts[2]}`;
  }
  if (pathParts[0] === "datasets" && pathParts.length >= 3) {
    return `${pathParts[1]}/${pathParts[2]}`;
  }
  if (pathParts[0] === "papers" && pathParts[1]) {
    return `Paper ${pathParts[1]}`;
  }
  if (pathParts.length >= 2) return `${pathParts[0]}/${pathParts[1]}`;
  return "Hugging Face";
}

export function classifySmartLink(href: string, label?: string | null): LinkDisplay {
  const fallbackLabel = label?.trim() || stripScheme(href);
  let parsed: URL;
  try {
    parsed = new URL(href);
  } catch {
    return {
      kind: "external",
      sourceLabel: "LINK",
      label: truncateMiddle(fallbackLabel),
      chip: RAW_URL_RE.test(fallbackLabel),
    };
  }

  const host = parsed.hostname.toLowerCase().replace(/^www\./, "");
  const pathParts = parsed.pathname.split("/").filter(Boolean).map(safeDecode);
  const rawishLabel = isRawishLabel(fallbackLabel, host);

  if (host === "github.com") {
    const repo = githubLabel(pathParts);
    return {
      kind: "github",
      sourceLabel: "GitHub",
      label: truncateMiddle(rawishLabel || /^github$/i.test(fallbackLabel) ? repo : fallbackLabel),
      chip: true,
    };
  }

  if (host === "huggingface.co") {
    const entity = huggingFaceLabel(pathParts);
    return {
      kind: "huggingface",
      sourceLabel: "Hugging Face",
      label: truncateMiddle(
        rawishLabel || /^(hugging face|hf)$/i.test(fallbackLabel) ? entity : fallbackLabel,
      ),
      chip: true,
    };
  }

  if (host === "arxiv.org" && pathParts[0] === "abs" && pathParts[1]) {
    const arxivLabel =
      !rawishLabel && /^arxiv:/i.test(fallbackLabel) ? fallbackLabel : `arXiv:${pathParts[1]}`;
    return {
      kind: "arxiv",
      sourceLabel: "arXiv",
      label: arxivLabel,
      chip: true,
    };
  }

  if (host === "doi.org" && pathParts.length > 0) {
    return {
      kind: "doi",
      sourceLabel: "DOI",
      label: truncateMiddle(pathParts.join("/")),
      chip: true,
    };
  }

  return {
    kind: "external",
    sourceLabel: host,
    label: truncateMiddle(rawishLabel ? stripScheme(href) : fallbackLabel),
    chip: rawishLabel,
  };
}

function ProviderIcon({ kind }: { kind: SmartLinkKind }) {
  if (kind === "github") {
    return (
      <span aria-hidden="true" style={{ ...iconStyle, background: "#26292E", color: "#FFFFFF" }}>
        GH
      </span>
    );
  }
  if (kind === "huggingface") {
    return (
      <span aria-hidden="true" style={{ ...iconStyle, background: "#FFE9A8", color: "#6E4B00" }}>
        HF
      </span>
    );
  }
  if (kind === "arxiv") {
    return (
      <span
        aria-hidden="true"
        style={{ ...iconStyle, background: "#8F3F3F", color: "#FFFFFF", fontSize: 8 }}
      >
        arX
      </span>
    );
  }
  if (kind === "doi") {
    return (
      <span
        aria-hidden="true"
        style={{ ...iconStyle, background: "#E8EEF5", color: "#3F668A", fontSize: 7.5 }}
      >
        DOI
      </span>
    );
  }
  return (
    <span
      aria-hidden="true"
      style={{ ...iconStyle, background: "var(--pr-bg-card)", color: "var(--pr-text-muted)" }}
    >
      ↗
    </span>
  );
}

export function SmartInlineLink({ href, label, className }: SmartInlineLinkProps) {
  const display = classifySmartLink(href, label);
  const accessibleName =
    display.chip &&
    display.sourceLabel !== display.label &&
    !display.label.toLowerCase().startsWith(display.sourceLabel.toLowerCase())
      ? `${display.sourceLabel} ${display.label}`
      : display.label;

  if (!display.chip) {
    return (
      <a
        className={className}
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        style={textLinkStyle}
      >
        {display.label}
      </a>
    );
  }

  return (
    <a
      className={className}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={accessibleName}
      title={href}
      style={chipStyle}
    >
      <ProviderIcon kind={display.kind} />
      <span style={{ minWidth: 0 }}>{display.label}</span>
    </a>
  );
}

export function MetadataChip({
  icon,
  label,
  children,
  href,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
  href?: string;
}) {
  const content = (
    <>
      <span aria-hidden="true" style={{ display: "inline-flex", color: "var(--pr-text-muted)" }}>
        {icon}
      </span>
      <span style={{ color: "var(--pr-text-muted)", fontWeight: 600 }}>{label}</span>
      <span style={{ minWidth: 0, color: "var(--pr-text)" }}>{children}</span>
    </>
  );
  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    minHeight: 22,
    maxWidth: "100%",
    padding: "1px 8px",
    borderRadius: 5,
    border: "1px solid var(--pr-border-card)",
    background: "var(--pr-bg-card)",
    fontFamily: "var(--pr-font-ui)",
    fontSize: 10.5,
    lineHeight: 1.45,
    textDecoration: "none",
    overflowWrap: "anywhere",
  };

  if (href) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" style={style}>
        {content}
      </a>
    );
  }
  return <span style={style}>{content}</span>;
}

export function AuthorGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true">
      <circle cx="6.5" cy="4.1" r="2.2" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M2.4 11c.55-2 2.1-3.1 4.1-3.1S10.05 9 10.6 11"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function CalendarGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true">
      <rect x="2" y="2.8" width="9" height="8.2" rx="1.2" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M4 1.7v2.2M9 1.7v2.2M2.4 5.6h8.2"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function LicenseGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true">
      <path
        d="M6.5 1.6 10.2 3v3.1c0 2.4-1.4 4.4-3.7 5.3-2.3-.9-3.7-2.9-3.7-5.3V3l3.7-1.4Z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path
        d="m4.7 6.5 1.2 1.2 2.5-2.5"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
