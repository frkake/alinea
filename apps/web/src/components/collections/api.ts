import type {
  CollectionDetail,
  CollectionListResponse,
  CollectionPatch,
  CollectionEntry,
  EntryPatch,
  ShareInfo,
} from "@/components/collections/types";

/**
 * collections API(plans/03 §13)向けの薄い fetch ラッパー。
 * deviations: main.py へのルータ登録は他レーンの担当のため `@alinea/api-client` に
 * まだ生成されていない(rule 6 の許容範囲。生成後は `@alinea/api-client` 呼び出しへ
 * 差し替える)。同一オリジン相対パスのみを使い、セッションクッキーは `credentials: "include"`
 * で送る(`packages/api-client/src/index.ts` と同方針)。
 */
export class ApiError extends Error {
  code: string;
  status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let code = "internal_error";
    let message = "リクエストに失敗しました";
    try {
      const problem = (await res.json()) as { code?: string; title?: string; detail?: string };
      code = problem.code ?? code;
      message = problem.detail ?? problem.title ?? message;
    } catch {
      // JSON でないボディ(204 など)は無視する。
    }
    throw new ApiError(res.status, code, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * 204 応答用(`request<void>` は `@typescript-eslint/no-invalid-void-type` に抵触するため
 * `unknown` 経由で握り潰す。決定・deviations)。
 */
async function requestVoid(path: string, init?: RequestInit): Promise<void> {
  await request<unknown>(path, init);
}

export function listCollections(): Promise<CollectionListResponse> {
  return request<CollectionListResponse>("/api/collections");
}

export function getCollection(collectionId: string): Promise<CollectionDetail> {
  return request<CollectionDetail>(`/api/collections/${collectionId}`);
}

export function createCollection(body: {
  name: string;
  description?: string;
  deadline?: string;
}): Promise<CollectionDetail> {
  return request<CollectionDetail>("/api/collections", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function patchCollection(
  collectionId: string,
  body: CollectionPatch,
): Promise<CollectionDetail> {
  return request<CollectionDetail>(`/api/collections/${collectionId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteCollection(collectionId: string): Promise<void> {
  return requestVoid(`/api/collections/${collectionId}`, { method: "DELETE" });
}

export function addEntry(collectionId: string, libraryItemId: string): Promise<CollectionEntry> {
  return request<CollectionEntry>(`/api/collections/${collectionId}/entries`, {
    method: "POST",
    body: JSON.stringify({ library_item_id: libraryItemId }),
  });
}

export function patchEntry(entryId: string, body: EntryPatch): Promise<CollectionEntry> {
  return request<CollectionEntry>(`/api/collection-entries/${entryId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function removeEntry(entryId: string): Promise<void> {
  return requestVoid(`/api/collection-entries/${entryId}`, { method: "DELETE" });
}

export function reorderEntries(
  collectionId: string,
  entryIds: string[],
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/collections/${collectionId}/entries/order`, {
    method: "PUT",
    body: JSON.stringify({ entry_ids: entryIds }),
  });
}

export function issueShare(collectionId: string): Promise<ShareInfo> {
  return request<ShareInfo>(`/api/collections/${collectionId}/share`, { method: "POST" });
}

export function patchShare(collectionId: string, includeNotes: boolean): Promise<ShareInfo> {
  return request<ShareInfo>(`/api/collections/${collectionId}/share`, {
    method: "PATCH",
    body: JSON.stringify({ include_notes: includeNotes }),
  });
}

export function revokeShare(collectionId: string): Promise<void> {
  return requestVoid(`/api/collections/${collectionId}/share`, { method: "DELETE" });
}
