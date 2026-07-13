"use client";

import type { AnchorRef, EvidenceRef } from "@alinea/api-client";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import ReactMarkdown, { type Components, type ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";
import { EvidenceChip } from "@/components/ui/EvidenceChip";
import { EVIDENCE_PROPERTY, remarkEvidence } from "@/components/chat/chat-markdown-plugins";

export interface ChatMarkdownProps {
  text: string;
  evidence: EvidenceRef[];
  onEvidenceJump?: (anchor: AnchorRef) => void;
}

type MarkdownAnchorProps = ComponentPropsWithoutRef<"a"> &
  ExtraProps & {
    [EVIDENCE_PROPERTY]?: unknown;
  };
type MarkdownTableProps = ComponentPropsWithoutRef<"table"> & ExtraProps;
type MarkdownPreProps = ComponentPropsWithoutRef<"pre"> & ExtraProps;
type MarkdownImageProps = ComponentPropsWithoutRef<"img"> & ExtraProps;

function parseEvidenceReference(value: unknown): number | undefined {
  if (typeof value === "number") return Number.isSafeInteger(value) ? value : undefined;
  if (typeof value !== "string" || !/^\d+$/.test(value)) return undefined;

  const reference = Number(value);
  return Number.isSafeInteger(reference) ? reference : undefined;
}

function isSafeExternalHref(href: string | undefined): href is string {
  if (href === undefined) return false;

  try {
    const url = new URL(href);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

function ChatTable({ children }: MarkdownTableProps) {
  return (
    <div className="alinea-chat-table-scroll" role="region" aria-label="Markdown表" tabIndex={0}>
      <table>{children}</table>
    </div>
  );
}

function ChatPre({ children }: MarkdownPreProps) {
  return <pre className="alinea-chat-code-block">{children}</pre>;
}

function ChatImage({ alt }: MarkdownImageProps) {
  return <span className="alinea-chat-image-alt">画像: {alt ?? ""}</span>;
}

/** Safe GFM renderer for assistant messages, including verified evidence references. */
export function ChatMarkdown({ text, evidence, onEvidenceJump }: ChatMarkdownProps): ReactNode {
  const evidenceByReference = new Map<number, EvidenceRef>();
  for (const item of evidence) evidenceByReference.set(item.ref, item);

  const components: Components = {
    a({ [EVIDENCE_PROPERTY]: markerValue, href, children }: MarkdownAnchorProps) {
      if (markerValue !== undefined) {
        const reference = parseEvidenceReference(markerValue);
        const item = reference === undefined ? undefined : evidenceByReference.get(reference);
        if (item === undefined) return null;

        return (
          <EvidenceChip
            anchor={{ type: "section", sectionNumber: item.display }}
            label={item.display}
            size="inline"
            onJump={() => onEvidenceJump?.(item.anchor)}
          />
        );
      }

      if (!isSafeExternalHref(href)) return <>{children}</>;

      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      );
    },
    table: ChatTable,
    pre: ChatPre,
    img: ChatImage,
  };

  return (
    <div className="alinea-chat-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkEvidence]} skipHtml components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
