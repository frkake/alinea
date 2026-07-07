"use client";

import type { ApiKeyItem } from "@yakudoku/api-client";
import { Card } from "@/components/ui/Card";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { SettingToggleRow } from "@/components/settings/SettingToggleRow";
import { ModelRoutingRow } from "@/components/settings/ModelRoutingRow";
import { ApiKeyRow } from "@/components/settings/ApiKeyRow";
import {
  BYOK_PROVIDERS,
  type ByokProvider,
  type LlmUseCase,
  type RouteEntry,
  type SettingsData,
} from "@/components/settings/types";

/** アカウントカテゴリ(M0): BYOK 登録 + モデルルーティング(詳細)。 */
export interface AccountSettingsProps {
  settings: SettingsData;
  apiKeys: ApiKeyItem[];
  onRouteChange: (useCase: LlmUseCase, entry: RouteEntry) => void;
  onRasterChange: (next: boolean) => void;
  onSaveKey: (provider: ByokProvider, apiKey: string) => void;
  onDeleteKey: (provider: ByokProvider) => void;
}

const ACCOUNT_ROUTES: ReadonlyArray<{ useCase: LlmUseCase; label: string; description: string }> = [
  { useCase: "summary", label: "要約", description: "3 行要約・詳細要約の生成に使用" },
  { useCase: "article", label: "記事生成", description: "記事モードの本文生成に使用" },
  { useCase: "vocab", label: "語彙生成", description: "語彙帳の用語抽出・説明生成に使用" },
  { useCase: "figure_dsl", label: "概要図データ生成", description: "概要図の構造データ生成に使用" },
  { useCase: "figure_image", label: "解説図画像", description: "解説図のラスター画像生成に使用" },
];

export function AccountSettings({
  settings,
  apiKeys,
  onRouteChange,
  onRasterChange,
  onSaveKey,
  onDeleteKey,
}: AccountSettingsProps) {
  const byProvider = new Map(apiKeys.map((k) => [k.provider, k]));

  return (
    <>
      <SettingsSection title="API キー(BYOK)" titleNote="設定するとクォータを消費しません">
        <Card>
          {BYOK_PROVIDERS.map((provider: ByokProvider, index) => {
            const item = byProvider.get(provider);
            return (
              <ApiKeyRow
                key={provider}
                provider={provider}
                masked={item?.masked ?? null}
                createdAt={item?.created_at ?? null}
                divider={index < BYOK_PROVIDERS.length - 1}
                onSave={(apiKey) => {
                  onSaveKey(provider, apiKey);
                }}
                onDelete={() => {
                  onDeleteKey(provider);
                }}
              />
            );
          })}
          <div style={{ padding: "0 18px 12px", fontSize: 10, color: "var(--pr-text-muted)" }}>
            キーは暗号化して保存され、再表示はできません(再入力のみ)
          </div>
        </Card>
      </SettingsSection>

      <SettingsSection title="モデルルーティング(詳細)">
        <Card>
          {ACCOUNT_ROUTES.map((r) => (
            <ModelRoutingRow
              key={r.useCase}
              useCase={r.useCase}
              label={r.label}
              description={r.description}
              value={settings.llm_routing[r.useCase]}
              availableModels={settings.available_models}
              onChange={(entry) => {
                onRouteChange(r.useCase, entry);
              }}
              divider
            />
          ))}
          <SettingToggleRow
            title="概要図をラスター画像で生成"
            description="オフ(既定)では SVG 決定的レンダリング。オンで画像生成 API を使用"
            checked={settings.llm_routing.overview_figure_raster_mode}
            onChange={onRasterChange}
          />
        </Card>
      </SettingsSection>
    </>
  );
}
