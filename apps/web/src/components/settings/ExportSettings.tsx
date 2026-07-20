"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/ui/Card";
import { Popover } from "@/components/ui/Popover";
import { useToast } from "@/components/ui/Toast";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { ExportFormatCard } from "@/components/settings/ExportFormatCard";
import { ExportPaperPickerModal } from "@/components/settings/ExportPaperPickerModal";
import { triggerDownload } from "@/components/settings/download";
import type { DataJobState } from "@/components/settings/types";

export interface ExportSettingsProps {
  /** モバイル縮退(mobile.md §1.2-7)。エクスポート実行(変更系)を非描画にする。設定値の参照は可。 */
  readOnly?: boolean;
}

/** ポーリング間隔(4f §2.3 決定)。 */
const EXPORT_JOB_POLL_MS = 2000;

interface ExportFullStatus {
  job: DataJobState;
  download_url: string | null;
}

interface ImportFullStatus {
  job: DataJobState;
  summary: Record<string, unknown> | null;
}

/**
 * データカテゴリ(4f §4.6 + 完全データ移行 Task 6)。
 * 2 エクスポートカード + 完全バックアップカード + インポートカード。
 */
export function ExportSettings({ readOnly = false }: ExportSettingsProps = {}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [formatOpen, setFormatOpen] = useState(false);
  const formatAnchor = useRef<HTMLDivElement>(null);
  const [backupJobId, setBackupJobId] = useState<string | null>(null);
  const [backupError, setBackupError] = useState<string | null>(null);
  const [importJobId, setImportJobId] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const importFileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  // 完全バックアップジョブポーリング
  const backupJobQuery = useQuery({
    queryKey: ["export", "backup", backupJobId],
    queryFn: async (): Promise<ExportFullStatus> => {
      const res = await fetch(`/api/export/full/${backupJobId}`, { credentials: "include" });
      if (!res.ok) throw new Error("export_full backup status request failed");
      return (await res.json()) as ExportFullStatus;
    },
    enabled: backupJobId !== null,
    refetchInterval: (query) => (query.state.data?.download_url ? false : EXPORT_JOB_POLL_MS),
  });

  useEffect(() => {
    const data = backupJobQuery.data;
    if (!data || backupJobId === null) return;
    if (data.download_url) {
      triggerDownload(data.download_url);
      setBackupJobId(null);
      setBackupError(null);
    } else if (data.job.status === "failed") {
      const msg = data.job.error ?? "処理に失敗しました";
      toast({ kind: "error", message: msg });
      setBackupError(msg);
      setBackupJobId(null);
    }
  }, [backupJobQuery.data, backupJobId, toast]);

  async function startBackupExport(): Promise<void> {
    setBackupError(null);
    try {
      const res = await fetch("/api/export/full", { method: "POST", credentials: "include" });
      if (!res.ok) throw new Error("export_full backup start failed");
      const body = (await res.json()) as { job_id: string };
      setBackupJobId(body.job_id);
    } catch {
      const msg = "完全バックアップの準備に失敗しました。もう一度お試しください";
      toast({ kind: "error", message: msg });
      setBackupError(msg);
    }
  }

  // インポートジョブポーリング
  const importJobQuery = useQuery({
    queryKey: ["import", "full", importJobId],
    queryFn: async (): Promise<ImportFullStatus> => {
      const res = await fetch(`/api/import/full/${importJobId}`, { credentials: "include" });
      if (!res.ok) throw new Error("import_full status request failed");
      return (await res.json()) as ImportFullStatus;
    },
    enabled: importJobId !== null,
    refetchInterval: (query) =>
      query.state.data?.job.status === "succeeded" ||
      query.state.data?.job.status === "failed"
        ? false
        : EXPORT_JOB_POLL_MS,
  });

  // インポート完了 or 失敗のトースト
  useEffect(() => {
    const data = importJobQuery.data;
    if (!data || importJobId === null) return;
    if (data.job.status === "succeeded") {
      toast({ kind: "success", message: "インポートが完了しました" });
      setImportJobId(null);
      setImportError(null);
    } else if (data.job.status === "failed") {
      const msg = data.job.error ?? "処理に失敗しました";
      toast({ kind: "error", message: msg });
      setImportError(msg);
      setImportJobId(null);
    }
  }, [importJobQuery.data, importJobId, toast]);

  async function handleImportFile(file: File): Promise<void> {
    setImportError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/import/full", {
        method: "POST",
        body: form,
        credentials: "include",
      });
      if (!res.ok) throw new Error("import_full start failed");
      const body = (await res.json()) as { job_id: string };
      setImportJobId(body.job_id);
    } catch {
      toast({ kind: "error", message: "インポートの開始に失敗しました。もう一度お試しください" });
    }
  }

  if (readOnly) {
    return (
      <SettingsSection title="データ" titleNote="データはいつでも持ち出せます(P5)">
        <Card padding="md" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 11.5, fontWeight: 600 }}>論文単位 Markdown</span>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
            メモ+注釈+チャットを 1 ファイルに(原文引用・アンカー付き)。実行はデスクトップから行えます
          </span>
          <span style={{ fontSize: 11.5, fontWeight: 600, marginTop: 6 }}>BibTeX / CSV</span>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
            書誌+ステータス+タグ+日付。実行はデスクトップから行えます
          </span>
          <span style={{ fontSize: 11.5, fontWeight: 600, marginTop: 6 }}>完全バックアップ</span>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
            全データ(論文本文・翻訳・PDF・図・メモ等)を 1 つの zip に。実行はデスクトップから行えます
          </span>
          <span style={{ fontSize: 11.5, fontWeight: 600, marginTop: 6 }}>インポート(復元)</span>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
            zip を読み込んでデータを復元。実行はデスクトップから行えます
          </span>
        </Card>
      </SettingsSection>
    );
  }

  return (
    <SettingsSection title="データ" titleNote="データはいつでも持ち出せます(P5)">
      <Card padding="md" style={{ display: "flex", gap: 10 }}>
        <ExportFormatCard
          title="論文単位 Markdown"
          description="メモ+注釈+チャットを 1 ファイルに(原文引用・アンカー付き)。Obsidian 互換の体裁"
          onExport={() => {
            setPickerOpen(true);
          }}
        />
        <div ref={formatAnchor} style={{ flex: 1 }}>
          <ExportFormatCard
            title="BibTeX / CSV"
            description="書誌+ステータス+タグ+日付。主要リファレンスマネージャで読み込み可"
            onExport={() => {
              setFormatOpen((v) => !v);
            }}
          />
        </div>
        <Popover
          open={formatOpen}
          onClose={() => setFormatOpen(false)}
          anchorRef={formatAnchor}
          width={180}
          placement="bottom-start"
          caret={false}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              triggerDownload("/api/export/bibtex");
              setFormatOpen(false);
            }}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              height: 28,
              padding: "0 10px",
              border: "none",
              background: "transparent",
              cursor: "pointer",
              fontFamily: "inherit",
              fontSize: 11.5,
              color: "var(--pr-text-mid)",
            }}
          >
            BibTeX (.bib)
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              triggerDownload("/api/export/csv");
              setFormatOpen(false);
            }}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              height: 28,
              padding: "0 10px",
              border: "none",
              background: "transparent",
              cursor: "pointer",
              fontFamily: "inherit",
              fontSize: 11.5,
              color: "var(--pr-text-mid)",
            }}
          >
            CSV (.csv)
          </button>
        </Popover>
      </Card>
      <Card padding="md" style={{ display: "flex", gap: 10 }}>
        <ExportFormatCard
          title="完全バックアップ"
          description="全データ(論文本文・翻訳・PDF・図・メモ等)を 1 つの zip に。別 PC への移行に使えます"
          busyLabel={backupJobId !== null ? "準備中…" : null}
          errorLabel={backupError}
          onExport={() => {
            void startBackupExport();
          }}
        />
        <div
          role="article"
          aria-label="インポート(復元)"
          style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}
        >
          <span style={{ fontSize: 11.5, fontWeight: 600 }}>インポート(復元)</span>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
            既存データはマージされ上書きされません。
            BYOK(API キー)は移行されないため復元後に再登録してください。
          </span>
          {importJobId === null ? (
            <button
              type="button"
              style={{
                alignSelf: "flex-start",
                marginTop: 4,
                padding: "4px 12px",
                border: "1px solid var(--pr-border-mid)",
                borderRadius: 4,
                background: "transparent",
                cursor: "pointer",
                fontFamily: "inherit",
                fontSize: 11.5,
                color: "var(--pr-text-mid)",
              }}
              onClick={() => importFileRef.current?.click()}
            >
              zip を選択して復元
            </button>
          ) : (
            <span style={{ fontSize: 11, color: "var(--pr-text-muted)", marginTop: 4 }}>
              復元中…
            </span>
          )}
          {importError != null && (
            <span style={{ fontSize: 10.5, color: "var(--pr-warn)", marginTop: 2 }}>
              {importError}
            </span>
          )}
          <input
            ref={importFileRef}
            type="file"
            accept=".zip"
            style={{ display: "none" }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleImportFile(file);
              e.target.value = "";
            }}
          />
        </div>
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
