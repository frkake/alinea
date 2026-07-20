/**
 * リソース API(plans/03 §12)。
 *
 * `@alinea/api-client` の生成 SDK 関数をラップし、呼び出し元が既存の
 * `ResourceApiError` でエラーを判別できる薄い mapper を提供する。
 */
import {
  resourcesList,
  resourcesCreate,
  resourcesUpdate,
  resourcesDelete,
  resourcesRefreshMeta,
  resourcesSuggestionAccept,
  resourcesSuggestionDismiss,
  type ResourceLink as SdkResourceLink,
  type ResourceListResponse as SdkResourceListResponse,
} from "@alinea/api-client";
import type { ResKind, ResourceLink, ResourceListResponse } from "@/components/viewer/resources/types";

export class ResourceApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    super(`resource api error: ${status}`);
    this.status = status;
    this.body = body;
  }
}

function throwIfError(result: { error?: unknown; response: Response }): void {
  if (result.error !== undefined) {
    throw new ResourceApiError(result.response.status, result.error);
  }
}

/** SDK `ResourceLink` の optional nullable フィールドを `T | null` へ正規化する。 */
function toResourceLink(raw: SdkResourceLink): ResourceLink {
  return {
    ...raw,
    thumbnail_url: raw.thumbnail_url ?? null,
    note: raw.note ?? null,
    meta: raw.meta ?? {},
  };
}

/** SDK `ResourceListResponse` の items を正規化済み `ResourceLink` 配列に変換する。 */
function toResourceListResponse(raw: SdkResourceListResponse): ResourceListResponse {
  return {
    ...raw,
    items: raw.items.map(toResourceLink),
    suggestion: raw.suggestion ?? null,
  };
}

export async function listResources(itemId: string): Promise<ResourceListResponse> {
  const r = await resourcesList({ path: { item_id: itemId } });
  throwIfError(r);
  return toResourceListResponse(r.data as SdkResourceListResponse);
}

export async function createResource(
  itemId: string,
  payload: { url: string; note?: string },
): Promise<ResourceLink> {
  const r = await resourcesCreate({ path: { item_id: itemId }, body: payload });
  throwIfError(r);
  return toResourceLink(r.data as SdkResourceLink);
}

export async function patchResource(
  resourceId: string,
  patch: { title?: string; kind?: ResKind; note?: string | null },
): Promise<ResourceLink> {
  const r = await resourcesUpdate({ path: { resource_id: resourceId }, body: patch });
  throwIfError(r);
  return toResourceLink(r.data as SdkResourceLink);
}

export async function deleteResource(resourceId: string): Promise<void> {
  const r = await resourcesDelete({ path: { resource_id: resourceId } });
  throwIfError(r);
}

export async function refreshResourceMeta(resourceId: string): Promise<ResourceLink> {
  const r = await resourcesRefreshMeta({ path: { resource_id: resourceId } });
  throwIfError(r);
  return toResourceLink(r.data as SdkResourceLink);
}

export async function acceptResourceSuggestion(itemId: string): Promise<ResourceLink> {
  const r = await resourcesSuggestionAccept({ path: { item_id: itemId } });
  throwIfError(r);
  return toResourceLink(r.data as SdkResourceLink);
}

export async function dismissResourceSuggestion(itemId: string): Promise<void> {
  const r = await resourcesSuggestionDismiss({ path: { item_id: itemId } });
  throwIfError(r);
}
