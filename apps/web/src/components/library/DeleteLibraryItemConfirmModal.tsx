"use client";

import { Modal } from "@/components/ui/Modal";

export interface DeleteLibraryItemConfirmModalProps {
  open: boolean;
  title: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function DeleteLibraryItemConfirmModal({
  open,
  title,
  pending,
  onCancel,
  onConfirm,
}: DeleteLibraryItemConfirmModalProps) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      width={440}
      dismissible={!pending}
      labelledBy="delete-library-item-title"
    >
      <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
        <h2
          id="delete-library-item-title"
          style={{ fontSize: 14, fontWeight: 700, margin: 0, color: "var(--pr-text)" }}
        >
          ライブラリから削除しますか?
        </h2>
        <div style={{ fontSize: 12, color: "var(--pr-text)", fontWeight: 600, lineHeight: 1.5 }}>
          {title}
        </div>
        <p style={{ fontSize: 12, color: "var(--pr-text-sub)", lineHeight: 1.65, margin: 0 }}>
          メモ、注釈、チャット、語彙、記事、コレクションへの追加など、この論文に紐づく個人データも削除されます。この操作は取り消せません。
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
            戻る
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
              background: "var(--pr-warn)",
              color: "#FFFFFF",
              fontSize: 12,
              fontWeight: 600,
              cursor: pending ? "default" : "pointer",
              opacity: pending ? 0.7 : 1,
              fontFamily: "inherit",
            }}
          >
            削除する
          </button>
        </div>
      </div>
    </Modal>
  );
}
