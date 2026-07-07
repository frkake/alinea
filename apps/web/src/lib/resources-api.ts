/**
 * リソース API(plans/03 §12)。
 *
 * `@yakudoku/api-client` は本エンドポイント群を未生成のため(main.py 未登録。M2-13 deviations
 * 参照)、`fetch()` 直書きで契約どおりに呼ぶ(apps/web/src/hooks/use-reading-session.ts と同方針)。
 */
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

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // 本文が JSON でない場合は無視(P3。呼び出し側は status のみでも判定できる)。
    }
    throw new ResourceApiError(res.status, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function listResources(itemId: string): Promise<ResourceListResponse> {
  return request<ResourceListResponse>(`/api/library-items/${itemId}/resources`);
}

export function createResource(
  itemId: string,
  payload: { url: string; note?: string },
): Promise<ResourceLink> {
  return request<ResourceLink>(`/api/library-items/${itemId}/resources`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function patchResource(
  resourceId: string,
  patch: { title?: string; kind?: ResKind; note?: string | null },
): Promise<ResourceLink> {
  return request<ResourceLink>(`/api/resources/${resourceId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deleteResource(resourceId: string): Promise<void> {
  await request(`/api/resources/${resourceId}`, { method: "DELETE" });
}

export function refreshResourceMeta(resourceId: string): Promise<ResourceLink> {
  return request<ResourceLink>(`/api/resources/${resourceId}/refresh-meta`, { method: "POST" });
}

export function acceptResourceSuggestion(itemId: string): Promise<ResourceLink> {
  return request<ResourceLink>(`/api/library-items/${itemId}/resource-suggestion/accept`, {
    method: "POST",
  });
}

export async function dismissResourceSuggestion(itemId: string): Promise<void> {
  await request(`/api/library-items/${itemId}/resource-suggestion/dismiss`, {
    method: "POST",
  });
}
