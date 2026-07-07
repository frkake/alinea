"use client";

import { useState, type RefObject } from "react";
import { Popover } from "@/components/ui/Popover";

export interface RewriteInstructionPopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  placeholder: string;
  onSubmit: (instruction: string) => void;
  pending: boolean;
  /** ボタン文言(既定「✦ 書き直す」。概要図・ブロックで共用。1h §5.4/§5.5)。 */
  submitLabel?: string;
}

/**
 * 書き直し指示ポップオーバー(1h §3.2・§5.4・§5.5)。ブロック・概要図で共用(width 320)。
 */
export function RewriteInstructionPopover({
  open,
  onClose,
  anchorRef,
  placeholder,
  onSubmit,
  pending,
  submitLabel = "✦ 書き直す",
}: RewriteInstructionPopoverProps) {
  const [instruction, setInstruction] = useState("");

  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={320} placement="bottom-end">
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <textarea
          aria-label="書き直し指示"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder={placeholder}
          rows={3}
          style={{
            width: "100%",
            boxSizing: "border-box",
            height: 56,
            border: "1px solid var(--pr-border-control)",
            borderRadius: 6,
            fontSize: 11.5,
            padding: 8,
            resize: "none",
            fontFamily: "inherit",
          }}
        />
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              onSubmit(instruction.trim());
              setInstruction("");
            }}
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
            {submitLabel}
          </button>
        </div>
      </div>
    </Popover>
  );
}
