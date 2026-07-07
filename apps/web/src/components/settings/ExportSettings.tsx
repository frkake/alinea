"use client";

import { useState } from "react";
import { Card } from "@/components/ui/Card";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { ExportFormatCard } from "@/components/settings/ExportFormatCard";
import { ExportPaperPickerModal } from "@/components/settings/ExportPaperPickerModal";
import { triggerDownload } from "@/components/settings/download";

export interface ExportSettingsProps {
  /** モバイル縮退(mobile.md §1.2-7)。エクスポート実行(変更系)を非描画にする。設定値の参照は可。 */
  readOnly?: boolean;
}

/**
 * エクスポートカテゴリ(4f §4.6)。M1-17 スコープ: 論文単位 Markdown・BibTeX のみ
 * (CSV・JSON 一括は M2-15 まで非表示 — plans/13 §3.2 M1-17)。
 */
export function ExportSettings({ readOnly = false }: ExportSettingsProps = {}) {
  const [pickerOpen, setPickerOpen] = useState(false);

  if (readOnly) {
    return (
      <SettingsSection title="エクスポート" titleNote="データはいつでも持ち出せます(P5)">
        <Card padding="md" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 11.5, fontWeight: 600 }}>論文単位 Markdown</span>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
            メモ+注釈+チャットを 1 ファイルに(原文引用・アンカー付き)。実行はデスクトップから行えます
          </span>
          <span style={{ fontSize: 11.5, fontWeight: 600, marginTop: 6 }}>BibTeX</span>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
            書誌+ステータス+タグ+日付。実行はデスクトップから行えます
          </span>
        </Card>
      </SettingsSection>
    );
  }

  return (
    <SettingsSection title="エクスポート" titleNote="データはいつでも持ち出せます(P5)">
      <Card padding="md" style={{ display: "flex", gap: 10 }}>
        <ExportFormatCard
          title="論文単位 Markdown"
          description="メモ+注釈+チャットを 1 ファイルに(原文引用・アンカー付き)。Obsidian 互換の体裁"
          onExport={() => {
            setPickerOpen(true);
          }}
        />
        <ExportFormatCard
          title="BibTeX"
          description="書誌+ステータス+タグ+日付。主要リファレンスマネージャで読み込み可"
          onExport={() => {
            triggerDownload("/api/export/bibtex");
          }}
        />
      </Card>
      <ExportPaperPickerModal
        open={pickerOpen}
        onClose={() => {
          setPickerOpen(false);
        }}
      />
    </SettingsSection>
  );
}
