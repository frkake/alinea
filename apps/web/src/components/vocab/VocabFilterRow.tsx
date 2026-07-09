"use client";

import type { CSSProperties } from "react";
import type { VocabCounts } from "@alinea/api-client";
import { FilterChip } from "@/components/ui/FilterChip";
import { VOCAB_KIND_LABEL } from "@/components/vocab/VocabKindBadge";
import type { VocabKind } from "@/components/vocab/types";

const KIND_ORDER: VocabKind[] = ["word", "collocation", "idiom"];

export interface VocabFilterRowProps {
  counts: VocabCounts;
  /** null = 「すべて」選択中(排他単一選択。4d §5.2 決定)。 */
  kind: VocabKind | null;
  /** 「復習期」は種別と独立のトグル(4d §5.2 決定)。 */
  dueOnly: boolean;
  onKindChange: (kind: VocabKind | null) => void;
  onDueToggle: () => void;
}

/** フィルタチップ行(4d §4.2.4)。すべて/単語/コロケーション/イディオム + 復習期(常時強調)。 */
export function VocabFilterRow({ counts, kind, dueOnly, onKindChange, onDueToggle }: VocabFilterRowProps) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <FilterChip label="すべて" count={counts.all} selected={kind === null} onClick={() => onKindChange(null)} />
      {KIND_ORDER.map((k) => (
        <FilterChip
          key={k}
          label={VOCAB_KIND_LABEL[k]}
          count={counts[k]}
          selected={kind === k}
          onClick={() => onKindChange(k)}
        />
      ))}
      <DueFilterChip count={counts.due} active={dueOnly} onClick={onDueToggle} />
      <span style={{ marginLeft: "auto", fontSize: 10.5, color: "var(--pr-text-muted)" }}>
        本文で選択 → 「語彙に追加」で文脈ごと保存されます
      </span>
    </div>
  );
}

export interface DueFilterChipProps {
  count: number;
  active: boolean;
  onClick: () => void;
}

/**
 * 「復習期」チップ(4d §4.2.4)。琥珀系の常時強調表示(選択トーンとは別)。
 * ON 状態は地色反転(背景 #8A6A24・白文字。4d §5.2 決定)。
 */
export function DueFilterChip({ count, active, onClick }: DueFilterChipProps) {
  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    height: 22,
    padding: "0 10px",
    borderRadius: 999,
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
    fontFamily: "inherit",
    border: active ? "none" : "1px solid #E4CFA6",
    color: active ? "#FFFFFF" : "#8A6A24",
    background: active ? "#8A6A24" : "#FFF9F0",
  };
  return (
    <button type="button" aria-pressed={active} style={style} onClick={onClick}>
      復習期 {count}
    </button>
  );
}
