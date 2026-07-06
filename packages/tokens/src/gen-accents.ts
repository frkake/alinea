// packages/tokens/src/gen-accents.ts
// accent.ts から css/accents.css を決定的に生成する(同一入力→バイト同一。CI で git diff 空を検証)。
import { writeFileSync } from "node:fs";
import { ACCENTS, accentVars, type AccentKey, DEFAULT_ACCENT } from "./accent";

let css = `/* 生成物: pnpm gen で再生成。手編集禁止 */\n`;
for (const key of Object.keys(ACCENTS) as AccentKey[]) {
  const vars = accentVars(key);
  const sel =
    key === DEFAULT_ACCENT ? `:root, html[data-accent="${key}"]` : `html[data-accent="${key}"]`;
  css += `${sel} {\n`;
  for (const [k, v] of Object.entries(vars)) css += `  ${k}: ${v};\n`;
  css += `}\n`;
}
css += `html[data-theme="dark"] { --pr-selection: var(--pr-selection-dark); }\n`;
writeFileSync(new URL("../css/accents.css", import.meta.url), css);
