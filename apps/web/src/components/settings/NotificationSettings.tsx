"use client";

import { Card } from "@/components/ui/Card";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { SettingToggleRow } from "@/components/settings/SettingToggleRow";
import type { SettingsData } from "@/components/settings/types";

/** 通知カテゴリ(4f §4.7.7。docs/06 §7 の通知 3 種)。 */
export interface NotificationSettingsProps {
  settings: SettingsData;
  onTranslationCompleteToggle: (checked: boolean) => void;
  onStatusSuggestionToggle: (checked: boolean) => void;
  onDeadlineReminderToggle: (checked: boolean) => void;
}

export function NotificationSettings({
  settings,
  onTranslationCompleteToggle,
  onStatusSuggestionToggle,
  onDeadlineReminderToggle,
}: NotificationSettingsProps) {
  const n = settings.notifications;
  return (
    <SettingsSection title="通知">
      <Card padding="none">
        <SettingToggleRow
          title="翻訳完了"
          description="取り込み・全文翻訳が終わったとき"
          checked={n.translation_complete}
          onChange={onTranslationCompleteToggle}
          divider
        />
        <SettingToggleRow
          title="ステータス提案"
          description="「読んでいる」「読了」への変更提案(✦)"
          checked={n.status_suggestion}
          onChange={onStatusSuggestionToggle}
          divider
        />
        <SettingToggleRow
          title="締切リマインド"
          description="コレクションの締切が近づいたとき"
          checked={n.deadline_reminder}
          onChange={onDeadlineReminderToggle}
        />
      </Card>
    </SettingsSection>
  );
}
