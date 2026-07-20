import { presentationsGet, presentationsGenerate, type Problem } from "@alinea/api-client";
import type { Audience, Preset, PresentationStatus } from "@/components/viewer/presentation/types";

/**
 * 論文→スライド生成の TanStack Query キー・fetcher(Task 30 §3)。
 * 初回表示で既存 artifact と進行中 job を 1 リクエスト(GET /presentation)で取得する。
 */
export const presentationKeys = {
  status: (itemId: string) => ["presentation", itemId] as const,
};

/** GET /presentation: 最新成果物 + 進行中 job(未生成なら artifact=null)。 */
export async function fetchPresentationStatus(itemId: string): Promise<PresentationStatus> {
  const res = await presentationsGet({ path: { item_id: itemId }, throwOnError: true });
  return res.data;
}

/** POST /presentation: 生成/再生成を開始し、job_id を返す(進行中は既存 job_id を返す)。 */
export async function startPresentation(
  itemId: string,
  body: { preset: Preset; audience: Audience; instruction?: string },
): Promise<string> {
  const res = await presentationsGenerate({
    path: { item_id: itemId },
    body,
    throwOnError: true,
  });
  return res.data.job_id;
}

/** ダウンロード URL(同一オリジン GET。Content-Disposition: attachment)。 */
export function presentationDownloadUrl(itemId: string): string {
  return `/api/library-items/${itemId}/presentation/download`;
}

export function isProblem(error: unknown): Partial<Problem> {
  return (error as Partial<Problem> | undefined) ?? {};
}
