import { Card } from "@/components/ui/Card";
import { SharedNoteBox } from "@/components/share/SharedNoteBox";
import { renderInlineMath } from "@/lib/katex-ssr";

/** 論文カード(plans/09-screens/4c §4.4)。 */
export interface SharePaperCardProps {
  order: number;
  title: string;
  authorsShort: string;
  venueYear: string | null;
  arxivUrl: string | null;
  summary3line: string[] | null;
  sharedNote: string | null;
}

export function SharePaperCard({
  order,
  title,
  authorsShort,
  venueYear,
  arxivUrl,
  summary3line,
  sharedNote,
}: SharePaperCardProps) {
  return (
    <Card
      as="article"
      padding="none"
      style={{
        borderColor: "var(--pr-border-control)",
        padding: "14px 18px",
        display: "flex",
        gap: 14,
        overflow: "visible",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 22,
          height: 22,
          borderRadius: "50%",
          background: "var(--pr-elev-bg)",
          color: "#FFFFFF",
          fontSize: 11,
          fontWeight: 700,
          flex: "none",
          marginTop: 2,
        }}
      >
        {order}
      </span>
      <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, lineHeight: 1.5 }}>
          {renderInlineMath(title)}
        </div>
        <div style={{ fontSize: 11, color: "var(--pr-text-muted)" }}>
          {authorsShort}
          {venueYear !== null ? ` · ${venueYear}` : ""}
          {arxivUrl !== null ? (
            <>
              {" · "}
              <a
                href={arxivUrl}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="yk-share-arxiv"
                style={{ color: "var(--pr-acc)", fontWeight: 600, textDecoration: "none" }}
              >
                arXiv ↗
              </a>
            </>
          ) : null}
        </div>
        {summary3line !== null ? (
          <div style={{ fontSize: 11.5, lineHeight: 1.7, color: "var(--pr-text-sub)" }}>
            {renderInlineMath(`✦ ${summary3line.join("")}`)}
          </div>
        ) : null}
        {sharedNote !== null ? <SharedNoteBox note={sharedNote} /> : null}
      </div>
    </Card>
  );
}
