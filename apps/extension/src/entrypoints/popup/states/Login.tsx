// 状態0: 未ログイン(3a §5.1)。web のログインへ導線。フッタなし。

export interface LoginProps {
  onLogin: () => void;
}

export function Login({ onLogin }: LoginProps) {
  return (
    <div className="ext-body ext-login">
      <p className="ext-login-desc">保存にはログインが必要です。</p>
      <button type="button" className="ext-btn ext-btn-save ext-login-btn" onClick={onLogin}>
        ログイン
      </button>
    </div>
  );
}
