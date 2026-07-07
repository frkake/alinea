// arXiv ページ内「訳 保存」ピルと background 間のメッセージ契約(plans/10 §10.3)。
// content script は API を直接呼ばない(same-site クッキー送信は拡張コンテキスト発が条件のため)。

export interface PillCheckMessage {
  type: "PILL_CHECK";
  url: string;
}

export interface PillSaveMessage {
  type: "PILL_SAVE";
  url: string;
}

export type PillMessage = PillCheckMessage | PillSaveMessage;

/** ピルの表示状態(plans/10 §10.3 の表を型化)。 */
export type PillState =
  | "hidden" // 未ログイン・判定失敗 → ピル非表示(再試行しない)
  | "idle" // 未保存 → 「保存」表示
  | "saved" // 保存済み → 「✓ 保存済み」
  | "error"; // 送信失敗 → 一時的に「保存できませんでした」

export interface PillResult {
  state: PillState;
}

export function isPillMessage(value: unknown): value is PillMessage {
  if (!value || typeof value !== "object") return false;
  const type = (value as { type?: unknown }).type;
  return type === "PILL_CHECK" || type === "PILL_SAVE";
}
