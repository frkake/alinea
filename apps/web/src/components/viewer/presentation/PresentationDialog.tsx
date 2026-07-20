"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Problem } from "@alinea/api-client";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { triggerDownload } from "@/components/settings/download";
import { PresentationProgress } from "@/components/viewer/presentation/PresentationProgress";
import {
  fetchPresentationStatus,
  presentationDownloadUrl,
  presentationKeys,
  startPresentation,
} from "@/components/viewer/presentation/queries";
import {
  AUDIENCE_OPTIONS,
  INSTRUCTION_MAX_LEN,
  PRESET_DEFAULT_AUDIENCE,
  PRESET_OPTIONS,
  audienceLabel,
  presetLabel,
  stageLabel,
  type Audience,
  type PresentationArtifact,
  type Preset,
} from "@/components/viewer/presentation/types";

const DIALOG_TITLE_ID = "presentation-dialog-title";

/**
 * 論文→スライド生成ダイアログ(Task 30 §4/§5)。状態機械:
 *
 * - 初回表示で GET /presentation を取得し、既存 artifact と進行中 job を得る。
 * - 進行中 job があれば(再読込直後含む)即「進捗」を表示する(二重送信防止)。
 * - job が無く artifact も無ければ「開始フォーム」(3 用途 + 聴衆 + 任意指示 ≤500)。
 * - job が無く artifact があれば「成功カード」(生成日時/用途/model/ppt-master rev/DL/再生成)。
 * - 進行中 job が失敗したら「失敗表示」(stage + Problem Details + 再試行)。旧 artifact が
 *   あればそのダウンロードを同時に残す(失敗で旧成果物を失わない)。
 *
 * 色・書体・画像方針・言語はユーザー選択にせず、worker 側の安全な既定値へ固定する。
 */
export interface PresentationDialogProps {
  open: boolean;
  itemId: string;
  onClose: () => void;
}

type Failure = { problem: Partial<Problem>; stage: string | null };

export function PresentationDialog({ open, itemId, onClose }: PresentationDialogProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const titleRef = useRef<HTMLHeadingElement>(null);

  const statusQuery = useQuery({
    queryKey: presentationKeys.status(itemId),
    queryFn: () => fetchPresentationStatus(itemId),
    enabled: open,
    staleTime: 10_000,
  });

  const artifact = statusQuery.data?.artifact ?? null;
  const serverJob = statusQuery.data?.job ?? null;

  // 進行中 job を追跡する。初回は GET の job(再読込後も active job を追う)、開始後は
  // POST が返した job_id を採用する。job が失敗/成功したら null に戻す。
  const [jobId, setJobId] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // 「再生成」を押した / 未生成 → 開始フォームを出すためのフラグ。
  const [showForm, setShowForm] = useState(false);

  // GET が進行中 job を返したら、その job を追跡対象にする(初期化・リロード復帰)。
  useEffect(() => {
    if (serverJob && serverJob.status !== "succeeded" && serverJob.status !== "failed") {
      setJobId(serverJob.id);
      setFailure(null);
    }
  }, [serverJob]);

  // フォーム入力状態。
  const [preset, setPreset] = useState<Preset>("reading_group");
  const [audience, setAudience] = useState<Audience>(PRESET_DEFAULT_AUDIENCE.reading_group);
  const [audienceTouched, setAudienceTouched] = useState(false);
  const [instruction, setInstruction] = useState("");

  const onPresetChange = (next: Preset) => {
    setPreset(next);
    if (!audienceTouched) setAudience(PRESET_DEFAULT_AUDIENCE[next]);
  };

  const onStart = async () => {
    if (submitting || jobId) return;
    setSubmitting(true);
    setFailure(null);
    try {
      const trimmed = instruction.trim();
      const newJobId = await startPresentation(itemId, {
        preset,
        audience,
        ...(trimmed ? { instruction: trimmed } : {}),
      });
      setShowForm(false);
      setJobId(newJobId);
    } catch (err) {
      const problem = (err as Partial<Problem> | undefined) ?? {};
      toast({ kind: "error", message: problem.title ?? "スライド生成を開始できませんでした" });
    } finally {
      setSubmitting(false);
    }
  };

  const onJobDone = () => {
    setJobId(null);
    setFailure(null);
    setShowForm(false);
    void qc.invalidateQueries({ queryKey: presentationKeys.status(itemId) });
    toast({ kind: "success", message: "✓ スライドを生成しました" });
  };

  const onJobError = (problem: Partial<Problem>, stage: string | null) => {
    setJobId(null);
    setFailure({ problem, stage });
    // 旧 artifact を保つため invalidate はしない(GET は旧成果物を返し続ける)。
  };

  const onDownload = () => triggerDownload(presentationDownloadUrl(itemId));

  // 表示状態の決定。
  const loading = statusQuery.isLoading;
  const running = jobId !== null;
  // 開始フォームを出すべきか: 明示的にフォーム表示中 / (job なし・artifact なし・失敗なし)。
  const wantForm = showForm || (!running && !artifact && !failure);

  return (
    <Modal open={open} onClose={onClose} labelledBy={DIALOG_TITLE_ID} width={480} initialFocusRef={titleRef}>
      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 16 }}>
        <h2 ref={titleRef} tabIndex={-1} id={DIALOG_TITLE_ID} style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>
          論文からスライドを生成
        </h2>

        {loading ? (
          <div style={{ fontSize: 12, color: "var(--pr-text-muted)" }}>読み込み中…</div>
        ) : running && jobId ? (
          <PresentationProgress jobId={jobId} onDone={onJobDone} onError={onJobError} />
        ) : (
          <>
            {failure ? <FailureNotice failure={failure} /> : null}
            {/* 失敗しても旧 artifact のダウンロードは残す(失敗で旧成果物を失わない)。 */}
            {artifact && !wantForm ? (
              <SuccessCard
                artifact={artifact}
                onDownload={onDownload}
                onRegenerate={() => {
                  // 再生成フォームは現在の成果物設定を初期値にする。
                  setPreset(normalizePreset(artifact.preset));
                  setAudience(normalizeAudience(artifact.audience));
                  setAudienceTouched(true);
                  setInstruction(artifact.instruction ?? "");
                  setShowForm(true);
                }}
                failed={failure !== null}
                onRetry={() => setShowForm(true)}
              />
            ) : (
              <StartForm
                preset={preset}
                audience={audience}
                instruction={instruction}
                submitting={submitting}
                hasPriorArtifact={artifact !== null}
                onPresetChange={onPresetChange}
                onAudienceChange={(a) => {
                  setAudience(a);
                  setAudienceTouched(true);
                }}
                onInstructionChange={setInstruction}
                onSubmit={() => void onStart()}
                onCancel={artifact ? () => setShowForm(false) : undefined}
              />
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

function normalizePreset(preset: string): Preset {
  return PRESET_OPTIONS.some((o) => o.value === preset) ? (preset as Preset) : "reading_group";
}

function normalizeAudience(audience: string): Audience {
  return AUDIENCE_OPTIONS.some((o) => o.value === audience) ? (audience as Audience) : "beginner";
}

// ---------------------------------------------------------------------------
// 開始フォーム
// ---------------------------------------------------------------------------
function StartForm({
  preset,
  audience,
  instruction,
  submitting,
  hasPriorArtifact,
  onPresetChange,
  onAudienceChange,
  onInstructionChange,
  onSubmit,
  onCancel,
}: {
  preset: Preset;
  audience: Audience;
  instruction: string;
  submitting: boolean;
  hasPriorArtifact: boolean;
  onPresetChange: (p: Preset) => void;
  onAudienceChange: (a: Audience) => void;
  onInstructionChange: (v: string) => void;
  onSubmit: () => void;
  onCancel?: () => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Field label="用途">
        <RadioRow
          name="preset"
          options={PRESET_OPTIONS}
          value={preset}
          onChange={(v) => onPresetChange(v as Preset)}
        />
      </Field>

      <Field label="想定聴衆">
        <RadioRow
          name="audience"
          options={AUDIENCE_OPTIONS}
          value={audience}
          onChange={(v) => onAudienceChange(v as Audience)}
        />
      </Field>

      <Field label="任意指示(強調点や語り口の希望)">
        <textarea
          aria-label="任意指示"
          value={instruction}
          maxLength={INSTRUCTION_MAX_LEN}
          onChange={(e) => onInstructionChange(e.target.value)}
          rows={3}
          placeholder="例: 実験結果を厚めに。数式は最小限に。"
          style={{
            width: "100%",
            resize: "vertical",
            padding: "8px 10px",
            borderRadius: 8,
            border: "1px solid var(--pr-border-control)",
            background: "var(--pr-bg-control)",
            color: "var(--pr-text)",
            fontSize: 12.5,
            fontFamily: "inherit",
            lineHeight: 1.6,
          }}
        />
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--pr-text-muted)" }}>
          <span>指示は語り口や強調の調整だけに使われ、スライドの事実根拠にはしません。</span>
          <span style={{ fontVariantNumeric: "tabular-nums" }}>
            {instruction.length} / {INSTRUCTION_MAX_LEN}
          </span>
        </div>
      </Field>

      <div style={{ fontSize: 10.5, color: "var(--pr-text-muted)", lineHeight: 1.6 }}>
        送信されるのは論文本文・書誌・図表だけです(メモ・注釈・チャットは送りません)。
        色・書体・画像方針・言語は研究発表向けの安全な既定値(日本語)に固定されます。
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        {onCancel ? (
          <button type="button" onClick={onCancel} style={secondaryBtn}>
            キャンセル
          </button>
        ) : null}
        <button
          type="button"
          disabled={submitting}
          onClick={onSubmit}
          style={{ ...primaryBtn, opacity: submitting ? 0.7 : 1, cursor: submitting ? "default" : "pointer" }}
        >
          {hasPriorArtifact ? "✦ 再生成する" : "✦ 生成する"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 成功カード
// ---------------------------------------------------------------------------
function SuccessCard({
  artifact,
  onDownload,
  onRegenerate,
  failed,
  onRetry,
}: {
  artifact: PresentationArtifact;
  onDownload: () => void;
  onRegenerate: () => void;
  failed: boolean;
  onRetry: () => void;
}) {
  const generatedAt = useMemo(() => formatDateTime(artifact.generated_at), [artifact.generated_at]);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 12px", fontSize: 12 }}>
        <MetaRow term="生成日時" value={generatedAt} />
        <MetaRow term="用途" value={`${presetLabel(artifact.preset)}(${audienceLabel(artifact.audience)}向け)`} />
        <MetaRow term="使用モデル" value={`${artifact.model_provider} / ${artifact.model_id}`} />
        <MetaRow term="ppt-master" value={artifact.ppt_master_revision} />
      </dl>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button type="button" onClick={onDownload} style={primaryBtn}>
          ダウンロード
        </button>
        {failed ? (
          <button type="button" onClick={onRetry} style={secondaryBtn}>
            再試行
          </button>
        ) : (
          <button type="button" onClick={onRegenerate} style={secondaryBtn}>
            ✦ 再生成
          </button>
        )}
      </div>
    </div>
  );
}

function FailureNotice({ failure }: { failure: Failure }) {
  const { problem, stage } = failure;
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "10px 12px",
        borderRadius: 8,
        border: "1px solid var(--pr-warn)",
        background: "var(--pr-warn-bg, transparent)",
      }}
    >
      <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--pr-warn)" }}>
        {problem.title ?? "スライド生成に失敗しました"}
      </span>
      {stage ? (
        <span style={{ fontSize: 11, color: "var(--pr-text-sub)" }}>
          失敗した工程: {stageLabel(stage)}
        </span>
      ) : null}
      {problem.detail ? (
        <span style={{ fontSize: 11, color: "var(--pr-text-sub)" }}>{problem.detail}</span>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 小物
// ---------------------------------------------------------------------------
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--pr-text-mid)" }}>{label}</span>
      {children}
    </div>
  );
}

function MetaRow({ term, value }: { term: string; value: string }) {
  return (
    <>
      <dt style={{ color: "var(--pr-text-muted)" }}>{term}</dt>
      <dd style={{ margin: 0, color: "var(--pr-text)" }}>{value}</dd>
    </>
  );
}

/** アクセシブルなラジオ行(role=radio・aria-checked。SegmentedControl は role=radio を持たない)。 */
function RadioRow<T extends string>({
  name,
  options,
  value,
  onChange,
}: {
  name: string;
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={name} style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {options.map((o) => {
        const selected = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(o.value)}
            style={{
              height: 28,
              padding: "0 14px",
              borderRadius: 7,
              border: `1px solid ${selected ? "var(--pr-acc)" : "var(--pr-border-control)"}`,
              background: selected ? "var(--pr-acc-s)" : "var(--pr-bg-control)",
              color: selected ? "var(--pr-acc)" : "var(--pr-text-mid)",
              fontWeight: selected ? 600 : 400,
              fontSize: 12,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const primaryBtn: CSSProperties = {
  height: 30,
  padding: "0 16px",
  background: "var(--pr-a)",
  color: "#FFFFFF",
  border: "none",
  borderRadius: 7,
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const secondaryBtn: CSSProperties = {
  height: 30,
  padding: "0 14px",
  borderRadius: 7,
  border: "1px solid var(--pr-border-control)",
  background: "var(--pr-bg-control)",
  color: "var(--pr-text)",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};
