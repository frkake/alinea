/**
 * collections API(plans/03 §13)向けの薄い SDK ラッパー。
 *
 * `@alinea/api-client` の生成関数をラップし、呼び出し元が既存の `ApiError` で
 * エラーを判別できるようにする。
 */
import {
  collectionsList,
  collectionsGet,
  collectionsCreate,
  collectionsUpdate,
  collectionsDelete,
  collectionsAddEntry,
  collectionEntriesUpdate,
  collectionEntriesDelete,
  collectionsReorderEntries,
  collectionsShareIssue,
  collectionsShareUpdate,
  collectionsShareRevoke,
  type CollectionDetailResponse,
  type CollectionListResponse,
  type CollectionEntryOut,
  type ShareInfo,
} from "@alinea/api-client";
import type { CollectionDetail, CollectionPatch, CollectionEntry, EntryPatch } from "@/components/collections/types";

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

function toApiError(status: number, body: unknown): ApiError {
  let code = "internal_error";
  let message = "リクエストに失敗しました";
  if (body && typeof body === "object") {
    const b = body as { code?: string; title?: string; detail?: string };
    code = b.code ?? code;
    message = b.detail ?? b.title ?? message;
  }
  return new ApiError(status, code, message);
}

function throwIfError(result: { error?: unknown; response: Response }): void {
  if (result.error !== undefined) {
    throw toApiError(result.response.status, result.error);
  }
}

export async function listCollections(): Promise<CollectionListResponse> {
  const r = await collectionsList();
  throwIfError(r);
  return r.data as CollectionListResponse;
}

export async function getCollection(collectionId: string): Promise<CollectionDetail> {
  const r = await collectionsGet({ path: { collection_id: collectionId } });
  throwIfError(r);
  return r.data as CollectionDetailResponse as CollectionDetail;
}

export async function createCollection(body: {
  name: string;
  description?: string;
  deadline?: string;
}): Promise<CollectionDetail> {
  const r = await collectionsCreate({ body });
  throwIfError(r);
  return r.data as CollectionDetailResponse as CollectionDetail;
}

export async function patchCollection(
  collectionId: string,
  body: CollectionPatch,
): Promise<CollectionDetail> {
  const r = await collectionsUpdate({ path: { collection_id: collectionId }, body });
  throwIfError(r);
  return r.data as CollectionDetailResponse as CollectionDetail;
}

export async function deleteCollection(collectionId: string): Promise<void> {
  const r = await collectionsDelete({ path: { collection_id: collectionId } });
  throwIfError(r);
}

export async function addEntry(
  collectionId: string,
  libraryItemId: string,
): Promise<CollectionEntry> {
  const r = await collectionsAddEntry({
    path: { collection_id: collectionId },
    body: { library_item_id: libraryItemId },
  });
  throwIfError(r);
  return r.data as CollectionEntryOut as CollectionEntry;
}

export async function patchEntry(entryId: string, body: EntryPatch): Promise<CollectionEntry> {
  const r = await collectionEntriesUpdate({ path: { entry_id: entryId }, body });
  throwIfError(r);
  return r.data as CollectionEntryOut as CollectionEntry;
}

export async function removeEntry(entryId: string): Promise<void> {
  const r = await collectionEntriesDelete({ path: { entry_id: entryId } });
  throwIfError(r);
}

export async function reorderEntries(
  collectionId: string,
  entryIds: string[],
): Promise<{ ok: boolean }> {
  const r = await collectionsReorderEntries({
    path: { collection_id: collectionId },
    body: { entry_ids: entryIds },
  });
  throwIfError(r);
  return { ok: (r.data as { ok?: boolean })?.ok ?? true };
}

export async function issueShare(collectionId: string): Promise<ShareInfo> {
  const r = await collectionsShareIssue({ path: { collection_id: collectionId } });
  throwIfError(r);
  return r.data as ShareInfo;
}

export async function patchShare(collectionId: string, includeNotes: boolean): Promise<ShareInfo> {
  const r = await collectionsShareUpdate({
    path: { collection_id: collectionId },
    body: { include_notes: includeNotes },
  });
  throwIfError(r);
  return r.data as ShareInfo;
}

export async function revokeShare(collectionId: string): Promise<void> {
  const r = await collectionsShareRevoke({ path: { collection_id: collectionId } });
  throwIfError(r);
}
