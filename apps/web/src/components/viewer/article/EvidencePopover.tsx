"use client";

import { type RefObject } from "react";
import { useQuery } from "@tanstack/react-query";
import { viewerGetBlock, type EvidenceItemOut } from "@alinea/api-client";
import { Popover } from "@/components/ui/Popover";
import type { DocBlock } from "@/components/viewer/document-types";
import { articleKeys } from "@/components/viewer/article/queries";
import type { AnchorRef } from "@/components/viewer/article/types";

function blockPlainText(block: DocBlock | null | undefined): string {
  if (!block?.inlines) return "";
  return block.inlines
    .map((i) => i.v ?? "")
    .join("")
    .trim();
}

function EvidenceRow({
  revisionId,
  item,
  onJumpToAnchor,
}: {
  revisionId: string;
  item: EvidenceItemOut;
  onJumpToAnchor: (anchor: AnchorRef) => void;
}) {
  const query = useQuery({
    queryKey: articleKeys.blockPreview(revisionId, item.anchor.block_id),
    queryFn: async () =>
      (
        await viewerGetBlock({
          path: { revision_id: revisionId, block_id: item.anchor.block_id },
          throwOnError: true,
        })
      ).data,
    staleTime: Infinity,
  });
  const preview = blockPlainText(query.data?.block as DocBlock | undefined).slice(0, 120);

  return (
    <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--pr-border-hair)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            height: 15,
            padding: "0 6px",
            border: "1px solid var(--pr-am)",
            color: "var(--pr-a)",
            borderRadius: 3,
            fontSize: 9,
            fontWeight: 600,
          }}
        >
          {item.display}
        </span>
      </div>
      {preview ? (
        <div
          style={{
            fontSize: 11,
            color: "var(--pr-text-sub)",
            lineHeight: 1.7,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {preview}
        </div>
      ) : null}
      <button
        type="button"
        onClick={() => onJumpToAnchor(item.anchor)}
        style={{
          marginTop: 4,
          border: "none",
          background: "transparent",
          padding: 0,
          cursor: "pointer",
          fontFamily: "inherit",
          fontSize: 10.5,
          color: "var(--pr-a)",
          fontWeight: 600,
        }}
      >
        原文で見る →
      </button>
    </div>
  );
}

/** 「根拠を表示」ポップオーバー(1h §5.5。width 320)。 */
export function EvidencePopover({
  open,
  onClose,
  anchorRef,
  revisionId,
  evidence,
  onJumpToAnchor,
}: {
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  revisionId: string;
  evidence: EvidenceItemOut[];
  onJumpToAnchor: (anchor: AnchorRef) => void;
}) {
  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={320} placement="bottom-end">
      {evidence.map((item) => (
        <EvidenceRow key={item.ref} revisionId={revisionId} item={item} onJumpToAnchor={onJumpToAnchor} />
      ))}
    </Popover>
  );
}
