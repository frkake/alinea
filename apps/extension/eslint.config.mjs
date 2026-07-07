// 拡張の flat config。ルート設定を拡張し、WXT 生成物とビルド用 Node スクリプトを無視する
// (ルート eslint.config.mjs の「各 app/package が extends して拡張する」方針)。
import root from "../../eslint.config.mjs";

export default [
  ...root,
  {
    ignores: [".wxt/**", ".output/**", "scripts/**"],
  },
];
