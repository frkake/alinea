import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ShareCollectionHeader } from "@/components/share/ShareCollectionHeader";

describe("ShareCollectionHeader", () => {
  test("通常表示(§4.3): 名前・説明・メタ行・締切バッジ", () => {
    render(
      <ShareCollectionHeader
        name="輪読会 2026-07"
        description="7/16(木)の輪読会で扱う候補。"
        sharedBy="YK"
        updatedAt="2026-07-06T10:00:00Z"
        itemCount={5}
        deadline="2026-07-16"
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "輪読会 2026-07" })).toBeInTheDocument();
    expect(screen.getByText("7/16(木)の輪読会で扱う候補。")).toBeInTheDocument();
    expect(screen.getByText(/YK さんが共有/)).toBeInTheDocument();
    expect(screen.getByText(/5 本/)).toBeInTheDocument();
    expect(screen.getByText("締切 7/16")).toBeInTheDocument();
  });

  test("縮退規則(§5.5): description=null で説明文が省略される", () => {
    render(
      <ShareCollectionHeader
        name="輪読会"
        description={null}
        sharedBy="YK"
        updatedAt="2026-07-06T10:00:00Z"
        itemCount={1}
        deadline={null}
      />,
    );
    expect(screen.queryByText(/輪読会で扱う/)).not.toBeInTheDocument();
  });

  test("縮退規則(§5.5): deadline=null で締切バッジと直前の中点が省略される", () => {
    render(
      <ShareCollectionHeader
        name="輪読会"
        description={null}
        sharedBy="YK"
        updatedAt="2026-07-06T10:00:00Z"
        itemCount={1}
        deadline={null}
      />,
    );
    expect(screen.queryByText(/締切/)).not.toBeInTheDocument();
    expect(screen.getByText(/1 本$/)).toBeInTheDocument();
  });
});
