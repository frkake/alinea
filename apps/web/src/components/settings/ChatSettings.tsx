"use client";

import { Card } from "@/components/ui/Card";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { SettingToggleRow } from "@/components/settings/SettingToggleRow";
import { ModelRoutingRow } from "@/components/settings/ModelRoutingRow";
import type { LlmUseCase, RouteEntry, SettingsData } from "@/components/settings/types";

/** チャットカテゴリ(4f §4.7.6)。 */
export interface ChatSettingsProps {
  settings: SettingsData;
  onIncludeToggle: (checked: boolean) => void;
  onRouteChange: (useCase: LlmUseCase, entry: RouteEntry) => void;
}

export function ChatSettings({ settings, onIncludeToggle, onRouteChange }: ChatSettingsProps) {
  return (
    <>
      <SettingsSection title="チャット">
        <Card padding="none">
          <SettingToggleRow
            title="注釈・メモを文脈に含める"
            description="オンにすると「さっき疑問ハイライトした箇所」のような参照が通じます(既定オン)"
            checked={settings.chat.include_annotations_and_notes}
            onChange={onIncludeToggle}
          />
        </Card>
      </SettingsSection>

      <SettingsSection title="チャットモデル">
        <Card>
          <ModelRoutingRow
            useCase="chat"
            label="チャット"
            description="論文についての質疑・定型チップに使用"
            value={settings.llm_routing.chat}
            availableModels={settings.available_models}
            onChange={(entry) => {
              onRouteChange("chat", entry);
            }}
          />
        </Card>
      </SettingsSection>
    </>
  );
}
