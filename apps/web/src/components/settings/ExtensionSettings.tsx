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
          description="この好み設定です。実際の有効化はブラウザ拡張のポップアップから行います(arxiv.org へのアクセス権限を拡張側で要求するため)"
          checked={settings.extension.arxiv_inline_button}
          onChange={onToggle}
        />
        <div style={{ padding: "0 18px 12px", fontSize: 10, color: "var(--pr-text-muted)" }}>
          拡張ポップアップの ⚙ 設定で「arXiv ページに保存ボタンを表示」をオンにすると有効になります。
        </div>
      </Card>
    </SettingsSection>
  );
}
