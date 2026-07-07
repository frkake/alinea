"use client";

import { Card } from "@/components/ui/Card";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { ModelRoutingRow } from "@/components/settings/ModelRoutingRow";
import type { LlmUseCase, RouteEntry, SettingsData } from "@/components/settings/types";

/**
 * 翻訳カテゴリの LLM 節(M0): 「翻訳モデル」カード(4f §4.7.3)。
 * 翻訳スタイル・トグル(§4.4)は M0 では非表示。
 */
export interface TranslationSettingsProps {
  settings: SettingsData;
  onRouteChange: (useCase: LlmUseCase, entry: RouteEntry) => void;
}

export function TranslationSettings({ settings, onRouteChange }: TranslationSettingsProps) {
  return (
    <SettingsSection title="翻訳モデル">
      <Card>
        <ModelRoutingRow
          useCase="translation"
          label="全文翻訳"
          description="取り込み時の自動翻訳・オンデマンド翻訳に使用"
          value={settings.llm_routing.translation}
          availableModels={settings.available_models}
          onChange={(entry) => {
            onRouteChange("translation", entry);
          }}
          divider
        />
        <ModelRoutingRow
          useCase="retranslation"
          label="再翻訳(高品質)"
          description="段落単位の「✦ 高品質で再翻訳」に使用"
          value={settings.llm_routing.retranslation}
          availableModels={settings.available_models}
          onChange={(entry) => {
            onRouteChange("retranslation", entry);
          }}
        />
      </Card>
    </SettingsSection>
  );
}
