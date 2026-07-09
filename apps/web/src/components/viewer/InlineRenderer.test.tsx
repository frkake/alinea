import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";

describe("InlineRenderer citations", () => {
  test("compacts expanded author-year citation text", () => {
    render(
      <p>
        <InlineRenderer
          inlines={[
            { t: "text", v: "Recent advancements by " },
            {
              t: "citation",
              ref: "achiam2023gpt4",
              v: "Achiam et al.(2023)Achiam, Adler, Agarwal, Ahmad, Akkaya, Aleman, Almeida, Altenschmidt, Altman, Anadkat, et al.",
            },
            { t: "text", v: " enabled new results." },
          ]}
        />
      </p>,
    );

    expect(screen.getByRole("button", { name: "Achiam et al. (2023)" })).toBeInTheDocument();
    expect(screen.queryByText(/Adler/)).not.toBeInTheDocument();
  });

  test("compacts raw bibliography-like citation text", () => {
    render(
      <InlineRenderer
        inlines={[
          {
            t: "citation",
            ref: "dubey2024llama",
            v: "Dubey, Jauhri, Pandey, Kadian, Al-Dahle, Letman, Mathur, Schelten, Yang, Fan, et al. 2024. The Llama 3 Herd of Models.",
          },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: "Dubey et al. (2024)" })).toBeInTheDocument();
    expect(screen.queryByText(/Jauhri/)).not.toBeInTheDocument();
  });
});
