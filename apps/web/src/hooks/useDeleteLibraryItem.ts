"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { libraryItemsDelete } from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";

export interface DeleteLibraryItemInput {
  id: string;
  title: string;
}

interface UseDeleteLibraryItemOptions {
  onSuccess?: (item: DeleteLibraryItemInput) => void;
  onError?: (error: unknown) => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  const problem = error as { detail?: string; title?: string };
  return problem.detail ?? problem.title ?? "論文を削除できませんでした";
}

export function useDeleteLibraryItem(options: UseDeleteLibraryItemOptions = {}) {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: async (item: DeleteLibraryItemInput) => {
      await libraryItemsDelete({ path: { item_id: item.id }, throwOnError: true });
    },
    onSuccess: (_data, item) => {
      qc.removeQueries({ queryKey: ["viewer", item.id] });
      qc.removeQueries({ queryKey: ["article", item.id] });
      void qc.invalidateQueries({ queryKey: ["library"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      void qc.invalidateQueries({ queryKey: ["search"] });
      void qc.invalidateQueries({ queryKey: ["vocab"] });
      toast({ kind: "success", message: "論文を削除しました" });
      options.onSuccess?.(item);
    },
    onError: (error) => {
      toast({ kind: "error", message: errorMessage(error) });
      options.onError?.(error);
    },
  });
}
