// @ts-check
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import prettier from "eslint-config-prettier";

/**
 * ルート flat config。各 app/package が `extends` して拡張する。
 * - typescript-eslint strict
 * - react-hooks
 * - 親方向 import (`../`) 禁止 → `@/` エイリアスを使う (plans/00 §6.3)
 * - apps 間 import 禁止
 */
export default tseslint.config(
  {
    ignores: [
      "**/node_modules/**",
      "**/.next/**",
      "**/.output/**",
      "**/dist/**",
      "**/coverage/**",
      "**/.turbo/**",
      "packages/api-client/src/generated/**",
      "packages/tokens/css/accents.css",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    files: ["**/*.{ts,tsx,mts}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["../*"],
              message:
                "親方向の相対 import は禁止。`@/` エイリアスを使ってください (plans/00 §6.3)。",
            },
            {
              group: ["@alinea/web/*", "@alinea/extension/*"],
              message: "apps 間の import は禁止。共有物は packages/ へ (plans/00 §6.3)。",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["apps/web/public/sw.js"],
    languageOptions: {
      globals: { self: "readonly", caches: "readonly", fetch: "readonly", URL: "readonly" },
    },
  },
  {
    files: ["apps/web/scripts/**/*.mjs"],
    languageOptions: {
      globals: { console: "readonly", process: "readonly" },
    },
  },
  prettier,
);
