import { describe, expect, test } from "vitest";
import { cleanLatexDisplayText } from "@/components/viewer/latex-display-clean";
import cases from "./latex-display-clean.fixtures.json";

interface FixtureCase {
  description: string;
  input: string;
  output: string;
}

describe("cleanLatexDisplayText — shared fixture parity", () => {
  test.each(cases as FixtureCase[])(
    "$description",
    ({ input, output }) => {
      expect(cleanLatexDisplayText(input)).toBe(output);
    },
  );
});
