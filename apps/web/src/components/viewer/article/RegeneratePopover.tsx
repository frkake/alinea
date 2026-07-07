"use client";

import { useState, type RefObject } from "react";
import { Popover } from "@/components/ui/Popover";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Toggle } from "@/components/ui/Toggle";
import type { Preset } from "@/components/viewer/article/types";

const PRESET_OPTIONS: ReadonlyArray<{ value: Preset; label: string }> = [
  { value: "beginner", label: "初学者向け" },
  { value: "implementer", label: "実装者向け" },
  { value: "researcher", label: "研究者向け" },
  { value: "reading_group", label: "輪読会向け" },
];

export interface RegeneratePopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  currentPreset: Preset;
  currentIncludeMath: boolean;
  onSubmit: (req: { instruction?: string; preset?: Preset; include_math?: boolean }) => void;
  pending: boolean;
}

/** ヘッダ「✦ 指示つき再生成」ポップオーバー(1h §5.3。width 360)。 */
export function RegeneratePopover({
  open,
  onClose,
  anchorRef,
  currentPreset,
  currentIncludeMath,
  onSubmit,
  pending,
}: RegeneratePopoverProps) {
  const [instruction, setInstruction] = useState("");
  const [preset, setPreset] = useState<Preset>(currentPreset);
  const [includeMath, setIncludeMath] = useState(currentIncludeMath);

  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={360} placement="bottom-end">
      <div style={{ padding: "12px 14px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 700 }}>✦ 指示つき再生成</div>
        <textarea
          aria-label="再生成の指示"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="例: 実験の部分を削って手法を厚く"
          rows={4}
          style={{
            width: "100%",
            boxSizing: "border-box",
            height: 72,
            border: "1px solid var(--pr-border-control)",
            borderRadius: 6,
            fontSize: 11.5,
            padding: 8,
            resize: "none",
            fontFamily: "inherit",
          }}
        />
        <SegmentedControl ariaLabel="プリセット" size="sm" options={PRESET_OPTIONS} value={preset} onChange={setPreset} />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Toggle ariaLabel="数式を含める" checked={includeMath} onChange={setIncludeMath} />
          <span style={{ fontSize: 11.5, color: "var(--pr-text-mid)" }}>数式を含める</span>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            type="button"
            disabled={pending}
            onClick={() =>
              onSubmit({
                instruction: instruction.trim().length > 0 ? instruction.trim() : undefined,
                preset,
                include_math: includeMath,
              })
            }
            style={{
              height: 26,
              padding: "0 12px",
              border: "none",
              borderRadius: 6,
              background: "var(--pr-acc)",
              color: "#FFFFFF",
              fontSize: 11.5,
              fontWeight: 600,
              cursor: pending ? "default" : "pointer",
              opacity: pending ? 0.6 : 1,
              fontFamily: "inherit",
            }}
          >
            ✦ 再生成
          </button>
        </div>
      </div>
    </Popover>
  );
}
