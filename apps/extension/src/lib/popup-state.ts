// ポップアップの状態分岐(3a §2.4・§5.1)。純粋関数にして単体テスト可能にする。
import type { IngestCheckResponse } from "@yakudoku/api-client";

export type PopupState =
  | "loading" // check 取得中(状態 L)
  | "login" // 未ログイン(状態 0)
  | "saveform" // 保存前(状態 1)
  | "existing" // 既にライブラリ(状態 3)
  | "pdf" // 一般ページ PDF(状態 4)
  | "unsupported"; // 非対応ページ(状態 5)

export interface ResolveArgs {
  /** GET /api/auth/me が 200 なら true、401 なら false、未取得なら null。 */
  authed: boolean | null;
  /** GET /api/ingest/check の結果。未取得なら null。 */
  check: IngestCheckResponse | null;
}

/**
 * ポップアップ開時の状態判定(3a §2.4)。
 * 1. 認証未取得 → loading
 * 2. 未ログイン → login
 * 3. check 未取得 → loading
 * 4. saved != null → existing
 * 5. kind==="arxiv" → saveform / "pdf" → pdf / それ以外 → unsupported
 */
export function resolvePopupState({ authed, check }: ResolveArgs): PopupState {
  if (authed === null) return "loading";
  if (authed === false) return "login";
  if (check === null) return "loading";
  if (check.saved != null) return "existing";
  if (check.kind === "arxiv") return "saveform";
  if (check.kind === "pdf") return "pdf";
  return "unsupported";
}
