#!/usr/bin/env node
/**
 * PWA アイコン生成(一度だけ実行し、生成物 public/icons/*.png を Git にコミットする)。
 *
 * ビルドはこのスクリプトにも sharp にも依存しない — 出荷するのはコミット済み PNG のみ。
 * sharp は当リポジトリに推移的依存として既に存在する(node_modules/.pnpm/sharp@*)。
 *
 * 使い方(apps/web から):
 *   node scripts/gen-pwa-icons.mjs
 *
 * 入力:
 *   src/app/icon.svg               → icon-192.png / icon-512.png ("any")
 *   public/icons/icon-maskable.svg → icon-512-maskable.png ("maskable", 全面背景)
 *   src/app/icon.svg               → apple-touch-icon.png (180x180, iOS ホーム画面)
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { readFileSync } from "node:fs";

const require = createRequire(import.meta.url);
// sharp は推移的依存(未 hoist な場合あり)。通常解決 → SHARP_PATH 環境変数の順で探す。
function loadSharp() {
  try {
    return require("sharp");
  } catch {
    if (process.env.SHARP_PATH) return require(process.env.SHARP_PATH);
    throw new Error(
      "sharp を解決できません。`pnpm approve-builds` で sharp を許可するか、SHARP_PATH に sharp のパスを指定してください。",
    );
  }
}
const sharp = loadSharp();

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const iconSvg = readFileSync(resolve(webRoot, "src/app/icon.svg"));
const maskableSvg = readFileSync(resolve(webRoot, "public/icons/icon-maskable.svg"));
const outDir = resolve(webRoot, "public/icons");

const jobs = [
  { src: iconSvg, size: 192, out: "icon-192.png" },
  { src: iconSvg, size: 512, out: "icon-512.png" },
  { src: maskableSvg, size: 512, out: "icon-512-maskable.png" },
  { src: iconSvg, size: 180, out: "apple-touch-icon.png" },
];

for (const { src, size, out } of jobs) {
  await sharp(src, { density: 384 })
    .resize(size, size, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toFile(resolve(outDir, out));
  console.log(`wrote public/icons/${out} (${size}x${size})`);
}
