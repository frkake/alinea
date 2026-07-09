"use client";

import { Card } from "@/components/ui/Card";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { SettingToggleRow } from "@/components/settings/SettingToggleRow";
import type { SettingsData } from "@/components/settings/types";

/** ブラウザ拡張カテゴリ(4f §4.7.8。docs/08 §5)。 */
export interface ExtensionSettingsProps {
  settings: SettingsData;
  onToggle: (checked: boolean) => void;
}

export function ExtensionSettings({ settings, onToggle }: ExtensionSettingsProps) {
  return (
    <SettingsSection title="ブラウザ拡張">
      <Card padding="none">
        <SettingToggleRow
          title="arXiv ページ内に「A 保存」ボタンを表示"
          description="arxiv.org の論文ページ限定のオプトイン。既定はオフ"
          checked={settings.extension.arxiv_inline_button}
          onChange={onToggle}
        />
      </Card>
    </SettingsSection>
  );
}
