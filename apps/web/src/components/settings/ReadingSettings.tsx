"use client";

import { Card } from "@/components/ui/Card";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { SettingToggleRow } from "@/components/settings/SettingToggleRow";
import { StatusTransitionRow } from "@/components/settings/StatusTransitionRow";
import type { SettingsData, StatusTransition } from "@/components/settings/types";

/** 読書の計測と提案カテゴリ(4f §4.5)。 */
export interface ReadingSettingsProps {
  settings: SettingsData;
  onTrackToggle: (checked: boolean) => void;
  onStatusTransitionChange: (next: StatusTransition) => void;
}

export function ReadingSettings({
  settings,
  onTrackToggle,
  onStatusTransitionChange,
}: ReadingSettingsProps) {
  const r = settings.reading;
  return (
    <SettingsSection title="読書の計測と提案">
      <Card padding="none">
        <SettingToggleRow
          title="読書時間を計測する"
          description="タブ前面かつ操作中のみ記録。統計と「読んでいる」提案に使用。いつでもオフにできます"
          checked={r.track_reading_time}
          onChange={onTrackToggle}
          divider
        />
        <StatusTransitionRow value={r.status_transition} onChange={onStatusTransitionChange} />
      </Card>
    </SettingsSection>
  );
}
