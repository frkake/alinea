"use client";

import { useEffect, useRef, type CSSProperties } from "react";
import type { ReferenceItem } from "@yakudoku/api-client";

export type ReferenceImportState = "idle" | "importing" | "imported";

export interface ReferencesListProps {
  references: ReferenceItem[];
  /** 展開中の行(排他・最大 1 件。1c §5.6)。 */
  expandedRefId: string | null;
  onToggle: (refId: string) => void;
  /** ref_id → 取り込み状態(1c §5.6)。 */
  importStates?: Record<string, ReferenceImportState>;
  onImport: (reference: ReferenceItem) => void;
  onOpenInLibrary: (libraryItemId: string) => void;
}

/** 行本文「{number} {authors}. {title}. {venue_year}」(null フィールドは省略。1c §5.6)。 */
function referenceLine(ref: ReferenceItem): { number: string; head: string; venue: string | null } {
  const parts: string[] = [];
  if (ref.authors) parts.push(ref.authors);
  if (ref.title) parts.push(ref.title);
  const raw = ref.raw?.trim() ?? "";
  const head = parts.length ? `${parts.join(". ")}.` : raw;
  return { number: ref.number, head, venue: ref.venue_year };
}

const rowBtn: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  height: 22,
  padding: "0 9px",
  borderRadius: 5,
  fontSize: 10.5,
  fontFamily: "var(--pr-font-ui)",
  cursor: "pointer",
};

/** 参考文献一覧(1c §4.6 / §5.6)。行クリックで排他展開、arXiv 取り込み・在庫遷移。 */
export function ReferencesList({
  references,
  expandedRefId,
  onToggle,
  importStates = {},
  onImport,
  onOpenInLibrary,
}: ReferencesListProps) {
  const rowRefs = useRef(new Map<string, HTMLDivElement>());

  useEffect(() => {
    if (!expandedRefId) return;
    const row = rowRefs.current.get(expandedRefId);
    if (typeof row?.scrollIntoView === "function") {
      row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [expandedRefId, references]);

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 11, lineHeight: 1.55 }}
    >
      {references.map((ref) => {
        const { number, head, venue } = referenceLine(ref);
        const expanded = expandedRefId === ref.ref_id;
        const importState = importStates[ref.ref_id] ?? "idle";
        const inLibrary = ref.in_library;

        return (
          <div
            key={ref.ref_id}
            ref={(node) => {
              if (node) rowRefs.current.set(ref.ref_id, node);
              else rowRefs.current.delete(ref.ref_id);
            }}
            data-reference-row={ref.ref_id}
            style={{
              padding: "6px 8px",
              borderRadius: 6,
              color: "var(--pr-text-mid)",
              background: expanded ? "var(--pr-bg-pop)" : "transparent",
              border: expanded ? "1px solid var(--pr-border-control)" : "1px solid transparent",
              display: "flex",
              flexDirection: "column",
              gap: expanded ? 6 : 0,
            }}
          >
            <div
              role="button"
              tabIndex={0}
              onClick={() => onToggle(ref.ref_id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onToggle(ref.ref_id);
                }
              }}
              style={{ cursor: "pointer", overflowWrap: "anywhere" }}
            >
              <span style={{ color: "var(--pr-text-muted)", fontFamily: "var(--pr-font-mono)" }}>
                {number}
              </span>{" "}
              {head} {venue ? <i style={{ color: "var(--pr-text-sub2)" }}>{venue}</i> : null}
              {expanded && ref.arxiv_id ? (
                <>
                  {" · "}
                  <a
                    href={`https://arxiv.org/abs/${ref.arxiv_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    style={{ color: "var(--pr-acc)" }}
                  >
                    arXiv
                  </a>
                </>
              ) : null}
            </div>

            {expanded ? (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {inLibrary ? (
                  <button
                    type="button"
                    style={{
                      ...rowBtn,
                      border: "1px solid var(--pr-border-pop)",
                      color: "var(--pr-text-sub)",
                      background: "transparent",
                    }}
                    onClick={() => onOpenInLibrary(inLibrary.library_item_id)}
                  >
                    ライブラリに有り ✓
                  </button>
                ) : ref.arxiv_id ? (
                  <button
                    type="button"
                    disabled={importState === "importing"}
                    style={{
                      ...rowBtn,
                      background: importState === "importing" ? "var(--pr-acc-s)" : "var(--pr-acc)",
                      color: importState === "importing" ? "var(--pr-acc)" : "var(--pr-bg-app)",
                      fontWeight: 700,
                      border: "none",
                    }}
                    onClick={() => onImport(ref)}
                  >
                    {importState === "importing" ? "取り込み中…" : "+ この論文も取り込む"}
                  </button>
                ) : null}
                {ref.doi ? (
                  <a
                    href={`https://doi.org/${ref.doi}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      ...rowBtn,
                      border: "1px solid var(--pr-border-pop)",
                      color: "var(--pr-acc)",
                      textDecoration: "none",
                    }}
                  >
                    DOI
                  </a>
                ) : null}
                {ref.url ? (
                  <a
                    href={ref.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      ...rowBtn,
                      border: "1px solid var(--pr-border-pop)",
                      color: "var(--pr-acc)",
                      textDecoration: "none",
                    }}
                  >
                    外部リンク
                  </a>
                ) : null}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
