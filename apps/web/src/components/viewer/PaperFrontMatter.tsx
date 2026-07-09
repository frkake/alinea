"use client";

import type { CSSProperties, ReactNode } from "react";
import { SmartInlineLink } from "@/components/viewer/SmartInlineLink";
import { cleanLatexDisplayText } from "@/components/viewer/latex-display-clean";
import type { DocBlock, Inline } from "@/components/viewer/document-types";

interface FrontMatterLink {
  href: string;
  label: string;
}

interface ParsedFrontMatter {
  authors: string[];
  affiliations: Array<{ marker: string | null; text: string }>;
  notes: string[];
  links: FrontMatterLink[];
}

const FRONT_MATTER_HINT_RE =
  /(Equal contribution|Corresponding author|Project leader|University|Laboratory|Institute|Academy|College|School|Department)/i;
const AFFILIATION_KEYWORD_RE =
  /(University|Laboratory|Institute|Academy|College|School|Department)/i;

const blockStyle: CSSProperties = {
  margin: "8px 0 20px",
  padding: "12px 14px",
  border: "1px solid var(--pr-border-card)",
  borderRadius: 8,
  background: "var(--pr-bg-inset)",
  fontFamily: "var(--pr-font-ui)",
  color: "var(--pr-text)",
};

const headingStyle: CSSProperties = {
  marginBottom: 9,
  fontSize: 10,
  fontWeight: 750,
  letterSpacing: "0.3px",
  color: "var(--pr-text-muted)",
};

const rowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "64px minmax(0, 1fr)",
  gap: 9,
  alignItems: "start",
  marginTop: 7,
};

const labelStyle: CSSProperties = {
  paddingTop: 2,
  fontSize: 10.5,
  fontWeight: 700,
  color: "var(--pr-text-muted)",
};

const wrapStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  minWidth: 0,
};

const authorChipStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  minHeight: 22,
  padding: "1px 8px",
  borderRadius: 5,
  background: "var(--pr-bg-card)",
  border: "1px solid var(--pr-border-card)",
  fontSize: 11,
  lineHeight: 1.45,
  color: "var(--pr-text)",
};

const tagStyle: CSSProperties = {
  ...authorChipStyle,
  color: "var(--pr-text-sub)",
};

function inlinePlainText(inline: Inline): string {
  if (inline.t === "url") return " ";
  if (inline.t === "emphasis" && inline.children?.length)
    return inline.children.map(inlinePlainText).join("");
  return cleanLatexDisplayText(inline.v ?? "");
}

function collectLinks(inlines: Inline[]): FrontMatterLink[] {
  const links: FrontMatterLink[] = [];
  const walk = (items: Inline[]) => {
    for (const inline of items) {
      if (inline.t === "url" && inline.href) {
        links.push({ href: inline.href, label: inline.v || inline.href });
      }
      if (inline.children?.length) walk(inline.children);
    }
  };
  walk(inlines);
  return links;
}

function frontMatterText(block: DocBlock): string {
  return (block.inlines ?? []).map(inlinePlainText).join("").replace(/\s+/g, " ").trim();
}

function findAffiliationIndex(value: string): number {
  const markerRe = /\s(\d+)\]?\s*(?=[A-Z])/g;
  for (const match of value.matchAll(markerRe)) {
    const index = match.index ?? -1;
    if (index < 0) continue;
    if (AFFILIATION_KEYWORD_RE.test(value.slice(index))) return index + 1;
  }
  return -1;
}

function splitAuthors(value: string): string[] {
  const cleaned = value
    .replace(/^\[[^\]]*]\s*/, "")
    .replace(/\b\d+\]?/g, " ")
    .replace(/[＊*†‡✉]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return [];
  if (/[;,、，]/.test(cleaned)) {
    return cleaned
      .split(/[;,、，]/)
      .map((part) => part.trim())
      .filter(Boolean);
  }

  const tokens = cleaned.split(/\s+/).filter(Boolean);
  const names: string[] = [];
  for (let i = 0; i < tokens.length; i += 2) {
    const first = tokens[i];
    const second = tokens[i + 1];
    if (!first) continue;
    names.push(second ? `${first} ${second}` : first);
  }
  return names;
}

function parseAffiliations(value: string): Array<{ marker: string | null; text: string }> {
  const rows: Array<{ marker: string | null; text: string }> = [];
  const re = /(\d+)\]?\s*([^0-9]+?)(?=\s+\d+\]?|$)/g;
  for (const match of value.matchAll(re)) {
    const text = (match[2] ?? "").replace(/\s+/g, " ").trim();
    if (text) rows.push({ marker: match[1] ?? null, text });
  }
  if (rows.length > 0) return rows;
  const fallback = value.replace(/\s+/g, " ").trim();
  return fallback ? [{ marker: null, text: fallback }] : [];
}

function parseNotes(value: string): string[] {
  const notes: string[] = [];
  if (/Equal contribution/i.test(value)) notes.push("Equal contribution");
  if (/Corresponding author/i.test(value)) notes.push("Corresponding author");
  if (/Project leader/i.test(value)) notes.push("Project leader");
  return notes;
}

function parseFrontMatter(block: DocBlock): ParsedFrontMatter {
  const text = frontMatterText(block);
  const links = collectLinks(block.inlines ?? []);
  const noteStartCandidates = [
    text.search(/Equal contribution/i),
    text.search(/Corresponding author/i),
    text.search(/Project leader/i),
  ].filter((index) => index >= 0);
  const rawNoteStart = noteStartCandidates.length > 0 ? Math.min(...noteStartCandidates) : -1;
  const bracketBeforeNote = rawNoteStart >= 0 ? text.lastIndexOf("[", rawNoteStart) : -1;
  const noteStart =
    bracketBeforeNote >= 0 && rawNoteStart - bracketBeforeNote <= 4
      ? bracketBeforeNote
      : rawNoteStart;
  const namesAndAffiliations = noteStart >= 0 ? text.slice(0, noteStart).trim() : text;
  const noteText = noteStart >= 0 ? text.slice(noteStart) : "";

  const affiliationIndex = findAffiliationIndex(namesAndAffiliations);
  const authorsText =
    affiliationIndex >= 0
      ? namesAndAffiliations.slice(0, affiliationIndex).trim()
      : namesAndAffiliations;
  const affiliationsText =
    affiliationIndex >= 0 ? namesAndAffiliations.slice(affiliationIndex).trim() : "";

  return {
    authors: splitAuthors(authorsText),
    affiliations: parseAffiliations(affiliationsText),
    notes: parseNotes(noteText),
    links,
  };
}

export function isPaperFrontMatterBlock(block: DocBlock): boolean {
  if (block.type !== "paragraph") return false;
  const inlines = block.inlines ?? [];
  const text = frontMatterText(block);
  const links = collectLinks(inlines);
  const hasCodeLinks = links.some((link) => /github\.com|huggingface\.co/i.test(link.href));
  if (text.length < 24 || text.length > 1200) return false;
  if (!FRONT_MATTER_HINT_RE.test(text) && !hasCodeLinks) return false;
  return /^\s*\[[^\]]+]\s*[A-Z]/.test(text) || (hasCodeLinks && FRONT_MATTER_HINT_RE.test(text));
}

function FrontMatterRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={rowStyle}>
      <div style={labelStyle}>{label}</div>
      <div style={wrapStyle}>{children}</div>
    </div>
  );
}

export function PaperFrontMatterBlock({ block }: { block: DocBlock }) {
  const parsed = parseFrontMatter(block);
  const heading =
    parsed.authors.length > 0 || parsed.affiliations.length > 0 ? "論文メタデータ" : "注記・リンク";

  return (
    <div style={blockStyle}>
      <div style={headingStyle}>{heading}</div>
      {parsed.authors.length > 0 ? (
        <FrontMatterRow label="著者">
          {parsed.authors.map((author) => (
            <span key={author} style={authorChipStyle}>
              {author}
            </span>
          ))}
        </FrontMatterRow>
      ) : null}
      {parsed.affiliations.length > 0 ? (
        <FrontMatterRow label="所属">
          {parsed.affiliations.map((affiliation) => (
            <span key={`${affiliation.marker ?? ""}-${affiliation.text}`} style={tagStyle}>
              {affiliation.marker ? `${affiliation.marker}. ` : ""}
              {affiliation.text}
            </span>
          ))}
        </FrontMatterRow>
      ) : null}
      {parsed.notes.length > 0 ? (
        <FrontMatterRow label="注記">
          {parsed.notes.map((note) => (
            <span key={note} style={tagStyle}>
              {note}
            </span>
          ))}
        </FrontMatterRow>
      ) : null}
      {parsed.links.length > 0 ? (
        <FrontMatterRow label="リンク">
          {parsed.links.map((link) => (
            <SmartInlineLink key={link.href} href={link.href} label={link.label} />
          ))}
        </FrontMatterRow>
      ) : null}
    </div>
  );
}
