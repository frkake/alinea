import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { ApiKeyRow } from "@/components/settings/ApiKeyRow";
import { ModelRoutingRow } from "@/components/settings/ModelRoutingRow";
import type { AvailableModels } from "@/components/settings/types";

// 設定フォームの基本動作(M0-33)
describe("ApiKeyRow (BYOK)", () => {
  test("unset row shows 未設定 and 設定, then saves a trimmed key", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <ApiKeyRow provider="openai" masked={null} createdAt={null} onSave={onSave} onDelete={() => {}} />,
    );

    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getByText("未設定")).toBeInTheDocument();
    expect(screen.queryByText("削除")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "設定" }));
    const input = screen.getByPlaceholderText("API キーを貼り付け");
    await user.type(input, "  sk-secret-key  ");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(onSave).toHaveBeenCalledWith("sk-secret-key");
  });

  test("set row shows masked + created date and deletes", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(
      <ApiKeyRow
        provider="anthropic"
        masked="sk-…3fA"
        createdAt="2026-07-01T12:00:00Z"
        onSave={() => Promise.resolve()}
        onDelete={onDelete}
      />,
    );
    expect(screen.getByText("sk-…3fA · 登録: 2026/7/1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "再設定" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "削除" }));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  test("422 (validation_error) shows an inline message and keeps the popover open (4f §5.4)", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue({ code: "validation_error", status: 422 });
    render(
      <ApiKeyRow provider="openai" masked={null} createdAt={null} onSave={onSave} onDelete={() => {}} />,
    );

    await user.click(screen.getByRole("button", { name: "設定" }));
    const input = screen.getByPlaceholderText("API キーを貼り付け");
    await user.type(input, "bad-key");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("キーの形式が正しくありません")).toBeInTheDocument();
    // ポップオーバーは開いたまま(入力欄が見え続ける)
    expect(screen.getByPlaceholderText("API キーを貼り付け")).toBeInTheDocument();
  });

  test("non-422 failure does not show the inline 422 message (Toast is the caller's responsibility)", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue(new Error("network error"));
    render(
      <ApiKeyRow provider="openai" masked={null} createdAt={null} onSave={onSave} onDelete={() => {}} />,
    );

    await user.click(screen.getByRole("button", { name: "設定" }));
    const input = screen.getByPlaceholderText("API キーを貼り付け");
    await user.type(input, "some-key");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(screen.queryByText("キーの形式が正しくありません")).not.toBeInTheDocument();
  });
});

describe("ModelRoutingRow", () => {
  const availableModels: AvailableModels = {
    anthropic: [
      { model: "claude-opus-4-8", label: "Claude Opus 4.8" },
      { model: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
    ],
    openai: [{ model: "gpt-5", label: "GPT-5" }],
  };

  test("changing provider resets the model to the new provider's first model", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelRoutingRow
        useCase="summary"
        label="要約"
        description="3 行要約に使用"
        value={{ provider: "anthropic", model: "claude-opus-4-8" }}
        availableModels={availableModels}
        onChange={onChange}
      />,
    );

    // 現在のプロバイダ・モデルが表示されている
    expect(screen.getByRole("button", { name: "要約 のプロバイダ" })).toHaveTextContent("Anthropic");
    expect(screen.getByRole("button", { name: "要約 のモデル" })).toHaveTextContent("Claude Opus 4.8");

    await user.click(screen.getByRole("button", { name: "要約 のプロバイダ" }));
    await user.click(screen.getByRole("option", { name: "OpenAI" }));
    expect(onChange).toHaveBeenCalledWith({ provider: "openai", model: "gpt-5" });
  });
});
