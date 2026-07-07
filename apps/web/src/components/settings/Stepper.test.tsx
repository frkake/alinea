import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { Stepper } from "@/components/settings/Stepper";

describe("Stepper (4f §4.7.5)", () => {
  test("increments/decrements by step and formats the displayed value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <Stepper
        value={16.5}
        min={14}
        max={20}
        step={0.5}
        onChange={onChange}
        formatValue={(v) => `${v}px`}
        ariaLabel="本文サイズ"
      />,
    );
    expect(screen.getByRole("status", { name: "本文サイズ" })).toHaveTextContent("16.5px");

    await user.click(screen.getByRole("button", { name: "本文サイズを増やす" }));
    expect(onChange).toHaveBeenCalledWith(17);

    await user.click(screen.getByRole("button", { name: "本文サイズを減らす" }));
    expect(onChange).toHaveBeenCalledWith(16);
  });

  test("disables the decrement button at min and increment button at max", () => {
    render(
      <Stepper value={14} min={14} max={20} step={0.5} onChange={() => {}} ariaLabel="本文サイズ" />,
    );
    expect(screen.getByRole("button", { name: "本文サイズを減らす" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "本文サイズを増やす" })).not.toBeDisabled();
  });
});
