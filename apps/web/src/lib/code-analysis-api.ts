/**
 * コード対応解析 API(設計 §10・Task 21 のエンドポイント)。
 *
 * `@alinea/api-client` の生成 SDK をラップし、呼び出し元が `CodeAnalysisApiError` で
 * ステータス(特に見積り失効・commit 変化の 409)を判別できる薄い mapper を提供する。
 *
 * NOTE: 設計 §10 には `POST /api/code-analysis/{run_id}/rerun` があるが、Task 21 の
 * マージ済みバックエンドには rerun エンドポイントが無い。再解析は新しい見積り→start で行う。
 */
import {
  codeAnalysisEstimate,
  codeAnalysisList,
  codeAnalysisStart,
  type CodeAnalysisEstimateResponse,
  type RunsResponse,
  type StartResponse,
} from "@alinea/api-client";

export class CodeAnalysisApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    super(`code analysis api error: ${status}`);
    this.status = status;
    this.body = body;
  }
}

function throwIfError(result: { error?: unknown; response: Response }): void {
  if (result.error !== undefined) {
    throw new CodeAnalysisApiError(result.response.status, result.error);
  }
}

export async function estimateCodeAnalysis(
  itemId: string,
  body: { resource_id: string; section_ids?: string[] | null },
): Promise<CodeAnalysisEstimateResponse> {
  const r = await codeAnalysisEstimate({ path: { item_id: itemId }, body });
  throwIfError(r);
  return r.data as CodeAnalysisEstimateResponse;
}

export async function startCodeAnalysis(
  itemId: string,
  body: { resource_id: string; estimate_id: string; section_ids?: string[] | null },
): Promise<StartResponse> {
  const r = await codeAnalysisStart({ path: { item_id: itemId }, body });
  throwIfError(r);
  return r.data as StartResponse;
}

export async function listCodeAnalysis(itemId: string): Promise<RunsResponse> {
  const r = await codeAnalysisList({ path: { item_id: itemId } });
  throwIfError(r);
  return r.data as RunsResponse;
}
