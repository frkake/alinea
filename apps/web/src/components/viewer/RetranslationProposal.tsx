"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { translationsAcceptProposal, translationsDiscardProposal, type UnitProposal } from "@alinea/api-client";

export interface RetranslationProposalProps {
  unitId: string;
  currentTextJa: string;
  proposal: UnitProposal;
  /** units query key — invalidated after accept/discard. */
  queryKey: readonly unknown[];
}

/**
 * 再翻訳提案カード(Task 6)。
 * 現行訳と候補訳を並べ、採用・破棄ボタンを表示する。
 * 採否完了後に units query を invalidate する。
 */
export function RetranslationProposal({
  unitId,
  currentTextJa,
  proposal,
  queryKey,
}: RetranslationProposalProps) {
  const queryClient = useQueryClient();
  const [acting, setActing] = useState(false);

  const handleAccept = async () => {
    setActing(true);
    try {
      await translationsAcceptProposal({ path: { unit_id: unitId } });
      await queryClient.invalidateQueries({ queryKey: queryKey as unknown[] });
    } finally {
      setActing(false);
    }
  };

  const handleDiscard = async () => {
    setActing(true);
    try {
      await translationsDiscardProposal({ path: { unit_id: unitId } });
      await queryClient.invalidateQueries({ queryKey: queryKey as unknown[] });
    } finally {
      setActing(false);
    }
  };

  return (
    <div
      data-testid="retranslation-proposal"
      style={{
        border: "1px solid var(--pr-border-card)",
        borderLeft: "3px solid var(--pr-acc)",
        background: "var(--pr-bg-card)",
        borderRadius: 8,
        padding: "12px 16px",
        margin: "0 0 16px",
        fontFamily: "var(--pr-font-ui)",
        fontSize: 12,
      }}
    >
      <div
        style={{
          color: "var(--pr-text-muted)",
          marginBottom: 8,
          fontSize: 10.5,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
        }}
      >
        再翻訳の提案
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 10.5, color: "var(--pr-text-faint)", marginBottom: 4 }}>現在の訳</div>
          <div
            style={{
              fontSize: 13,
              lineHeight: 1.7,
              color: "var(--pr-text-body)",
              background: "var(--pr-bg-inset)",
              borderRadius: 6,
              padding: "8px 10px",
            }}
          >
            {currentTextJa}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 10.5, color: "var(--pr-acc)", marginBottom: 4, fontWeight: 600 }}>
            候補訳
          </div>
          <div
            style={{
              fontSize: 13,
              lineHeight: 1.7,
              color: "var(--pr-text-body)",
              background: "var(--pr-bg-inset)",
              borderRadius: 6,
              padding: "8px 10px",
              border: "1px solid var(--pr-acc)",
            }}
          >
            {proposal.text_ja}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button
          type="button"
          onClick={() => void handleAccept()}
          disabled={acting}
          style={{
            padding: "4px 14px",
            background: "var(--pr-acc)",
            color: "#fff",
            border: "none",
            borderRadius: 5,
            fontSize: 12,
            cursor: acting ? "not-allowed" : "pointer",
            opacity: acting ? 0.6 : 1,
            fontFamily: "var(--pr-font-ui)",
          }}
        >
          採用
        </button>
        <button
          type="button"
          onClick={() => void handleDiscard()}
          disabled={acting}
          style={{
            padding: "4px 14px",
            background: "transparent",
            color: "var(--pr-text-muted)",
            border: "1px solid var(--pr-border-control)",
            borderRadius: 5,
            fontSize: 12,
            cursor: acting ? "not-allowed" : "pointer",
            opacity: acting ? 0.6 : 1,
            fontFamily: "var(--pr-font-ui)",
          }}
        >
          破棄
        </button>
        <span
          style={{
            marginLeft: "auto",
            fontSize: 10.5,
            color: "var(--pr-text-faint)",
          }}
        >
          {proposal.model}
        </span>
      </div>
    </div>
  );
}
