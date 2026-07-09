import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
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

  test("formats consecutive citations as a bracketed comma-separated group", () => {
    const onCitationClick = vi.fn();
    const { container } = render(
      <p>
        <InlineRenderer
          inlines={[
            { t: "text", v: "Related work " },
            { t: "citation", ref: "lu2024", v: "Lu et al. (2024)" },
            { t: "citation", ref: "wang2023", v: "Wang et al. (2023)" },
            { t: "text", v: " covers this." },
          ]}
          onCitationClick={onCitationClick}
        />
      </p>,
    );

    expect(container.textContent).toBe(
      "Related work [ Lu et al. (2024), Wang et al. (2023) ] covers this.",
    );

    fireEvent.click(screen.getByRole("button", { name: "Lu et al. (2024)" }));
    fireEvent.click(screen.getByRole("button", { name: "Wang et al. (2023)" }));
    expect(onCitationClick).toHaveBeenNthCalledWith(1, "lu2024");
    expect(onCitationClick).toHaveBeenNthCalledWith(2, "wang2023");
  });
});
