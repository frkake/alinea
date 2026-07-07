// 状態2/3 のボタン行(3a §3・§4.5/§4.6)。プライマリ(flex:1)+ セカンダリ。

export interface PopupButtonRowProps {
  primary: { label: string; onClick: () => void };
  secondary: { label: string; onClick: () => void };
}

export function PopupButtonRow({ primary, secondary }: PopupButtonRowProps) {
  return (
    <div className="ext-button-row">
      <button type="button" className="ext-btn ext-btn-primary" onClick={primary.onClick}>
        {primary.label}
      </button>
      <button type="button" className="ext-btn ext-btn-secondary" onClick={secondary.onClick}>
        {secondary.label}
      </button>
    </div>
  );
}
