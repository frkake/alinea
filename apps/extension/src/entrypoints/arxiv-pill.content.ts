// arXiv ページ内「A 保存」ピル(オプトイン・既定オフ。plans/10 §10.3・docs/08 §5)。
// abs ページのタイトル末尾に注入する。Shadow DOM でホスト CSS から隔離し、API 呼び出しは
// すべて background に委譲する(same-site クッキー送信は拡張コンテキスト発が条件のため)。
import { defineContentScript } from "wxt/utils/define-content-script";
import { createShadowRootUi } from "wxt/utils/content-script-ui/shadow-root";
import { browser } from "wxt/browser";

import { accentVars, DEFAULT_ACCENT, type AccentKey } from "@alinea/tokens";

import type { PillMessage, PillResult } from "@/lib/pill-protocol";

import "@/styles/arxiv-pill.css";

const SAVE_LABEL = "保存";
const SAVING_LABEL = "保存中…";
const SAVED_LABEL = "保存済み";
const ERROR_LABEL = "保存できませんでした";

function renderPillContents(pill: HTMLButtonElement, state: PillResult["state"] | "saving"): void {
  pill.classList.toggle("alinea-pill-saved", state === "saved");
  pill.style.opacity = state === "saving" ? "0.7" : "1";
  pill.disabled = state === "saved" || state === "saving";
  const logo = state === "saved" ? "✓" : "A";
  const label =
    state === "saved"
      ? SAVED_LABEL
      : state === "saving"
        ? SAVING_LABEL
        : state === "error"
          ? ERROR_LABEL
          : SAVE_LABEL;
  pill.replaceChildren();
  const logoEl = document.createElement("span");
  logoEl.className = "alinea-pill-logo";
  logoEl.textContent = logo;
  const labelEl = document.createElement("span");
  labelEl.className = "alinea-pill-label";
  labelEl.textContent = label;
  pill.append(logoEl, labelEl);
}

async function resolveAccentCss(): Promise<string> {
  const stored = await browser.storage.local.get("ui:accent");
  const key = (stored["ui:accent"] as AccentKey | undefined) ?? DEFAULT_ACCENT;
  const vars = accentVars(key);
  const decls = Object.entries(vars)
    .map(([name, value]) => `${name}: ${value};`)
    .join(" ");
  return `:host { ${decls} }`;
}

export default defineContentScript({
  // 静的 content_scripts を宣言しない(既定オフ。SettingsView 側の
  // chrome.scripting.registerContentScripts が実行時に登録する。plans/10 §10.1)。
  registration: "runtime",
  cssInjectionMode: "ui",
  async main(ctx) {
    // abs ページのみ動作(念のための防御。登録自体も abs 限定の matches で行う)。
    if (!/^\/abs\//.test(location.pathname)) return;
    const titleEl = document.querySelector("h1.title");
    if (!titleEl) return;

    let pillEl: HTMLButtonElement | null = null;
    let currentState: PillResult["state"] = "idle";
    let resetTimer: ReturnType<typeof setTimeout> | undefined;

    const setState = (state: PillResult["state"] | "saving") => {
      currentState = state === "saving" ? "idle" : state;
      if (pillEl) renderPillContents(pillEl, state);
    };

    async function sendPillMessage(message: PillMessage): Promise<PillResult> {
      const result = (await browser.runtime.sendMessage(message)) as PillResult | undefined;
      return result ?? { state: "hidden" };
    }

    const ui = await createShadowRootUi(ctx, {
      name: "alinea-pill",
      position: "inline",
      anchor: titleEl,
      append: "last",
      css: await resolveAccentCss(),
      onMount(container) {
        const pill = document.createElement("button");
        pill.type = "button";
        pill.className = "alinea-pill";
        pill.addEventListener("click", () => {
          if (currentState !== "idle") return;
          setState("saving");
          void sendPillMessage({ type: "PILL_SAVE", url: location.href }).then((result) => {
            setState(result.state);
            if (result.state === "error") {
              resetTimer = setTimeout(() => setState("idle"), 3000);
            }
          });
        });
        renderPillContents(pill, "idle");
        container.appendChild(pill);
        pillEl = pill;
        return pill;
      },
      onRemove() {
        if (resetTimer) clearTimeout(resetTimer);
      },
    });

    const initial = await sendPillMessage({ type: "PILL_CHECK", url: location.href });
    if (initial.state === "hidden") return; // 未ログイン・判定失敗 → 表示しない(再試行しない)。
    ui.mount();
    setState(initial.state);
  },
});
