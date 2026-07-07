"use client";

import { Card } from "@/components/ui/Card";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { SettingToggleRow } from "@/components/settings/SettingToggleRow";
import { TranslationStyleRow } from "@/components/settings/TranslationStyleRow";
import { ModelRoutingRow } from "@/components/settings/ModelRoutingRow";
import type {
  LlmUseCase,
  RouteEntry,
  SettingsData,
  TranslationStyle,
} from "@/components/settings/types";

/**
 * 翻訳カテゴリ(4f §4.4・§4.7.3): 「翻訳」(スタイル既定+トグル×3)+「翻訳モデル」カード。
 */
export interface TranslationSettingsProps {
  settings: SettingsData;
  onRouteChange: (useCase: LlmUseCase, entry: RouteEntry) => void;
  onStyleChange: (style: TranslationStyle) => void;
  /** UI は否定形(ON=翻訳しない)。checked はトグルの見た目状態。 */
  onAutoTranslateAppendixToggle: (checked: boolean) => void;
  onTranslateTableCellsToggle: (checked: boolean) => void;
  onSuggestSectionToggle: (checked: boolean) => void;
}

export function TranslationSettings({
  settings,
  onRouteChange,
  onStyleChange,
  onAutoTranslateAppendixToggle,
  onTranslateTableCellsToggle,
  onSuggestSectionToggle,
}: TranslationSettingsProps) {
  const t = settings.translation;
  return (
    <>
      <SettingsSection title="翻訳">
        <Card padding="none">
          <TranslationStyleRow value={t.default_style} onChange={onStyleChange} />
          <SettingToggleRow
            title="付録(Appendix)を自動翻訳しない"
            description="開いたとき・ボタンでオンデマンド翻訳(コスト対策)"
            checked={!t.auto_translate_appendix}
            onChange={onAutoTranslateAppendixToggle}
            divider
          />
          <SettingToggleRow
            title="表のセル内テキストを翻訳しない"
            description="数値・記号が主のため。表単位の「この表を翻訳」は常に利用可"
            checked={!t.translate_table_cells}
            onChange={onTranslateTableCellsToggle}
            divider
          />
          <SettingToggleRow
            title="30 ページ超の論文はセクション選択を提案"
            description="全文翻訳の前に翻訳対象を選べます(既定は全選択)"
            checked={t.suggest_section_selection_over_30_pages}
            onChange={onSuggestSectionToggle}
          />
        </Card>
      </SettingsSection>

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
    </>
  );
}
