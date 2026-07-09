// 拡張設定(⚙ の中身。plans/10 §10.1・§10.2)。独立オプションページは持たない
// (権限要求のユーザージェスチャーをポップアップ内で完結させるため)。
import { useEffect, useState } from "react";
import { browser } from "wxt/browser";

const ARXIV_ORIGIN = "https://arxiv.org/*";
const PILL_SCRIPT_ID = "arxiv-pill";
const PILL_ENABLED_KEY = "settings:arxivPillEnabled";

export interface SettingsProps {
  version: string;
  onOpenSiteSettings: () => void;
}

async function readPillEnabled(): Promise<boolean> {
  const store = await browser.storage.local.get(PILL_ENABLED_KEY);
  return store[PILL_ENABLED_KEY] === true;
}

/** ON: 権限要求 → 許可されたら runtime 登録。OFF: 登録解除 → 権限も返す(plans/10 §10.1)。 */
async function setPillEnabled(next: boolean): Promise<boolean> {
  if (next) {
    const granted = await browser.permissions.request({ origins: [ARXIV_ORIGIN] });
    if (!granted) return false;
    await browser.scripting.registerContentScripts([
      {
        id: PILL_SCRIPT_ID,
        matches: ["https://arxiv.org/abs/*"],
        js: ["content-scripts/arxiv-pill.js"],
        runAt: "document_idle",
        persistAcrossSessions: true,
      },
    ]);
  } else {
    await browser.scripting.unregisterContentScripts({ ids: [PILL_SCRIPT_ID] }).catch(() => undefined);
    await browser.permissions.remove({ origins: [ARXIV_ORIGIN] }).catch(() => undefined);
  }
  await browser.storage.local.set({ [PILL_ENABLED_KEY]: next });
  return true;
}

export function Settings({ version, onOpenSiteSettings }: SettingsProps) {
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void readPillEnabled().then((v) => {
      if (!cancelled) setEnabled(v);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleToggle = async () => {
    if (busy) return;
    setBusy(true);
    const next = !enabled;
    const applied = await setPillEnabled(next);
    if (applied) setEnabled(next);
    setBusy(false);
  };

  return (
    <div className="ext-body ext-settings">
      <div className="ext-settings-row">
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          className="ext-toggle"
          data-on={enabled}
          onClick={() => void handleToggle()}
          disabled={busy}
        >
          <span className="ext-toggle-track">
            <span className="ext-toggle-thumb" />
          </span>
          <span className="ext-toggle-label">arXiv ページに保存ボタンを表示</span>
        </button>
        <p className="ext-settings-desc">
          arxiv.org の論文ページに「A 保存」ボタンを追加します。有効化時に arxiv.org へのアクセス権限を求めます。
        </p>
      </div>

      <button type="button" className="ext-settings-link" onClick={onOpenSiteSettings}>
        Alineaの設定を開く ↗
      </button>

      <div className="ext-settings-version">Alinea拡張 v{version}</div>
    </div>
  );
}
