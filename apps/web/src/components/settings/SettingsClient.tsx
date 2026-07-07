"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  settingsDeleteApiKey,
  settingsGet,
  settingsListApiKeys,
  settingsPutApiKey,
  settingsUpdate,
} from "@yakudoku/api-client";
import { useToast } from "@/components/ui/Toast";
import { EmptyState } from "@/components/ui/EmptyState";
import { AccountSettings } from "@/components/settings/AccountSettings";
import { TranslationSettings } from "@/components/settings/TranslationSettings";
import {
  type ByokProvider,
  type LlmUseCase,
  type RouteEntry,
  type SettingsCategory,
  type SettingsData,
} from "@/components/settings/types";

const CATEGORIES: ReadonlyArray<{ id: SettingsCategory; label: string }> = [
  { id: "account", label: "アカウント" },
  { id: "translation", label: "翻訳" },
];

/** 設定画面のクライアント本体(4f、M0 スコープ)。カテゴリ = account / translation。 */
export function SettingsClient({ category }: { category: SettingsCategory }) {
  const queryClient = useQueryClient();
  const toast = useToast();

  const settingsQuery = useQuery({
    queryKey: ["settings", "detail"],
    queryFn: async () => (await settingsGet({ throwOnError: true })).data as unknown as SettingsData,
    staleTime: 60_000,
  });

  const apiKeysQuery = useQuery({
    queryKey: ["settings", "api-keys"],
    queryFn: async () => (await settingsListApiKeys({ throwOnError: true })).data.items,
    staleTime: 60_000,
  });

  const patchMutation = useMutation({
    mutationFn: async (body: Record<string, unknown>) =>
      (await settingsUpdate({ body, throwOnError: true })).data as unknown as SettingsData,
    onSuccess: (data) => {
      queryClient.setQueryData(["settings", "detail"], data);
    },
    onError: () => {
      toast({ kind: "error", message: "設定を更新できませんでした。もう一度お試しください" });
    },
  });

  const saveKeyMutation = useMutation({
    mutationFn: async ({ provider, apiKey }: { provider: ByokProvider; apiKey: string }) =>
      settingsPutApiKey({ path: { provider }, body: { api_key: apiKey }, throwOnError: true }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "api-keys"] });
    },
    onError: () => {
      toast({ kind: "error", message: "API キーを保存できませんでした。もう一度お試しください" });
    },
  });

  const deleteKeyMutation = useMutation({
    mutationFn: async (provider: ByokProvider) =>
      settingsDeleteApiKey({ path: { provider }, throwOnError: true }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "api-keys"] });
    },
    onError: () => {
      toast({ kind: "error", message: "API キーを削除できませんでした。もう一度お試しください" });
    },
  });

  const onRouteChange = (useCase: LlmUseCase, entry: RouteEntry) => {
    patchMutation.mutate({ llm_routing: { [useCase]: entry } });
  };
  const onRasterChange = (next: boolean) => {
    patchMutation.mutate({ llm_routing: { overview_figure_raster_mode: next } });
  };

  const settings = settingsQuery.data;

  return (
    <div style={{ display: "flex", minHeight: 0, flex: 1 }}>
      <SettingsCategoryNav active={category} />
      <div
        style={{
          flex: 1,
          minWidth: 0,
          overflowY: "auto",
          display: "flex",
          justifyContent: "center",
          alignItems: "flex-start",
        }}
      >
        <div
          style={{
            width: 720,
            maxWidth: "100%",
            padding: "26px 22px",
            display: "flex",
            flexDirection: "column",
            gap: 22,
          }}
        >
          {settingsQuery.isError ? (
            <EmptyState
              title="読み込みに失敗しました"
              description="通信に失敗しました"
              action={{ label: "再試行", onClick: () => void settingsQuery.refetch() }}
            />
          ) : !settings ? (
            <div style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>読み込み中…</div>
          ) : category === "account" ? (
            <AccountSettings
              settings={settings}
              apiKeys={apiKeysQuery.data ?? []}
              onRouteChange={onRouteChange}
              onRasterChange={onRasterChange}
              onSaveKey={(provider, apiKey) => {
                saveKeyMutation.mutate({ provider, apiKey });
              }}
              onDeleteKey={(provider) => {
                deleteKeyMutation.mutate(provider);
              }}
            />
          ) : (
            <TranslationSettings settings={settings} onRouteChange={onRouteChange} />
          )}
        </div>
      </div>
    </div>
  );
}

function SettingsCategoryNav({ active }: { active: SettingsCategory }) {
  return (
    <nav
      aria-label="設定カテゴリ"
      style={{
        width: 200,
        flex: "none",
        borderRight: "1px solid var(--pr-border-pane)",
        background: "var(--pr-bg-pane)",
        padding: "14px 10px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        fontSize: 12.5,
      }}
    >
      {CATEGORIES.map((c) => {
        const selected = c.id === active;
        return (
          <Link
            key={c.id}
            href={`/settings?category=${c.id}`}
            aria-current={selected ? "page" : undefined}
            style={{
              padding: "7px 10px",
              borderRadius: 6,
              textDecoration: "none",
              color: selected ? "var(--pr-acc)" : "var(--pr-text-nav)",
              fontWeight: selected ? 600 : 400,
              background: selected ? "var(--pr-acc-s)" : undefined,
            }}
          >
            {c.label}
          </Link>
        );
      })}
    </nav>
  );
}
