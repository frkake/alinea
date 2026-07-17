"use client";

import { Card } from "@/components/ui/Card";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { SettingsControlRow } from "@/components/settings/SettingsControlRow";
import { Stepper } from "@/components/settings/Stepper";
import {
  ACCENT_SWATCHES,
  type AccentHex,
  type BodyFontValue,
  type SettingsData,
  type ThemePrefValue,
} from "@/components/settings/types";

/**
 * 表示カテゴリ(4f §4.7.5)。テーマ(light/dark/system)・アクセント 4 色・本文書体・本文サイズ。
 * テーマは ThemeProvider(data-theme + cookie)で即時反映しつつ settings に永続化する(S1 #3)。
 */
export interface DisplaySettingsProps {
  settings: SettingsData;
  onThemeChange: (theme: ThemePrefValue) => void;
  onAccentChange: (hex: AccentHex) => void;
  onBodyFontChange: (font: BodyFontValue) => void;
  onFontSizeChange: (px: number) => void;
}

const THEME_OPTIONS: ReadonlyArray<{ value: ThemePrefValue; label: string }> = [
  { value: "light", label: "ライト" },
  { value: "dark", label: "ダーク" },
  { value: "system", label: "システム" },
];

const BODY_FONT_OPTIONS: ReadonlyArray<{ value: BodyFontValue; label: string }> = [
  { value: "serif", label: "明朝" },
  { value: "sans", label: "ゴシック" },
];

export function DisplaySettings({
  settings,
  onThemeChange,
  onAccentChange,
  onBodyFontChange,
  onFontSizeChange,
}: DisplaySettingsProps) {
  const d = settings.display;
  return (
    <SettingsSection title="表示">
      <Card padding="none">
        <SettingsControlRow
          title="テーマ"
          description="ライト / ダーク / システム(OS 設定に追従)"
          divider
        >
          <SegmentedControl
            options={THEME_OPTIONS}
            value={d.theme}
            onChange={onThemeChange}
            size="lg"
            ariaLabel="テーマ"
          />
        </SettingsControlRow>

        <SettingsControlRow
          title="アクセントカラー"
          description="リンク・選択・ハイライトの基調色"
          divider
        >
          <div role="radiogroup" aria-label="アクセントカラー" style={{ display: "flex", gap: 8 }}>
            {ACCENT_SWATCHES.map((swatch) => {
              const selected = swatch.hex === d.accent;
              return (
                <button
                  key={swatch.hex}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  aria-label={swatch.label}
                  onClick={() => {
                    onAccentChange(swatch.hex);
                  }}
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: "50%",
                    background: swatch.hex,
                    border: "none",
                    padding: 0,
                    cursor: "pointer",
                    boxShadow: selected
                      ? "0 0 0 2px var(--pr-bg-card), 0 0 0 3.5px var(--pr-acc)"
                      : undefined,
                  }}
                />
              );
            })}
          </div>
        </SettingsControlRow>

        <SettingsControlRow title="本文の書体" description="訳文・原文本文に適用" divider>
          <SegmentedControl
            options={BODY_FONT_OPTIONS}
            value={d.body_font}
            onChange={onBodyFontChange}
            size="lg"
            ariaLabel="本文の書体"
          />
        </SettingsControlRow>

        <SettingsControlRow title="本文サイズ" description="14〜20px">
          <Stepper
            value={d.font_size_px}
            min={14}
            max={20}
            step={0.5}
            onChange={onFontSizeChange}
            formatValue={(v) => `${v}px`}
            ariaLabel="本文サイズ"
          />
        </SettingsControlRow>
      </Card>
    </SettingsSection>
  );
}
