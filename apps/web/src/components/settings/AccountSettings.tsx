"use client";

import { useRef, useState } from "react";
import type { ApiKeyItem, MeResponse, QuotaResponse } from "@alinea/api-client";
import { purgeUserAndWait } from "@/lib/offline-viewer";
import { Card } from "@/components/ui/Card";
import { Modal } from "@/components/ui/Modal";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { SettingsControlRow } from "@/components/settings/SettingsControlRow";
import { SettingToggleRow } from "@/components/settings/SettingToggleRow";
import { ModelRoutingRow } from "@/components/settings/ModelRoutingRow";
import { ApiKeyRow } from "@/components/settings/ApiKeyRow";
import { CodeAnalysisSettings } from "@/components/settings/CodeAnalysisSettings";
import {
  BYOK_PROVIDERS,
  type ByokProvider,
  type CodeAnalysisMode,
  type LlmUseCase,
  type RouteEntry,
  type SettingsData,
} from "@/components/settings/types";

/** アカウントカテゴリ(M0 + S1 #4): identity・クォータ残量・BYOK・モデルルーティング・危険操作。 */
export interface AccountSettingsProps {
  settings: SettingsData;
  apiKeys: ApiKeyItem[];
  me?: MeResponse;
  quota?: QuotaResponse;
  onRouteChange: (useCase: LlmUseCase, entry: RouteEntry) => void;
  onRasterChange: (next: boolean) => void;
  onSaveKey: (provider: ByokProvider, apiKey: string) => Promise<unknown>;
  onDeleteKey: (provider: ByokProvider) => void;
  onLogout: () => void;
  onDeleteAccount: () => void;
  /** GitHub コード対応解析の当月実費(USD)。未取得時は null(Task 22)。 */
  codeAnalysisMonthCostUsd?: number | null;
  onCodeAnalysisModeChange: (mode: CodeAnalysisMode) => void;
  onCodeAnalysisBudgetChange: (usd: number) => void;
  /** モバイル縮退(mobile.md §1.2-7)。API キーの設定/削除・危険操作(変更系)を非描画にする。参照は可。 */
  readOnly?: boolean;
}

const ACCOUNT_ROUTES: ReadonlyArray<{ useCase: LlmUseCase; label: string; description: string }> = [
  { useCase: "summary", label: "要約", description: "3 行要約・詳細要約の生成に使用" },
  { useCase: "article", label: "記事生成", description: "記事モードの本文生成に使用" },
  { useCase: "vocab", label: "語彙生成", description: "語彙帳の用語抽出・説明生成に使用" },
  { useCase: "figure_dsl", label: "概要図データ生成", description: "概要図の構造データ生成に使用" },
  { useCase: "figure_image", label: "解説図画像", description: "解説図のラスター画像生成に使用" },
];

/** クォータ 5 カウンタの表示ラベル(plans/07 §9.2)。 */
const QUOTA_ROWS: ReadonlyArray<{ key: keyof QuotaResponse["usage"]; label: string }> = [
  { key: "translation_papers", label: "全文翻訳(本)" },
  { key: "chat_messages", label: "チャット(メッセージ)" },
  { key: "images", label: "画像生成(枚)" },
  { key: "article_generations", label: "記事生成(回)" },
  { key: "vocab_generations", label: "語彙生成(語)" },
];

const PROVIDER_LOGIN_LABEL: Record<string, string> = {
  google: "Google",
  github: "GitHub",
  email: "メールリンク",
};

const secondaryButtonStyle = {
  height: 28,
  padding: "0 12px",
  borderRadius: 7,
  border: "1px solid var(--pr-border-control)",
  background: "var(--pr-bg-control)",
  color: "var(--pr-text)",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
} as const;

export function AccountSettings({
  settings,
  apiKeys,
  me,
  quota,
  onRouteChange,
  onRasterChange,
  onSaveKey,
  onDeleteKey,
  onLogout,
  onDeleteAccount,
  codeAnalysisMonthCostUsd = null,
  onCodeAnalysisModeChange,
  onCodeAnalysisBudgetChange,
  readOnly = false,
}: AccountSettingsProps) {
  const byProvider = new Map(apiKeys.map((k) => [k.provider, k]));

  // Task 23(オフライン閲覧の per-user 分離): 明示ログアウト / アカウント削除では、
  // この端末に残る当該ユーザーのオフラインキャッシュ(直近論文の本文・訳文・図)を
  // Service Worker から完全に削除し、その「完了を待ってから」実際のログアウト/削除
  // (=ログイン画面への遷移を伴う親コールバック)を実行する。SW 非対応・controller 不在時は
  // purgeUserAndWait が即解決するため遷移を妨げない。
  const purgeThen = async (next: () => void) => {
    const userId = me?.user.id;
    if (userId) await purgeUserAndWait(userId);
    next();
  };

  return (
    <>
      <SettingsSection title="アカウント">
        <Card padding="none">
          <SettingsControlRow
            title="サインイン中"
            description={
              me
                ? `${me.user.providers.map((p) => PROVIDER_LOGIN_LABEL[p] ?? p).join(" / ")} で認証`
                : "読み込み中…"
            }
            divider={!readOnly}
          >
            <span style={{ fontSize: 12, color: "var(--pr-text-mid)" }}>{me?.user.email ?? ""}</span>
          </SettingsControlRow>
          {!readOnly ? (
            <SettingsControlRow title="ログアウト" description="このデバイスのセッションを終了します">
              <button
                type="button"
                onClick={() => void purgeThen(onLogout)}
                style={secondaryButtonStyle}
              >
                ログアウト
              </button>
            </SettingsControlRow>
          ) : null}
        </Card>
      </SettingsSection>

      <SettingsSection
        title="今月の利用状況"
        titleNote={quota ? `${quota.period} · 上限到達時は BYOK 登録で解除` : undefined}
      >
        <Card padding="none">
          {QUOTA_ROWS.map((row, index) => {
            const counter = quota?.usage[row.key];
            const isImage = row.key === "images";
            const byokActive = isImage ? quota?.byok_active.image : quota?.byok_active.text;
            return (
              <SettingsControlRow
                key={row.key}
                title={row.label}
                divider={index < QUOTA_ROWS.length - 1}
              >
                <span
                  style={{
                    fontSize: 12,
                    color: "var(--pr-text-mid)",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {counter
                    ? byokActive
                      ? "無制限(BYOK)"
                      : `${counter.used} / ${counter.limit}`
                    : "—"}
                </span>
              </SettingsControlRow>
            );
          })}
        </Card>
      </SettingsSection>

      <SettingsSection title="API キー(BYOK)" titleNote="設定するとクォータを消費しません">
        <Card>
          {BYOK_PROVIDERS.map((provider: ByokProvider, index) => {
            const item = byProvider.get(provider);
            return (
              <ApiKeyRow
                key={provider}
                provider={provider}
                masked={item?.masked ?? null}
                createdAt={item?.created_at ?? null}
                divider={index < BYOK_PROVIDERS.length - 1}
                onSave={(apiKey) => onSaveKey(provider, apiKey)}
                onDelete={() => {
                  onDeleteKey(provider);
                }}
                readOnly={readOnly}
              />
            );
          })}
          <div style={{ padding: "0 18px 12px", fontSize: 10, color: "var(--pr-text-muted)" }}>
            キーは暗号化して保存され、再表示はできません(再入力のみ)
          </div>
        </Card>
      </SettingsSection>

      <SettingsSection title="モデルルーティング(詳細)">
        <Card>
          {ACCOUNT_ROUTES.map((r) => (
            <ModelRoutingRow
              key={r.useCase}
              useCase={r.useCase}
              label={r.label}
              description={r.description}
              value={settings.llm_routing[r.useCase]}
              availableModels={settings.available_models}
              onChange={(entry) => {
                onRouteChange(r.useCase, entry);
              }}
              divider
            />
          ))}
          <SettingToggleRow
            title="概要図をラスター画像で生成"
            description="オフ(既定)では SVG 決定的レンダリング。オンで画像生成 API を使用"
            checked={settings.llm_routing.overview_figure_raster_mode}
            onChange={onRasterChange}
          />
        </Card>
      </SettingsSection>

      <SettingsSection
        title="GitHub コード対応解析"
        titleNote="論文の主張とリポジトリのコードを対応付けます(LLM・埋め込み API を使用)"
      >
        <Card padding="none">
          <CodeAnalysisSettings
            mode={settings.code_analysis.mode}
            monthlyBudgetUsd={settings.code_analysis.monthly_budget_usd}
            currentMonthCostUsd={codeAnalysisMonthCostUsd}
            onModeChange={onCodeAnalysisModeChange}
            onBudgetChange={onCodeAnalysisBudgetChange}
          />
        </Card>
      </SettingsSection>

      {!readOnly ? (
        <SettingsSection title="アカウントの削除">
          <Card padding="none">
            <SettingsControlRow
              title="アカウントを完全に削除"
              description="ライブラリ・注釈・メモ・チャットを含む全データを削除します。取り消せません"
            >
              <DeleteAccountButton onConfirm={() => void purgeThen(onDeleteAccount)} />
            </SettingsControlRow>
          </Card>
        </SettingsSection>
      ) : null}
    </>
  );
}

/** アカウント削除の合言葉確認モーダル(auth §2: confirm='delete' のみ受理)。 */
function DeleteAccountButton({ onConfirm }: { onConfirm: () => void }) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        style={{ ...secondaryButtonStyle, color: "var(--pr-warn)", borderColor: "var(--pr-warn)" }}
      >
        アカウントを削除
      </button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        labelledBy="delete-account-title"
        initialFocusRef={inputRef}
        width={420}
      >
        <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 id="delete-account-title" style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>
            アカウントを削除しますか?
          </h2>
          <p style={{ fontSize: 12, color: "var(--pr-text-sub)", margin: 0, lineHeight: 1.7 }}>
            全データが削除され、取り消せません。続けるには下の欄に delete と入力してください。
          </p>
          <input
            ref={inputRef}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="delete"
            aria-label="削除の確認"
            style={{
              height: 34,
              padding: "0 10px",
              borderRadius: 8,
              border: "1px solid var(--pr-border-control)",
              background: "var(--pr-bg-control)",
              color: "var(--pr-text)",
              fontSize: 13,
              fontFamily: "inherit",
            }}
          />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button type="button" onClick={() => setOpen(false)} style={secondaryButtonStyle}>
              キャンセル
            </button>
            <button
              type="button"
              disabled={confirm !== "delete"}
              onClick={() => {
                setOpen(false);
                onConfirm();
              }}
              style={{
                ...secondaryButtonStyle,
                background: "var(--pr-warn)",
                color: "#FFFFFF",
                borderColor: "var(--pr-warn)",
                opacity: confirm !== "delete" ? 0.5 : 1,
                cursor: confirm !== "delete" ? "default" : "pointer",
              }}
            >
              削除する
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}
