"use client";

import { useMutation } from "@tanstack/react-query";
import { libraryItemsDelete } from "@alinea/api-client";

/**
 * 取り込みキャンセル(docs/08 §2.2)。`DELETE /api/library-items/{id}` はライブラリ項目ごと
 * 削除する(取り込み中の部分データも含めて消える。§cancel-ingest の決定)。
 */
export function useCancelIngest(onSuccess: () => void, onError: () => void) {
  return useMutation({
    mutationFn: async (itemId: string) => {
      await libraryItemsDelete({ path: { item_id: itemId }, throwOnError: true });
    },
    onSuccess,
    onError,
  });
}
