// 状態2: 保存直後(3a §4.5・§5.3)。パイプライン進捗行 + プログレスバー + ボタン行。
// ヘッダ/フッタは App が描画する。進捗ポーリングは App が 2,000ms 間隔で行う。
import { PopupButtonRow } from "@/components/PopupButtonRow";
import { isProcessingStage, pipelineRows, type PipelineTone } from "@/lib/pipeline";

export interface SavedProps {
  title: string;
  stage: string;
  progressPct: number;
  failedReason?: string | null;
  /** 「サイトで開く ↗」/失敗時「サイトで確認 ↗」。 */
  onOpen: () => void;
  onClose: () => void;
  /** 取り込みキャンセル(docs/08 §2.2)。処理中(isProcessingStage)の間のみ表示。 */
  onCancel: () => void;
}

const TONE_CLASS: Record<PipelineTone, string> = {
  done: "ext-pl-done",
  active: "ext-pl-active",
  muted: "ext-pl-muted",
  warn: "ext-pl-warn",
};

export function Saved({
  title,
  stage,
  progressPct,
  failedReason,
  onOpen,
  onClose,
  onCancel,
}: SavedProps) {
  const failed = stage === "failed";
  const rows = pipelineRows(stage, progressPct, failedReason);

  return (
    <div className="ext-body">
      <div className="ext-paper-card">
        <div className="ext-thumb" aria-hidden="true" />
        <div className="ext-paper-main">
          <div className="ext-paper-title">{title}</div>
          <div className="ext-pipeline-row">
            {rows.map((row) => (
              <span key={row.label} className={TONE_CLASS[row.tone]}>
                {row.label}
              </span>
            ))}
          </div>
          <div className="ext-progress-track" aria-hidden="true">
            <div
              className="ext-progress-fill"
              style={{ width: `${Math.max(0, Math.min(100, progressPct))}%` }}
            />
          </div>
          {isProcessingStage(stage) ? (
            <button type="button" className="ext-link-cancel" onClick={onCancel}>
              取り込みを中止
            </button>
          ) : null}
        </div>
      </div>

      <PopupButtonRow
        primary={{ label: failed ? "サイトで確認 ↗" : "サイトで開く ↗", onClick: onOpen }}
        secondary={{ label: "閉じる", onClick: onClose }}
      />

      <div className="ext-desc-note">
        進捗はツールバーのバッジにも表示されます(処理中スピナー → 完了チェック)。読める部分から開けます。
      </div>
    </div>
  );
}
