import { defineConfig } from "@hey-api/openapi-ts";

/**
 * @alinea/api-client の生成設定(plans/00 §2、plans/03 §1.10、M0-24)。
 *
 * 入力は apps/api が書き出した openapi.json、出力は src/generated/(手編集禁止・
 * CI の openapi-drift ジョブでドリフト検出)。
 *
 * - format/lint はいずれも false(既定)。生成物は prettier/eslint の対象外
 *   (.prettierignore / eslint.config.mjs で src/generated を無視)なので、
 *   フォーマッタを通さず決定的なバイト列を保つ。
 * - clean:true(既定)で毎回出力フォルダを掃除し、再生成の冪等性を担保する。
 * - tsConfigPath は本パッケージの tsconfig に固定し、環境差(上位 tsconfig の
 *   探索)で相対 import の拡張子有無が揺れないようにする。
 */
export default defineConfig({
  input: "./openapi.json",
  output: {
    path: "src/generated",
    tsConfigPath: "./tsconfig.json",
  },
  plugins: ["@hey-api/client-fetch", "@hey-api/typescript", "@hey-api/sdk"],
});
