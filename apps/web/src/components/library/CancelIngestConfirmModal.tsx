"use client";

import { Modal } from "@/components/ui/Modal";

export interface CancelIngestConfirmModalProps {
  open: boolean;
  /** 送信中はダイアログを閉じられなくする(誤操作防止。ReingestConfirmModal と同じ規約)。 */
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/** 取り込みキャンセルの確認モーダル(docs/08 §2.2 拡張ポップアップ・Web 進捗表示の「キャンセル」)。 */
export function CancelIngestConfirmModal({
  open,
  pending,
  onCancel,
  onConfirm,
}: CancelIngestConfirmModalProps) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      width={420}
      dismissible={!pending}
      labelledBy="cancel-ingest-confirm-title"
    >
      <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
        <h2
          id="cancel-ingest-confirm-title"
          style={{ fontSize: 14, fontWeight: 700, margin: 0, color: "var(--pr-text)" }}
        >
          取り込みをキャンセルしますか?
        </h2>
        <p style={{ fontSize: 12, color: "var(--pr-text-sub)", lineHeight: 1.65, margin: 0 }}>
          ここまで取り込んだ内容(書誌・構造化済み本文など)も含めてライブラリから削除されます。この操作は取り消せません。
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
            取り込みをキャンセル
          </button>
        </div>
      </div>
    </Modal>
  );
}
