"use client";

import type { EvidenceItemOut } from "@yakudoku/api-client";
import { EvidenceChip, type EvidenceChipProps } from "@/components/ui/EvidenceChip";
import type { AnchorRef } from "@/components/viewer/article/types";

/**
 * 記事ブロック共通の根拠チップ行(1h §4.7 決定・docs/07 §2.4)。
 * `EvidenceChip` の `anchor` 値は表示専用ダミー(実ジャンプは `onJump` クロージャで実施する
 * — 既存の ChatPanel/NotesPanel と同じ規約)。
 */
export function ArticleEvidenceChips({
  evidence,
  onJumpToAnchor,
  size = "inline",
}: {
  evidence: EvidenceItemOut[];
  onJumpToAnchor: (anchor: AnchorRef) => void;
  size?: EvidenceChipProps["size"];
}) {
  if (evidence.length === 0) return null;
  return (
    <>
      {evidence.map((ev) => (
        <EvidenceChip
          key={ev.ref}
          anchor={{ type: "section", sectionNumber: ev.display }}
          label={ev.display}
          size={size}
          onJump={() => onJumpToAnchor(ev.anchor)}
        />
      ))}
    </>
  );
}
