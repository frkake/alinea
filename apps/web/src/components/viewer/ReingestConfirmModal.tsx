"use client";

import { Modal } from "@/components/ui/Modal";

export interface ReingestConfirmModalProps {
  open: boolean;
  /** 送信中はダイアログを閉じられなくする(誤操作防止)。 */
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/**
 * 「再取り込み」確認モーダル(2a §4.3 逐語・width 460px)。
 * 情報タブのアクションリンクからのみ開く(ヘッダ「⋯」メニューの再取り込みは確認なし・shell 担当)。
 */
export function ReingestConfirmModal({ open, pending, onCancel, onConfirm }: ReingestConfirmModalProps) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      width={460}
      dismissible={!pending}
      labelledBy="reingest-confirm-title"
    >
      <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
        <h2
          id="reingest-confirm-title"
          style={{ fontSize: 14, fontWeight: 700, margin: 0, color: "var(--pr-text)" }}
        >
          再取り込みしますか?
        </h2>
        <p style={{ fontSize: 12, color: "var(--pr-text-sub)", lineHeight: 1.65, margin: 0 }}>
          最新のソースから構造化と翻訳をやり直します。注釈は新しいリビジョンへ自動で引き継がれます(位置を失った注釈は「未配置」として残ります)。
        </p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            style={{
              height: 30,
              padding: "0 14px",
              border: "1px solid var(--pr-border-control)",
              borderRadius: 6,
              background: "transparent",
              color: "var(--pr-text-mid)",
              fontSize: 12,
              cursor: pending ? "default" : "pointer",
              fontFamily: "inherit",
            }}
          >
            キャンセル
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            style={{
              height: 30,
              padding: "0 14px",
              border: "none",
              borderRadius: 6,
              background: "var(--pr-acc)",
              color: "#FFFFFF",
              fontSize: 12,
              fontWeight: 600,
              cursor: pending ? "default" : "pointer",
              opacity: pending ? 0.7 : 1,
              fontFamily: "inherit",
            }}
          >
            再取り込み
          </button>
        </div>
      </div>
    </Modal>
  );
}
