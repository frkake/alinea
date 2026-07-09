import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { ReferenceItem } from "@alinea/api-client";
import { ReferencesList } from "./ReferencesList";

function refItem(overrides: Partial<ReferenceItem> = {}): ReferenceItem {
  return {
    ref_id: "ref-1",
    aliases: ["bib-1"],
    number: "[1]",
    raw: "Doe, J. Notes on raw references. 2025.",
    authors: null,
    title: null,
    venue_year: null,
    arxiv_id: null,
    doi: null,
    url: null,
    in_library: null,
    ...overrides,
  };
}

describe("ReferencesList", () => {
  test("falls back to the raw reference text when structured fields are absent", () => {
    render(
      <ReferencesList
        references={[refItem()]}
        expandedRefId={null}
        onToggle={vi.fn()}
        onImport={vi.fn()}
        onOpenInLibrary={vi.fn()}
      />,
    );
    expect(screen.getByText(/Doe, J\. Notes on raw references/)).toBeInTheDocument();
  });

  test("scrolls the expanded reference row into view", async () => {
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;

    try {
      render(
        <ReferencesList
          references={[
            refItem(),
            refItem({ ref_id: "ref-2", number: "[2]", raw: "Second reference." }),
          ]}
          expandedRefId="ref-2"
          onToggle={vi.fn()}
          onImport={vi.fn()}
          onOpenInLibrary={vi.fn()}
        />,
      );
      await waitFor(() =>
        expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", behavior: "smooth" }),
      );
    } finally {
      if (originalScrollIntoView) {
        HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
      } else {
        delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
      }
    }
  });
});
