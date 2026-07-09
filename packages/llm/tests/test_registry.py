"""ModelRegistry / models.yaml の検証(plans/04 §7・§17-5)。"""

from __future__ import annotations

from pathlib import Path

from alinea_llm.registry import ModelRegistry
from alinea_llm.types import Usage

_MODELS_YAML = Path(__file__).resolve().parents[1] / "models.yaml"


def _registry() -> ModelRegistry:
    return ModelRegistry.from_yaml(_MODELS_YAML)


def test_models_yaml_loads_all_seed_models() -> None:
    reg = _registry()
    ids = {m.id for m in reg.all_models()}
    # 10 テキスト + 4 画像
    assert "gpt-5.5" in ids and "claude-opus-4-8" in ids and "grok-4.3" in ids
    assert "gemini-3.1-flash-image" in ids and "gpt-image-2" in ids
    assert reg.get("deepseek-v4-flash").pricing is not None


def test_forbidden_models_absent() -> None:
    # docs/09 §3.2 / plans/04 §7: 廃止・停止予定モデルは登録しない
    ids = {m.id for m in _registry().all_models()}
    for banned in (
        "deepseek-chat",
        "deepseek-reasoner",
        "imagen-4.0-generate-001",
        "gpt-image-1.5",
    ):
        assert banned not in ids


def test_text_cost_deepseek_flash_example() -> None:
    # §17-5: input 10,000 / cached 90,000 / output 5,000 → ≈ $0.00305
    reg = _registry()
    usage = Usage(input_tokens=10_000, cached_input_tokens=90_000, output_tokens=5_000)
    cost = reg.text_cost_usd("deepseek-v4-flash", usage)
    assert abs(cost - 0.003052) < 1e-9


def test_text_cost_anthropic_cache_write_uses_write_rate() -> None:
    # claude-opus-4-8: cache_write_per_mtok = 6.25(入力単価 5.00 の 1.25 倍)
    reg = _registry()
    usage = Usage(cache_write_input_tokens=1_000_000)
    cost = reg.text_cost_usd("claude-opus-4-8", usage)
    assert abs(cost - 6.25) < 1e-9


def test_image_cost_lookup() -> None:
    reg = _registry()
    assert reg.image_cost_usd("gpt-image-2", "standard") == 0.053
    assert reg.image_cost_usd("gpt-image-2", "high") == 0.211
