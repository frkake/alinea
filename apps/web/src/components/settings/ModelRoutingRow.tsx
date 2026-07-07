"use client";

import { SettingsSelect } from "@/components/settings/SettingsSelect";
import {
  PROVIDER_LABELS,
  providersFor,
  type AvailableModels,
  type LlmUseCase,
  type ProviderId,
  type RouteEntry,
} from "@/components/settings/types";

/** 用途別 LLM ルーティング行(4f §4.7.2)。プロバイダ + モデルの 2 セレクト。 */
export interface ModelRoutingRowProps {
  useCase: LlmUseCase;
  label: string;
  description: string;
  value: RouteEntry;
  availableModels: AvailableModels;
  onChange: (next: RouteEntry) => void;
  divider?: boolean;
}

export function ModelRoutingRow({
  useCase,
  label,
  description,
  value,
  availableModels,
  onChange,
  divider = false,
}: ModelRoutingRowProps) {
  const providerOptions = providersFor(useCase).map((p) => ({ value: p, label: PROVIDER_LABELS[p] }));
  const models = availableModels[value.provider] ?? [];
  const modelOptions = models.map((m) => ({ value: m.model, label: m.label }));

  const onProviderChange = (provider: ProviderId) => {
    // プロバイダ変更時、モデルは新プロバイダの先頭に自動リセット(§4.7.2)。
    const first = availableModels[provider]?.[0]?.model ?? "";
    onChange({ provider, model: first });
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 18px",
        borderBottom: divider ? "1px solid var(--pr-border-hair)" : undefined,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{label}</span>
        <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>{description}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <SettingsSelect
          options={providerOptions}
          value={value.provider as ProviderId}
          onChange={onProviderChange}
          width={120}
          ariaLabel={`${label} のプロバイダ`}
        />
        <SettingsSelect
          options={modelOptions}
          value={value.model}
          onChange={(model) => {
            onChange({ provider: value.provider, model });
          }}
          width={200}
          ariaLabel={`${label} のモデル`}
        />
      </div>
    </div>
  );
}
