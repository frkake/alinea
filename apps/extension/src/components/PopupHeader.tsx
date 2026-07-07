// ポップアップ共通ヘッダ(3a §3・§4.4/§4.5/§4.7)。ロゴ「訳」+タイトル+バッジ+設定ギア。

export type HeaderBadge =
  | { kind: "detect"; label: string } // 緑ピル「arXiv 論文を検出」
  | { kind: "success" } // 16×16 円 ✓
  | { kind: "pdf"; label: string } // グレーピル「PDF を表示中」
  | { kind: "unsupported"; label: string }; // グレーピル「対応外のページ」

export interface PopupHeaderProps {
  title: string;
  badge?: HeaderBadge;
  onOpenSettings?: () => void;
  /** 設定ビュー内(plans/10 §10.2)。指定時は「⚙」の代わりに「←」を表示する。 */
  onBack?: () => void;
}

export function PopupHeader({ title, badge, onOpenSettings, onBack }: PopupHeaderProps) {
  return (
    <header className="ext-header">
      {onBack ? (
        <button type="button" className="ext-back" aria-label="戻る" title="戻る" onClick={onBack}>
          ←
        </button>
      ) : (
        <span className="ext-logo" aria-hidden="true">
          訳
        </span>
      )}
      <span className="ext-header-title">{title}</span>
      {badge?.kind === "detect" && <span className="ext-badge ext-badge-ok">{badge.label}</span>}
      {badge?.kind === "pdf" && <span className="ext-badge ext-badge-gray">{badge.label}</span>}
      {badge?.kind === "unsupported" && (
        <span className="ext-badge ext-badge-gray">{badge.label}</span>
      )}
      {badge?.kind === "success" && (
        <span className="ext-badge-success" aria-hidden="true">
          ✓
        </span>
      )}
      {!onBack && onOpenSettings && (
        <button
          type="button"
          className="ext-gear"
          aria-label="設定"
          title="設定"
          onClick={onOpenSettings}
        >
          ⚙
        </button>
      )}
    </header>
  );
}
