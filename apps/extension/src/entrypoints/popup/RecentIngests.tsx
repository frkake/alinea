// フッタ「直近の取り込み」(3a §4.4 フッタ・§5.3。M0-36)。全状態共通で最大 3 件。
import { footerRightText, isProcessingStage } from "@/lib/pipeline";

/** API の IngestRecentItem を camelCase に詰め替えた行(3a §3・§5.3)。 */
export interface RecentIngestRow {
  libraryItemId: string;
  title: string;
  pipeline: { stage: string; progress_pct: number };
  completedAt: string | null;
  viewerUrl: string;
}

export interface RecentIngestsProps {
  items: RecentIngestRow[];
  onOpen: (viewerUrl: string) => void;
  /** 相対時刻の基準(テスト用に注入可能)。 */
  now?: Date;
}

export function RecentIngests({ items, onOpen, now }: RecentIngestsProps) {
  // 0 件のとき(履歴なし/取得失敗)はフッタ全体を非表示(3a §4.4 決定)。
  if (items.length === 0) return null;

  return (
    <footer className="ext-footer">
      <div className="ext-footer-heading">直近の取り込み</div>
      {items.map((item) => {
        const stage = item.pipeline.stage;
        const failed = stage === "failed";
        const processing = isProcessingStage(stage);
        const right = footerRightText(item.pipeline, item.completedAt, now);
        return (
          <button
            key={item.libraryItemId}
            type="button"
            className="ext-recent-row"
            onClick={() => onOpen(item.viewerUrl)}
            title={item.title}
          >
            {processing ? (
              <span className="ext-spinner" aria-hidden="true" />
            ) : failed ? (
              <span className="ext-recent-mark ext-recent-mark-fail" aria-hidden="true">
                ×
              </span>
            ) : (
              <span className="ext-recent-mark ext-recent-mark-done" aria-hidden="true">
                ✓
              </span>
            )}
            <span className="ext-recent-title">{item.title}</span>
            <span className={failed ? "ext-recent-right ext-recent-right-fail" : "ext-recent-right"}>
              {right}
            </span>
          </button>
        );
      })}
    </footer>
  );
}
