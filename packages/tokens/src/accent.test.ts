// VT-TOK-01: アクセント導出とトークン値がデザイン(plans/08)と一致することを検証
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test, expect, describe } from "vitest";
import { accentVars, ACCENTS, type AccentKey } from "./accent";
import { STATUS_COLORS, ANNOTATION_COLORS } from "./tokens";

const tokensCss = readFileSync(
  fileURLToPath(new URL("../css/tokens.css", import.meta.url)),
  "utf8",
);

describe("accentVars — plans/08 §2.3", () => {
  test("slate matches the §2.3 example byte-for-byte", () => {
    expect(accentVars("slate")).toEqual({
      "--pr-a": "#3E5C76",
      "--pr-as": "rgba(62,92,118,0.1)",
      "--pr-am": "rgba(62,92,118,0.32)",
      "--pr-ad": "#8FAECB",
      "--pr-ads": "rgba(143,174,203,0.14)",
      "--pr-adm": "rgba(143,174,203,0.4)",
      "--pr-selection": "rgba(62,92,118,0.22)",
      "--pr-selection-dark": "rgba(143,174,203,0.3)",
    });
  });

  test("all 4 accents use fixed transparency rules 0.10/0.32/0.14/0.40/0.22/0.30", () => {
    for (const key of Object.keys(ACCENTS) as AccentKey[]) {
      const v = accentVars(key);
      expect(v["--pr-a"]).toBe(ACCENTS[key].light);
      expect(v["--pr-ad"]).toBe(ACCENTS[key].dark);
      expect(v["--pr-as"]).toMatch(/,0\.1\)$/);
      expect(v["--pr-am"]).toMatch(/,0\.32\)$/);
      expect(v["--pr-ads"]).toMatch(/,0\.14\)$/);
      expect(v["--pr-adm"]).toMatch(/,0\.4\)$/);
      expect(v["--pr-selection"]).toMatch(/,0\.22\)$/);
      expect(v["--pr-selection-dark"]).toMatch(/,0\.3\)$/);
    }
  });
});

describe("tokens.css hex values — plans/08 §2.1", () => {
  test("annotation 4 colors are present verbatim", () => {
    expect(tokensCss).toContain("--pr-ann-important: #c49432;");
    expect(tokensCss).toContain("--pr-ann-question: #5884aa;");
    expect(tokensCss).toContain("--pr-ann-idea: #659471;");
    expect(tokensCss).toContain("--pr-ann-term: #82827e;");
  });

  test("status 6 colors are present verbatim (reading = accent alias)", () => {
    expect(tokensCss).toContain("--pr-status-to-read: #9aa0a6;");
    expect(tokensCss).toContain("--pr-status-read-next: #c49432;");
    expect(tokensCss).toContain("--pr-status-reading: var(--pr-acc);");
    expect(tokensCss).toContain("--pr-status-done: #659471;");
    expect(tokensCss).toContain("--pr-status-reread: #8e7aa6;");
    expect(tokensCss).toContain("--pr-status-on-hold: #b0aca2;");
  });

  test("dark block re-maps accent alias to dark accent", () => {
    expect(tokensCss).toContain("--pr-acc: var(--pr-ad);");
    expect(tokensCss).toContain('html[data-theme="dark"]');
  });
});

describe("tokens.ts constants align with tokens.css", () => {
  test("STATUS_COLORS hex equals tokens.css (case-insensitive)", () => {
    // planned=#9AA0A6, up_next=#C49432, done=#659471, reread=#8E7AA6, on_hold=#B0ACA2
    expect(STATUS_COLORS.planned.toLowerCase()).toBe("#9aa0a6");
    expect(STATUS_COLORS.up_next.toLowerCase()).toBe("#c49432");
    expect(STATUS_COLORS.reading).toBe("var(--pr-acc)");
    expect(STATUS_COLORS.done.toLowerCase()).toBe("#659471");
    expect(STATUS_COLORS.reread.toLowerCase()).toBe("#8e7aa6");
    expect(STATUS_COLORS.on_hold.toLowerCase()).toBe("#b0aca2");
  });

  test("ANNOTATION_COLORS fg equals tokens.css annotation colors", () => {
    expect(ANNOTATION_COLORS.important.fg.toLowerCase()).toBe("#c49432");
    expect(ANNOTATION_COLORS.question.fg.toLowerCase()).toBe("#5884aa");
    expect(ANNOTATION_COLORS.idea.fg.toLowerCase()).toBe("#659471");
    expect(ANNOTATION_COLORS.term.fg.toLowerCase()).toBe("#82827e");
  });
});
